`default_nettype none

// Pair testbench top of the gpio unit DV (wiki/src/dv/gpio/DV_PLAN.md).
//
// Two pcie_gpio instances, a and b, each on its own clock (a_clk_i,
// b_clk_i), joined pin to pin by the modelled GPIO wires: each one's
// receive pins are the other's transmit pins, the forwarded clock included.
// Every pcie_gpio port is a port of this top, prefixed a_ or b_; the
// transmit pins stay visible for the wire monitor. The cocotb environment
// (tb/gpio_env.py) can override a direction's control and data wires
// (<dir>_ovr_i, ab: a to b, ba: b to a) to damage or forge transfers; the
// forwarded clocks are never overridden.
//
// RATIO selects b's clock against a's (1/2, 1 or 2) and is echoed on
// cfg_ratio_o with CLK_DIV on cfg_clk_div_o; the environment starts the
// clocks from them. A pin checker stops the run if a transmit pin changes
// at an edge where the forwarded clock does not fall (HAS, pins and
// transfers).
module pcie_gpio_tb #(
  parameter int unsigned CLK_DIV = 2,
  parameter int unsigned RATIO   = 1   // b's clock: 0 half of a's, 1 equal, 2 twice
) (
  input  logic        a_clk_i,
  input  logic        b_clk_i,
  input  logic        a_rst_i,
  input  logic        a_port_role_i,
  input  logic        a_pkt_tx_valid_i,
  input  logic [15:0] a_pkt_tx_data_i,
  input  logic        a_pkt_tx_sop_i,
  input  logic        a_pkt_tx_eop_i,
  input  logic        a_pkt_tx_dllp_i,
  input  logic        a_hot_reset_req_i,
  output logic        a_pkt_tx_ready_o,
  output logic        a_pkt_rx_valid_o,
  output logic [15:0] a_pkt_rx_data_o,
  output logic        a_pkt_rx_sop_o,
  output logic        a_pkt_rx_eop_o,
  output logic        a_pkt_rx_dllp_o,
  output logic        a_pkt_rx_err_o,
  output logic        a_pkt_rx_nullify_o,
  output logic        a_link_up_o,
  output logic [5:0]  a_ltssm_state_o,
  output logic        a_rcv_err_o,
  output logic        a_gpio_tx_clk_o,
  output logic        a_gpio_tx_ctrl_o,
  output logic [3:0]  a_gpio_tx_data_o,
  input  logic        b_rst_i,
  input  logic        b_port_role_i,
  input  logic        b_pkt_tx_valid_i,
  input  logic [15:0] b_pkt_tx_data_i,
  input  logic        b_pkt_tx_sop_i,
  input  logic        b_pkt_tx_eop_i,
  input  logic        b_pkt_tx_dllp_i,
  input  logic        b_hot_reset_req_i,
  output logic        b_pkt_tx_ready_o,
  output logic        b_pkt_rx_valid_o,
  output logic [15:0] b_pkt_rx_data_o,
  output logic        b_pkt_rx_sop_o,
  output logic        b_pkt_rx_eop_o,
  output logic        b_pkt_rx_dllp_o,
  output logic        b_pkt_rx_err_o,
  output logic        b_pkt_rx_nullify_o,
  output logic        b_link_up_o,
  output logic [5:0]  b_ltssm_state_o,
  output logic        b_rcv_err_o,
  output logic        b_gpio_tx_clk_o,
  output logic        b_gpio_tx_ctrl_o,
  output logic [3:0]  b_gpio_tx_data_o,
  // Wire overrides: while <dir>_ovr_i is 1 the receiver sees the given
  // control and data values instead of the sender's.
  input  logic        ab_ovr_i,
  input  logic        ab_ovr_ctrl_i,
  input  logic [3:0]  ab_ovr_data_i,
  input  logic        ba_ovr_i,
  input  logic        ba_ovr_ctrl_i,
  input  logic [3:0]  ba_ovr_data_i,
  output logic [6:0]  cfg_clk_div_o,
  output logic [1:0]  cfg_ratio_o
);

  logic       ab_ctrl;
  logic [3:0] ab_data;
  logic       ba_ctrl;
  logic [3:0] ba_data;

  assign cfg_clk_div_o = 7'(CLK_DIV);
  assign cfg_ratio_o   = 2'(RATIO);

  // The modelled wires (zero delay): the receiver captures mid-transfer.
  assign ab_ctrl = ab_ovr_i ? ab_ovr_ctrl_i : a_gpio_tx_ctrl_o;
  assign ab_data = ab_ovr_i ? ab_ovr_data_i : a_gpio_tx_data_o;
  assign ba_ctrl = ba_ovr_i ? ba_ovr_ctrl_i : b_gpio_tx_ctrl_o;
  assign ba_data = ba_ovr_i ? ba_ovr_data_i : b_gpio_tx_data_o;

  pcie_gpio #(
    .CLK_DIV (CLK_DIV)
  ) u_a (
    .clk_i            (a_clk_i),
    .rst_i            (a_rst_i),
    .port_role_i      (a_port_role_i),
    .pkt_tx_valid_i   (a_pkt_tx_valid_i),
    .pkt_tx_ready_o   (a_pkt_tx_ready_o),
    .pkt_tx_data_i    (a_pkt_tx_data_i),
    .pkt_tx_sop_i     (a_pkt_tx_sop_i),
    .pkt_tx_eop_i     (a_pkt_tx_eop_i),
    .pkt_tx_dllp_i    (a_pkt_tx_dllp_i),
    .pkt_rx_valid_o   (a_pkt_rx_valid_o),
    .pkt_rx_data_o    (a_pkt_rx_data_o),
    .pkt_rx_sop_o     (a_pkt_rx_sop_o),
    .pkt_rx_eop_o     (a_pkt_rx_eop_o),
    .pkt_rx_dllp_o    (a_pkt_rx_dllp_o),
    .pkt_rx_err_o     (a_pkt_rx_err_o),
    .pkt_rx_nullify_o (a_pkt_rx_nullify_o),
    .link_up_o        (a_link_up_o),
    .ltssm_state_o    (a_ltssm_state_o),
    .rcv_err_o        (a_rcv_err_o),
    .hot_reset_req_i  (a_hot_reset_req_i),
    .gpio_tx_clk_o    (a_gpio_tx_clk_o),
    .gpio_tx_ctrl_o   (a_gpio_tx_ctrl_o),
    .gpio_tx_data_o   (a_gpio_tx_data_o),
    .gpio_rx_clk_i    (b_gpio_tx_clk_o),
    .gpio_rx_ctrl_i   (ba_ctrl),
    .gpio_rx_data_i   (ba_data)
  );

  pcie_gpio #(
    .CLK_DIV (CLK_DIV)
  ) u_b (
    .clk_i            (b_clk_i),
    .rst_i            (b_rst_i),
    .port_role_i      (b_port_role_i),
    .pkt_tx_valid_i   (b_pkt_tx_valid_i),
    .pkt_tx_ready_o   (b_pkt_tx_ready_o),
    .pkt_tx_data_i    (b_pkt_tx_data_i),
    .pkt_tx_sop_i     (b_pkt_tx_sop_i),
    .pkt_tx_eop_i     (b_pkt_tx_eop_i),
    .pkt_tx_dllp_i    (b_pkt_tx_dllp_i),
    .pkt_rx_valid_o   (b_pkt_rx_valid_o),
    .pkt_rx_data_o    (b_pkt_rx_data_o),
    .pkt_rx_sop_o     (b_pkt_rx_sop_o),
    .pkt_rx_eop_o     (b_pkt_rx_eop_o),
    .pkt_rx_dllp_o    (b_pkt_rx_dllp_o),
    .pkt_rx_err_o     (b_pkt_rx_err_o),
    .pkt_rx_nullify_o (b_pkt_rx_nullify_o),
    .link_up_o        (b_link_up_o),
    .ltssm_state_o    (b_ltssm_state_o),
    .rcv_err_o        (b_rcv_err_o),
    .hot_reset_req_i  (b_hot_reset_req_i),
    .gpio_tx_clk_o    (b_gpio_tx_clk_o),
    .gpio_tx_ctrl_o   (b_gpio_tx_ctrl_o),
    .gpio_tx_data_o   (b_gpio_tx_data_o),
    .gpio_rx_clk_i    (a_gpio_tx_clk_o),
    .gpio_rx_ctrl_i   (ab_ctrl),
    .gpio_rx_data_i   (ab_data)
  );

  // Pin checker (HAS, pins and transfers): the control and data pins change
  // only at an edge where the forwarded clock falls, and all three are 0
  // in reset.
  pcie_gpio_pin_check u_a_pins (
    .clk_i  (a_clk_i),
    .rst_i  (a_rst_i),
    .tclk_i (a_gpio_tx_clk_o),
    .ctrl_i (a_gpio_tx_ctrl_o),
    .data_i (a_gpio_tx_data_o)
  );

  pcie_gpio_pin_check u_b_pins (
    .clk_i  (b_clk_i),
    .rst_i  (b_rst_i),
    .tclk_i (b_gpio_tx_clk_o),
    .ctrl_i (b_gpio_tx_ctrl_o),
    .data_i (b_gpio_tx_data_o)
  );

endmodule

`default_nettype wire
