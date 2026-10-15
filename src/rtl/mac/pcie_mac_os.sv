// Ordered sets of pcie_mac: TS1/TS2, SKP (with the SKP schedule), EIOS,
// FTS and the compliance pattern, sent and recognised two symbols per
// clock, with the received TS fields decoded for the LTSSM.
// Interface, behavior and structure: wiki/src/rtl/mac/MAS.md; rules:
// wiki/src/arch/mac/SYMBOLS.md.
`default_nettype none

`include "common_macros.svh"

module pcie_mac_os #(
  parameter int unsigned N_FTS = 32  // 1..255: sent in the N_FTS field of every TS
) (
  input  logic        clk_i,
  input  logic        rst_i,                // synchronous, active high

  // Transmit requests (LTSSM); at most one high per cycle
  input  logic        tx_ts_req_i,
  input  logic        tx_skp_req_i,         // requested SKP: the count restarts at 0
  input  logic        tx_eios_req_i,
  input  logic        tx_fts_req_i,
  input  logic        tx_compliance_req_i,
  input  logic        tx_ts2_i,             // 0 TS1, 1 TS2
  input  logic        tx_link_pad_i,
  input  logic [7:0]  tx_link_i,
  input  logic        tx_lane_pad_i,
  input  logic [7:0]  tx_lane_i,
  input  logic [pcie_mac_pkg::TX_CTL_W-1:0] tx_ctl_i,            // Hot Reset, Disable Link, Loopback
  output logic        tx_ack_o,             // a requested set starts this cycle
  output logic        tx_last_o,            // last word of a set this cycle
  output logic        tx_skp_due_o,

  // Transmit schedule (data source)
  input  logic        tx_skp_ok_i,          // a scheduled SKP may start this cycle
  input  logic        tx_count_i,           // the data source sent a counted word

  // Transmit word
  output logic        tx_valid_o,
  output logic [15:0] tx_data_o,            // [7:0] first
  output logic [1:0]  tx_datak_o,
  output logic        tx_compliance_o,

  // Receive
  input  logic [15:0] rx_data_i,            // [7:0] first
  input  logic [1:0]  rx_datak_i,
  input  logic        rx_valid_i,
  input  logic        rx_elec_idle_i,
  output logic [1:0]  rx_os_sym_o,
  output logic        rx_ts_o,
  output logic        rx_ts2_o,
  output logic        rx_ts_inv_o,
  output logic [7:0]  rx_link_o,
  output logic        rx_link_pad_o,
  output logic        rx_link_num_o,
  output logic [7:0]  rx_lane_o,
  output logic        rx_lane_pad_o,
  output logic        rx_lane_num_o,
  output logic [7:0]  rx_n_fts_o,
  output logic [pcie_mac_pkg::RX_CTL_W-1:0] rx_ctl_o,            // ..., Disable Scrambling, Compliance Receive
  output logic        rx_ts_bad_o,
  output logic        rx_skp_o,
  output logic        rx_eios_o,
  output logic        rx_fts_o
);

  `COMMON_ASSERT_PARAM(g_n_fts_check, (N_FTS >= 1) && (N_FTS <= 255),
                       "pcie_mac_os: N_FTS must be 1 to 255")

  localparam logic [7:0] N_FTS_BYTE = 8'(N_FTS);

  // ------------------------------------------------------------- transmit

  logic                                       tx_busy_q, tx_busy_d;
  pcie_mac_pkg::t_tx_kind                  tx_kind_q, tx_kind_d, kind;
  logic [pcie_mac_pkg::TX_IDX_W-1:0]       tx_idx_q, tx_idx_d, idx;
  logic [pcie_mac_pkg::SKP_CREDIT_W-1:0]   skp_credit_q, skp_credit_d;

  // TS fields taken at tx_ack_o.
  logic       f_ts2_q, f_lane_pad_q;
  logic [7:0] f_lane_q;
  logic [pcie_mac_pkg::TX_CTL_W-1:0] f_ctl_q;

  logic       req_any, start_sched, start_req, start, last, counted;
  logic       f_ts2, f_lane_pad;
  logic [7:0] f_lane;
  logic [pcie_mac_pkg::TX_CTL_W-1:0] f_ctl;
  logic [7:0] id_byte, link_byte, lane_byte;
  logic       link_k, lane_k;

  assign req_any = tx_ts_req_i | tx_skp_req_i | tx_eios_req_i | tx_fts_req_i
                 | tx_compliance_req_i;
  assign tx_skp_due_o = (skp_credit_q >= pcie_mac_pkg::SKP_CREDIT_W'(pcie_mac_pkg::SKP_INTERVAL));
  assign start_sched  = !tx_busy_q && tx_skp_due_o && tx_skp_ok_i;
  assign start_req    = !tx_busy_q && !start_sched && req_any;
  assign start        = start_sched | start_req;

  // Kind and word index of the word on the line this cycle.
  always_comb begin
    kind = tx_kind_q;
    idx  = tx_idx_q;
    if (start) begin
      idx = '0;
      if (start_sched || tx_skp_req_i) begin
        kind = pcie_mac_pkg::KIND_SKP;
      end else if (tx_eios_req_i) begin
        kind = pcie_mac_pkg::KIND_EIOS;
      end else if (tx_fts_req_i) begin
        kind = pcie_mac_pkg::KIND_FTS;
      end else if (tx_compliance_req_i) begin
        kind = pcie_mac_pkg::KIND_COMPL;
      end else begin
        kind = pcie_mac_pkg::KIND_TS;
      end
    end
  end

  assign last = (kind == pcie_mac_pkg::KIND_TS)
              ? (idx == pcie_mac_pkg::TX_IDX_W'(pcie_mac_pkg::TS_WORDS - 1))
              : (idx == pcie_mac_pkg::TX_IDX_W'(pcie_mac_pkg::OS_WORDS - 1));

  assign tx_valid_o = tx_busy_q | start;
  assign tx_ack_o   = start_req;
  assign tx_last_o  = tx_valid_o & last;
  assign tx_compliance_o = tx_valid_o && (kind == pcie_mac_pkg::KIND_COMPL)
                        && (idx == '0);

  // Fields: from the inputs in the start cycle, from the registers after.
  assign f_ts2      = start ? tx_ts2_i      : f_ts2_q;
  assign f_lane_pad = start ? tx_lane_pad_i : f_lane_pad_q;
  assign f_lane     = start ? tx_lane_i     : f_lane_q;
  assign f_ctl      = start ? tx_ctl_i      : f_ctl_q;

  assign id_byte   = f_ts2 ? common_pkg::TS2_ID : common_pkg::TS1_ID;
  assign link_k    = tx_link_pad_i;  // word 0 only, the start cycle
  assign link_byte = tx_link_pad_i ? common_pkg::SYM_PAD : tx_link_i;
  assign lane_k    = f_lane_pad;
  assign lane_byte = f_lane_pad ? common_pkg::SYM_PAD : f_lane;

  // The word: {[15:8], [7:0]} and its K flags.
  always_comb begin
    tx_data_o  = 16'd0;
    tx_datak_o = 2'b00;
    if (tx_valid_o) begin
      case (kind)
        pcie_mac_pkg::KIND_TS: begin
          case (idx)
            3'd0: begin
              tx_data_o  = {link_byte, common_pkg::SYM_COM};
              tx_datak_o = {link_k, 1'b1};
            end
            3'd1: begin
              tx_data_o  = {N_FTS_BYTE, lane_byte};
              tx_datak_o = {1'b0, lane_k};
            end
            3'd2: tx_data_o = {5'd0, f_ctl, common_pkg::RATE_GEN1};
            default: tx_data_o = {id_byte, id_byte};
          endcase
        end
        pcie_mac_pkg::KIND_SKP: begin
          tx_data_o  = {common_pkg::SYM_SKP,
                        (idx == '0) ? common_pkg::SYM_COM : common_pkg::SYM_SKP};
          tx_datak_o = 2'b11;
        end
        pcie_mac_pkg::KIND_EIOS: begin
          tx_data_o  = {common_pkg::SYM_IDL,
                        (idx == '0) ? common_pkg::SYM_COM : common_pkg::SYM_IDL};
          tx_datak_o = 2'b11;
        end
        pcie_mac_pkg::KIND_FTS: begin
          tx_data_o  = {common_pkg::SYM_FTS,
                        (idx == '0) ? common_pkg::SYM_COM : common_pkg::SYM_FTS};
          tx_datak_o = 2'b11;
        end
        pcie_mac_pkg::KIND_COMPL: begin
          tx_data_o  = {(idx == '0) ? common_pkg::COMPL_D21_5 : common_pkg::COMPL_D10_2,
                        common_pkg::SYM_COM};
          tx_datak_o = 2'b01;
        end
        default: ;
      endcase
    end
  end

  // Set in progress.
  always_comb begin
    tx_busy_d = 1'b0;
    tx_kind_d = kind;
    tx_idx_d  = idx + 3'd1;
    if (tx_valid_o && !last) begin
      tx_busy_d = 1'b1;
    end
  end

  // SKP count: +2 per counted word; a scheduled SKP takes 1180, a requested
  // one restarts it. Saturates (an assertion fails first).
  assign counted = (tx_valid_o && (kind != pcie_mac_pkg::KIND_COMPL)) || tx_count_i;

  logic [pcie_mac_pkg::SKP_CREDIT_W:0] sum;

  always_comb begin
    sum = {1'b0, skp_credit_q};
    if (start_req && tx_skp_req_i) begin
      sum = '0;
    end else if (start_sched) begin
      sum = sum - (pcie_mac_pkg::SKP_CREDIT_W + 1)'(pcie_mac_pkg::SKP_INTERVAL);
    end
    if (counted) begin
      sum = sum + (pcie_mac_pkg::SKP_CREDIT_W + 1)'(2);
    end
    if (sum[pcie_mac_pkg::SKP_CREDIT_W]) begin
      skp_credit_d = pcie_mac_pkg::SKP_CREDIT_MAX;
    end else begin
      skp_credit_d = sum[pcie_mac_pkg::SKP_CREDIT_W-1:0];
    end
  end

  `COMMON_DFF_RST(tx_busy_q, tx_busy_d, clk_i, rst_i)
  `COMMON_DFF_RST_VAL(tx_kind_q, tx_kind_d, clk_i, rst_i, pcie_mac_pkg::KIND_TS)
  `COMMON_DFF_RST(tx_idx_q, tx_idx_d, clk_i, rst_i)
  `COMMON_DFF_RST(skp_credit_q, skp_credit_d, clk_i, rst_i)
  `COMMON_DFF_EN(f_ts2_q, tx_ts2_i, clk_i, start)
  `COMMON_DFF_EN(f_lane_pad_q, tx_lane_pad_i, clk_i, start)
  `COMMON_DFF_EN(f_lane_q, tx_lane_i, clk_i, start)
  `COMMON_DFF_EN(f_ctl_q, tx_ctl_i, clk_i, start)

  `COMMON_ASSERT(a_one_request, clk_i, rst_i,
                 $onehot0({tx_ts_req_i, tx_skp_req_i, tx_eios_req_i, tx_fts_req_i,
                           tx_compliance_req_i}))
  `COMMON_ASSERT_NEVER(a_count_while_valid, clk_i, rst_i, tx_count_i && tx_valid_o)
  `COMMON_ASSERT_NEVER(a_skp_credit_saturates, clk_i, rst_i,
                       skp_credit_q == pcie_mac_pkg::SKP_CREDIT_MAX)

  // -------------------------------------------------------------- receive

  pcie_mac_pkg::t_rx_state rx_q, rx_d;
  pcie_mac_pkg::t_rx_step  step0, step1;
  pcie_mac_pkg::t_rx_ev    ev0, ev1;
  logic                       rx_qual;
  logic [7:0]                 sym0, sym1;
  logic                       k0, k1;

  assign rx_qual = rx_valid_i && !rx_elec_idle_i;
  assign sym0    = rx_data_i[7:0];
  assign sym1    = rx_data_i[15:8];
  assign k0      = rx_datak_i[0];
  assign k1      = rx_datak_i[1];

  // [7:0] then [15:8], one symbol step each.
  always_comb begin
    step0 = pcie_mac_pkg::rx_step(rx_q, k0, sym0);
    step1 = pcie_mac_pkg::rx_step(step0.st, k1, sym1);
    ev0   = '0;
    ev1   = '0;
    rx_d  = rx_q;
    if (rx_qual) begin
      ev0  = step0.ev;
      ev1  = step1.ev;
      rx_d = step1.st;
    end
  end

  `COMMON_DFF_RST_VAL(rx_q, rx_d, clk_i, rst_i, pcie_mac_pkg::RX_STATE_RESET)

  assign rx_os_sym_o = {ev1.os_sym, ev0.os_sym};
  assign rx_ts_o     = ev0.ts | ev1.ts;
  assign rx_ts_bad_o = ev0.ts_bad | ev1.ts_bad;
  assign rx_skp_o    = ev0.skp | ev1.skp;
  assign rx_eios_o   = ev0.eios | ev1.eios;
  assign rx_fts_o    = ev0.fts | ev1.fts;

  // Fields of the TS completing this cycle; all were filled in earlier cycles.
  assign rx_ts2_o      = (rx_q.id == common_pkg::TS2_ID)
                       || (rx_q.id == common_pkg::TS2_ID_INV);
  assign rx_ts_inv_o   = (rx_q.id == common_pkg::TS1_ID_INV)
                       || (rx_q.id == common_pkg::TS2_ID_INV);
  assign rx_link_o     = rx_q.link;
  assign rx_link_pad_o = rx_q.link_k && (rx_q.link == common_pkg::SYM_PAD);
  assign rx_link_num_o = !rx_q.link_k;
  assign rx_lane_o     = rx_q.lane;
  assign rx_lane_pad_o = rx_q.lane_k && (rx_q.lane == common_pkg::SYM_PAD);
  assign rx_lane_num_o = !rx_q.lane_k;
  assign rx_n_fts_o    = rx_q.n_fts;
  assign rx_ctl_o      = rx_q.ctl;

  `COMMON_ASSERT_NEVER(a_ts_and_bad, clk_i, rst_i, rx_ts_o && rx_ts_bad_o)
  `COMMON_ASSERT(a_one_os_event, clk_i, rst_i, $onehot0({rx_skp_o, rx_eios_o, rx_fts_o}))
  `COMMON_ASSERT_NEVER(a_two_ts_events, clk_i, rst_i, (ev0.ts | ev0.ts_bad) && (ev1.ts | ev1.ts_bad))

endmodule

`default_nettype wire
