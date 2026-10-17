// Data link layer over the phy packet stream.
// Interfaces and behavior: wiki/src/arch/dll/HAS.md; structure:
// wiki/src/rtl/dll/MAS.md.
//
// The top: the DLLP path (receive, transmit arbiter), the data link
// control states and the flow-control initialisation and UpdateFC. The
// outputs that the TLP paths drive (#212 transmit, #213 receive) and the
// error bits they own are tied to their reset values here
// (MAS, Microarchitecture).
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

  // ------------------------------------------------------------ blocks

  pcie_dll_pkg::t_pkt_rx   pkt_rx;
  pcie_dll_pkg::t_pkt_tx   pkt_tx;
  pcie_dll_pkg::t_rx_dllp  rx_dllp;
  pcie_dll_pkg::t_dllp_req acknak_req;
  pcie_dll_pkg::t_dllp_req initfc_req;
  pcie_dll_pkg::t_dllp_req updfc_req;
  pcie_dll_pkg::t_dl_state dl_state;
  pcie_dll_pkg::t_fc_vals  avail;
  logic                    bad_dllp;
  logic                    flush;
  logic                    enter_active;
  logic                    fc_recorded;
  logic                    rx_tlp_good;
  logic                    tx_busy;
  logic                    acknak_take;
  logic                    initfc_take;
  logic                    updfc_take;

  assign pkt_rx = '{valid: pkt_rx_valid_i, data: pkt_rx_data_i, sop: pkt_rx_sop_i,
                    eop: pkt_rx_eop_i, dllp: pkt_rx_dllp_i, err: pkt_rx_err_i,
                    nullify: pkt_rx_nullify_i};

  // Owned by the TLP receive path and Ack/Nak scheduling (#213): no Ack or
  // Nak is due and no good TLP ends FC_INIT2 yet.
  assign acknak_req  = '0;
  assign rx_tlp_good = 1'b0;

  pcie_dll_dllp_rx u_dllp_rx (
    .clk_i      (clk_i),
    .rst_i      (rst_i),
    .flush_i    (flush),
    .pkt_rx_i   (pkt_rx),
    .rx_dllp_o  (rx_dllp),
    .bad_dllp_o (bad_dllp)
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
    .avail_o        (avail)
  );

  pcie_dll_tx u_tx (
    .clk_i          (clk_i),
    .rst_i          (rst_i),
    .flush_i        (flush),
    .acknak_req_i   (acknak_req),
    .initfc_req_i   (initfc_req),
    .updfc_req_i    (updfc_req),
    .acknak_take_o  (acknak_take),
    .initfc_take_o  (initfc_take),
    .updfc_take_o   (updfc_take),
    .busy_o         (tx_busy),
    .pkt_tx_valid_o (pkt_tx_valid_o),
    .pkt_tx_ready_i (pkt_tx_ready_i),
    .pkt_tx_o       (pkt_tx)
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

  // err_o bit 1 is Bad DLLP; the others belong to the TLP paths (#212,
  // #213) and the receive credit check (#214).
  assign err_o = {5'd0, bad_dllp, 1'b0};

  // Owned by the TLP transmit path (#212) and receive path (#213).
  assign tlp_tx_ready_o = 1'b0;
  assign tlp_rx_valid_o = 1'b0;
  assign tlp_rx_data_o  = 32'd0;
  assign tlp_rx_sop_o   = 1'b0;
  assign tlp_rx_eop_o   = 1'b0;
  assign retrain_req_o  = 1'b0;

  // Inputs the TLP paths (#212, #213) consume, and the Ack/Nak grant.
  `COMMON_UNUSED_BITS(unused_inputs, {tlp_tx_valid_i, tlp_tx_data_i, tlp_tx_sop_i,
                                      tlp_tx_eop_i, tlp_rx_ready_i, ltssm_state_i,
                                      acknak_take})

  // Nothing is sent in DL_Inactive (HAS, reset values).
  `COMMON_ASSERT_NEVER(a_quiet_when_inactive, clk_i, rst_i,
                       dl_state == pcie_dll_pkg::DL_INACTIVE && pkt_tx_valid_o)

endmodule

`default_nettype wire
