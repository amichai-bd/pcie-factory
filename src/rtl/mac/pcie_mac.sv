// Gen1 x1 MAC: LTSSM, scrambler, ordered sets and framing, with the upper
// packet stream above and the PIPE MAC side below.
// Interfaces and behavior: wiki/src/arch/mac/HAS.md; structure:
// wiki/src/rtl/mac/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The wave-2 sub-IPs are instanced as they land, inert and not yet
// driving the outputs, until the integration (#78).
`default_nettype none

`include "common_macros.svh"

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

  // Ordered sets (wiki/src/rtl/mac/MAS.md) and the LTSSM
  // (wiki/src/rtl/mac/MAS.md), joined port to port; the frame
  // connections, the transmit word and the outputs arrive with #78. Until
  // then the LTSSM's PCS status is tied to "not ready", so it stays in
  // Detect.Quiet and requests no ordered set, and the receive side only
  // parses.
  logic        os_tx_ack, os_tx_last, os_tx_skp_due, os_tx_valid, os_tx_compliance;
  logic [15:0] os_tx_data;
  logic [1:0]  os_tx_datak, os_rx_os_sym;
  logic        os_rx_ts, os_rx_ts2, os_rx_ts_inv, os_rx_link_pad, os_rx_link_num;
  logic        os_rx_lane_pad, os_rx_lane_num, os_rx_ts_bad, os_rx_skp, os_rx_eios;
  logic        os_rx_fts;
  logic [7:0]  os_rx_link, os_rx_lane, os_rx_n_fts;
  logic [4:0]  os_rx_ctl;

  // Frame signals (wiki/src/rtl/mac/MAS.md).
  logic        frame_tx_ready;
  logic        frame_rx_valid;
  logic [15:0] frame_rx_data;
  logic [4:0]  frame_rx_flags;   // sop, eop, dllp, err, nullify
  logic        frame_skp_gnt;
  logic        frame_tx_act;
  logic [15:0] frame_tx_data;
  logic [1:0]  frame_tx_datak;
  logic        frame_tx_pkt;
  logic        frame_rx_frame_err;
  logic [1:0]  frame_rx_idle;

  // LTSSM signals (wiki/src/rtl/mac/MAS.md).
  logic        ltssm_fr_tx_idle;   // the frame's idle word is on the line
  logic        ltssm_link_up, ltssm_reset_n, ltssm_tx_elec_idle, ltssm_tx_detect_rx;
  logic        ltssm_rx_polarity, ltssm_tx_ts_req, ltssm_tx_compliance_req, ltssm_tx_ts2;
  logic        ltssm_tx_link_pad, ltssm_tx_lane_pad, ltssm_tx_l0, ltssm_tx_stop, ltssm_rx_l0;
  logic [5:0]  ltssm_state;
  logic [1:0]  ltssm_power_down;
  logic [7:0]  ltssm_tx_link, ltssm_tx_lane, ltssm_partner_n_fts;
  logic [2:0]  ltssm_tx_ctl;

  pcie_mac_os #(
    .N_FTS(N_FTS)
  ) u_os (
    .clk_i               (clk_i),
    .rst_i               (rst_i),
    .tx_ts_req_i         (ltssm_tx_ts_req),
    .tx_skp_req_i        (1'b0),
    .tx_eios_req_i       (1'b0),
    .tx_fts_req_i        (1'b0),
    .tx_compliance_req_i (ltssm_tx_compliance_req),
    .tx_ts2_i            (ltssm_tx_ts2),
    .tx_link_pad_i       (ltssm_tx_link_pad),
    .tx_link_i           (ltssm_tx_link),
    .tx_lane_pad_i       (ltssm_tx_lane_pad),
    .tx_lane_i           (ltssm_tx_lane),
    .tx_ctl_i            (ltssm_tx_ctl),
    .tx_ack_o            (os_tx_ack),
    .tx_last_o           (os_tx_last),
    .tx_skp_due_o        (os_tx_skp_due),
    .tx_skp_ok_i         (1'b0),
    .tx_count_i          (1'b0),
    .tx_valid_o          (os_tx_valid),
    .tx_data_o           (os_tx_data),
    .tx_datak_o          (os_tx_datak),
    .tx_compliance_o     (os_tx_compliance),
    .rx_data_i           (pipe_rx_data_i),
    .rx_datak_i          (pipe_rx_datak_i),
    .rx_valid_i          (pipe_rx_valid_i),
    .rx_elec_idle_i      (pipe_rx_elec_idle_i),
    .rx_os_sym_o         (os_rx_os_sym),
    .rx_ts_o             (os_rx_ts),
    .rx_ts2_o            (os_rx_ts2),
    .rx_ts_inv_o         (os_rx_ts_inv),
    .rx_link_o           (os_rx_link),
    .rx_link_pad_o       (os_rx_link_pad),
    .rx_link_num_o       (os_rx_link_num),
    .rx_lane_o           (os_rx_lane),
    .rx_lane_pad_o       (os_rx_lane_pad),
    .rx_lane_num_o       (os_rx_lane_num),
    .rx_n_fts_o          (os_rx_n_fts),
    .rx_ctl_o            (os_rx_ctl),
    .rx_ts_bad_o         (os_rx_ts_bad),
    .rx_skp_o            (os_rx_skp),
    .rx_eios_o           (os_rx_eios),
    .rx_fts_o            (os_rx_fts)
  );

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
    .tx_l0_o             (ltssm_tx_l0),
    .tx_stop_o           (ltssm_tx_stop),
    .rx_l0_o             (ltssm_rx_l0),
    .partner_n_fts_o     (ltssm_partner_n_fts),
    .fr_tx_idle_i        (ltssm_fr_tx_idle),
    .fr_rx_idle_i        (frame_rx_idle)
  );

  `COMMON_UNUSED_BITS(unused_os, {os_tx_skp_due, os_tx_compliance, os_tx_data, os_tx_datak})
  `COMMON_UNUSED_BITS(unused_ltssm, {ltssm_link_up, ltssm_reset_n, ltssm_tx_elec_idle,
                                     ltssm_tx_detect_rx, ltssm_rx_polarity, ltssm_state,
                                     ltssm_power_down, ltssm_partner_n_fts})

  // Framing (wiki/src/rtl/mac/MAS.md), inert until the integration (#78):
  // its LTSSM controls come from u_ltssm, which stays in Detect.Quiet (no
  // L0), so it holds the line idle; its other outputs are not connected yet.
  pcie_mac_frame u_frame (
    .clk_i           (clk_i),
    .rst_i           (rst_i),
    .pkt_tx_valid_i  (pkt_tx_valid_i),
    .pkt_tx_ready_o  (frame_tx_ready),
    .pkt_tx_data_i   (pkt_tx_data_i),
    .pkt_tx_sop_i    (pkt_tx_sop_i),
    .pkt_tx_eop_i    (pkt_tx_eop_i),
    .pkt_tx_dllp_i   (pkt_tx_dllp_i),
    .pkt_tx_nullify_i(pkt_tx_nullify_i),
    .pkt_rx_valid_o  (frame_rx_valid),
    .pkt_rx_data_o   (frame_rx_data),
    .pkt_rx_sop_o    (frame_rx_flags[0]),
    .pkt_rx_eop_o    (frame_rx_flags[1]),
    .pkt_rx_dllp_o   (frame_rx_flags[2]),
    .pkt_rx_err_o    (frame_rx_flags[3]),
    .pkt_rx_nullify_o(frame_rx_flags[4]),
    .tx_l0_i         (ltssm_tx_l0),
    .tx_stop_i       (ltssm_tx_stop),
    .rx_l0_i         (ltssm_rx_l0),
    .skp_req_i       (1'b0),
    .skp_gnt_o       (frame_skp_gnt),
    .tx_act_o        (frame_tx_act),
    .tx_data_o       (frame_tx_data),
    .tx_datak_o      (frame_tx_datak),
    .tx_pkt_o        (frame_tx_pkt),
    .rx_data_i       (pipe_rx_data_i),
    .rx_datak_i      (pipe_rx_datak_i),
    .rx_valid_i      (pipe_rx_valid_i),
    .rx_elec_idle_i  (pipe_rx_elec_idle_i),
    .rx_status_i     (pipe_rx_status_i),
    .rx_frame_err_o  (frame_rx_frame_err),
    .rx_idle_o       (frame_rx_idle)
  );

  // The frame's idle word reaches the line only when the ordered-set block
  // is not sending (the transmit-word selection itself arrives with #78).
  assign ltssm_fr_tx_idle = frame_tx_act && !frame_tx_pkt && !os_tx_valid;

  `COMMON_UNUSED_BITS(unused_frame, {frame_tx_ready, frame_rx_valid, frame_rx_data,
                                     frame_rx_flags, frame_skp_gnt, frame_tx_data,
                                     frame_tx_datak, frame_rx_frame_err})

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
  assign ltssm_state_o        = common_pkg::LTSSM_DETECT_QUIET;
  assign rcv_err_o            = 1'b0;
  assign pipe_reset_no        = 1'b0;
  assign pipe_tx_data_o       = 16'd0;
  assign pipe_tx_datak_o      = 2'd0;
  assign pipe_tx_detect_rx_o  = 1'b0;
  assign pipe_tx_elec_idle_o  = 1'b1;
  assign pipe_tx_compliance_o = 1'b0;
  assign pipe_rx_polarity_o   = 1'b0;
  assign pipe_power_down_o    = common_pkg::PD_P1;

endmodule

`default_nettype wire
