// Testbench top of pcie_mac_ltssm (wiki/src/dv/mac_ltssm/DV_PLAN.md):
// the DUT's inputs and outputs packed into one bus each, so the cocotb
// testbench touches two handles per cycle. The field order is FIELDS_IN and
// FIELDS_OUT in tb/harness.py, first field in the most significant bits.
`default_nettype none

module pcie_mac_ltssm_tb #(
  parameter int unsigned TIMEOUT_DIV     = 1,
  parameter int unsigned ERR_TIMEOUT_DIV = 1
) (
  input  logic        clk_i,
  input  logic        rst_i,
  input  logic        port_role_i,
  input  logic [59:0] in_i,
  output logic [47:0] out_o
);

  logic       phy_status, rx_valid, rx_elec_idle, os_tx_ack, os_tx_valid, os_tx_last;
  logic       os_rx_ts, os_rx_ts_bad, os_rx_ts2, os_rx_ts_inv, os_rx_link_pad;
  logic       os_rx_link_num, os_rx_lane_pad, os_rx_lane_num, os_rx_skp, os_rx_eios;
  logic       os_rx_fts, fr_tx_idle, retrain_req, hot_reset_req, disable_req;
  logic       loopback_req;
  logic [2:0] rx_status;
  logic [4:0] os_rx_ctl;
  logic [1:0] os_rx_os_sym, fr_rx_idle, pm_state_req;
  logic [7:0] os_rx_link, os_rx_lane, os_rx_n_fts;

  assign {phy_status, rx_elec_idle, rx_status, rx_valid, os_tx_ack, os_tx_valid,
          os_tx_last, os_rx_ts, os_rx_ts_bad, os_rx_ts2, os_rx_ts_inv, os_rx_link,
          os_rx_link_pad, os_rx_link_num, os_rx_lane, os_rx_lane_pad, os_rx_lane_num,
          os_rx_n_fts, os_rx_ctl, os_rx_skp, os_rx_eios, os_rx_fts, os_rx_os_sym,
          fr_tx_idle, fr_rx_idle, retrain_req, hot_reset_req, disable_req, loopback_req,
          pm_state_req} = in_i;

  logic       reset_n, tx_elec_idle, tx_detect_rx, rx_polarity, tx_ts_req;
  logic       tx_compliance_req, tx_ts2, tx_link_pad, tx_lane_pad, tx_l0, tx_stop;
  logic       rx_l0, link_up;
  logic [1:0] power_down;
  logic [2:0] tx_ctl;
  logic [7:0] tx_link, tx_lane, partner_n_fts;
  logic [5:0] ltssm_state;

  assign out_o = {reset_n, tx_elec_idle, tx_detect_rx, rx_polarity, power_down, tx_ts_req,
                  tx_compliance_req, tx_ts2, tx_link_pad, tx_link, tx_lane_pad, tx_lane,
                  tx_ctl, tx_l0, tx_stop, rx_l0, link_up, ltssm_state, partner_n_fts};

  pcie_mac_ltssm #(
    .TIMEOUT_DIV     (TIMEOUT_DIV),
    .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV)
  ) u_dut (
    .clk_i               (clk_i),
    .rst_i               (rst_i),
    .port_role_i         (port_role_i),
    .link_up_o           (link_up),
    .ltssm_state_o       (ltssm_state),
    .retrain_req_i       (retrain_req),
    .hot_reset_req_i     (hot_reset_req),
    .disable_req_i       (disable_req),
    .loopback_req_i      (loopback_req),
    .pm_state_req_i      (pm_state_req),
    .pipe_reset_no       (reset_n),
    .pipe_tx_elec_idle_o (tx_elec_idle),
    .pipe_tx_detect_rx_o (tx_detect_rx),
    .pipe_rx_polarity_o  (rx_polarity),
    .pipe_power_down_o   (power_down),
    .pipe_phy_status_i   (phy_status),
    .pipe_rx_valid_i     (rx_valid),
    .pipe_rx_elec_idle_i (rx_elec_idle),
    .pipe_rx_status_i    (rx_status),
    .tx_ts_req_o         (tx_ts_req),
    .tx_compliance_req_o (tx_compliance_req),
    .tx_ts2_o            (tx_ts2),
    .tx_link_pad_o       (tx_link_pad),
    .tx_link_o           (tx_link),
    .tx_lane_pad_o       (tx_lane_pad),
    .tx_lane_o           (tx_lane),
    .tx_ctl_o            (tx_ctl),
    .os_tx_ack_i         (os_tx_ack),
    .os_tx_valid_i       (os_tx_valid),
    .os_tx_last_i        (os_tx_last),
    .os_rx_ts_i          (os_rx_ts),
    .os_rx_ts_bad_i      (os_rx_ts_bad),
    .os_rx_ts2_i         (os_rx_ts2),
    .os_rx_ts_inv_i      (os_rx_ts_inv),
    .os_rx_link_i        (os_rx_link),
    .os_rx_link_pad_i    (os_rx_link_pad),
    .os_rx_link_num_i    (os_rx_link_num),
    .os_rx_lane_i        (os_rx_lane),
    .os_rx_lane_pad_i    (os_rx_lane_pad),
    .os_rx_lane_num_i    (os_rx_lane_num),
    .os_rx_n_fts_i       (os_rx_n_fts),
    .os_rx_ctl_i         (os_rx_ctl),
    .os_rx_skp_i         (os_rx_skp),
    .os_rx_eios_i        (os_rx_eios),
    .os_rx_fts_i         (os_rx_fts),
    .os_rx_os_sym_i      (os_rx_os_sym),
    .tx_l0_o             (tx_l0),
    .tx_stop_o           (tx_stop),
    .rx_l0_o             (rx_l0),
    .partner_n_fts_o     (partner_n_fts),
    .fr_tx_idle_i        (fr_tx_idle),
    .fr_rx_idle_i        (fr_rx_idle)
  );

endmodule

`default_nettype wire
