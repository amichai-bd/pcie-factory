// DE2-115 synthesis and timing harness for pcie_phy
// (wiki/src/fpga/phy/FPGA.md). Board policy only, not part of phy.rtl.f.
//
// The board has no pins for the stack's 100-odd data ports, so the wrapper
// bounds every pcie_phy port with flops on its own clock: the inputs come
// from a serial-in shift register per domain, fed by one slide switch, and
// the outputs are registered and folded into one parity LED. No port is
// constant and every output is observed, so synthesis keeps the whole
// stack. Both clocks enter on clock pins at 50 MHz, the FPGA timing target.
// The board loopback demo is a separate issue (#89).
`default_nettype none

`include "common_macros.svh"

module pcie_phy_de2_115 (
  input  logic       clk_i,      // CLOCK_50: board oscillator, core clock and PCLK (clk_i)
  input  logic       rx_clk_i,   // HSMC_CLKIN0: independent recovered RX clock (ser_rx_clk_i)
  input  logic       key0_ni,    // KEY[0]: push button, low while pressed; held: reset
  input  logic [2:0] sw_i,       // SW[2:0]: [0] port role, [1] core-domain stimulus,
                                 //   [2] RX-domain stimulus
  output logic [5:0] ledr_o,     // LEDR[5:0]: LTSSM state
  output logic [1:0] ledg_o      // LEDG[1:0]: [0] link up, [1] parity of every other output
);

  // Power-on reset: por_cnt_q starts at 0 after configuration (power-up
  // don't-care is disabled in de2_115.qsf.tcl) and counts to all ones;
  // reset is held until then, at least the 8 clk_i cycles the phy needs.
  localparam int unsigned POR_W = 4;

  // Stimulus widths: every non-clock pcie_phy input except the static
  // port role, per domain.
  localparam int unsigned STIM_W    = 28;  // clk_i: packet transmit 21, link control 6, rxdet 1
  localparam int unsigned RX_STIM_W = 21;  // ser_rx_clk_i: 20 data bits, electrical idle
  // Registered pcie_phy outputs folded into the parity LED.
  localparam int unsigned OBS_W     = 45;

  logic [POR_W-1:0]     por_cnt_d;
  logic [POR_W-1:0]     por_cnt_q;
  logic                 por_done;

  // Two-flop synchronizers: the button and the switches are asynchronous
  // to both clocks.
  logic                 key0_n;
  logic [1:0]           sw_core;
  logic                 sw_rx;
  logic                 key_sync_q;
  logic [1:0]           sw_sync_q;
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

  logic                 pkt_tx_ready;
  logic                 pkt_rx_valid;
  logic [15:0]          pkt_rx_data;
  logic                 pkt_rx_sop;
  logic                 pkt_rx_eop;
  logic                 pkt_rx_dllp;
  logic                 pkt_rx_err;
  logic                 pkt_rx_nullify;
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
  assign sw_core = sw_i[1:0];
  assign sw_rx   = sw_i[2];

  // The synchronizers have no reset: the reset itself comes from KEY[0].
  `COMMON_METAFLOP(u_key_sync, key_sync_q, key0_n, clk_i, 1'b0)
  `COMMON_METAFLOP(u_sw_sync, sw_sync_q, sw_core, clk_i, 1'b0)
  `COMMON_METAFLOP(u_rx_sw_sync, rx_sw_sync_q, sw_rx, rx_clk_i, 1'b0)

  assign rst_d = ~(por_done & key_sync_q);

  `COMMON_DFF(rst_q, rst_d, clk_i)

  // Serial-in shift registers: each stimulus bit is a distinct flop on the
  // domain of the input it drives. No reset; they start at 0.
  assign stim_d    = {stim_q[STIM_W-2:0], sw_sync_q[1]};
  assign rx_stim_d = {rx_stim_q[RX_STIM_W-2:0], rx_sw_sync_q};

  `COMMON_DFF(stim_q, stim_d, clk_i)
  `COMMON_DFF(rx_stim_q, rx_stim_d, rx_clk_i)

  // FPGA builds use TIMEOUT_DIV and ERR_TIMEOUT_DIV 1 (phy HAS).
  pcie_phy #(
    .TIMEOUT_DIV     (1),
    .ERR_TIMEOUT_DIV (1)
  ) u_phy (
    .clk_i              (clk_i),
    .rst_i              (rst_q),
    .port_role_i        (sw_sync_q[0]),
    .pkt_tx_valid_i     (stim_q[0]),
    .pkt_tx_ready_o     (pkt_tx_ready),
    .pkt_tx_data_i      (stim_q[16:1]),
    .pkt_tx_sop_i       (stim_q[17]),
    .pkt_tx_eop_i       (stim_q[18]),
    .pkt_tx_dllp_i      (stim_q[19]),
    .pkt_tx_nullify_i   (stim_q[20]),
    .pkt_rx_valid_o     (pkt_rx_valid),
    .pkt_rx_data_o      (pkt_rx_data),
    .pkt_rx_sop_o       (pkt_rx_sop),
    .pkt_rx_eop_o       (pkt_rx_eop),
    .pkt_rx_dllp_o      (pkt_rx_dllp),
    .pkt_rx_err_o       (pkt_rx_err),
    .pkt_rx_nullify_o   (pkt_rx_nullify),
    .link_up_o          (link_up),
    .ltssm_state_o      (ltssm_state),
    .rcv_err_o          (rcv_err),
    .retrain_req_i      (stim_q[21]),
    .hot_reset_req_i    (stim_q[22]),
    .disable_req_i      (stim_q[23]),
    .loopback_req_i     (stim_q[24]),
    .pm_state_req_i     (stim_q[26:25]),
    .ser_tx_data_o      (ser_tx_data),
    .ser_tx_elec_idle_o (ser_tx_elec_idle),
    .ser_tx_rxdet_i     (stim_q[27]),
    .ser_rx_clk_i       (rx_clk_i),
    .ser_rx_data_i      (rx_stim_q[19:0]),
    .ser_rx_elec_idle_i (rx_stim_q[20])
  );

  // Every output is registered on clk_i before it is folded, so the parity
  // tree never lengthens a pcie_phy path.
  assign obs_d = {pkt_tx_ready, pkt_rx_valid, pkt_rx_data, pkt_rx_sop,
                  pkt_rx_eop, pkt_rx_dllp, pkt_rx_err, pkt_rx_nullify,
                  rcv_err, ser_tx_data, ser_tx_elec_idle};
  assign par_d = ^obs_q;

  `COMMON_DFF(obs_q, obs_d, clk_i)
  `COMMON_DFF(par_q, par_d, clk_i)
  `COMMON_DFF(ltssm_q, ltssm_state, clk_i)
  `COMMON_DFF(link_up_q, link_up, clk_i)

  assign ledr_o = ltssm_q;
  assign ledg_o = {par_q, link_up_q};

endmodule

`default_nettype wire
