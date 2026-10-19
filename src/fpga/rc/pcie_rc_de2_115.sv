// DE2-115 board build of pcie_rc (wiki/src/fpga/rc/FPGA.md). Board policy
// only, not part of rc.rtl.f.
//
// pcie_rc runs at LOWER 1: the GPIO adapter takes the place of the phy
// inside link, and its 12 pins go to the 40-pin expansion header as the
// gpio HAS (DE2-115 pins) maps them. rc_seq's start comes from KEY[1] and
// its result goes to LEDs, so the build is the host board of the board
// pair (goal items 9 and 10). The partner's forwarded clock,
// gpio_rx_clk_i, is the second clock. Synthesis evidence only: no board is
// programmed.
`default_nettype none

`include "common_macros.svh"

module pcie_rc_de2_115 (
  input  logic        clk_i,            // CLOCK_50: board oscillator, core clock (clk_i)
  input  logic [1:0]  key_ni,           // KEY[1:0]: push buttons, low while pressed; [0] held: reset, [1] start
  output logic [15:0] ledr_o,           // LEDR[15:0]: fail_step_o
  output logic [3:0]  ledg_o,           // LEDG[3:0]: done, pass, link up, data link up
  output logic        gpio_tx_clk_o,    // GPIO[9]: forwarded clock, clk_i / GPIO_CLK_DIV
  output logic        gpio_tx_ctrl_o,   // GPIO[4]: control pin
  output logic [3:0]  gpio_tx_data_o,   // GPIO[3:0]: data pins
  input  logic        gpio_rx_clk_i,    // GPIO[11]: the partner's forwarded clock // cdc-domain: gpio_rx_clk_i
  input  logic        gpio_rx_ctrl_i,   // GPIO[10]: the partner's control pin // cdc-domain: gpio_rx_clk_i
  input  logic [3:0]  gpio_rx_data_i    // GPIO[8:5]: the partner's data pins // cdc-domain: gpio_rx_clk_i
);

  // Power-on reset: por_cnt_q starts at 0 after configuration (power-up
  // don't-care is disabled in de2_115.qsf.tcl) and counts to all ones;
  // reset is held until then, at least the 8 clk_i cycles the stack needs.
  localparam int unsigned POR_W = 4;

  logic [POR_W-1:0] por_cnt_d;
  logic [POR_W-1:0] por_cnt_q;
  logic             por_done;

  // Two-flop synchronizers: the buttons are asynchronous to clk_i.
  logic             key0_n;
  logic             key1_n;
  logic             key0_sync_q;
  logic             key1_sync_q;

  // rst_q is the stack reset, high during the power-on count and while
  // KEY[0] is held. It is a flop with no logic after it, because it reaches
  // the gpio receive-domain reset synchronizer's asynchronous input
  // (common_cdc_rst takes its source straight from a flop). It powers up 0
  // and goes high at the first clk_i edge.
  logic             rst_d;
  logic             rst_q;

  // start_i: 1 while KEY[1] is held; a press is the rising edge that
  // starts a run (rc HAS, sequencer run).
  logic             start_d;
  logic             start_q;

  logic             done;
  logic             pass;
  logic [15:0]      fail_step;
  logic             dl_up;
  logic [1:0]       dl_state;
  logic [6:0]       dl_err;
  logic             link_up;
  logic [5:0]       ltssm_state;
  logic             rcv_err;
  logic [19:0]      ser_tx_data;
  logic             ser_tx_elec_idle;

  logic [19:0]      led_d;
  logic [19:0]      led_q;

  assign por_done = &por_cnt_q;

  always_comb begin
    por_cnt_d = por_cnt_q;
    if (!por_done) por_cnt_d = por_cnt_q + POR_W'(1);
  end

  `COMMON_DFF(por_cnt_q, por_cnt_d, clk_i)

  assign key0_n = key_ni[0];
  assign key1_n = key_ni[1];

  // The synchronizers have no reset: the reset itself comes from KEY[0].
  `COMMON_METAFLOP(u_key0_sync, key0_sync_q, key0_n, clk_i, 1'b0)
  `COMMON_METAFLOP(u_key1_sync, key1_sync_q, key1_n, clk_i, 1'b0)

  assign rst_d   = ~(por_done & key0_sync_q);
  assign start_d = ~key1_sync_q;

  `COMMON_DFF(rst_q, rst_d, clk_i)
  `COMMON_DFF(start_q, start_d, clk_i)

  // Default parameters but LOWER: TIMEOUT_DIV, ERR_TIMEOUT_DIV and
  // CPL_TIMEOUT_DIV 1, as the rc HAS asks for the FPGA build; PROGRAM 1
  // (MMIO, enumeration then BAR0 traffic), the board build's program;
  // GPIO_CLK_DIV 2. LOWER 1 selects the GPIO adapter.
  pcie_rc #(
    .LOWER (1)
  ) u_rc (
    .clk_i              (clk_i),
    .rst_i              (rst_q),
    .start_i            (start_q),
    .done_o             (done),
    .pass_o             (pass),
    .fail_step_o        (fail_step),
    .dl_up_o            (dl_up),
    .dl_state_o         (dl_state),
    .dl_err_o           (dl_err),
    .link_up_o          (link_up),
    .ltssm_state_o      (ltssm_state),
    .rcv_err_o          (rcv_err),
    // The phy symbol interface: unused at LOWER 1 (link HAS, lower layer).
    .ser_tx_data_o      (ser_tx_data),
    .ser_tx_elec_idle_o (ser_tx_elec_idle),
    .ser_tx_rxdet_i     (1'b0),
    .ser_rx_clk_i       (1'b0),
    .ser_rx_data_i      (20'd0),
    .ser_rx_elec_idle_i (1'b1),
    .gpio_tx_clk_o      (gpio_tx_clk_o),
    .gpio_tx_ctrl_o     (gpio_tx_ctrl_o),
    .gpio_tx_data_o     (gpio_tx_data_o),
    .gpio_rx_clk_i      (gpio_rx_clk_i),
    .gpio_rx_ctrl_i     (gpio_rx_ctrl_i),
    .gpio_rx_data_i     (gpio_rx_data_i)
  );

  // The data link and LTSSM detail has no LED: the result LEDs and the
  // link-up LEDs carry the board build's outcome. The phy outputs are
  // constant at LOWER 1.
  `COMMON_UNUSED_BITS(unused_status, {dl_state, dl_err, ltssm_state, rcv_err,
                                      ser_tx_data, ser_tx_elec_idle})

  // The LEDs are registered on clk_i.
  assign led_d = {dl_up, link_up, pass, done, fail_step};

  `COMMON_DFF(led_q, led_d, clk_i)

  assign ledr_o = led_q[15:0];
  assign ledg_o = led_q[19:16];

endmodule

`default_nettype wire
