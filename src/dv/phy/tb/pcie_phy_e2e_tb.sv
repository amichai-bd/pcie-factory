`default_nettype none

`include "common_macros.svh"

// Back-to-back testbench top of the phy e2e (wiki/src/dv/phy/DV_PLAN.md).
//
// Two stacks, a and b, each on its own clock; each one's
// ser_rx_clk_i is the far port's clock (the recovered clock follows the far
// transmitter). Every other pcie_phy port is a port of this top, prefixed
// a_ or b_, so the cocotb environment (tb/phy_e2e_env.py) drives them when
// a layer is RTL. The layer switches are parameters, set per regression
// entry with params, and are echoed on cfg_*_o for the environment:
//
//   MAC_RTL, PCS_RTL  0: the layer is a Python model and the instance idles
//                     in reset;
//                     1: the layer is RTL. PIPE top ports connect a model
//                     to the other layer; both RTL connect directly.
//   CHAN_RTL          0: the Python channel model joins the ports (ppm,
//                     slips, errors, idle); 1: an ideal one-register channel
//                     here (needs PCS_RTL 1), with receiver detection from the
//                     far port's reset.
module pcie_phy_e2e_tb #(
  parameter int unsigned MAC_RTL     = 0,
  parameter int unsigned PCS_RTL     = 0,
  parameter int unsigned CHAN_RTL    = 0,
  parameter int unsigned TIMEOUT_DIV = 1024,
  parameter int unsigned ERR_TIMEOUT_DIV = 1
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
  input logic a_pipe_reset_n_i,
  output logic a_pipe_reset_n_o,
  input logic [15:0] a_pipe_tx_data_i,
  output logic [15:0] a_pipe_tx_data_o,
  input logic [1:0] a_pipe_tx_datak_i,
  output logic [1:0] a_pipe_tx_datak_o,
  input logic a_pipe_tx_detect_rx_i,
  output logic a_pipe_tx_detect_rx_o,
  input logic a_pipe_tx_elec_idle_i,
  output logic a_pipe_tx_elec_idle_o,
  input logic a_pipe_tx_compliance_i,
  output logic a_pipe_tx_compliance_o,
  input logic a_pipe_rx_polarity_i,
  output logic a_pipe_rx_polarity_o,
  input logic [1:0] a_pipe_power_down_i,
  output logic [1:0] a_pipe_power_down_o,
  input logic [15:0] a_pipe_rx_data_i,
  output logic [15:0] a_pipe_rx_data_o,
  input logic [1:0] a_pipe_rx_datak_i,
  output logic [1:0] a_pipe_rx_datak_o,
  input logic a_pipe_rx_valid_i,
  output logic a_pipe_rx_valid_o,
  input logic a_pipe_phy_status_i,
  output logic a_pipe_phy_status_o,
  input logic a_pipe_rx_elec_idle_i,
  output logic a_pipe_rx_elec_idle_o,
  input logic [2:0] a_pipe_rx_status_i,
  output logic [2:0] a_pipe_rx_status_o,
  input logic b_pipe_reset_n_i,
  output logic b_pipe_reset_n_o,
  input logic [15:0] b_pipe_tx_data_i,
  output logic [15:0] b_pipe_tx_data_o,
  input logic [1:0] b_pipe_tx_datak_i,
  output logic [1:0] b_pipe_tx_datak_o,
  input logic b_pipe_tx_detect_rx_i,
  output logic b_pipe_tx_detect_rx_o,
  input logic b_pipe_tx_elec_idle_i,
  output logic b_pipe_tx_elec_idle_o,
  input logic b_pipe_tx_compliance_i,
  output logic b_pipe_tx_compliance_o,
  input logic b_pipe_rx_polarity_i,
  output logic b_pipe_rx_polarity_o,
  input logic [1:0] b_pipe_power_down_i,
  output logic [1:0] b_pipe_power_down_o,
  input logic [15:0] b_pipe_rx_data_i,
  output logic [15:0] b_pipe_rx_data_o,
  input logic [1:0] b_pipe_rx_datak_i,
  output logic [1:0] b_pipe_rx_datak_o,
  input logic b_pipe_rx_valid_i,
  output logic b_pipe_rx_valid_o,
  input logic b_pipe_phy_status_i,
  output logic b_pipe_phy_status_o,
  input logic b_pipe_rx_elec_idle_i,
  output logic b_pipe_rx_elec_idle_o,
  input logic [2:0] b_pipe_rx_status_i,
  output logic [2:0] b_pipe_rx_status_o,
  output logic        cfg_mac_rtl_o,
  output logic        cfg_pcs_rtl_o,
  output logic        cfg_chan_rtl_o,
  output logic [10:0] cfg_timeout_div_o,
  output logic [10:0] cfg_err_timeout_div_o
);

  assign cfg_mac_rtl_o     = (MAC_RTL != 0);
  assign cfg_pcs_rtl_o     = (PCS_RTL != 0);
  assign cfg_chan_rtl_o    = (CHAN_RTL != 0);
  assign cfg_timeout_div_o = 11'(TIMEOUT_DIV);
  assign cfg_err_timeout_div_o = 11'(ERR_TIMEOUT_DIV);

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

  generate
    if (1) begin : u_a
      // PIPE nets (wiki/src/arch/phy/PIPE.md#signals): flat, as the PIPE
      // specification names them; each is pipe_<x>_o of one IP and pipe_<x>_i
      // of the other.
      logic        pipe_reset_n;
      logic [15:0] pipe_tx_data;
      logic [1:0]  pipe_tx_datak;
      logic        pipe_tx_detect_rx;
      logic        pipe_tx_elec_idle;
      logic        pipe_tx_compliance;
      logic        pipe_rx_polarity;
      logic [1:0]  pipe_power_down;
      logic [15:0] pipe_rx_data;
      logic [1:0]  pipe_rx_datak;
      logic        pipe_rx_valid;
      logic        pipe_phy_status;
      logic        pipe_rx_elec_idle;
      logic [2:0]  pipe_rx_status;

      pcie_mac #(
        .TIMEOUT_DIV     (TIMEOUT_DIV),
        .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV)
      ) u_mac (
        .clk_i                (a_clk_i),
        .rst_i                (a_rst_i || MAC_RTL == 0),
        .port_role_i          (a_port_role_i),
        .pkt_tx_valid_i       (a_pkt_tx_valid_i),
        .pkt_tx_ready_o       (a_pkt_tx_ready_o),
        .pkt_tx_data_i        (a_pkt_tx_data_i),
        .pkt_tx_sop_i         (a_pkt_tx_sop_i),
        .pkt_tx_eop_i         (a_pkt_tx_eop_i),
        .pkt_tx_dllp_i        (a_pkt_tx_dllp_i),
        .pkt_tx_nullify_i     (a_pkt_tx_nullify_i),
        .pkt_rx_valid_o       (a_pkt_rx_valid_o),
        .pkt_rx_data_o        (a_pkt_rx_data_o),
        .pkt_rx_sop_o         (a_pkt_rx_sop_o),
        .pkt_rx_eop_o         (a_pkt_rx_eop_o),
        .pkt_rx_dllp_o        (a_pkt_rx_dllp_o),
        .pkt_rx_err_o         (a_pkt_rx_err_o),
        .pkt_rx_nullify_o     (a_pkt_rx_nullify_o),
        .link_up_o            (a_link_up_o),
        .ltssm_state_o        (a_ltssm_state_o),
        .rcv_err_o            (a_rcv_err_o),
        .retrain_req_i        (a_retrain_req_i),
        .hot_reset_req_i      (a_hot_reset_req_i),
        .disable_req_i        (a_disable_req_i),
        .loopback_req_i       (a_loopback_req_i),
        .pm_state_req_i       (a_pm_state_req_i),
        .pipe_reset_no        (pipe_reset_n),
        .pipe_tx_data_o       (pipe_tx_data),
        .pipe_tx_datak_o      (pipe_tx_datak),
        .pipe_tx_detect_rx_o  (pipe_tx_detect_rx),
        .pipe_tx_elec_idle_o  (pipe_tx_elec_idle),
        .pipe_tx_compliance_o (pipe_tx_compliance),
        .pipe_rx_polarity_o   (pipe_rx_polarity),
        .pipe_power_down_o    (pipe_power_down),
        .pipe_rx_data_i       ((PCS_RTL != 0) ? pipe_rx_data : a_pipe_rx_data_i),
        .pipe_rx_datak_i      ((PCS_RTL != 0) ? pipe_rx_datak : a_pipe_rx_datak_i),
        .pipe_rx_valid_i      ((PCS_RTL != 0) ? pipe_rx_valid : a_pipe_rx_valid_i),
        .pipe_phy_status_i    ((PCS_RTL != 0) ? pipe_phy_status : a_pipe_phy_status_i),
        .pipe_rx_elec_idle_i  ((PCS_RTL != 0) ? pipe_rx_elec_idle : a_pipe_rx_elec_idle_i),
        .pipe_rx_status_i     ((PCS_RTL != 0) ? pipe_rx_status : a_pipe_rx_status_i)
      );

      pcie_pcs u_pcs (
        .clk_i                (a_clk_i),
        .rst_i                (a_rst_i || PCS_RTL == 0),
        .pipe_reset_ni        ((MAC_RTL != 0) ? pipe_reset_n : a_pipe_reset_n_i),
        .pipe_tx_data_i       ((MAC_RTL != 0) ? pipe_tx_data : a_pipe_tx_data_i),
        .pipe_tx_datak_i      ((MAC_RTL != 0) ? pipe_tx_datak : a_pipe_tx_datak_i),
        .pipe_tx_detect_rx_i  ((MAC_RTL != 0) ? pipe_tx_detect_rx : a_pipe_tx_detect_rx_i),
        .pipe_tx_elec_idle_i  ((MAC_RTL != 0) ? pipe_tx_elec_idle : a_pipe_tx_elec_idle_i),
        .pipe_tx_compliance_i ((MAC_RTL != 0) ? pipe_tx_compliance : a_pipe_tx_compliance_i),
        .pipe_rx_polarity_i   ((MAC_RTL != 0) ? pipe_rx_polarity : a_pipe_rx_polarity_i),
        .pipe_power_down_i    ((MAC_RTL != 0) ? pipe_power_down : a_pipe_power_down_i),
        .pipe_rx_data_o       (pipe_rx_data),
        .pipe_rx_datak_o      (pipe_rx_datak),
        .pipe_rx_valid_o      (pipe_rx_valid),
        .pipe_phy_status_o    (pipe_phy_status),
        .pipe_rx_elec_idle_o  (pipe_rx_elec_idle),
        .pipe_rx_status_o     (pipe_rx_status),
        .ser_tx_data_o        (a_ser_tx_data_o),
        .ser_tx_elec_idle_o   (a_ser_tx_elec_idle_o),
        .ser_tx_rxdet_i       (a_rxdet),
        .ser_rx_clk_i         (b_clk_i),
        .ser_rx_data_i        (a_rx_data),
        .ser_rx_elec_idle_i   (a_rx_idle)
      );
      assign a_pipe_reset_n_o = pipe_reset_n;
      assign a_pipe_tx_data_o = pipe_tx_data;
      assign a_pipe_tx_datak_o = pipe_tx_datak;
      assign a_pipe_tx_detect_rx_o = pipe_tx_detect_rx;
      assign a_pipe_tx_elec_idle_o = pipe_tx_elec_idle;
      assign a_pipe_tx_compliance_o = pipe_tx_compliance;
      assign a_pipe_rx_polarity_o = pipe_rx_polarity;
      assign a_pipe_power_down_o = pipe_power_down;
      assign a_pipe_rx_data_o = pipe_rx_data;
      assign a_pipe_rx_datak_o = pipe_rx_datak;
      assign a_pipe_rx_valid_o = pipe_rx_valid;
      assign a_pipe_phy_status_o = pipe_phy_status;
      assign a_pipe_rx_elec_idle_o = pipe_rx_elec_idle;
      assign a_pipe_rx_status_o = pipe_rx_status;
    end
  endgenerate

  generate
    if (1) begin : u_b
      // PIPE nets (wiki/src/arch/phy/PIPE.md#signals): flat, as the PIPE
      // specification names them; each is pipe_<x>_o of one IP and pipe_<x>_i
      // of the other.
      logic        pipe_reset_n;
      logic [15:0] pipe_tx_data;
      logic [1:0]  pipe_tx_datak;
      logic        pipe_tx_detect_rx;
      logic        pipe_tx_elec_idle;
      logic        pipe_tx_compliance;
      logic        pipe_rx_polarity;
      logic [1:0]  pipe_power_down;
      logic [15:0] pipe_rx_data;
      logic [1:0]  pipe_rx_datak;
      logic        pipe_rx_valid;
      logic        pipe_phy_status;
      logic        pipe_rx_elec_idle;
      logic [2:0]  pipe_rx_status;

      pcie_mac #(
        .TIMEOUT_DIV     (TIMEOUT_DIV),
        .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV)
      ) u_mac (
        .clk_i                (b_clk_i),
        .rst_i                (b_rst_i || MAC_RTL == 0),
        .port_role_i          (b_port_role_i),
        .pkt_tx_valid_i       (b_pkt_tx_valid_i),
        .pkt_tx_ready_o       (b_pkt_tx_ready_o),
        .pkt_tx_data_i        (b_pkt_tx_data_i),
        .pkt_tx_sop_i         (b_pkt_tx_sop_i),
        .pkt_tx_eop_i         (b_pkt_tx_eop_i),
        .pkt_tx_dllp_i        (b_pkt_tx_dllp_i),
        .pkt_tx_nullify_i     (b_pkt_tx_nullify_i),
        .pkt_rx_valid_o       (b_pkt_rx_valid_o),
        .pkt_rx_data_o        (b_pkt_rx_data_o),
        .pkt_rx_sop_o         (b_pkt_rx_sop_o),
        .pkt_rx_eop_o         (b_pkt_rx_eop_o),
        .pkt_rx_dllp_o        (b_pkt_rx_dllp_o),
        .pkt_rx_err_o         (b_pkt_rx_err_o),
        .pkt_rx_nullify_o     (b_pkt_rx_nullify_o),
        .link_up_o            (b_link_up_o),
        .ltssm_state_o        (b_ltssm_state_o),
        .rcv_err_o            (b_rcv_err_o),
        .retrain_req_i        (b_retrain_req_i),
        .hot_reset_req_i      (b_hot_reset_req_i),
        .disable_req_i        (b_disable_req_i),
        .loopback_req_i       (b_loopback_req_i),
        .pm_state_req_i       (b_pm_state_req_i),
        .pipe_reset_no        (pipe_reset_n),
        .pipe_tx_data_o       (pipe_tx_data),
        .pipe_tx_datak_o      (pipe_tx_datak),
        .pipe_tx_detect_rx_o  (pipe_tx_detect_rx),
        .pipe_tx_elec_idle_o  (pipe_tx_elec_idle),
        .pipe_tx_compliance_o (pipe_tx_compliance),
        .pipe_rx_polarity_o   (pipe_rx_polarity),
        .pipe_power_down_o    (pipe_power_down),
        .pipe_rx_data_i       ((PCS_RTL != 0) ? pipe_rx_data : b_pipe_rx_data_i),
        .pipe_rx_datak_i      ((PCS_RTL != 0) ? pipe_rx_datak : b_pipe_rx_datak_i),
        .pipe_rx_valid_i      ((PCS_RTL != 0) ? pipe_rx_valid : b_pipe_rx_valid_i),
        .pipe_phy_status_i    ((PCS_RTL != 0) ? pipe_phy_status : b_pipe_phy_status_i),
        .pipe_rx_elec_idle_i  ((PCS_RTL != 0) ? pipe_rx_elec_idle : b_pipe_rx_elec_idle_i),
        .pipe_rx_status_i     ((PCS_RTL != 0) ? pipe_rx_status : b_pipe_rx_status_i)
      );

      pcie_pcs u_pcs (
        .clk_i                (b_clk_i),
        .rst_i                (b_rst_i || PCS_RTL == 0),
        .pipe_reset_ni        ((MAC_RTL != 0) ? pipe_reset_n : b_pipe_reset_n_i),
        .pipe_tx_data_i       ((MAC_RTL != 0) ? pipe_tx_data : b_pipe_tx_data_i),
        .pipe_tx_datak_i      ((MAC_RTL != 0) ? pipe_tx_datak : b_pipe_tx_datak_i),
        .pipe_tx_detect_rx_i  ((MAC_RTL != 0) ? pipe_tx_detect_rx : b_pipe_tx_detect_rx_i),
        .pipe_tx_elec_idle_i  ((MAC_RTL != 0) ? pipe_tx_elec_idle : b_pipe_tx_elec_idle_i),
        .pipe_tx_compliance_i ((MAC_RTL != 0) ? pipe_tx_compliance : b_pipe_tx_compliance_i),
        .pipe_rx_polarity_i   ((MAC_RTL != 0) ? pipe_rx_polarity : b_pipe_rx_polarity_i),
        .pipe_power_down_i    ((MAC_RTL != 0) ? pipe_power_down : b_pipe_power_down_i),
        .pipe_rx_data_o       (pipe_rx_data),
        .pipe_rx_datak_o      (pipe_rx_datak),
        .pipe_rx_valid_o      (pipe_rx_valid),
        .pipe_phy_status_o    (pipe_phy_status),
        .pipe_rx_elec_idle_o  (pipe_rx_elec_idle),
        .pipe_rx_status_o     (pipe_rx_status),
        .ser_tx_data_o        (b_ser_tx_data_o),
        .ser_tx_elec_idle_o   (b_ser_tx_elec_idle_o),
        .ser_tx_rxdet_i       (b_rxdet),
        .ser_rx_clk_i         (a_clk_i),
        .ser_rx_data_i        (b_rx_data),
        .ser_rx_elec_idle_i   (b_rx_idle)
      );
      assign b_pipe_reset_n_o = pipe_reset_n;
      assign b_pipe_tx_data_o = pipe_tx_data;
      assign b_pipe_tx_datak_o = pipe_tx_datak;
      assign b_pipe_tx_detect_rx_o = pipe_tx_detect_rx;
      assign b_pipe_tx_elec_idle_o = pipe_tx_elec_idle;
      assign b_pipe_tx_compliance_o = pipe_tx_compliance;
      assign b_pipe_rx_polarity_o = pipe_rx_polarity;
      assign b_pipe_power_down_o = pipe_power_down;
      assign b_pipe_rx_data_o = pipe_rx_data;
      assign b_pipe_rx_datak_o = pipe_rx_datak;
      assign b_pipe_rx_valid_o = pipe_rx_valid;
      assign b_pipe_phy_status_o = pipe_phy_status;
      assign b_pipe_rx_elec_idle_o = pipe_rx_elec_idle;
      assign b_pipe_rx_status_o = pipe_rx_status;
    end
  endgenerate

endmodule

`default_nettype wire
