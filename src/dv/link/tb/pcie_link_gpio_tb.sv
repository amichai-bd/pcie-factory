`default_nettype none

// GPIO back-to-back testbench top of the link e2e (wiki/src/dv/link/DV_PLAN.md,
// GPIO lower layer).
//
// Two pcie_link instances at LOWER 1, a (downstream in the tests) and b,
// each on its own clock, joined by the modelled GPIO wires: each one's
// gpio_rx_* are the other's gpio_tx_*, the forwarded clock included. The
// ports are those of pcie_link_e2e_tb (the environment tb/link_e2e_env.py
// drives them; the symbol interface is idle at LOWER 1), plus a wire
// override per direction (<dir>_gpio_ovr_i, ab: a to b) that replaces the
// control pin the receiver sees, to damage or forge transfers, and the
// adapter configuration on cfg_gpio_clk_div_o and cfg_ratio_o (b's clock:
// 0 half of a's, 1 equal, 2 twice). CREDITS picks the credit configuration
// as in pcie_link_e2e_tb; cfg_replay_timeout_o is the replay timeout the
// dlls run with: REPLAY_TIMEOUT or the GPIO build's value, whichever is
// larger (link HAS, replay timeout at LOWER 1).
module pcie_link_gpio_tb #(
  parameter int unsigned CREDITS         = 0,
  parameter int unsigned TIMEOUT_DIV     = 1024,
  parameter int unsigned ERR_TIMEOUT_DIV = 1,
  parameter int unsigned REPLAY_TIMEOUT  = 356,
  parameter int unsigned GPIO_CLK_DIV    = 2,
  parameter int unsigned RATIO           = 1
) (
  input  logic        a_clk_i,
  input  logic        b_clk_i,
  input  logic        a_rst_i,
  input  logic        a_port_role_i,
  input  logic        a_tlp_tx_valid_i,
  input  logic [31:0] a_tlp_tx_data_i,
  input  logic        a_tlp_tx_sop_i,
  input  logic        a_tlp_tx_eop_i,
  input  logic        a_tlp_rx_ready_i,
  input  logic        a_fc_rel_p_i,
  input  logic [3:0]  a_fc_rel_pd_i,
  input  logic        a_fc_rel_np_i,
  input  logic [3:0]  a_fc_rel_npd_i,
  input  logic        a_retrain_req_i,
  input  logic        a_hot_reset_req_i,
  input  logic        a_disable_req_i,
  input  logic        a_loopback_req_i,
  input  logic        a_ser_tx_rxdet_i,
  input  logic [19:0] a_ser_rx_data_i,
  input  logic        a_ser_rx_elec_idle_i,
  output logic        a_tlp_tx_ready_o,
  output logic        a_tlp_rx_valid_o,
  output logic [31:0] a_tlp_rx_data_o,
  output logic        a_tlp_rx_sop_o,
  output logic        a_tlp_rx_eop_o,
  output logic [7:0]  a_fc_ph_avail_o,
  output logic [11:0] a_fc_pd_avail_o,
  output logic [7:0]  a_fc_nph_avail_o,
  output logic [11:0] a_fc_npd_avail_o,
  output logic [7:0]  a_fc_cplh_avail_o,
  output logic [11:0] a_fc_cpld_avail_o,
  output logic        a_dl_up_o,
  output logic [1:0]  a_dl_state_o,
  output logic [6:0]  a_dl_err_o,
  output logic        a_link_up_o,
  output logic [5:0]  a_ltssm_state_o,
  output logic        a_rcv_err_o,
  output logic [19:0] a_ser_tx_data_o,
  output logic        a_ser_tx_elec_idle_o,
  output logic        a_mon_pkt_tx_valid_o,
  output logic        a_mon_pkt_tx_ready_o,
  output logic [15:0] a_mon_pkt_tx_data_o,
  output logic        a_mon_pkt_tx_sop_o,
  output logic        a_mon_pkt_tx_eop_o,
  output logic        a_mon_pkt_tx_dllp_o,
  output logic        a_mon_pkt_rx_valid_o,
  output logic [15:0] a_mon_pkt_rx_data_o,
  output logic        a_mon_pkt_rx_sop_o,
  output logic        a_mon_pkt_rx_eop_o,
  output logic        a_mon_pkt_rx_dllp_o,
  output logic        a_mon_pkt_rx_err_o,
  output logic [71:0] a_cfg_fc_o,
  input  logic        b_rst_i,
  input  logic        b_port_role_i,
  input  logic        b_tlp_tx_valid_i,
  input  logic [31:0] b_tlp_tx_data_i,
  input  logic        b_tlp_tx_sop_i,
  input  logic        b_tlp_tx_eop_i,
  input  logic        b_tlp_rx_ready_i,
  input  logic        b_fc_rel_p_i,
  input  logic [3:0]  b_fc_rel_pd_i,
  input  logic        b_fc_rel_np_i,
  input  logic [3:0]  b_fc_rel_npd_i,
  input  logic        b_retrain_req_i,
  input  logic        b_hot_reset_req_i,
  input  logic        b_disable_req_i,
  input  logic        b_loopback_req_i,
  input  logic        b_ser_tx_rxdet_i,
  input  logic [19:0] b_ser_rx_data_i,
  input  logic        b_ser_rx_elec_idle_i,
  output logic        b_tlp_tx_ready_o,
  output logic        b_tlp_rx_valid_o,
  output logic [31:0] b_tlp_rx_data_o,
  output logic        b_tlp_rx_sop_o,
  output logic        b_tlp_rx_eop_o,
  output logic [7:0]  b_fc_ph_avail_o,
  output logic [11:0] b_fc_pd_avail_o,
  output logic [7:0]  b_fc_nph_avail_o,
  output logic [11:0] b_fc_npd_avail_o,
  output logic [7:0]  b_fc_cplh_avail_o,
  output logic [11:0] b_fc_cpld_avail_o,
  output logic        b_dl_up_o,
  output logic [1:0]  b_dl_state_o,
  output logic [6:0]  b_dl_err_o,
  output logic        b_link_up_o,
  output logic [5:0]  b_ltssm_state_o,
  output logic        b_rcv_err_o,
  output logic [19:0] b_ser_tx_data_o,
  output logic        b_ser_tx_elec_idle_o,
  output logic        b_mon_pkt_tx_valid_o,
  output logic        b_mon_pkt_tx_ready_o,
  output logic [15:0] b_mon_pkt_tx_data_o,
  output logic        b_mon_pkt_tx_sop_o,
  output logic        b_mon_pkt_tx_eop_o,
  output logic        b_mon_pkt_tx_dllp_o,
  output logic        b_mon_pkt_rx_valid_o,
  output logic [15:0] b_mon_pkt_rx_data_o,
  output logic        b_mon_pkt_rx_sop_o,
  output logic        b_mon_pkt_rx_eop_o,
  output logic        b_mon_pkt_rx_dllp_o,
  output logic        b_mon_pkt_rx_err_o,
  output logic [71:0] b_cfg_fc_o,
  output logic [2:0]  cfg_credits_o,
  output logic [10:0] cfg_timeout_div_o,
  output logic [10:0] cfg_err_timeout_div_o,
  output logic [15:0] cfg_replay_timeout_o,
  input  logic        ab_gpio_ovr_i,
  input  logic        ab_gpio_ovr_ctrl_i,
  input  logic        ba_gpio_ovr_i,
  input  logic        ba_gpio_ovr_ctrl_i,
  output logic        a_gpio_tx_clk_o,
  output logic        a_gpio_tx_ctrl_o,
  output logic        b_gpio_tx_clk_o,
  output logic        b_gpio_tx_ctrl_o,
  output logic [6:0]  cfg_gpio_clk_div_o,
  output logic [1:0]  cfg_ratio_o
);

  typedef int unsigned t_fc [6];
  localparam t_fc FC_DEFAULT = '{8, 32, 8, 8, 0, 0};
  localparam t_fc FC_MINIMUM = '{1, 8, 1, 2, 1, 8};
  localparam t_fc FC_FINITE  = '{8, 32, 8, 8, 8, 32};
  localparam t_fc FC_INF     = '{0, 0, 0, 0, 0, 0};
  localparam t_fc A_FC = CREDITS == 1 || CREDITS == 4 ? FC_MINIMUM
                       : CREDITS == 2 ? FC_FINITE : CREDITS == 3 ? FC_INF : FC_DEFAULT;
  localparam t_fc B_FC = CREDITS == 1 ? FC_MINIMUM
                       : CREDITS == 2 ? FC_FINITE : CREDITS == 3 ? FC_INF : FC_DEFAULT;
  localparam int unsigned REL = CREDITS == 5 ? 1 : 0;

  // The modelled GPIO wires (zero delay).
  logic [3:0] a_gpio_tx_data;
  logic [3:0] b_gpio_tx_data;
  logic       ab_ctrl;
  logic       ba_ctrl;
  localparam int unsigned GPIO_REPLAY = pcie_gpio_pkg::replay_timeout(GPIO_CLK_DIV);

  assign ab_ctrl            = ab_gpio_ovr_i ? ab_gpio_ovr_ctrl_i : a_gpio_tx_ctrl_o;
  assign ba_ctrl            = ba_gpio_ovr_i ? ba_gpio_ovr_ctrl_i : b_gpio_tx_ctrl_o;
  assign cfg_gpio_clk_div_o = 7'(GPIO_CLK_DIV);
  assign cfg_ratio_o        = 2'(RATIO);

  assign cfg_credits_o         = 3'(CREDITS);
  assign cfg_timeout_div_o     = 11'(TIMEOUT_DIV);
  assign cfg_err_timeout_div_o = 11'(ERR_TIMEOUT_DIV);
  assign cfg_replay_timeout_o  = 16'(GPIO_REPLAY > REPLAY_TIMEOUT ? GPIO_REPLAY : REPLAY_TIMEOUT);

  pcie_link #(
    .FC_PH(A_FC[0]), .FC_PD(A_FC[1]), .FC_NPH(A_FC[2]),
    .FC_NPD(A_FC[3]), .FC_CPLH(A_FC[4]), .FC_CPLD(A_FC[5]), .FC_RELEASE(REL),
    .TIMEOUT_DIV(TIMEOUT_DIV), .ERR_TIMEOUT_DIV(ERR_TIMEOUT_DIV),
    .REPLAY_TIMEOUT(REPLAY_TIMEOUT), .LOWER(1), .GPIO_CLK_DIV(GPIO_CLK_DIV)
  ) u_a (
    .clk_i(a_clk_i),
    .ser_rx_clk_i(1'b0),
    .rst_i(a_rst_i),
    .port_role_i(a_port_role_i),
    .tlp_tx_valid_i(a_tlp_tx_valid_i),
    .tlp_tx_data_i(a_tlp_tx_data_i),
    .tlp_tx_sop_i(a_tlp_tx_sop_i),
    .tlp_tx_eop_i(a_tlp_tx_eop_i),
    .tlp_rx_ready_i(a_tlp_rx_ready_i),
    .fc_rel_p_i(a_fc_rel_p_i),
    .fc_rel_pd_i(a_fc_rel_pd_i),
    .fc_rel_np_i(a_fc_rel_np_i),
    .fc_rel_npd_i(a_fc_rel_npd_i),
    .retrain_req_i(a_retrain_req_i),
    .hot_reset_req_i(a_hot_reset_req_i),
    .disable_req_i(a_disable_req_i),
    .loopback_req_i(a_loopback_req_i),
    .ser_tx_rxdet_i(a_ser_tx_rxdet_i),
    .ser_rx_data_i(a_ser_rx_data_i),
    .ser_rx_elec_idle_i(a_ser_rx_elec_idle_i),
    .tlp_tx_ready_o(a_tlp_tx_ready_o),
    .tlp_rx_valid_o(a_tlp_rx_valid_o),
    .tlp_rx_data_o(a_tlp_rx_data_o),
    .tlp_rx_sop_o(a_tlp_rx_sop_o),
    .tlp_rx_eop_o(a_tlp_rx_eop_o),
    .fc_ph_avail_o(a_fc_ph_avail_o),
    .fc_pd_avail_o(a_fc_pd_avail_o),
    .fc_nph_avail_o(a_fc_nph_avail_o),
    .fc_npd_avail_o(a_fc_npd_avail_o),
    .fc_cplh_avail_o(a_fc_cplh_avail_o),
    .fc_cpld_avail_o(a_fc_cpld_avail_o),
    .dl_up_o(a_dl_up_o),
    .dl_state_o(a_dl_state_o),
    .dl_err_o(a_dl_err_o),
    .link_up_o(a_link_up_o),
    .ltssm_state_o(a_ltssm_state_o),
    .rcv_err_o(a_rcv_err_o),
    .ser_tx_data_o(a_ser_tx_data_o),
    .ser_tx_elec_idle_o(a_ser_tx_elec_idle_o),
    .gpio_tx_clk_o(a_gpio_tx_clk_o),
    .gpio_tx_ctrl_o(a_gpio_tx_ctrl_o),
    .gpio_tx_data_o(a_gpio_tx_data),
    .gpio_rx_clk_i(b_gpio_tx_clk_o),
    .gpio_rx_ctrl_i(ba_ctrl),
    .gpio_rx_data_i(b_gpio_tx_data)
  );
  assign a_mon_pkt_tx_valid_o = u_a.pkt_tx_valid;
  assign a_mon_pkt_tx_ready_o = u_a.pkt_tx_ready;
  assign a_mon_pkt_tx_data_o = u_a.pkt_tx_data;
  assign a_mon_pkt_tx_sop_o = u_a.pkt_tx_sop;
  assign a_mon_pkt_tx_eop_o = u_a.pkt_tx_eop;
  assign a_mon_pkt_tx_dllp_o = u_a.pkt_tx_dllp;
  assign a_mon_pkt_rx_valid_o = u_a.pkt_rx_valid;
  assign a_mon_pkt_rx_data_o = u_a.pkt_rx_data;
  assign a_mon_pkt_rx_sop_o = u_a.pkt_rx_sop;
  assign a_mon_pkt_rx_eop_o = u_a.pkt_rx_eop;
  assign a_mon_pkt_rx_dllp_o = u_a.pkt_rx_dllp;
  assign a_mon_pkt_rx_err_o = u_a.pkt_rx_err;
  assign a_cfg_fc_o = {12'(A_FC[5]), 12'(A_FC[4]), 12'(A_FC[3]),
                        12'(A_FC[2]), 12'(A_FC[1]), 12'(A_FC[0])};

  pcie_link #(
    .FC_PH(B_FC[0]), .FC_PD(B_FC[1]), .FC_NPH(B_FC[2]),
    .FC_NPD(B_FC[3]), .FC_CPLH(B_FC[4]), .FC_CPLD(B_FC[5]), .FC_RELEASE(REL),
    .TIMEOUT_DIV(TIMEOUT_DIV), .ERR_TIMEOUT_DIV(ERR_TIMEOUT_DIV),
    .REPLAY_TIMEOUT(REPLAY_TIMEOUT), .LOWER(1), .GPIO_CLK_DIV(GPIO_CLK_DIV)
  ) u_b (
    .clk_i(b_clk_i),
    .ser_rx_clk_i(1'b0),
    .rst_i(b_rst_i),
    .port_role_i(b_port_role_i),
    .tlp_tx_valid_i(b_tlp_tx_valid_i),
    .tlp_tx_data_i(b_tlp_tx_data_i),
    .tlp_tx_sop_i(b_tlp_tx_sop_i),
    .tlp_tx_eop_i(b_tlp_tx_eop_i),
    .tlp_rx_ready_i(b_tlp_rx_ready_i),
    .fc_rel_p_i(b_fc_rel_p_i),
    .fc_rel_pd_i(b_fc_rel_pd_i),
    .fc_rel_np_i(b_fc_rel_np_i),
    .fc_rel_npd_i(b_fc_rel_npd_i),
    .retrain_req_i(b_retrain_req_i),
    .hot_reset_req_i(b_hot_reset_req_i),
    .disable_req_i(b_disable_req_i),
    .loopback_req_i(b_loopback_req_i),
    .ser_tx_rxdet_i(b_ser_tx_rxdet_i),
    .ser_rx_data_i(b_ser_rx_data_i),
    .ser_rx_elec_idle_i(b_ser_rx_elec_idle_i),
    .tlp_tx_ready_o(b_tlp_tx_ready_o),
    .tlp_rx_valid_o(b_tlp_rx_valid_o),
    .tlp_rx_data_o(b_tlp_rx_data_o),
    .tlp_rx_sop_o(b_tlp_rx_sop_o),
    .tlp_rx_eop_o(b_tlp_rx_eop_o),
    .fc_ph_avail_o(b_fc_ph_avail_o),
    .fc_pd_avail_o(b_fc_pd_avail_o),
    .fc_nph_avail_o(b_fc_nph_avail_o),
    .fc_npd_avail_o(b_fc_npd_avail_o),
    .fc_cplh_avail_o(b_fc_cplh_avail_o),
    .fc_cpld_avail_o(b_fc_cpld_avail_o),
    .dl_up_o(b_dl_up_o),
    .dl_state_o(b_dl_state_o),
    .dl_err_o(b_dl_err_o),
    .link_up_o(b_link_up_o),
    .ltssm_state_o(b_ltssm_state_o),
    .rcv_err_o(b_rcv_err_o),
    .ser_tx_data_o(b_ser_tx_data_o),
    .ser_tx_elec_idle_o(b_ser_tx_elec_idle_o),
    .gpio_tx_clk_o(b_gpio_tx_clk_o),
    .gpio_tx_ctrl_o(b_gpio_tx_ctrl_o),
    .gpio_tx_data_o(b_gpio_tx_data),
    .gpio_rx_clk_i(a_gpio_tx_clk_o),
    .gpio_rx_ctrl_i(ab_ctrl),
    .gpio_rx_data_i(a_gpio_tx_data)
  );
  assign b_mon_pkt_tx_valid_o = u_b.pkt_tx_valid;
  assign b_mon_pkt_tx_ready_o = u_b.pkt_tx_ready;
  assign b_mon_pkt_tx_data_o = u_b.pkt_tx_data;
  assign b_mon_pkt_tx_sop_o = u_b.pkt_tx_sop;
  assign b_mon_pkt_tx_eop_o = u_b.pkt_tx_eop;
  assign b_mon_pkt_tx_dllp_o = u_b.pkt_tx_dllp;
  assign b_mon_pkt_rx_valid_o = u_b.pkt_rx_valid;
  assign b_mon_pkt_rx_data_o = u_b.pkt_rx_data;
  assign b_mon_pkt_rx_sop_o = u_b.pkt_rx_sop;
  assign b_mon_pkt_rx_eop_o = u_b.pkt_rx_eop;
  assign b_mon_pkt_rx_dllp_o = u_b.pkt_rx_dllp;
  assign b_mon_pkt_rx_err_o = u_b.pkt_rx_err;
  assign b_cfg_fc_o = {12'(B_FC[5]), 12'(B_FC[4]), 12'(B_FC[3]),
                        12'(B_FC[2]), 12'(B_FC[1]), 12'(B_FC[0])};

endmodule

`default_nettype wire
