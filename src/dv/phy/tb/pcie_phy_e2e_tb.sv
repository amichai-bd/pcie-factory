`default_nettype none

`include "common_macros.svh"

// Back-to-back testbench top of the phy e2e (wiki/src/dv/phy/DV_PLAN.md).
//
// Two pcie_phy instances, a and b, each on its own clock; each one's
// ser_rx_clk_i is the far port's clock (the recovered clock follows the far
// transmitter). Every other pcie_phy port is a port of this top, prefixed
// a_ or b_, so the cocotb environment (tb/phy_e2e_env.py) drives them when
// a layer is RTL. The layer switches are parameters, set per regression
// entry with params, and are echoed on cfg_*_o for the environment:
//
//   MAC_RTL, PCS_RTL  0: the layer is a Python model and the instance idles
//                     in reset;
//                     1: the layer is the RTL in the instance. Today only
//                     both-or-neither is wired: the instance is the whole
//                     stack (the DV plan has the per-layer recipe).
//   CHAN_RTL          0: the Python channel model joins the ports (ppm,
//                     slips, errors, idle); 1: an ideal one-register channel
//                     here (needs PCS_RTL 1), with receiver detection from the
//                     far port's reset.
module pcie_phy_e2e_tb #(
  parameter int unsigned MAC_RTL     = 0,
  parameter int unsigned PCS_RTL     = 0,
  parameter int unsigned CHAN_RTL    = 0,
  parameter int unsigned TIMEOUT_DIV = 1024
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
  input  logic        a_pkt_tx_nullify_i,
  input  logic        a_retrain_req_i,
  input  logic        a_hot_reset_req_i,
  input  logic        a_disable_req_i,
  input  logic        a_loopback_req_i,
  input  logic [1:0]  a_pm_state_req_i,
  input  logic        a_ser_tx_rxdet_i,
  input  logic [19:0] a_ser_rx_data_i,
  input  logic        a_ser_rx_elec_idle_i,
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
  output logic [19:0] a_ser_tx_data_o,
  output logic        a_ser_tx_elec_idle_o,
  input  logic        b_rst_i,
  input  logic        b_port_role_i,
  input  logic        b_pkt_tx_valid_i,
  input  logic [15:0] b_pkt_tx_data_i,
  input  logic        b_pkt_tx_sop_i,
  input  logic        b_pkt_tx_eop_i,
  input  logic        b_pkt_tx_dllp_i,
  input  logic        b_pkt_tx_nullify_i,
  input  logic        b_retrain_req_i,
  input  logic        b_hot_reset_req_i,
  input  logic        b_disable_req_i,
  input  logic        b_loopback_req_i,
  input  logic [1:0]  b_pm_state_req_i,
  input  logic        b_ser_tx_rxdet_i,
  input  logic [19:0] b_ser_rx_data_i,
  input  logic        b_ser_rx_elec_idle_i,
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
  output logic [19:0] b_ser_tx_data_o,
  output logic        b_ser_tx_elec_idle_o,
  output logic        cfg_mac_rtl_o,
  output logic        cfg_pcs_rtl_o,
  output logic        cfg_chan_rtl_o,
  output logic [10:0] cfg_timeout_div_o
);

  assign cfg_mac_rtl_o     = (MAC_RTL != 0);
  assign cfg_pcs_rtl_o     = (PCS_RTL != 0);
  assign cfg_chan_rtl_o    = (CHAN_RTL != 0);
  assign cfg_timeout_div_o = 11'(TIMEOUT_DIV);

  // Ideal channel (CHAN_RTL 1): one register in the transmitter's clock,
  // which is the receiver's ser_rx_clk_i.
  logic [19:0] ab_data_q, ba_data_q;
  logic        ab_idle_q, ba_idle_q;
  `COMMON_DFF(ab_data_q, a_ser_tx_data_o, a_clk_i)
  `COMMON_DFF(ab_idle_q, a_ser_tx_elec_idle_o, a_clk_i)
  `COMMON_DFF(ba_data_q, b_ser_tx_data_o, b_clk_i)
  `COMMON_DFF(ba_idle_q, b_ser_tx_elec_idle_o, b_clk_i)

  logic [19:0] a_rx_data, b_rx_data;
  logic        a_rx_idle, b_rx_idle, a_rxdet, b_rxdet;
  assign a_rx_data = (CHAN_RTL != 0) ? ba_data_q : a_ser_rx_data_i;
  assign b_rx_data = (CHAN_RTL != 0) ? ab_data_q : b_ser_rx_data_i;
  assign a_rx_idle = (CHAN_RTL != 0) ? ba_idle_q : a_ser_rx_elec_idle_i;
  assign b_rx_idle = (CHAN_RTL != 0) ? ab_idle_q : b_ser_rx_elec_idle_i;
  assign a_rxdet   = (CHAN_RTL != 0) ? !b_rst_i  : a_ser_tx_rxdet_i;
  assign b_rxdet   = (CHAN_RTL != 0) ? !a_rst_i  : b_ser_tx_rxdet_i;

  // With the layers as models the instances idle, held in reset.
  logic a_rst, b_rst;
  assign a_rst = (MAC_RTL != 0) ? a_rst_i : 1'b1;
  assign b_rst = (MAC_RTL != 0) ? b_rst_i : 1'b1;

  pcie_phy #(.TIMEOUT_DIV(TIMEOUT_DIV)) u_a (
    .clk_i              (a_clk_i),
    .rst_i              (a_rst),
    .port_role_i        (a_port_role_i),
    .pkt_tx_valid_i     (a_pkt_tx_valid_i),
    .pkt_tx_data_i      (a_pkt_tx_data_i),
    .pkt_tx_sop_i       (a_pkt_tx_sop_i),
    .pkt_tx_eop_i       (a_pkt_tx_eop_i),
    .pkt_tx_dllp_i      (a_pkt_tx_dllp_i),
    .pkt_tx_nullify_i   (a_pkt_tx_nullify_i),
    .retrain_req_i      (a_retrain_req_i),
    .hot_reset_req_i    (a_hot_reset_req_i),
    .disable_req_i      (a_disable_req_i),
    .loopback_req_i     (a_loopback_req_i),
    .pm_state_req_i     (a_pm_state_req_i),
    .ser_tx_rxdet_i     (a_rxdet),
    .ser_rx_data_i      (a_rx_data),
    .ser_rx_elec_idle_i (a_rx_idle),
    .ser_rx_clk_i       (b_clk_i),
    .pkt_tx_ready_o     (a_pkt_tx_ready_o),
    .pkt_rx_valid_o     (a_pkt_rx_valid_o),
    .pkt_rx_data_o      (a_pkt_rx_data_o),
    .pkt_rx_sop_o       (a_pkt_rx_sop_o),
    .pkt_rx_eop_o       (a_pkt_rx_eop_o),
    .pkt_rx_dllp_o      (a_pkt_rx_dllp_o),
    .pkt_rx_err_o       (a_pkt_rx_err_o),
    .pkt_rx_nullify_o   (a_pkt_rx_nullify_o),
    .link_up_o          (a_link_up_o),
    .ltssm_state_o      (a_ltssm_state_o),
    .rcv_err_o          (a_rcv_err_o),
    .ser_tx_data_o      (a_ser_tx_data_o),
    .ser_tx_elec_idle_o (a_ser_tx_elec_idle_o)
  );

  pcie_phy #(.TIMEOUT_DIV(TIMEOUT_DIV)) u_b (
    .clk_i              (b_clk_i),
    .rst_i              (b_rst),
    .port_role_i        (b_port_role_i),
    .pkt_tx_valid_i     (b_pkt_tx_valid_i),
    .pkt_tx_data_i      (b_pkt_tx_data_i),
    .pkt_tx_sop_i       (b_pkt_tx_sop_i),
    .pkt_tx_eop_i       (b_pkt_tx_eop_i),
    .pkt_tx_dllp_i      (b_pkt_tx_dllp_i),
    .pkt_tx_nullify_i   (b_pkt_tx_nullify_i),
    .retrain_req_i      (b_retrain_req_i),
    .hot_reset_req_i    (b_hot_reset_req_i),
    .disable_req_i      (b_disable_req_i),
    .loopback_req_i     (b_loopback_req_i),
    .pm_state_req_i     (b_pm_state_req_i),
    .ser_tx_rxdet_i     (b_rxdet),
    .ser_rx_data_i      (b_rx_data),
    .ser_rx_elec_idle_i (b_rx_idle),
    .ser_rx_clk_i       (a_clk_i),
    .pkt_tx_ready_o     (b_pkt_tx_ready_o),
    .pkt_rx_valid_o     (b_pkt_rx_valid_o),
    .pkt_rx_data_o      (b_pkt_rx_data_o),
    .pkt_rx_sop_o       (b_pkt_rx_sop_o),
    .pkt_rx_eop_o       (b_pkt_rx_eop_o),
    .pkt_rx_dllp_o      (b_pkt_rx_dllp_o),
    .pkt_rx_err_o       (b_pkt_rx_err_o),
    .pkt_rx_nullify_o   (b_pkt_rx_nullify_o),
    .link_up_o          (b_link_up_o),
    .ltssm_state_o      (b_ltssm_state_o),
    .rcv_err_o          (b_rcv_err_o),
    .ser_tx_data_o      (b_ser_tx_data_o),
    .ser_tx_elec_idle_o (b_ser_tx_elec_idle_o)
  );

endmodule

`default_nettype wire
