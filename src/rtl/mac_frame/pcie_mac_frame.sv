// Packet framing between the upper packet stream and the L0 symbol stream:
// STP/SDP, END and EDB, logical idle, ready and SKP deferral on transmit;
// ordered-set skipping, deframing, realignment, err/nullify and framing
// errors on receive. Interface and behavior: wiki/src/rtl/mac_frame/MAS.md;
// the rules: wiki/src/arch/mac/HAS.md (Transmit, Receive) and
// wiki/src/arch/mac/SYMBOLS.md (Framing).
`default_nettype none

`include "common_macros.svh"

module pcie_mac_frame (
  input  logic        clk_i,
  input  logic        rst_i,             // synchronous, active high

  // Upper packet stream, transmit
  input  logic        pkt_tx_valid_i,
  output logic        pkt_tx_ready_o,
  input  logic [15:0] pkt_tx_data_i,     // [7:0] is the earlier byte
  input  logic        pkt_tx_sop_i,
  input  logic        pkt_tx_eop_i,
  input  logic        pkt_tx_dllp_i,     // 0 TLP, 1 DLLP; read on the sop beat
  input  logic        pkt_tx_nullify_i,  // on a TLP's eop beat: end with EDB

  // Upper packet stream, receive
  output logic        pkt_rx_valid_o,
  output logic [15:0] pkt_rx_data_o,
  output logic        pkt_rx_sop_o,
  output logic        pkt_rx_eop_o,
  output logic        pkt_rx_dllp_o,
  output logic        pkt_rx_err_o,
  output logic        pkt_rx_nullify_o,

  // From the LTSSM
  input  logic        tx_l0_i,           // L0, Tx_L0, transmitter active: the line is the frame's
  input  logic        tx_stop_i,         // start no packet: L0 exit pending or L1/L2 wait
  input  logic        rx_l0_i,           // L0 and Rx_L0: packets are received

  // SKP handshake with the ordered-set transmitter
  input  logic        skp_req_i,         // a SKP ordered set is due; held while it is sent
  output logic        skp_gnt_o,         // the line is the ordered-set transmitter's this cycle

  // Transmit symbols, before scrambling
  output logic        tx_act_o,          // the frame drives the line this cycle
  output logic [15:0] tx_data_o,         // [7:0] first on the line
  output logic [1:0]  tx_datak_o,        // bit i: byte i is a K symbol
  output logic        tx_pkt_o,          // this word carries a packet symbol

  // Receive symbols, after descrambling, with PIPE qualifiers of the word
  input  logic [15:0] rx_data_i,         // [7:0] first on the line
  input  logic [1:0]  rx_datak_i,
  input  logic        rx_valid_i,        // PIPE RxValid
  input  logic        rx_elec_idle_i,    // PIPE RxElecIdle
  input  logic [2:0]  rx_status_i,       // PIPE RxStatus; 4 to 7 is an error

  // Receive status, one cycle after the word
  output logic        rx_frame_err_o,    // a framing error in the word
  output logic [1:0]  rx_idle_o          // bit i: symbol i was an idle data symbol
);

  // ------------------------------------------------------------ transmit

  logic tx_up_open_q;    // a packet's sop was accepted, its eop not yet
  logic tx_up_open_d;
  logic tx_line_open_q;  // the packet's start is on the line, its end not yet
  logic tx_line_open_d;
  pcie_mac_frame_pkg::t_byte tx_carry_q;  // packet byte for [7:0] of the next word
  pcie_mac_frame_pkg::t_byte tx_carry_d;
  logic tx_dllp_q;
  logic tx_dllp_d;
  logic tx_tail_edb_q;   // the packet's end symbol is EDB (nullified TLP)
  logic tx_tail_edb_d;

  logic tx_start_ok;
  logic tx_ready;
  logic tx_accept;
  logic tx_abort;
  logic [15:0] tx_word;
  logic [1:0]  tx_word_k;
  logic        tx_word_pkt;

  // Between packets, ready needs L0 and Tx_L0, no stop, no SKP due and the
  // previous packet's end sent; from sop to eop it is 1 whatever happens.
  assign tx_start_ok = tx_l0_i && !tx_stop_i && !skp_req_i && !tx_line_open_q;
  assign tx_ready    = !rst_i && (tx_up_open_q || tx_start_ok);
  assign tx_accept   = pkt_tx_valid_i && tx_ready;
  // A packet open on the line ends with EDB when the line leaves L0 or the
  // upper layer leaves a bubble inside it.
  assign tx_abort    = tx_line_open_q && (!tx_l0_i || (tx_up_open_q && !pkt_tx_valid_i));

  always_comb begin
    tx_up_open_d   = tx_up_open_q;
    tx_line_open_d = tx_line_open_q;
    tx_carry_d     = tx_carry_q;
    tx_dllp_d      = tx_dllp_q;
    tx_tail_edb_d  = tx_tail_edb_q;
    tx_word        = {pcie_mac_frame_pkg::IDLE_DATA, pcie_mac_frame_pkg::IDLE_DATA};
    tx_word_k      = 2'b00;
    tx_word_pkt    = 1'b0;
    if (tx_abort) begin
      // EDB replaces the unsent symbols; one idle keeps the next set in [7:0].
      tx_word        = {pcie_mac_frame_pkg::IDLE_DATA, pcie_mac_frame_pkg::SYM_EDB};
      tx_word_k      = 2'b01;
      tx_word_pkt    = 1'b1;
      tx_line_open_d = 1'b0;
      if (tx_accept && pkt_tx_eop_i) begin
        tx_up_open_d = 1'b0;
      end
    end else if (tx_line_open_q && tx_up_open_q) begin
      // A beat inside the packet (valid is 1 here, or tx_abort would be).
      tx_word     = {pkt_tx_data_i[7:0], tx_carry_q};
      tx_word_pkt = 1'b1;
      tx_carry_d  = pkt_tx_data_i[15:8];
      if (pkt_tx_eop_i) begin
        tx_up_open_d  = 1'b0;
        tx_tail_edb_d = pkt_tx_nullify_i && !tx_dllp_q;
      end
    end else if (tx_line_open_q) begin
      // The end word: last byte, then END or EDB.
      tx_word        = {(tx_tail_edb_q ? pcie_mac_frame_pkg::SYM_EDB : pcie_mac_frame_pkg::SYM_END),
                        tx_carry_q};
      tx_word_k      = 2'b10;
      tx_word_pkt    = 1'b1;
      tx_line_open_d = 1'b0;
    end else if (tx_accept && !tx_up_open_q) begin
      // The sop beat: STP or SDP, then the first byte.
      tx_word        = {pkt_tx_data_i[7:0],
                        (pkt_tx_dllp_i ? pcie_mac_frame_pkg::SYM_SDP : pcie_mac_frame_pkg::SYM_STP)};
      tx_word_k      = 2'b01;
      tx_word_pkt    = 1'b1;
      tx_carry_d     = pkt_tx_data_i[15:8];
      tx_line_open_d = 1'b1;
      tx_up_open_d   = !pkt_tx_eop_i;
      tx_dllp_d      = pkt_tx_dllp_i;
      tx_tail_edb_d  = pkt_tx_eop_i && pkt_tx_nullify_i && !pkt_tx_dllp_i;
    end else if (tx_accept && pkt_tx_eop_i) begin
      // The rest of an aborted packet is taken and discarded.
      tx_up_open_d = 1'b0;
    end
  end

  `COMMON_DFF_RST(tx_up_open_q, tx_up_open_d, clk_i, rst_i)
  `COMMON_DFF_RST(tx_line_open_q, tx_line_open_d, clk_i, rst_i)
  `COMMON_DFF(tx_carry_q, tx_carry_d, clk_i)
  `COMMON_DFF(tx_dllp_q, tx_dllp_d, clk_i)
  `COMMON_DFF(tx_tail_edb_q, tx_tail_edb_d, clk_i)

  assign skp_gnt_o      = !rst_i && tx_l0_i && skp_req_i && !tx_line_open_q;
  assign tx_act_o       = !rst_i && (tx_l0_i || tx_line_open_q) && !skp_gnt_o;
  assign pkt_tx_ready_o = tx_ready;
  assign tx_data_o      = tx_word;
  assign tx_datak_o     = tx_word_k;
  assign tx_pkt_o       = tx_word_pkt && !rst_i;

  // The upper layer's contract (HAS, Transmit): sop starts a packet, and
  // only outside one.
  `COMMON_ASSERT_NEVER(a_tx_beat_without_sop, clk_i, rst_i,
                       tx_accept && !tx_up_open_q && !pkt_tx_sop_i)
  `COMMON_ASSERT_NEVER(a_tx_sop_inside_packet, clk_i, rst_i,
                       tx_accept && tx_up_open_q && pkt_tx_sop_i)
  `COMMON_ASSERT_NEVER(a_tx_ready_inside_packet, clk_i, rst_i, tx_up_open_q && !tx_ready)
  `COMMON_ASSERT_NEVER(a_tx_skp_inside_packet, clk_i, rst_i, skp_gnt_o && tx_line_open_q)

  // ------------------------------------------------------------- receive

  pcie_mac_frame_pkg::t_os_state os_q;
  pcie_mac_frame_pkg::t_os_state os_d;
  pcie_mac_frame_pkg::t_ts_cnt   ts_cnt_q;
  pcie_mac_frame_pkg::t_ts_cnt   ts_cnt_d;
  logic in_pkt_q;      // a packet is open
  logic in_pkt_d;
  logic pkt_dllp_q;
  logic pkt_dllp_d;
  logic pkt_bad_q;     // a word inside it had an RxStatus error
  logic pkt_bad_d;
  logic pkt_first_q;   // its first beat is not formed yet
  logic pkt_first_d;
  pcie_mac_frame_pkg::t_len len_q;  // bytes so far, saturating
  pcie_mac_frame_pkg::t_len len_d;
  logic resync_q;      // after a framing error: discard to STP, SDP, END or EDB
  logic resync_d;
  logic hold_v_q;      // one byte waiting for its pair
  logic hold_v_d;
  pcie_mac_frame_pkg::t_byte hold_q;
  pcie_mac_frame_pkg::t_byte hold_d;
  logic pend_v_q;      // a full beat waiting to learn whether it is the last
  logic pend_v_d;
  pcie_mac_frame_pkg::t_beat_data pend_q;
  pcie_mac_frame_pkg::t_beat_data pend_d;
  logic pend_sop_q;
  logic pend_sop_d;

  // Output queue: q0 is presented, q1 behind it.
  pcie_mac_frame_pkg::t_rx_beat q0_q;
  pcie_mac_frame_pkg::t_rx_beat q0_d;
  pcie_mac_frame_pkg::t_rx_beat q1_q;
  pcie_mac_frame_pkg::t_rx_beat q1_d;
  pcie_mac_frame_pkg::t_rxq_cnt cnt_q;
  pcie_mac_frame_pkg::t_rxq_cnt cnt_d;
  pcie_mac_frame_pkg::t_rxq_cnt cnt_left;  // after this cycle's pop
  logic [2:0] cnt_sum;                     // cnt_left plus pushes, before the check

  pcie_mac_frame_pkg::t_rx_beat push_a;
  pcie_mac_frame_pkg::t_rx_beat push_b;
  logic [1:0] npush;
  logic       push_late;  // a pending-beat push that is not the first (never)
  logic       ferr;
  logic [1:0] idle;
  logic       ferr_q;
  logic [1:0] idle_q;

  // Per-step scratch of the parser.
  logic       sym_ok;
  logic       sym_k;
  pcie_mac_frame_pkg::t_byte sym_b;
  logic       parse;
  logic       do_end;
  logic       end_err;
  logic       end_edb;
  logic       do_add;
  logic       do_start;
  logic       bad;
  pcie_mac_frame_pkg::t_rx_beat beat;

  assign sym_ok = rx_valid_i && !rx_elec_idle_i;

  // Step 0 is the word (cut short by leaving Rx_L0, RxValid low or
  // electrical idle); steps 1 and 2 are the symbols, [7:0] first. Each step
  // ends at most one packet or adds one byte, so it pushes at most one beat.
  always_comb begin
    os_d        = os_q;
    ts_cnt_d    = ts_cnt_q;
    in_pkt_d    = in_pkt_q;
    pkt_dllp_d  = pkt_dllp_q;
    pkt_bad_d   = pkt_bad_q;
    pkt_first_d = pkt_first_q;
    len_d       = len_q;
    resync_d    = resync_q;
    hold_v_d    = hold_v_q;
    hold_d      = hold_q;
    pend_v_d    = pend_v_q;
    pend_d      = pend_q;
    pend_sop_d  = pend_sop_q;
    push_a      = '0;
    push_b      = '0;
    npush       = 2'd0;
    push_late   = 1'b0;
    ferr        = 1'b0;
    idle        = 2'b00;
    sym_k       = 1'b0;
    sym_b       = '0;
    parse       = 1'b0;
    do_end      = 1'b0;
    end_err     = 1'b0;
    end_edb     = 1'b0;
    do_add      = 1'b0;
    do_start    = 1'b0;
    bad         = 1'b0;
    beat        = '0;

    // Resync belongs to L0's receive: it ends when the receiver leaves
    // Rx_L0 (an L0 exit or receive L0s), so idle counts work in Recovery.
    if (!rx_l0_i) begin
      resync_d = 1'b0;
    end

    if (in_pkt_q && rx_status_i[2]) begin
      pkt_bad_d = 1'b1;
    end

    for (int unsigned s = 0; s < 3; s++) begin
      do_end   = 1'b0;
      end_err  = 1'b0;
      end_edb  = 1'b0;
      do_add   = 1'b0;
      do_start = 1'b0;
      sym_k    = 1'b0;
      sym_b    = '0;
      if (s == 0) begin
        if (in_pkt_d && (!rx_l0_i || !sym_ok)) begin
          do_end  = 1'b1;
          end_err = 1'b1;
        end
      end else if (sym_ok) begin
        sym_k = rx_datak_i[s-1];
        sym_b = rx_data_i[8*(s-1) +: 8];
        parse = 1'b0;
        if (sym_k && sym_b == pcie_mac_frame_pkg::SYM_COM) begin
          // An ordered set starts, in every state; inside a packet it is a
          // framing error that ends the packet bad.
          os_d = pcie_mac_frame_pkg::OS_FIRST;
          if (in_pkt_d) begin
            do_end  = 1'b1;
            end_err = 1'b1;
            if (rx_l0_i) begin
              ferr     = 1'b1;
              resync_d = 1'b1;
            end
          end
        end else begin
          case (os_d)
            pcie_mac_frame_pkg::OS_FIRST: begin
              if (sym_k && sym_b == pcie_mac_frame_pkg::SYM_SKP) begin
                os_d = pcie_mac_frame_pkg::OS_SKP;
              end else if (sym_k && (sym_b == pcie_mac_frame_pkg::SYM_IDL
                                     || sym_b == pcie_mac_frame_pkg::SYM_FTS)) begin
                os_d = pcie_mac_frame_pkg::OS_RUN;
              end else begin
                os_d     = pcie_mac_frame_pkg::OS_TS;
                ts_cnt_d = pcie_mac_frame_pkg::t_ts_cnt'(1);
              end
            end
            pcie_mac_frame_pkg::OS_TS: begin
              ts_cnt_d = ts_cnt_d + pcie_mac_frame_pkg::t_ts_cnt'(1);
              if (ts_cnt_d == pcie_mac_frame_pkg::t_ts_cnt'(pcie_mac_frame_pkg::TS_BODY)) begin
                os_d = pcie_mac_frame_pkg::OS_NONE;
              end
            end
            pcie_mac_frame_pkg::OS_SKP: begin
              if (!(sym_k && sym_b == pcie_mac_frame_pkg::SYM_SKP)) begin
                os_d  = pcie_mac_frame_pkg::OS_NONE;
                parse = 1'b1;
              end
            end
            pcie_mac_frame_pkg::OS_RUN: begin
              if (!(sym_k && (sym_b == pcie_mac_frame_pkg::SYM_IDL
                              || sym_b == pcie_mac_frame_pkg::SYM_FTS))) begin
                os_d  = pcie_mac_frame_pkg::OS_NONE;
                parse = 1'b1;
              end
            end
            default: parse = 1'b1;
          endcase
        end
        if (parse && sym_k) begin
          if (sym_b == pcie_mac_frame_pkg::SYM_STP || sym_b == pcie_mac_frame_pkg::SYM_SDP) begin
            if (in_pkt_d) begin
              do_end  = 1'b1;
              end_err = 1'b1;
              ferr    = ferr || rx_l0_i;
            end
            do_start = rx_l0_i;
            resync_d = 1'b0;
          end else if ((sym_b == pcie_mac_frame_pkg::SYM_END
                        || sym_b == pcie_mac_frame_pkg::SYM_EDB) && in_pkt_d) begin
            do_end  = 1'b1;
            end_edb = sym_b == pcie_mac_frame_pkg::SYM_EDB;
          end else if ((sym_b == pcie_mac_frame_pkg::SYM_END
                        || sym_b == pcie_mac_frame_pkg::SYM_EDB) && resync_d) begin
            resync_d = 1'b0;
          end else if (in_pkt_d) begin
            do_end  = 1'b1;
            end_err = 1'b1;
            if (rx_l0_i) begin
              ferr     = 1'b1;
              resync_d = 1'b1;
            end
          end else if (!resync_d && rx_l0_i) begin
            ferr     = 1'b1;
            resync_d = 1'b1;
          end
        end else if (parse) begin
          if (in_pkt_d) begin
            do_add = 1'b1;
          end else if (resync_d) begin
            // discarded
          end else if (sym_b == pcie_mac_frame_pkg::IDLE_DATA) begin
            idle[s-1] = 1'b1;
          end else if (rx_l0_i) begin
            ferr     = 1'b1;
            resync_d = 1'b1;
          end
        end
      end

      if (do_end) begin
        bad = end_err || pkt_bad_d || hold_v_d
              || (pkt_dllp_d && len_d != pcie_mac_frame_pkg::t_len'(pcie_mac_frame_pkg::DLLP_BYTES))
              || (end_edb && pkt_dllp_d);
        beat         = '0;
        beat.eop     = 1'b1;
        beat.dllp    = pkt_dllp_d;
        beat.err     = bad;
        beat.nullify = end_edb && !bad;
        if (pend_v_d) begin
          beat.data = pend_d;
          beat.sop  = pend_sop_d;
        end else begin
          beat.data = {pcie_mac_frame_pkg::IDLE_DATA, hold_d};
          beat.sop  = pkt_first_d;
        end
        if (pend_v_d || hold_v_d) begin
          if (npush == 2'd0) begin
            push_a = beat;
          end else begin
            push_b = beat;
          end
          npush = npush + 2'd1;
        end else begin
          // A packet with no data byte has no beat to carry it: it is not
          // delivered and counts as a framing error in L0 (MAS, Receive).
          ferr = ferr || rx_l0_i;
        end
        in_pkt_d = 1'b0;
        pend_v_d = 1'b0;
        hold_v_d = 1'b0;
      end

      if (do_start) begin
        in_pkt_d    = 1'b1;
        pkt_dllp_d  = sym_b == pcie_mac_frame_pkg::SYM_SDP;
        pkt_bad_d   = 1'b0;
        pkt_first_d = 1'b1;
        len_d       = '0;
      end

      if (do_add) begin
        if (len_d != '1) begin
          len_d = len_d + pcie_mac_frame_pkg::t_len'(1);
        end
        if (pend_v_d) begin
          // A byte follows the pending beat: it is not the last. Always the
          // cycle's first push: a pending beat and a held byte never
          // coexist, so an earlier step of this cycle formed no beat.
          beat      = '0;
          beat.data = pend_d;
          beat.sop  = pend_sop_d;
          beat.dllp = pkt_dllp_d;
          push_late = push_late || npush != 2'd0;
          push_a    = beat;
          npush     = 2'd1;
          pend_v_d = 1'b0;
        end
        if (hold_v_d) begin
          pend_d      = {sym_b, hold_d};
          pend_v_d    = 1'b1;
          pend_sop_d  = pkt_first_d;
          pkt_first_d = 1'b0;
          hold_v_d    = 1'b0;
        end else begin
          hold_d   = sym_b;
          hold_v_d = 1'b1;
        end
      end
    end
  end

  // The queue pops q0 every cycle it holds a beat; pushes go behind what is
  // left.
  assign cnt_left = (cnt_q == '0) ? '0 : cnt_q - pcie_mac_frame_pkg::t_rxq_cnt'(1);
  assign cnt_sum  = {1'b0, cnt_left} + {1'b0, npush};

  always_comb begin
    q0_d = q0_q;
    q1_d = q1_q;
    if (cnt_left == '0) begin
      q0_d = push_a;
      q1_d = push_b;
    end else begin
      q0_d = q1_q;
      q1_d = push_a;
    end
    cnt_d = cnt_sum[1:0];
  end

  `COMMON_DFF_RST_VAL(os_q, os_d, clk_i, rst_i, pcie_mac_frame_pkg::OS_NONE)
  `COMMON_DFF(ts_cnt_q, ts_cnt_d, clk_i)
  `COMMON_DFF_RST(in_pkt_q, in_pkt_d, clk_i, rst_i)
  `COMMON_DFF(pkt_dllp_q, pkt_dllp_d, clk_i)
  `COMMON_DFF(pkt_bad_q, pkt_bad_d, clk_i)
  `COMMON_DFF(pkt_first_q, pkt_first_d, clk_i)
  `COMMON_DFF(len_q, len_d, clk_i)
  `COMMON_DFF_RST(resync_q, resync_d, clk_i, rst_i)
  `COMMON_DFF_RST(hold_v_q, hold_v_d, clk_i, rst_i)
  `COMMON_DFF(hold_q, hold_d, clk_i)
  `COMMON_DFF_RST(pend_v_q, pend_v_d, clk_i, rst_i)
  `COMMON_DFF(pend_q, pend_d, clk_i)
  `COMMON_DFF(pend_sop_q, pend_sop_d, clk_i)
  `COMMON_DFF(q0_q, q0_d, clk_i)
  `COMMON_DFF(q1_q, q1_d, clk_i)
  `COMMON_DFF_RST(cnt_q, cnt_d, clk_i, rst_i)
  `COMMON_DFF_RST(ferr_q, ferr, clk_i, rst_i)
  `COMMON_DFF_RST(idle_q, idle, clk_i, rst_i)

  `COMMON_UNUSED_BITS(unused_rx_status, rx_status_i[1:0])

  assign pkt_rx_valid_o   = !rst_i && cnt_q != '0;
  assign pkt_rx_data_o    = q0_q.data;
  assign pkt_rx_sop_o     = pkt_rx_valid_o && q0_q.sop;
  assign pkt_rx_eop_o     = pkt_rx_valid_o && q0_q.eop;
  assign pkt_rx_dllp_o    = q0_q.dllp;
  assign pkt_rx_err_o     = pkt_rx_valid_o && q0_q.eop && q0_q.err;
  assign pkt_rx_nullify_o = pkt_rx_valid_o && q0_q.eop && q0_q.nullify;
  assign rx_frame_err_o   = !rst_i && ferr_q;
  assign rx_idle_o        = rst_i ? 2'b00 : idle_q;

  // Two pushes at most per cycle, and the queue never overflows (MAS,
  // Receive: a two-beat cycle needs a pending beat, which only a cycle
  // without a push forms).
  `COMMON_ASSERT_NEVER(a_rx_queue_overflow, clk_i, rst_i, cnt_sum > 3'd2)
  `COMMON_ASSERT_NEVER(a_rx_pend_push_first, clk_i, rst_i, push_late)
  `COMMON_ASSERT_NEVER(a_rx_err_and_nullify, clk_i, rst_i, pkt_rx_err_o && pkt_rx_nullify_o)
  `COMMON_ASSERT_NEVER(a_rx_flags_without_eop, clk_i, rst_i,
                       pkt_rx_valid_o && !q0_q.eop && (q0_q.err || q0_q.nullify))

endmodule

`default_nettype wire
