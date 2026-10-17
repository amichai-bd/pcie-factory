// Data link layer over the phy packet stream.
// Interfaces and behavior: wiki/src/arch/dll/HAS.md; structure:
// wiki/src/rtl/dll/MAS.md.
//
// The top: the DLLP path (receive, transmit arbiter), the data link
// control states, flow control (initialisation, credit accounting, the
// credit gate, the receive overflow check, UpdateFC), the TLP transmit path
// (sequence numbers, LCRC, replay) and the TLP receive path with its
// receive buffer and Ack/Nak scheduling (MAS, top).
`default_nettype none

`include "common_macros.svh"

module pcie_dll #(
  parameter int unsigned FC_PH   = 8,   // posted header credits advertised; 0 infinite, else 1..127
  parameter int unsigned FC_PD   = 32,  // posted data credits; 0 infinite, else 8..2047
  parameter int unsigned FC_NPH  = 8,   // non-posted header credits; 0 infinite, else 1..127
  parameter int unsigned FC_NPD  = 8,   // non-posted data credits; 0 infinite, else 2..2047
  parameter int unsigned FC_CPLH = 0,   // completion header credits; 0 infinite (default), else 1..127
  parameter int unsigned FC_CPLD = 0    // completion data credits; 0 infinite (default), else 8..2047
) (
  // Clock and reset
  input  logic        clk_i,            // core clock, 125 MHz
  input  logic        rst_i,            // synchronous to clk_i, active high // cdc-domain: clk_i

  // Upper TLP stream, transmit (transaction layer to link)
  input  logic        tlp_tx_valid_i,   // a transmit beat is offered // cdc-domain: clk_i
  output logic        tlp_tx_ready_o,
  input  logic [31:0] tlp_tx_data_i,    // one DW, [7:0] is the earlier byte // cdc-domain: clk_i
  input  logic        tlp_tx_sop_i,     // first beat of a TLP // cdc-domain: clk_i
  input  logic        tlp_tx_eop_i,     // last beat of a TLP // cdc-domain: clk_i

  // Upper TLP stream, receive (link to transaction layer)
  output logic        tlp_rx_valid_o,
  input  logic        tlp_rx_ready_i,   // the upper layer takes the beat // cdc-domain: clk_i
  output logic [31:0] tlp_rx_data_o,    // one DW, [7:0] is the earlier byte
  output logic        tlp_rx_sop_o,
  output logic        tlp_rx_eop_o,

  // Transmit credits available at the partner; all ones is infinite
  output logic [7:0]  fc_ph_avail_o,
  output logic [11:0] fc_pd_avail_o,
  output logic [7:0]  fc_nph_avail_o,
  output logic [11:0] fc_npd_avail_o,
  output logic [7:0]  fc_cplh_avail_o,
  output logic [11:0] fc_cpld_avail_o,

  // Status
  output logic        dl_up_o,
  output logic [1:0]  dl_state_o,       // 0 DL_Inactive, 1 FC_INIT1, 2 FC_INIT2, 3 DL_Active
  output logic [6:0]  err_o,            // one-cycle error pulses, bit order in the HAS

  // Lower packet stream, transmit (to phy)
  output logic        pkt_tx_valid_o,
  input  logic        pkt_tx_ready_i,   // cdc-domain: clk_i
  output logic [15:0] pkt_tx_data_o,    // [7:0] is the earlier byte
  output logic        pkt_tx_sop_o,
  output logic        pkt_tx_eop_o,
  output logic        pkt_tx_dllp_o,    // tag: 0 TLP, 1 DLLP; held for the packet

  // Lower packet stream, receive (from phy)
  input  logic        pkt_rx_valid_i,   // cdc-domain: clk_i
  input  logic [15:0] pkt_rx_data_i,    // [7:0] is the earlier byte // cdc-domain: clk_i
  input  logic        pkt_rx_sop_i,     // cdc-domain: clk_i
  input  logic        pkt_rx_eop_i,     // cdc-domain: clk_i
  input  logic        pkt_rx_dllp_i,    // tag: 0 TLP, 1 DLLP // cdc-domain: clk_i
  input  logic        pkt_rx_err_i,     // with eop: bad or cut-short packet // cdc-domain: clk_i
  input  logic        pkt_rx_nullify_i, // with eop: ended by EDB // cdc-domain: clk_i

  // Physical-layer status and control
  input  logic        link_up_i,        // phy link_up_o // cdc-domain: clk_i
  input  logic [5:0]  ltssm_state_i,    // phy ltssm_state_o; L0 is 0x20..0x2F // cdc-domain: clk_i
  output logic        retrain_req_o     // one-cycle pulse on REPLAY_NUM rollover
);

  // Parameter ranges from the HAS: 0 is infinite; finite headers 1..127,
  // finite data at least one 128-byte payload (posted, completion) or one
  // 16-byte-operand CAS (non-posted), at most 2047.
  `COMMON_ASSERT_PARAM(g_bad_fc_ph, FC_PH <= 127, "FC_PH must be 0 or 1..127")
  `COMMON_ASSERT_PARAM(g_bad_fc_pd, FC_PD == 0 || (FC_PD >= 8 && FC_PD <= 2047),
                       "FC_PD must be 0 or 8..2047")
  `COMMON_ASSERT_PARAM(g_bad_fc_nph, FC_NPH <= 127, "FC_NPH must be 0 or 1..127")
  `COMMON_ASSERT_PARAM(g_bad_fc_npd, FC_NPD == 0 || (FC_NPD >= 2 && FC_NPD <= 2047),
                       "FC_NPD must be 0 or 2..2047")
  `COMMON_ASSERT_PARAM(g_bad_fc_cplh, FC_CPLH <= 127, "FC_CPLH must be 0 or 1..127")
  `COMMON_ASSERT_PARAM(g_bad_fc_cpld, FC_CPLD == 0 || (FC_CPLD >= 8 && FC_CPLD <= 2047),
                       "FC_CPLD must be 0 or 8..2047")

  // The credits this side advertises, per class (0 is infinite).
  localparam pcie_dll_pkg::t_fc_vals ADV = '{
    p:   '{hdr: 8'(FC_PH),   data: 12'(FC_PD)},
    np:  '{hdr: 8'(FC_NPH),  data: 12'(FC_NPD)},
    cpl: '{hdr: 8'(FC_CPLH), data: 12'(FC_CPLD)}
  };

  // Receive buffer size (MAS, receive buffer): for each class, 4 DWs per
  // header credit and 4 per data credit advertised (the reserve for an
  // infinite field), plus one maximum TLP being collected.
  localparam int unsigned RX_HDR_CAP  = (FC_PH == 0 ? pcie_dll_pkg::RX_INF_HDR : FC_PH)
                                      + (FC_NPH == 0 ? pcie_dll_pkg::RX_INF_HDR : FC_NPH)
                                      + (FC_CPLH == 0 ? pcie_dll_pkg::RX_INF_HDR : FC_CPLH);
  localparam int unsigned RX_DATA_CAP = (FC_PD == 0 ? pcie_dll_pkg::RX_INF_DATA : FC_PD)
                                      + (FC_NPD == 0 ? pcie_dll_pkg::RX_INF_DATA : FC_NPD)
                                      + (FC_CPLD == 0 ? pcie_dll_pkg::RX_INF_DATA : FC_CPLD);
  localparam int unsigned RX_BUF_DW   = 4 * (RX_HDR_CAP + RX_DATA_CAP)
                                      + int'(pcie_dll_pkg::RX_MAX_TLP_DW);
  localparam int unsigned RX_ADDR_W   = $clog2(RX_BUF_DW);
  // Any class advertised infinite: the upper layer never stalls (HAS, receive).
  localparam logic        RX_ANY_INF  = FC_PH == 0 || FC_PD == 0 || FC_NPH == 0 || FC_NPD == 0
                                     || FC_CPLH == 0 || FC_CPLD == 0;

  // ------------------------------------------------------------ blocks

  pcie_dll_pkg::t_pkt_rx   pkt_rx;
  pcie_dll_pkg::t_pkt_tx   pkt_tx;
  pcie_dll_pkg::t_rx_dllp  rx_dllp;
  pcie_dll_pkg::t_dllp_req acknak_req;
  pcie_dll_pkg::t_dllp_req initfc_req;
  pcie_dll_pkg::t_dllp_req updfc_req;
  pcie_dll_pkg::t_dl_state dl_state;
  pcie_dll_pkg::t_fc_vals  avail;
  logic                    tx_sop_take;
  logic                    fc_tx_ok;
  logic                    rx_overflow;
  logic                    bad_dllp;
  logic                    flush;
  logic                    enter_active;
  logic                    fc_recorded;
  logic                    rx_tlp_good;
  logic                    tx_busy;
  logic                    acknak_take;
  logic                    initfc_take;
  logic                    updfc_take;
  pcie_dll_pkg::t_tlp_beat tlp_tx;
  pcie_dll_pkg::t_tlp_src  tlp_src;
  logic                    tx_credit_ok;
  logic                    tlp_take;
  logic                    tlp_beat;
  logic                    tlp_abort;
  logic                    tlp_hold;
  logic [2:0]              tlp_tx_err;
  // TLP receive path (#213)
  pcie_dll_pkg::t_rx_word   rx_wr_word;
  pcie_dll_pkg::t_rx_word   rx_word;
  pcie_dll_pkg::t_rx_credit rx_chk;        // a TLP to store, for the overflow check
  pcie_dll_pkg::t_rx_credit rx_stored;     // receive accounting events
  pcie_dll_pkg::t_rx_credit rx_malformed;
  pcie_dll_pkg::t_rx_credit rx_taken;
  logic                     rx_fit;
  logic                     rx_wr_en;
  logic                     rx_commit;
  logic                     rx_rewind;
  logic                     rx_full;
  logic                     bad_tlp;
  logic                     malformed_tlp;

  assign pkt_rx = '{valid: pkt_rx_valid_i, data: pkt_rx_data_i, sop: pkt_rx_sop_i,
                    eop: pkt_rx_eop_i, dllp: pkt_rx_dllp_i, err: pkt_rx_err_i,
                    nullify: pkt_rx_nullify_i};

  // The transmit upper stream as flow control watches it (MAS, credit
  // interface): a taken sop consumes credits.
  assign tx_sop_take = tlp_tx_valid_i && tlp_tx_ready_o && tlp_tx_sop_i;

  // The credit gate (MAS, credit interface): the partner's credits cover
  // the TLP whose first DW is offered.
  assign tx_credit_ok = fc_tx_ok;

  assign tlp_tx = '{data: tlp_tx_data_i, sop: tlp_tx_sop_i, eop: tlp_tx_eop_i};

  pcie_dll_dllp_rx u_dllp_rx (
    .clk_i      (clk_i),
    .rst_i      (rst_i),
    .flush_i    (flush),
    .pkt_rx_i   (pkt_rx),
    .rx_dllp_o  (rx_dllp),
    .bad_dllp_o (bad_dllp)
  );

  pcie_dll_tlp_rx u_tlp_rx (
    .clk_i           (clk_i),
    .rst_i           (rst_i),
    .flush_i         (flush),
    .state_i         (dl_state),
    .pkt_rx_i        (pkt_rx),
    .buf_full_i      (rx_full),
    .wr_en_o         (rx_wr_en),
    .wr_word_o       (rx_wr_word),
    .commit_o        (rx_commit),
    .rewind_o        (rx_rewind),
    .chk_o           (rx_chk),
    .fit_i           (rx_fit),
    .stored_o        (rx_stored),
    .malformed_o     (rx_malformed),
    .good_o          (rx_tlp_good),
    .bad_tlp_o       (bad_tlp),
    .malformed_err_o (malformed_tlp),
    .acknak_req_o    (acknak_req),
    .acknak_take_i   (acknak_take)
  );

  pcie_dll_rx_buf #(
    .ADDR_W (RX_ADDR_W)
  ) u_rx_buf (
    .clk_i      (clk_i),
    .rst_i      (rst_i),
    .flush_i    (flush),
    .wr_en_i    (rx_wr_en),
    .wr_word_i  (rx_wr_word),
    .commit_i   (rx_commit),
    .rewind_i   (rx_rewind),
    .full_o     (rx_full),
    .rx_valid_o (tlp_rx_valid_o),
    .rx_ready_i (tlp_rx_ready_i),
    .rx_word_o  (rx_word),
    .taken_o    (rx_taken)
  );

  pcie_dll_ctrl u_ctrl (
    .clk_i          (clk_i),
    .rst_i          (rst_i),
    .link_up_i      (link_up_i),
    .adv_i          (ADV),
    .rx_dllp_i      (rx_dllp),
    .rx_tlp_good_i  (rx_tlp_good),
    .fc_recorded_i  (fc_recorded),
    .tx_busy_i      (tx_busy),
    .initfc_take_i  (initfc_take),
    .initfc_req_o   (initfc_req),
    .state_o        (dl_state),
    .flush_o        (flush),
    .enter_active_o (enter_active)
  );

  pcie_dll_fc u_fc (
    .clk_i          (clk_i),
    .rst_i          (rst_i),
    .flush_i        (flush),
    .state_i        (dl_state),
    .enter_active_i (enter_active),
    .adv_i          (ADV),
    .rx_dllp_i      (rx_dllp),
    .updfc_take_i   (updfc_take),
    .updfc_req_o    (updfc_req),
    .recorded_o     (fc_recorded),
    .avail_o        (avail),
    .tx_data_i      (tlp_tx_data_i),
    .tx_sop_take_i  (tx_sop_take),
    .tx_ok_o        (fc_tx_ok),
    .rx_chk_i       (rx_chk),
    .rx_fit_o       (rx_fit),
    .rx_stored_i    (rx_stored),
    .rx_malformed_i (rx_malformed),
    .rx_taken_i     (rx_taken),
    .overflow_o     (rx_overflow)
  );

  pcie_dll_tx u_tx (
    .clk_i          (clk_i),
    .rst_i          (rst_i),
    .flush_i        (flush),
    .acknak_req_i   (acknak_req),
    .initfc_req_i   (initfc_req),
    .updfc_req_i    (updfc_req),
    .tlp_src_i      (tlp_src),
    .tlp_hold_i     (tlp_hold),
    .acknak_take_o  (acknak_take),
    .initfc_take_o  (initfc_take),
    .updfc_take_o   (updfc_take),
    .tlp_take_o     (tlp_take),
    .tlp_beat_o     (tlp_beat),
    .tlp_abort_o    (tlp_abort),
    .busy_o         (tx_busy),
    .pkt_tx_valid_o (pkt_tx_valid_o),
    .pkt_tx_ready_i (pkt_tx_ready_i),
    .pkt_tx_o       (pkt_tx)
  );

  pcie_dll_tlp_tx u_tlp_tx (
    .clk_i         (clk_i),
    .rst_i         (rst_i),
    .flush_i       (flush),
    .state_i       (dl_state),
    .ltssm_state_i (ltssm_state_i),
    .tlp_valid_i   (tlp_tx_valid_i),
    .tlp_i         (tlp_tx),
    .tlp_ready_o   (tlp_tx_ready_o),
    .credit_ok_i   (tx_credit_ok),
    .rx_dllp_i     (rx_dllp),
    .src_o         (tlp_src),
    .take_i        (tlp_take),
    .beat_i        (tlp_beat),
    .abort_i       (tlp_abort),
    .hold_o        (tlp_hold),
    .err_o         (tlp_tx_err),
    .retrain_req_o (retrain_req_o)
  );

  // ------------------------------------------------------------ outputs

  assign pkt_tx_data_o = pkt_tx.data;
  assign pkt_tx_sop_o  = pkt_tx.sop;
  assign pkt_tx_eop_o  = pkt_tx.eop;
  assign pkt_tx_dllp_o = pkt_tx.dllp;

  assign dl_state_o = dl_state;
  assign dl_up_o    = dl_state == pcie_dll_pkg::DL_INIT2 || dl_state == pcie_dll_pkg::DL_ACTIVE;

  assign fc_ph_avail_o   = avail.p.hdr;
  assign fc_pd_avail_o   = avail.p.data;
  assign fc_nph_avail_o  = avail.np.hdr;
  assign fc_npd_avail_o  = avail.np.data;
  assign fc_cplh_avail_o = avail.cpl.hdr;
  assign fc_cpld_avail_o = avail.cpl.data;

  // err_o: bit 0 Bad TLP, 1 Bad DLLP, 2 to 4 the TLP transmit path's (replay
  // timeout, REPLAY_NUM rollover, DL protocol error), 5 Receiver overflow,
  // 6 Malformed TLP.
  assign err_o = {malformed_tlp, rx_overflow, tlp_tx_err, bad_dllp, bad_tlp};

  assign tlp_rx_data_o = rx_word.data;
  assign tlp_rx_sop_o  = tlp_rx_valid_o && rx_word.sop;
  assign tlp_rx_eop_o  = tlp_rx_valid_o && rx_word.eop;


  // The upper-layer obligation (HAS, receive): with any class infinite,
  // tlp_rx_ready_i is 1 in every cycle dl_up_o is 1.
  `COMMON_ASSERT_NEVER(a_rx_never_stall, clk_i, rst_i, RX_ANY_INF && dl_up_o && !tlp_rx_ready_i)

  // Nothing starts in DL_Inactive (HAS, reset values): only the tail of a
  // DLLP whose sop the phy took before the link went down.
  `COMMON_ASSERT_NEVER(a_quiet_when_inactive, clk_i, rst_i,
                       dl_state == pcie_dll_pkg::DL_INACTIVE && pkt_tx_valid_o && pkt_tx.sop)

endmodule

`default_nettype wire
