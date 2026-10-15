// Gen1 x1 MAC: LTSSM, scrambler, ordered sets and framing, with the upper
// packet stream above and the PIPE MAC side below.
// Interfaces and behavior: wiki/src/arch/mac/HAS.md; structure:
// wiki/src/rtl/mac/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The wave-2 sub-IPs are instanced as they land, inert and
// unconnected until the integration (#78).
`default_nettype none

module pcie_mac #(
  parameter int unsigned TIMEOUT_DIV     = 1,    // 1..1024: divides Detect.Quiet and the Hot Reset dwell
  parameter int unsigned ERR_TIMEOUT_DIV = 1,    // 1..1024: divides error-path timeouts, floored at 16384 cycles
  parameter int unsigned N_FTS           = 32,   // 1..255: FTS ordered sets this receiver needs to leave L0s
  parameter int unsigned L0S_IDLE_CYCLES = 256   // 16..875: transmit idle time before L0s entry
) (
  // Clocks, reset and strap
  input  logic        clk_i,                 // core clock and PCLK, 125 MHz
  input  logic        rst_i,                 // synchronous to clk_i, active high
  input  logic        port_role_i,           // 0 downstream, 1 upstream; static outside reset

  // Upper framed-packet stream, transmit (upper layer to link)
  input  logic        pkt_tx_valid_i,
  output logic        pkt_tx_ready_o,
  input  logic [15:0] pkt_tx_data_i,         // [7:0] is the earlier byte
  input  logic        pkt_tx_sop_i,
  input  logic        pkt_tx_eop_i,
  input  logic        pkt_tx_dllp_i,         // tag: 0 TLP, 1 DLLP; held for the packet
  input  logic        pkt_tx_nullify_i,      // on the eop beat of a TLP: end with EDB

  // Upper framed-packet stream, receive (link to upper layer)
  output logic        pkt_rx_valid_o,
  output logic [15:0] pkt_rx_data_o,         // [7:0] is the earlier byte
  output logic        pkt_rx_sop_o,
  output logic        pkt_rx_eop_o,
  output logic        pkt_rx_dllp_o,
  output logic        pkt_rx_err_o,          // on the eop beat: bad or cut-short packet
  output logic        pkt_rx_nullify_o,      // on the eop beat: TLP ended by EDB, no error

  // Link control and status
  output logic        link_up_o,
  output logic [5:0]  ltssm_state_o,
  output logic        rcv_err_o,             // one-cycle pulse per receiver error
  input  logic        retrain_req_i,         // pulse: L0 to Recovery
  input  logic        hot_reset_req_i,
  input  logic        disable_req_i,
  input  logic        loopback_req_i,
  input  logic [1:0]  pm_state_req_i,        // 0 L0, 1 L0s allowed, 2 L1, 3 L2

  // PIPE, MAC to PCS
  output logic        pipe_reset_no,
  output logic [15:0] pipe_tx_data_o,        // [7:0] first
  output logic [1:0]  pipe_tx_datak_o,
  output logic        pipe_tx_detect_rx_o,   // P1: receiver detection; P0: loopback
  output logic        pipe_tx_elec_idle_o,
  output logic        pipe_tx_compliance_o,
  output logic        pipe_rx_polarity_o,
  output logic [1:0]  pipe_power_down_o,     // 0 P0, 1 P0s, 2 P1, 3 P2

  // PIPE, PCS to MAC
  input  logic [15:0] pipe_rx_data_i,        // [7:0] first
  input  logic [1:0]  pipe_rx_datak_i,
  input  logic        pipe_rx_valid_i,
  input  logic        pipe_phy_status_i,
  input  logic        pipe_rx_elec_idle_i,
  input  logic [2:0]  pipe_rx_status_i
);

  // Inputs the shell does not use yet; the sub-IPs consume them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, rst_i, port_role_i, pkt_tx_valid_i, pkt_tx_data_i,
                           pkt_tx_sop_i, pkt_tx_eop_i, pkt_tx_dllp_i, pkt_tx_nullify_i,
                           retrain_req_i, hot_reset_req_i, disable_req_i,
                           loopback_req_i, pm_state_req_i, pipe_rx_data_i,
                           pipe_rx_datak_i, pipe_rx_valid_i, pipe_phy_status_i,
                           pipe_rx_elec_idle_i, pipe_rx_status_i,
                           TIMEOUT_DIV[0], ERR_TIMEOUT_DIV[0], N_FTS[0],
                           L0S_IDLE_CYCLES[0]};

  // Wave-2 sub-IP, elaborated but inert until the integration (#78): the
  // PCS status is tied to "not ready", so the LTSSM stays in Detect.Quiet,
  // and its outputs are not connected yet.
  logic        ltssm_link_up, ltssm_reset_n, ltssm_tx_elec_idle, ltssm_tx_detect_rx;
  logic        ltssm_rx_polarity, ltssm_tx_ts_req, ltssm_tx_compliance_req, ltssm_tx_ts2;
  logic        ltssm_tx_link_pad, ltssm_tx_lane_pad, ltssm_tx_l0, ltssm_tx_stop, ltssm_rx_l0;
  logic [5:0]  ltssm_state;
  logic [1:0]  ltssm_power_down;
  logic [7:0]  ltssm_tx_link, ltssm_tx_lane, ltssm_partner_n_fts;
  logic [2:0]  ltssm_tx_ctl;

  pcie_mac_ltssm #(
    .TIMEOUT_DIV     (TIMEOUT_DIV),
    .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV)
  ) u_ltssm (
    .clk_i               (clk_i),
    .rst_i               (rst_i),
    .port_role_i         (port_role_i),
    .link_up_o           (ltssm_link_up),
    .ltssm_state_o       (ltssm_state),
    .retrain_req_i       (1'b0),
    .hot_reset_req_i     (1'b0),
    .disable_req_i       (1'b0),
    .loopback_req_i      (1'b0),
    .pm_state_req_i      (2'd0),
    .pipe_reset_no       (ltssm_reset_n),
    .pipe_tx_elec_idle_o (ltssm_tx_elec_idle),
    .pipe_tx_detect_rx_o (ltssm_tx_detect_rx),
    .pipe_rx_polarity_o  (ltssm_rx_polarity),
    .pipe_power_down_o   (ltssm_power_down),
    .pipe_phy_status_i   (1'b1),
    .pipe_rx_valid_i     (1'b0),
    .pipe_rx_elec_idle_i (1'b1),
    .pipe_rx_status_i    (3'd0),
    .tx_ts_req_o         (ltssm_tx_ts_req),
    .tx_compliance_req_o (ltssm_tx_compliance_req),
    .tx_ts2_o            (ltssm_tx_ts2),
    .tx_link_pad_o       (ltssm_tx_link_pad),
    .tx_link_o           (ltssm_tx_link),
    .tx_lane_pad_o       (ltssm_tx_lane_pad),
    .tx_lane_o           (ltssm_tx_lane),
    .tx_ctl_o            (ltssm_tx_ctl),
    .os_tx_ack_i         (1'b0),
    .os_tx_valid_i       (1'b0),
    .os_tx_last_i        (1'b0),
    .os_rx_ts_i          (1'b0),
    .os_rx_ts_bad_i      (1'b0),
    .os_rx_ts2_i         (1'b0),
    .os_rx_ts_inv_i      (1'b0),
    .os_rx_link_i        (8'd0),
    .os_rx_link_pad_i    (1'b0),
    .os_rx_link_num_i    (1'b0),
    .os_rx_lane_i        (8'd0),
    .os_rx_lane_pad_i    (1'b0),
    .os_rx_lane_num_i    (1'b0),
    .os_rx_n_fts_i       (8'd0),
    .os_rx_ctl_i         (5'd0),
    .os_rx_skp_i         (1'b0),
    .os_rx_eios_i        (1'b0),
    .os_rx_fts_i         (1'b0),
    .os_rx_os_sym_i      (2'd0),
    .tx_l0_o             (ltssm_tx_l0),
    .tx_stop_o           (ltssm_tx_stop),
    .rx_l0_o             (ltssm_rx_l0),
    .partner_n_fts_o     (ltssm_partner_n_fts),
    .fr_tx_idle_i        (1'b0),
    .fr_rx_idle_i        (2'd0)
  );

  logic unused_ltssm;
  assign unused_ltssm = ^{ltssm_link_up, ltssm_reset_n, ltssm_tx_elec_idle,
                          ltssm_tx_detect_rx, ltssm_rx_polarity, ltssm_tx_ts_req,
                          ltssm_tx_compliance_req, ltssm_tx_ts2, ltssm_tx_link_pad,
                          ltssm_tx_lane_pad, ltssm_tx_l0, ltssm_tx_stop, ltssm_rx_l0,
                          ltssm_state, ltssm_power_down, ltssm_tx_link, ltssm_tx_lane,
                          ltssm_partner_n_fts, ltssm_tx_ctl};

  // Reset values from the HAS: no packet traffic, link down in Detect.Quiet
  // (code 0), PCS held in reset, transmitter in electrical idle, P1.
  assign pkt_tx_ready_o       = 1'b0;
  assign pkt_rx_valid_o       = 1'b0;
  assign pkt_rx_data_o        = 16'd0;
  assign pkt_rx_sop_o         = 1'b0;
  assign pkt_rx_eop_o         = 1'b0;
  assign pkt_rx_dllp_o        = 1'b0;
  assign pkt_rx_err_o         = 1'b0;
  assign pkt_rx_nullify_o     = 1'b0;
  assign link_up_o            = 1'b0;
  assign ltssm_state_o        = pcie_mac_pkg::LTSSM_DETECT_QUIET;
  assign rcv_err_o            = 1'b0;
  assign pipe_reset_no        = 1'b0;
  assign pipe_tx_data_o       = 16'd0;
  assign pipe_tx_datak_o      = 2'd0;
  assign pipe_tx_detect_rx_o  = 1'b0;
  assign pipe_tx_elec_idle_o  = 1'b1;
  assign pipe_tx_compliance_o = 1'b0;
  assign pipe_rx_polarity_o   = 1'b0;
  assign pipe_power_down_o    = pcie_mac_pkg::PD_P1;

endmodule

`default_nettype wire
