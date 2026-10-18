// DE2-115 synthesis and timing harness for pcie_core
// (wiki/src/fpga/core/FPGA.md). Board policy only, not part of core.rtl.f.
//
// The board has no pins for the endpoint's 350-odd data ports, so the
// wrapper bounds every pcie_core port with flops on its own clock: the
// inputs come from a serial-in shift register per domain, fed by one slide
// switch, and the outputs are registered and folded into one parity LED.
// No port is constant and every output is observed, so synthesis keeps the
// whole stack. Both clocks enter on clock pins at 50 MHz, the FPGA timing
// target. It follows the link harness (src/fpga/link/pcie_link_de2_115.sv).
`default_nettype none

`include "common_macros.svh"

module pcie_core_de2_115 (
  input  logic       clk_i,      // CLOCK_50: board oscillator, core clock (clk_i)
  input  logic       rx_clk_i,   // HSMC_CLKIN0: independent recovered RX clock (ser_rx_clk_i)
  input  logic       key0_ni,    // KEY[0]: push button, low while pressed; held: reset
  input  logic [1:0] sw_i,       // SW[1:0]: [0] core-domain stimulus, [1] RX-domain stimulus
  output logic [5:0] ledr_o,     // LEDR[5:0]: LTSSM state
  output logic [1:0] ledg_o      // LEDG[1:0]: [0] link up, [1] parity of every other output
);

  // Power-on reset: por_cnt_q starts at 0 after configuration (power-up
  // don't-care is disabled in de2_115.qsf.tcl) and counts to all ones;
  // reset is held until then, at least the 8 clk_i cycles the stack needs.
  localparam int unsigned POR_W = 4;

  // Stimulus widths: every non-clock pcie_core input except the reset, per
  // domain.
  localparam int unsigned STIM_W    = 165;  // clk_i: cq_ready 1, cc 38, rq 117, rc_ready 1,
                                            //   irq 7, rxdet 1
  localparam int unsigned RX_STIM_W = 21;   // ser_rx_clk_i: 20 data bits, electrical idle
  // Registered pcie_core outputs folded into the parity LED: every output
  // but ltssm_state_o and link_up_o, which have their own LEDs.
  localparam int unsigned OBS_W     = 159;

  logic [POR_W-1:0]     por_cnt_d;
  logic [POR_W-1:0]     por_cnt_q;
  logic                 por_done;

  // Two-flop synchronizers: the button and the switches are asynchronous
  // to both clocks.
  logic                 key0_n;
  logic                 sw_core;
  logic                 sw_rx;
  logic                 key_sync_q;
  logic                 sw_sync_q;
  logic                 rx_sw_sync_q;

  // rst_q is the stack reset, high during the power-on count and while
  // KEY[0] is held. It is a flop with no logic after it, because it reaches
  // the pcs RX reset synchronizer's asynchronous set (common_cdc_rst takes
  // its source straight from a flop). It powers up 0 and goes high at the
  // first clk_i edge.
  logic                 rst_d;
  logic                 rst_q;

  logic [STIM_W-1:0]    stim_d;
  logic [STIM_W-1:0]    stim_q;
  logic [RX_STIM_W-1:0] rx_stim_d;
  logic [RX_STIM_W-1:0] rx_stim_q;

  logic                 cq_valid;
  logic                 cq_sop;
  logic                 cq_eop;
  logic [31:0]          cq_data;
  logic                 cq_write;
  logic [11:0]          cq_addr;
  logic [10:0]          cq_len;
  logic [3:0]           cq_first_be;
  logic [3:0]           cq_last_be;
  logic [2:0]           cq_handle;
  logic                 cc_ready;
  logic                 rq_ready;
  logic                 rc_valid;
  logic                 rc_sop;
  logic                 rc_eop;
  logic [31:0]          rc_data;
  logic [2:0]           rc_tag;
  logic [1:0]           rc_status;
  logic                 rc_done;
  logic                 irq_ready;
  logic                 irq_done;
  logic [1:0]           irq_status;
  logic                 bus_master_en;
  logic                 mem_space_en;
  logic [1:0]           d_state;
  logic [2:0]           max_payload;
  logic [2:0]           max_read_req;
  logic                 dl_up;
  logic [1:0]           dl_state;
  logic [6:0]           dl_err;
  logic                 link_up;
  logic [5:0]           ltssm_state;
  logic                 rcv_err;
  logic [19:0]          ser_tx_data;
  logic                 ser_tx_elec_idle;

  logic [OBS_W-1:0]     obs_d;
  logic [OBS_W-1:0]     obs_q;
  logic                 par_d;
  logic                 par_q;
  logic [5:0]           ltssm_q;
  logic                 link_up_q;

  assign por_done = &por_cnt_q;

  always_comb begin
    por_cnt_d = por_cnt_q;
    if (!por_done) por_cnt_d = por_cnt_q + POR_W'(1);
  end

  `COMMON_DFF(por_cnt_q, por_cnt_d, clk_i)

  assign key0_n  = key0_ni;
  assign sw_core = sw_i[0];
  assign sw_rx   = sw_i[1];

  // The synchronizers have no reset: the reset itself comes from KEY[0].
  `COMMON_METAFLOP(u_key_sync, key_sync_q, key0_n, clk_i, 1'b0)
  `COMMON_METAFLOP(u_sw_sync, sw_sync_q, sw_core, clk_i, 1'b0)
  `COMMON_METAFLOP(u_rx_sw_sync, rx_sw_sync_q, sw_rx, rx_clk_i, 1'b0)

  assign rst_d = ~(por_done & key_sync_q);

  `COMMON_DFF(rst_q, rst_d, clk_i)

  // Serial-in shift registers: each stimulus bit is a distinct flop on the
  // domain of the input it drives. No reset; they start at 0.
  assign stim_d    = {stim_q[STIM_W-2:0], sw_sync_q};
  assign rx_stim_d = {rx_stim_q[RX_STIM_W-2:0], rx_sw_sync_q};

  `COMMON_DFF(stim_q, stim_d, clk_i)
  `COMMON_DFF(rx_stim_q, rx_stim_d, rx_clk_i)

  // Default parameters: TIMEOUT_DIV, ERR_TIMEOUT_DIV and CPL_TIMEOUT_DIV
  // are 1, as the core HAS asks for FPGA builds; BAR0_ADDR_W 12 and
  // MSIX_TABLE_SIZE 8.
  pcie_core u_core (
    .clk_i              (clk_i),
    .rst_i              (rst_q),
    .cq_valid_o         (cq_valid),
    .cq_ready_i         (stim_q[0]),
    .cq_sop_o           (cq_sop),
    .cq_eop_o           (cq_eop),
    .cq_data_o          (cq_data),
    .cq_write_o         (cq_write),
    .cq_addr_o          (cq_addr),
    .cq_len_o           (cq_len),
    .cq_first_be_o      (cq_first_be),
    .cq_last_be_o       (cq_last_be),
    .cq_handle_o        (cq_handle),
    .cc_valid_i         (stim_q[1]),
    .cc_ready_o         (cc_ready),
    .cc_sop_i           (stim_q[2]),
    .cc_eop_i           (stim_q[3]),
    .cc_data_i          (stim_q[35:4]),
    .cc_handle_i        (stim_q[38:36]),
    .rq_valid_i         (stim_q[39]),
    .rq_ready_o         (rq_ready),
    .rq_sop_i           (stim_q[40]),
    .rq_eop_i           (stim_q[41]),
    .rq_data_i          (stim_q[73:42]),
    .rq_write_i         (stim_q[74]),
    .rq_addr_i          (stim_q[138:75]),
    .rq_len_i           (stim_q[144:139]),
    .rq_first_be_i      (stim_q[148:145]),
    .rq_last_be_i       (stim_q[152:149]),
    .rq_tag_i           (stim_q[155:153]),
    .rc_valid_o         (rc_valid),
    .rc_ready_i         (stim_q[156]),
    .rc_sop_o           (rc_sop),
    .rc_eop_o           (rc_eop),
    .rc_data_o          (rc_data),
    .rc_tag_o           (rc_tag),
    .rc_status_o        (rc_status),
    .rc_done_o          (rc_done),
    .irq_valid_i        (stim_q[157]),
    .irq_ready_o        (irq_ready),
    .irq_vector_i       (stim_q[163:158]),
    .irq_done_o         (irq_done),
    .irq_status_o       (irq_status),
    .bus_master_en_o    (bus_master_en),
    .mem_space_en_o     (mem_space_en),
    .d_state_o          (d_state),
    .max_payload_o      (max_payload),
    .max_read_req_o     (max_read_req),
    .dl_up_o            (dl_up),
    .dl_state_o         (dl_state),
    .dl_err_o           (dl_err),
    .link_up_o          (link_up),
    .ltssm_state_o      (ltssm_state),
    .rcv_err_o          (rcv_err),
    .ser_tx_data_o      (ser_tx_data),
    .ser_tx_elec_idle_o (ser_tx_elec_idle),
    .ser_tx_rxdet_i     (stim_q[164]),
    .ser_rx_clk_i       (rx_clk_i),
    .ser_rx_data_i      (rx_stim_q[19:0]),
    .ser_rx_elec_idle_i (rx_stim_q[20])
  );

  // Every output is registered on clk_i before it is folded, so the parity
  // tree never lengthens a pcie_core path.
  assign obs_d = {cq_valid, cq_sop, cq_eop, cq_data, cq_write, cq_addr,
                  cq_len, cq_first_be, cq_last_be, cq_handle, cc_ready,
                  rq_ready, rc_valid, rc_sop, rc_eop, rc_data, rc_tag,
                  rc_status, rc_done, irq_ready, irq_done, irq_status,
                  bus_master_en, mem_space_en, d_state, max_payload,
                  max_read_req, dl_up, dl_state, dl_err, rcv_err,
                  ser_tx_data, ser_tx_elec_idle};
  assign par_d = ^obs_q;

  `COMMON_DFF(obs_q, obs_d, clk_i)
  `COMMON_DFF(par_q, par_d, clk_i)
  `COMMON_DFF(ltssm_q, ltssm_state, clk_i)
  `COMMON_DFF(link_up_q, link_up, clk_i)

  assign ledr_o = ltssm_q;
  assign ledg_o = {par_q, link_up_q};

endmodule

`default_nettype wire
