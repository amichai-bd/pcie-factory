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
  input  logic                   clk_i,
  input  logic                   rst_i,         // synchronous, active high

  // Transmit requests (LTSSM): at most one high per cycle, with the TS fields
  input  pcie_mac_pkg::t_os_req  tx_req_i,
  input  pcie_mac_pkg::t_ts_tx   tx_ts_i,
  output logic                   tx_skp_due_o,

  // Transmit schedule (data source)
  input  logic                   tx_skp_ok_i,   // a scheduled SKP may start this cycle
  input  logic                   tx_count_i,    // the data source sent a counted word

  // Transmit word and its events
  output pcie_mac_pkg::t_os_tx   tx_o,
  output common_pkg::t_word        tx_word_o,     // [7:0] first

  // Receive
  input  common_pkg::t_word        rx_word_i,     // [7:0] first
  input  common_pkg::t_pipe_rx     rx_i,          // RxValid and RxElecIdle of the word
  output pcie_mac_pkg::t_os_rx   rx_ev_o,
  output pcie_mac_pkg::t_ts_rx   rx_fields_o
);

  `COMMON_ASSERT_PARAM(g_n_fts_check, (N_FTS >= 1) && (N_FTS <= 255),
                       "pcie_mac_os: N_FTS must be 1 to 255")

  localparam logic [7:0] N_FTS_BYTE = 8'(N_FTS);

  // ------------------------------------------------------------- transmit

  logic                                       tx_busy_q, tx_busy_d;
  pcie_mac_pkg::t_tx_kind                  tx_kind_q, tx_kind_d, kind;
  logic [pcie_mac_pkg::TX_IDX_W-1:0]       tx_idx_q, tx_idx_d, idx;
  logic [pcie_mac_pkg::SKP_CREDIT_W-1:0]   skp_credit_q, skp_credit_d;

  // TS fields taken at tx_o.ack.
  logic       f_ts2_q, f_lane_pad_q;
  logic [7:0] f_lane_q;
  logic [pcie_mac_pkg::TX_CTL_W-1:0] f_ctl_q;

  logic       req_any, start_sched, start_req, start, last, counted;
  logic       tx_valid;
  logic [15:0] tx_data;
  logic [1:0]  tx_datak;
  logic       f_ts2, f_lane_pad;
  logic [7:0] f_lane;
  logic [pcie_mac_pkg::TX_CTL_W-1:0] f_ctl;
  logic [7:0] id_byte, link_byte, lane_byte;
  logic       link_k, lane_k;

  assign req_any = tx_req_i.ts | tx_req_i.skp | tx_req_i.eios | tx_req_i.fts
                 | tx_req_i.compliance;
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
      if (start_sched || tx_req_i.skp) begin
        kind = pcie_mac_pkg::KIND_SKP;
      end else if (tx_req_i.eios) begin
        kind = pcie_mac_pkg::KIND_EIOS;
      end else if (tx_req_i.fts) begin
        kind = pcie_mac_pkg::KIND_FTS;
      end else if (tx_req_i.compliance) begin
        kind = pcie_mac_pkg::KIND_COMPL;
      end else begin
        kind = pcie_mac_pkg::KIND_TS;
      end
    end
  end

  assign last = (kind == pcie_mac_pkg::KIND_TS)
              ? (idx == pcie_mac_pkg::TX_IDX_W'(pcie_mac_pkg::TS_WORDS - 1))
              : (idx == pcie_mac_pkg::TX_IDX_W'(pcie_mac_pkg::OS_WORDS - 1));

  assign tx_valid = tx_busy_q | start;
  assign tx_o.valid      = tx_valid;
  assign tx_word_o.data  = tx_data;
  assign tx_word_o.datak = tx_datak;
  assign tx_o.ack   = start_req;
  assign tx_o.last  = tx_valid & last;
  assign tx_o.compliance = tx_valid && (kind == pcie_mac_pkg::KIND_COMPL)
                        && (idx == '0);

  // Fields: from the inputs in the start cycle, from the registers after.
  assign f_ts2      = start ? tx_ts_i.ts2      : f_ts2_q;
  assign f_lane_pad = start ? tx_ts_i.lane_pad : f_lane_pad_q;
  assign f_lane     = start ? tx_ts_i.lane     : f_lane_q;
  assign f_ctl      = start ? tx_ts_i.ctl      : f_ctl_q;

  assign id_byte   = f_ts2 ? common_pkg::TS2_ID : common_pkg::TS1_ID;
  assign link_k    = tx_ts_i.link_pad;  // word 0 only, the start cycle
  assign link_byte = tx_ts_i.link_pad ? common_pkg::SYM_PAD : tx_ts_i.link;
  assign lane_k    = f_lane_pad;
  assign lane_byte = f_lane_pad ? common_pkg::SYM_PAD : f_lane;

  // The word: {[15:8], [7:0]} and its K flags.
  always_comb begin
    tx_data  = 16'd0;
    tx_datak = 2'b00;
    if (tx_valid) begin
      case (kind)
        pcie_mac_pkg::KIND_TS: begin
          case (idx)
            3'd0: begin
              tx_data  = {link_byte, common_pkg::SYM_COM};
              tx_datak = {link_k, 1'b1};
            end
            3'd1: begin
              tx_data  = {N_FTS_BYTE, lane_byte};
              tx_datak = {1'b0, lane_k};
            end
            3'd2: tx_data = {5'd0, f_ctl, common_pkg::RATE_GEN1};
            default: tx_data = {id_byte, id_byte};
          endcase
        end
        pcie_mac_pkg::KIND_SKP: begin
          tx_data  = {common_pkg::SYM_SKP,
                        (idx == '0) ? common_pkg::SYM_COM : common_pkg::SYM_SKP};
          tx_datak = 2'b11;
        end
        pcie_mac_pkg::KIND_EIOS: begin
          tx_data  = {common_pkg::SYM_IDL,
                        (idx == '0) ? common_pkg::SYM_COM : common_pkg::SYM_IDL};
          tx_datak = 2'b11;
        end
        pcie_mac_pkg::KIND_FTS: begin
          tx_data  = {common_pkg::SYM_FTS,
                        (idx == '0) ? common_pkg::SYM_COM : common_pkg::SYM_FTS};
          tx_datak = 2'b11;
        end
        pcie_mac_pkg::KIND_COMPL: begin
          tx_data  = {(idx == '0) ? common_pkg::COMPL_D21_5 : common_pkg::COMPL_D10_2,
                        common_pkg::SYM_COM};
          tx_datak = 2'b01;
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
    if (tx_valid && !last) begin
      tx_busy_d = 1'b1;
    end
  end

  // SKP count: +2 per counted word; a scheduled SKP takes 1180, a requested
  // one restarts it. Saturates (an assertion fails first).
  assign counted = (tx_valid && (kind != pcie_mac_pkg::KIND_COMPL)) || tx_count_i;

  logic [pcie_mac_pkg::SKP_CREDIT_W:0] sum;

  always_comb begin
    sum = {1'b0, skp_credit_q};
    if (start_req && tx_req_i.skp) begin
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
  `COMMON_DFF_EN(f_ts2_q, tx_ts_i.ts2, clk_i, start)
  `COMMON_DFF_EN(f_lane_pad_q, tx_ts_i.lane_pad, clk_i, start)
  `COMMON_DFF_EN(f_lane_q, tx_ts_i.lane, clk_i, start)
  `COMMON_DFF_EN(f_ctl_q, tx_ts_i.ctl, clk_i, start)

  `COMMON_ASSERT(a_one_request, clk_i, rst_i,
                 $onehot0({tx_req_i.ts, tx_req_i.skp, tx_req_i.eios, tx_req_i.fts,
                           tx_req_i.compliance}))
  `COMMON_ASSERT_NEVER(a_count_while_valid, clk_i, rst_i, tx_count_i && tx_valid)
  `COMMON_ASSERT_NEVER(a_skp_credit_saturates, clk_i, rst_i,
                       skp_credit_q == pcie_mac_pkg::SKP_CREDIT_MAX)

  // -------------------------------------------------------------- receive

  pcie_mac_pkg::t_rx_state rx_q, rx_d;
  pcie_mac_pkg::t_rx_step  step0, step1;
  pcie_mac_pkg::t_rx_ev    ev0, ev1;
  logic                       rx_qual;
  logic [7:0]                 sym0, sym1;
  logic                       k0, k1;

  assign rx_qual = rx_i.valid && !rx_i.elec_idle;
  assign sym0    = rx_word_i.data[7:0];
  assign sym1    = rx_word_i.data[15:8];
  assign k0      = rx_word_i.datak[0];
  assign k1      = rx_word_i.datak[1];

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

  assign rx_ev_o.os_sym = {ev1.os_sym, ev0.os_sym};
  assign rx_ev_o.ts     = ev0.ts | ev1.ts;
  assign rx_ev_o.ts_bad = ev0.ts_bad | ev1.ts_bad;
  assign rx_ev_o.skp    = ev0.skp | ev1.skp;
  assign rx_ev_o.eios   = ev0.eios | ev1.eios;
  assign rx_ev_o.fts    = ev0.fts | ev1.fts;

  // Fields of the TS completing this cycle; all were filled in earlier cycles.
  assign rx_fields_o.ts2      = (rx_q.id == common_pkg::TS2_ID)
                       || (rx_q.id == common_pkg::TS2_ID_INV);
  assign rx_fields_o.ts_inv   = (rx_q.id == common_pkg::TS1_ID_INV)
                       || (rx_q.id == common_pkg::TS2_ID_INV);
  assign rx_fields_o.link     = rx_q.link;
  assign rx_fields_o.link_pad = rx_q.link_k && (rx_q.link == common_pkg::SYM_PAD);
  assign rx_fields_o.link_num = !rx_q.link_k;
  assign rx_fields_o.lane     = rx_q.lane;
  assign rx_fields_o.lane_pad = rx_q.lane_k && (rx_q.lane == common_pkg::SYM_PAD);
  assign rx_fields_o.lane_num = !rx_q.lane_k;
  assign rx_fields_o.n_fts    = rx_q.n_fts;
  assign rx_fields_o.ctl      = rx_q.ctl;

  `COMMON_UNUSED_BITS(unused_rx, {rx_i.phy_status, rx_i.status})

  `COMMON_ASSERT_NEVER(a_ts_and_bad, clk_i, rst_i, rx_ev_o.ts && rx_ev_o.ts_bad)
  `COMMON_ASSERT(a_one_os_event, clk_i, rst_i, $onehot0({rx_ev_o.skp, rx_ev_o.eios, rx_ev_o.fts}))
  `COMMON_ASSERT_NEVER(a_two_ts_events, clk_i, rst_i, (ev0.ts | ev0.ts_bad) && (ev1.ts | ev1.ts_bad))

endmodule

`default_nettype wire
