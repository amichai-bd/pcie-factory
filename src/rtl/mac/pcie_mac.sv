// Gen1 x1 MAC: LTSSM, scrambler, ordered sets and framing, with the upper
// packet stream above and the PIPE MAC side below.
// Interfaces and behavior: wiki/src/arch/mac/HAS.md; structure:
// wiki/src/rtl/mac/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The sub-IPs (mac_scrambler, mac_os, mac_frame, mac_ltssm) arrive
// in wave 2.
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

  // Wave-2 sub-IP, elaborated but inert until the integration (#78):
  // the LTSSM controls are tied off (no L0), so it holds the line idle, and
  // its outputs are not connected yet.
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
    .tx_l0_i         (1'b0),
    .tx_stop_i       (1'b1),
    .rx_l0_i         (1'b0),
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

  logic unused_frame;
  assign unused_frame = ^{frame_tx_ready, frame_rx_valid, frame_rx_data, frame_rx_flags,
                          frame_skp_gnt, frame_tx_act, frame_tx_data, frame_tx_datak,
                          frame_tx_pkt, frame_rx_frame_err, frame_rx_idle};

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
