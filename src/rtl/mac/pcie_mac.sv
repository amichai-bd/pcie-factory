// Gen1 x1 MAC: LTSSM, scrambler, ordered sets and framing, with the upper
// packet stream above and the PIPE MAC side below.
// Interfaces and behavior: wiki/src/arch/mac/HAS.md; structure, the line
// mux and the receive alignment: wiki/src/rtl/mac/MAS.md#top.
//
// The ports mirror the HAS (and the PIPE signal names) one to one, so they
// stay flat; inside, the blocks connect through the common_pkg and
// pcie_mac_pkg structs (Q25).
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

  localparam common_pkg::t_pipe_ctl PIPE_RESET = '{reset_n: 1'b0, tx_elec_idle: 1'b1,
                                                 tx_detect_rx: 1'b0, rx_polarity: 1'b0,
                                                 power_down: common_pkg::PD_P1};

  `COMMON_ASSERT_PARAM(g_l0s_idle_check, (L0S_IDLE_CYCLES >= 16) && (L0S_IDLE_CYCLES <= 875),
                       "pcie_mac: L0S_IDLE_CYCLES must be 16 to 875")

  // ------------------------------------------------------- the PIPE ports

  common_pkg::t_word    rx_word;       // the received word, raw
  common_pkg::t_pipe_rx rx_st;         // its PIPE status
  logic               rx_parsed;     // its symbols are parsed (RxValid, no idle)

  assign rx_word   = '{data: pipe_rx_data_i, datak: pipe_rx_datak_i};
  assign rx_st     = '{phy_status: pipe_phy_status_i, valid: pipe_rx_valid_i,
                       elec_idle: pipe_rx_elec_idle_i, status: pipe_rx_status_i};
  assign rx_parsed = pipe_rx_valid_i && !pipe_rx_elec_idle_i;

  pcie_mac_pkg::t_link_req link_req;

  assign link_req = '{retrain: retrain_req_i, hot_reset: hot_reset_req_i,
                      disable_link: disable_req_i, loopback: loopback_req_i,
                      pm_state: pm_state_req_i};

  // ------------------------------------------------------------ the blocks

  // Ordered sets (MAS, Ordered sets): the receive parser reads the raw
  // word; its events are registered once (os_rx_q, os_ts_q) and reach the
  // LTSSM with the frame's idle marks of the same word.
  pcie_mac_pkg::t_os_req os_req;
  pcie_mac_pkg::t_ts_tx  ts_tx;
  pcie_mac_pkg::t_os_tx  os_tx;
  common_pkg::t_word       os_word;
  pcie_mac_pkg::t_os_rx  os_rx,  os_rx_q;
  pcie_mac_pkg::t_ts_rx  os_ts,  os_ts_q;
  logic                  os_skp_due, os_skp_ok, os_cont_q;

  // LTSSM (MAS, LTSSM).
  pcie_mac_pkg::t_os_req ltssm_os_req;
  pcie_mac_pkg::t_fr_ctl fr_ctl;
  common_pkg::t_pipe_ctl   ltssm_pipe, pipe_q;
  common_pkg::t_pipe_rx    rx_st_q;
  logic [7:0]            partner_n_fts;
  logic                  fr_tx_idle;

  // Frame (MAS, Frame).
  pcie_mac_pkg::t_pkt_tx pkt_tx;
  pcie_mac_pkg::t_pkt_rx pkt_rx;
  common_pkg::t_word       fr_word, rx_dscr_word;
  logic                  fr_skp_gnt, fr_tx_act, fr_tx_pkt, fr_tx_open, fr_frame_err;
  pcie_mac_pkg::t_fr_tx_stat fr_tx_stat;   // the line word and the upper offer, for the LTSSM
  logic [1:0]            fr_rx_idle;

  // Transmit line and scrambler.
  common_pkg::t_word       line_word, tx_scr_word;
  logic                  line_valid, tx_scr_valid, rx_scr_valid, compliance_q;
  logic                  status_err_q;

  assign pkt_tx = '{data: pkt_tx_data_i, sop: pkt_tx_sop_i, eop: pkt_tx_eop_i,
                    dllp: pkt_tx_dllp_i, nullify: pkt_tx_nullify_i};

  pcie_mac_ltssm #(
    .TIMEOUT_DIV     (TIMEOUT_DIV),
    .ERR_TIMEOUT_DIV (ERR_TIMEOUT_DIV),
    .N_FTS           (N_FTS),
    .L0S_IDLE_CYCLES (L0S_IDLE_CYCLES)
  ) u_ltssm (
    .clk_i           (clk_i),
    .rst_i           (rst_i),
    .port_role_i     (port_role_i),
    .link_up_o       (link_up_o),
    .ltssm_state_o   (ltssm_state_o),
    .req_i           (link_req),
    .pipe_o          (ltssm_pipe),
    .pipe_rx_i       (rx_st_q),
    .os_req_o        (ltssm_os_req),
    .ts_o            (ts_tx),
    .os_tx_i         (os_tx),
    .os_rx_i         (os_rx_q),
    .os_ts_i         (os_ts_q),
    .fr_o            (fr_ctl),
    .partner_n_fts_o (partner_n_fts),
    .fr_tx_idle_i    (fr_tx_idle),
    .fr_tx_i         (fr_tx_stat),
    .fr_rx_idle_i    (fr_rx_idle)
  );

  // The line mux (MAS, Top): the frame's word while it acts, else the
  // ordered-set block's. A requested set waits for a frame word (the abort
  // word) to finish; a scheduled SKP starts on the frame's grant, or at a
  // set boundary while the LTSSM asks for TS (never in electrical idle or
  // in the compliance pattern). The frame sees a SKP as requested from the
  // moment it is due to the last word of a set in progress.
  assign os_req = '{ts:         ltssm_os_req.ts && !fr_tx_act,
                    skp:        ltssm_os_req.skp && !fr_tx_act,
                    eios:       ltssm_os_req.eios && !fr_tx_act,
                    fts:        ltssm_os_req.fts && !fr_tx_act,
                    compliance: ltssm_os_req.compliance && !fr_tx_act};
  assign os_skp_ok = fr_skp_gnt || (ltssm_os_req.ts && !fr_tx_act);

  pcie_mac_os #(
    .N_FTS (N_FTS)
  ) u_os (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .tx_req_i     (os_req),
    .tx_ts_i      (ts_tx),
    .tx_skp_due_o (os_skp_due),
    .tx_skp_ok_i  (os_skp_ok),
    .tx_count_i   (fr_tx_act),
    .tx_o         (os_tx),
    .tx_word_o    (os_word),
    .rx_word_i    (rx_word),
    .rx_i         (rx_st),
    .rx_ev_o      (os_rx),
    .rx_fields_o  (os_ts)
  );

  `COMMON_DFF_RST(os_cont_q, os_tx.valid && !os_tx.last, clk_i, rst_i)

  pcie_mac_frame u_frame (
    .clk_i          (clk_i),
    .rst_i          (rst_i),
    .pkt_tx_valid_i (pkt_tx_valid_i),
    .pkt_tx_ready_o (pkt_tx_ready_o),
    .pkt_tx_i       (pkt_tx),
    .pkt_rx_o       (pkt_rx),
    .ctl_i          (fr_ctl),
    .skp_req_i      (os_skp_due || os_cont_q),
    .skp_gnt_o      (fr_skp_gnt),
    .tx_act_o       (fr_tx_act),
    .tx_word_o      (fr_word),
    .tx_pkt_o       (fr_tx_pkt),
    .tx_open_o      (fr_tx_open),
    .rx_word_i      (rx_dscr_word),
    .rx_i           (rx_st_q),
    .rx_frame_err_o (fr_frame_err),
    .rx_idle_o      (fr_rx_idle)
  );

  assign fr_tx_idle = fr_tx_act && !fr_tx_pkt;
  assign fr_tx_stat = '{pkt: fr_tx_pkt, open: fr_tx_open, offer: pkt_tx_valid_i};
  assign line_word  = fr_tx_act ? fr_word : os_word;
  assign line_valid = fr_tx_act || os_tx.valid;

  // Transmit scrambler: the frame's data symbols (packets and idle) are
  // scrambled, ordered-set symbols are sent as they are.
  pcie_mac_scrambler u_tx_scr (
    .clk_i   (clk_i),
    .rst_i   (rst_i),
    .valid_i (line_valid),
    .word_i  (line_word),
    .scr_i   ({2{fr_tx_act}}),
    .valid_o (tx_scr_valid),
    .word_o  (tx_scr_word)
  );

  // Receive descrambler: every parsed word; data symbols outside an ordered
  // set are descrambled (the parser's marks of the same raw word).
  pcie_mac_scrambler u_rx_scr (
    .clk_i   (clk_i),
    .rst_i   (rst_i),
    .valid_i (rx_parsed),
    .word_i  (rx_word),
    .scr_i   (~os_rx.os_sym),
    .valid_o (rx_scr_valid),
    .word_o  (rx_dscr_word)
  );

  // ------------------------------------------- receive and PIPE alignment

  // One stage on the receive side: the LTSSM and the frame see the word the
  // descrambler has just registered, with its PIPE status and parser events.
  `COMMON_DFF(rx_st_q, rx_st, clk_i)
  `COMMON_DFF(os_rx_q, os_rx, clk_i)
  `COMMON_DFF(os_ts_q, os_ts, clk_i)
  // One stage on the transmit side: the PIPE controls follow the word into
  // the scrambler's output register.
  `COMMON_DFF_RST_VAL(pipe_q, ltssm_pipe, clk_i, rst_i, PIPE_RESET)
  `COMMON_DFF_RST(compliance_q, os_tx.compliance, clk_i, rst_i)
  // RxStatus errors reach rcv_err_o with the frame's framing errors of the
  // same word.
  `COMMON_DFF_RST(status_err_q, rx_st_q.status[2], clk_i, rst_i)

  // ---------------------------------------------------------------- outputs

  assign pkt_rx_valid_o       = pkt_rx.valid;
  assign pkt_rx_data_o        = pkt_rx.data;
  assign pkt_rx_sop_o         = pkt_rx.sop;
  assign pkt_rx_eop_o         = pkt_rx.eop;
  assign pkt_rx_dllp_o        = pkt_rx.dllp;
  assign pkt_rx_err_o         = pkt_rx.err;
  assign pkt_rx_nullify_o     = pkt_rx.nullify;
  assign rcv_err_o            = !rst_i && (fr_frame_err || status_err_q);
  assign pipe_reset_no        = ltssm_pipe.reset_n;
  assign pipe_tx_data_o       = tx_scr_word.data;
  assign pipe_tx_datak_o      = tx_scr_word.datak;
  assign pipe_tx_detect_rx_o  = pipe_q.tx_detect_rx;
  assign pipe_tx_elec_idle_o  = pipe_q.tx_elec_idle;
  assign pipe_tx_compliance_o = compliance_q;
  assign pipe_rx_polarity_o   = pipe_q.rx_polarity;
  assign pipe_power_down_o    = pipe_q.power_down;

  // Not used here: the partner's N_FTS (an observation net for the DV; the
  // LTSSM keeps its own), and the scramblers' valid outputs (the PIPE status
  // qualifies the words).
  `COMMON_UNUSED_BITS(unused_top, {partner_n_fts, tx_scr_valid, rx_scr_valid,
                                   pipe_q.reset_n})

  // The line has one source at a time; the frame never drives it in
  // electrical idle.
  `COMMON_ASSERT_NEVER(a_one_line_source, clk_i, rst_i, fr_tx_act && os_tx.valid)
  `COMMON_ASSERT_NEVER(a_no_line_in_ei, clk_i, rst_i, line_valid && ltssm_pipe.tx_elec_idle)

endmodule

`default_nettype wire
