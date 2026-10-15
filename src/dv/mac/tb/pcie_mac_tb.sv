// Testbench top of pcie_mac (wiki/src/dv/mac/DV_PLAN.md): the DUT's ports
// packed into one input and one output bus, so the cocotb harness touches
// two handles per cycle, plus an observation bus of the MAS-named internal
// nets the DV plan checks (the registered ordered-set receive events and TS
// fields, and the partner's N_FTS). The field order is FIELDS_IN,
// FIELDS_OUT and FIELDS_OBS in tb/harness.py, first field in the most
// significant bits. The DUT is driven only through its ports.
`default_nettype none

module pcie_mac_tb #(
  parameter int unsigned TIMEOUT_DIV     = 1,
  parameter int unsigned ERR_TIMEOUT_DIV = 1,
  parameter int unsigned N_FTS           = 32,
  parameter int unsigned L0S_IDLE_CYCLES = 256
) (
  input  logic        clk_i,
  input  logic        rst_i,
  input  logic        port_role_i,
  input  logic [50:0] in_i,
  output logic [55:0] out_o,
  output logic [49:0] obs_o,
  output logic [7:0]  cfg_n_fts_o   // the N_FTS parameter, for the shadow
);

  assign cfg_n_fts_o = 8'(N_FTS);

  logic        pkt_tx_valid, pkt_tx_sop, pkt_tx_eop, pkt_tx_dllp, pkt_tx_nullify;
  logic        retrain_req, hot_reset_req, disable_req, loopback_req;
  logic        rx_valid, phy_status, rx_elec_idle;
  logic [1:0]  pm_state_req, rx_datak;
  logic [2:0]  rx_status;
  logic [15:0] pkt_tx_data, rx_data;

  assign {pkt_tx_valid, pkt_tx_data, pkt_tx_sop, pkt_tx_eop, pkt_tx_dllp, pkt_tx_nullify,
          retrain_req, hot_reset_req, disable_req, loopback_req, pm_state_req, rx_data,
          rx_datak, rx_valid, phy_status, rx_elec_idle, rx_status} = in_i;

  logic        pkt_tx_ready, pkt_rx_valid, pkt_rx_sop, pkt_rx_eop, pkt_rx_dllp, pkt_rx_err;
  logic        pkt_rx_nullify, link_up, rcv_err, reset_n, tx_detect_rx, tx_elec_idle;
  logic        tx_compliance, rx_polarity;
  logic [1:0]  tx_datak, power_down;
  logic [5:0]  ltssm_state;
  logic [15:0] pkt_rx_data, tx_data;

  assign out_o = {pkt_tx_ready, pkt_rx_valid, pkt_rx_data, pkt_rx_sop, pkt_rx_eop, pkt_rx_dllp,
                  pkt_rx_err, pkt_rx_nullify, link_up, ltssm_state, rcv_err, reset_n, tx_data,
                  tx_datak, tx_detect_rx, tx_elec_idle, tx_compliance, rx_polarity, power_down};

  // MAS-named internal nets (wiki/src/rtl/mac/MAS.md#top).
  assign obs_o = {u_dut.os_rx_q, u_dut.os_ts_q, u_dut.partner_n_fts};

  pcie_mac #(
    .TIMEOUT_DIV     (TIMEOUT_DIV),
    .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV),
    .N_FTS           (N_FTS),
    .L0S_IDLE_CYCLES (L0S_IDLE_CYCLES)
  ) u_dut (
    .clk_i                (clk_i),
    .rst_i                (rst_i),
    .port_role_i          (port_role_i),
    .pkt_tx_valid_i       (pkt_tx_valid),
    .pkt_tx_ready_o       (pkt_tx_ready),
    .pkt_tx_data_i        (pkt_tx_data),
    .pkt_tx_sop_i         (pkt_tx_sop),
    .pkt_tx_eop_i         (pkt_tx_eop),
    .pkt_tx_dllp_i        (pkt_tx_dllp),
    .pkt_tx_nullify_i     (pkt_tx_nullify),
    .pkt_rx_valid_o       (pkt_rx_valid),
    .pkt_rx_data_o        (pkt_rx_data),
    .pkt_rx_sop_o         (pkt_rx_sop),
    .pkt_rx_eop_o         (pkt_rx_eop),
    .pkt_rx_dllp_o        (pkt_rx_dllp),
    .pkt_rx_err_o         (pkt_rx_err),
    .pkt_rx_nullify_o     (pkt_rx_nullify),
    .link_up_o            (link_up),
    .ltssm_state_o        (ltssm_state),
    .rcv_err_o            (rcv_err),
    .retrain_req_i        (retrain_req),
    .hot_reset_req_i      (hot_reset_req),
    .disable_req_i        (disable_req),
    .loopback_req_i       (loopback_req),
    .pm_state_req_i       (pm_state_req),
    .pipe_reset_no        (reset_n),
    .pipe_tx_data_o       (tx_data),
    .pipe_tx_datak_o      (tx_datak),
    .pipe_tx_detect_rx_o  (tx_detect_rx),
    .pipe_tx_elec_idle_o  (tx_elec_idle),
    .pipe_tx_compliance_o (tx_compliance),
    .pipe_rx_polarity_o   (rx_polarity),
    .pipe_power_down_o    (power_down),
    .pipe_rx_data_i       (rx_data),
    .pipe_rx_datak_i      (rx_datak),
    .pipe_rx_valid_i      (rx_valid),
    .pipe_phy_status_i    (phy_status),
    .pipe_rx_elec_idle_i  (rx_elec_idle),
    .pipe_rx_status_i     (rx_status)
  );

endmodule

`default_nettype wire
