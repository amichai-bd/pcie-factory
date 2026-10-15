// Packet framing between the upper packet stream and the L0 symbol stream:
// STP/SDP, END and EDB, logical idle, ready and SKP deferral on transmit;
// ordered-set skipping, deframing, realignment, err/nullify and framing
// errors on receive. Interface and behavior: wiki/src/rtl/mac/MAS.md;
// the rules: wiki/src/arch/mac/HAS.md (Transmit, Receive) and
// wiki/src/arch/mac/SYMBOLS.md (Framing).
`default_nettype none

`include "common_macros.svh"

module pcie_mac_frame (
  input  logic                  clk_i,
  input  logic                  rst_i,           // synchronous, active high

  // Upper packet stream
  input  logic                  pkt_tx_valid_i,
  output logic                  pkt_tx_ready_o,
  input  pcie_mac_pkg::t_pkt_tx pkt_tx_i,
  output pcie_mac_pkg::t_pkt_rx pkt_rx_o,

  // From the LTSSM: tx_l0 (the line is the frame's), tx_stop (start no
  // packet), rx_l0 (L0 and Rx_L0: packets are received)
  input  pcie_mac_pkg::t_fr_ctl ctl_i,

  // SKP handshake with the ordered-set transmitter
  input  logic                  skp_req_i,       // a SKP ordered set is due or being sent
  output logic                  skp_gnt_o,       // the line is the ordered-set transmitter's this cycle

  // Transmit symbols, before scrambling
  output logic                  tx_act_o,        // the frame drives the line this cycle
  output common_pkg::t_word       tx_word_o,       // [7:0] first on the line
  output pcie_mac_pkg::t_fr_tx_stat tx_stat_o, // this word's status for the LTSSM

  // Receive symbols, after descrambling, with the PIPE status of the word
  input  common_pkg::t_word       rx_word_i,
  input  common_pkg::t_pipe_rx    rx_i,            // RxValid, RxElecIdle, RxStatus (4 to 7: error)

  // Receive status
  output logic                  rx_frame_err_o,  // one cycle after a word with a framing error
  output logic [1:0]            rx_idle_o        // same cycle: symbol i of the word is idle data
);

  // ------------------------------------------------------------ transmit

  pcie_mac_pkg::t_fr_tx_ctl  txc_q, txc_d;  // packet open upstream and on the line
  pcie_mac_pkg::t_fr_tx_data txd_q, txd_d;  // carried byte, DLLP tag, end symbol

  logic tx_start_ok;
  logic tx_ready;
  logic tx_accept;
  logic tx_abort;
  logic [15:0] tx_word;
  logic [1:0]  tx_word_k;
  logic        tx_word_pkt;

  // Between packets, ready needs L0 and Tx_L0, no stop, no SKP due and the
  // previous packet's end sent; from sop to eop it is 1 whatever happens.
  assign tx_start_ok = ctl_i.tx_l0 && !ctl_i.tx_stop && !skp_req_i && !txc_q.line_open;
  assign tx_ready    = !rst_i && (txc_q.up_open || tx_start_ok);
  assign tx_accept   = pkt_tx_valid_i && tx_ready;
  // A packet open on the line ends with EDB when the line leaves L0 or the
  // upper layer leaves a bubble inside it.
  assign tx_abort    = txc_q.line_open && (!ctl_i.tx_l0 || (txc_q.up_open && !pkt_tx_valid_i));

  always_comb begin
    txc_d.up_open   = txc_q.up_open;
    txc_d.line_open = txc_q.line_open;
    txd_d.carry     = txd_q.carry;
    txd_d.dllp      = txd_q.dllp;
    txd_d.tail_edb  = txd_q.tail_edb;
    tx_word        = {common_pkg::IDLE_DATA, common_pkg::IDLE_DATA};
    tx_word_k      = 2'b00;
    tx_word_pkt    = 1'b0;
    if (tx_abort) begin
      // EDB replaces the unsent symbols; one idle keeps the next set in [7:0].
      tx_word        = {common_pkg::IDLE_DATA, common_pkg::SYM_EDB};
      tx_word_k      = 2'b01;
      tx_word_pkt    = 1'b1;
      txc_d.line_open = 1'b0;
      if (tx_accept && pkt_tx_i.eop) begin
        txc_d.up_open = 1'b0;
      end
    end else if (txc_q.line_open && txc_q.up_open) begin
      // A beat inside the packet (valid is 1 here, or tx_abort would be).
      tx_word     = {pkt_tx_i.data[7:0], txd_q.carry};
      tx_word_pkt = 1'b1;
      txd_d.carry  = pkt_tx_i.data[15:8];
      if (pkt_tx_i.eop) begin
        txc_d.up_open  = 1'b0;
        txd_d.tail_edb = pkt_tx_i.nullify && !txd_q.dllp;
      end
    end else if (txc_q.line_open) begin
      // The end word: last byte, then END or EDB.
      tx_word        = {(txd_q.tail_edb ? common_pkg::SYM_EDB : common_pkg::SYM_END),
                        txd_q.carry};
      tx_word_k      = 2'b10;
      tx_word_pkt    = 1'b1;
      txc_d.line_open = 1'b0;
    end else if (tx_accept && !txc_q.up_open) begin
      // The sop beat: STP or SDP, then the first byte.
      tx_word        = {pkt_tx_i.data[7:0],
                        (pkt_tx_i.dllp ? common_pkg::SYM_SDP : common_pkg::SYM_STP)};
      tx_word_k      = 2'b01;
      tx_word_pkt    = 1'b1;
      txd_d.carry     = pkt_tx_i.data[15:8];
      txc_d.line_open = 1'b1;
      txc_d.up_open   = !pkt_tx_i.eop;
      txd_d.dllp      = pkt_tx_i.dllp;
      txd_d.tail_edb  = pkt_tx_i.eop && pkt_tx_i.nullify && !pkt_tx_i.dllp;
    end else if (tx_accept && pkt_tx_i.eop) begin
      // The rest of an aborted packet is taken and discarded.
      txc_d.up_open = 1'b0;
    end
  end


  assign skp_gnt_o      = !rst_i && ctl_i.tx_l0 && skp_req_i && !txc_q.line_open;
  assign tx_act_o       = !rst_i && (ctl_i.tx_l0 || txc_q.line_open) && !skp_gnt_o;
  assign pkt_tx_ready_o = tx_ready;
  assign tx_word_o.data      = tx_word;
  assign tx_word_o.datak     = tx_word_k;
  assign tx_stat_o.idle  = tx_act_o && !tx_word_pkt;
  assign tx_stat_o.pkt   = tx_word_pkt && !rst_i;
  assign tx_stat_o.open  = txc_d.line_open && !rst_i;
  assign tx_stat_o.offer = pkt_tx_valid_i;

  // The upper layer's contract (HAS, Transmit): sop starts a packet, and
  // only outside one.
  `COMMON_ASSERT_NEVER(a_tx_beat_without_sop, clk_i, rst_i,
                       tx_accept && !txc_q.up_open && !pkt_tx_i.sop)
  `COMMON_ASSERT_NEVER(a_tx_sop_inside_packet, clk_i, rst_i,
                       tx_accept && txc_q.up_open && pkt_tx_i.sop)
  `COMMON_ASSERT_NEVER(a_tx_ready_inside_packet, clk_i, rst_i, txc_q.up_open && !tx_ready)
  `COMMON_ASSERT_NEVER(a_tx_skp_inside_packet, clk_i, rst_i, skp_gnt_o && txc_q.line_open)

  // ------------------------------------------------------------- receive

  pcie_mac_pkg::t_fr_rx_ctl  rxc_q, rxc_d;  // ordered-set skip, packet, resync, held/pending valid
  pcie_mac_pkg::t_fr_rx_data rxd_q, rxd_d;  // TS count, packet tags and length, held byte, pending beat

  // Output queue: rxq.q0 is presented, rxq.q1 behind it.
  pcie_mac_pkg::t_fr_rxq     rxq_q, rxq_d;
  pcie_mac_pkg::t_rxq_cnt cnt_q;
  pcie_mac_pkg::t_rxq_cnt cnt_d;
  pcie_mac_pkg::t_rxq_cnt cnt_left;  // after this cycle's pop
  logic [2:0] cnt_sum;                     // cnt_left plus pushes, before the check

  pcie_mac_pkg::t_rx_beat push_a;
  pcie_mac_pkg::t_rx_beat push_b;
  logic [1:0] npush;
  logic       push_late;  // a pending-beat push that is not the first (never)
  logic       ferr;
  logic [1:0] idle;
  logic       ferr_q;

  // Per-step scratch of the parser.
  logic       sym_ok;
  logic       sym_k;
  pcie_mac_pkg::t_byte sym_b;
  logic       parse;
  logic       do_end;
  logic       end_err;
  logic       end_edb;
  logic       do_add;
  logic       do_start;
  logic       bad;
  pcie_mac_pkg::t_rx_beat beat;

  assign sym_ok = rx_i.valid && !rx_i.elec_idle;

  // Step 0 is the word (cut short by leaving Rx_L0, RxValid low or
  // electrical idle); steps 1 and 2 are the symbols, [7:0] first. Each step
  // ends at most one packet or adds one byte, so it pushes at most one beat.
  always_comb begin
    rxc_d.os        = rxc_q.os;
    rxd_d.ts_cnt    = rxd_q.ts_cnt;
    rxc_d.in_pkt    = rxc_q.in_pkt;
    rxd_d.pkt_dllp  = rxd_q.pkt_dllp;
    rxd_d.pkt_bad   = rxd_q.pkt_bad;
    rxd_d.pkt_first = rxd_q.pkt_first;
    rxd_d.len       = rxd_q.len;
    rxc_d.resync    = rxc_q.resync;
    rxc_d.hold_v    = rxc_q.hold_v;
    rxd_d.hold      = rxd_q.hold;
    rxc_d.pend_v    = rxc_q.pend_v;
    rxd_d.pend      = rxd_q.pend;
    rxd_d.pend_sop  = rxd_q.pend_sop;
    rxc_d.tail_v    = 1'b0;
    rxc_d.tail_ferr = 1'b0;
    rxd_d.tail      = rxd_q.tail;
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
    if (!ctl_i.rx_l0) begin
      rxc_d.resync = 1'b0;
    end

    if (rxc_q.in_pkt && rx_i.status[2]) begin
      rxd_d.pkt_bad = 1'b1;
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
        // The previous word's EDB in [15:8] counts only if this word is
        // received; before RxValid 0 or electrical idle it is the PCS's
        // idle tail and the stream was cut after [7:0] (HAS, Receive).
        // The packet it ended goes out now, the cycle's first push.
        if (rxc_q.tail_v) begin
          beat = rxd_q.tail;
          if (!sym_ok) begin
            beat.err     = 1'b1;
            beat.nullify = 1'b0;
            ferr         = ferr || ctl_i.rx_l0;
          end
          push_a = beat;
          npush  = 2'd1;
        end
        if (rxc_q.tail_ferr && sym_ok) begin
          ferr = 1'b1;
          if (ctl_i.rx_l0) begin
            rxc_d.resync = 1'b1;
          end
        end
        // A cut: in Rx_L0 a packet cut by RxValid 0 or electrical idle did
        // not end with END or EDB, a framing error (without resync).
        if (rxc_d.in_pkt && (!ctl_i.rx_l0 || !sym_ok)) begin
          do_end  = 1'b1;
          end_err = 1'b1;
          ferr    = ferr || (ctl_i.rx_l0 && !sym_ok);
        end
      end else if (sym_ok) begin
        sym_k = rx_word_i.datak[s-1];
        sym_b = rx_word_i.data[8*(s-1) +: 8];
        parse = 1'b0;
        if (sym_k && sym_b == common_pkg::SYM_COM) begin
          // An ordered set starts, in every state; inside a packet it is a
          // framing error that ends the packet bad.
          rxc_d.os = pcie_mac_pkg::OS_FIRST;
          if (rxc_d.in_pkt) begin
            do_end  = 1'b1;
            end_err = 1'b1;
            if (ctl_i.rx_l0) begin
              ferr     = 1'b1;
              rxc_d.resync = 1'b1;
            end
          end
        end else begin
          case (rxc_d.os)
            pcie_mac_pkg::OS_FIRST: begin
              if (sym_k && sym_b == common_pkg::SYM_SKP) begin
                rxc_d.os = pcie_mac_pkg::OS_SKP;
              end else if (sym_k && (sym_b == common_pkg::SYM_IDL
                                     || sym_b == common_pkg::SYM_FTS)) begin
                rxc_d.os = pcie_mac_pkg::OS_RUN;
              end else begin
                rxc_d.os     = pcie_mac_pkg::OS_TS;
                rxd_d.ts_cnt = pcie_mac_pkg::t_ts_cnt'(1);
              end
            end
            pcie_mac_pkg::OS_TS: begin
              rxd_d.ts_cnt = rxd_d.ts_cnt + pcie_mac_pkg::t_ts_cnt'(1);
              if (rxd_d.ts_cnt == pcie_mac_pkg::t_ts_cnt'(pcie_mac_pkg::TS_BODY)) begin
                rxc_d.os = pcie_mac_pkg::OS_NONE;
              end
            end
            pcie_mac_pkg::OS_SKP: begin
              if (!(sym_k && sym_b == common_pkg::SYM_SKP)) begin
                rxc_d.os  = pcie_mac_pkg::OS_NONE;
                parse = 1'b1;
              end
            end
            pcie_mac_pkg::OS_RUN: begin
              if (!(sym_k && (sym_b == common_pkg::SYM_IDL
                              || sym_b == common_pkg::SYM_FTS))) begin
                rxc_d.os  = pcie_mac_pkg::OS_NONE;
                parse = 1'b1;
              end
            end
            default: parse = 1'b1;
          endcase
        end
        if (parse && sym_k) begin
          if (sym_b == common_pkg::SYM_STP || sym_b == common_pkg::SYM_SDP) begin
            if (rxc_d.in_pkt) begin
              do_end  = 1'b1;
              end_err = 1'b1;
              ferr    = ferr || ctl_i.rx_l0;
            end
            do_start = ctl_i.rx_l0;
            rxc_d.resync = 1'b0;
          end else if ((sym_b == common_pkg::SYM_END
                        || sym_b == common_pkg::SYM_EDB) && rxc_d.in_pkt) begin
            do_end  = 1'b1;
            end_edb = sym_b == common_pkg::SYM_EDB;
          end else if ((sym_b == common_pkg::SYM_END
                        || sym_b == common_pkg::SYM_EDB) && rxc_d.resync) begin
            rxc_d.resync = 1'b0;
          end else if (rxc_d.in_pkt) begin
            do_end  = 1'b1;
            end_err = 1'b1;
            if (ctl_i.rx_l0) begin
              ferr     = 1'b1;
              rxc_d.resync = 1'b1;
            end
          end else if (s == 2 && sym_b == common_pkg::SYM_EDB && !rxc_d.resync
                       && ctl_i.rx_l0) begin
            rxc_d.tail_ferr = 1'b1;  // decided by the next word
          end else if (!rxc_d.resync && ctl_i.rx_l0) begin
            ferr     = 1'b1;
            rxc_d.resync = 1'b1;
          end
        end else if (parse) begin
          if (rxc_d.in_pkt) begin
            do_add = 1'b1;
          end else if (rxc_d.resync) begin
            // discarded
          end else if (sym_b == common_pkg::IDLE_DATA) begin
            idle[s-1] = 1'b1;
          end else if (ctl_i.rx_l0) begin
            ferr     = 1'b1;
            rxc_d.resync = 1'b1;
          end
        end
      end

      if (do_end) begin
        bad = end_err || rxd_d.pkt_bad || rxc_d.hold_v
              || (rxd_d.pkt_dllp && rxd_d.len != pcie_mac_pkg::t_len'(pcie_mac_pkg::DLLP_BYTES))
              || (end_edb && rxd_d.pkt_dllp);
        beat         = '0;
        beat.eop     = 1'b1;
        beat.dllp    = rxd_d.pkt_dllp;
        beat.err     = bad;
        beat.nullify = end_edb && !bad;
        if (rxc_d.pend_v) begin
          beat.data = rxd_d.pend;
          beat.sop  = rxd_d.pend_sop;
        end else begin
          beat.data = {common_pkg::IDLE_DATA, rxd_d.hold};
          beat.sop  = rxd_d.pkt_first;
        end
        if ((rxc_d.pend_v || rxc_d.hold_v) && end_edb && s == 2) begin
          // EDB in [15:8]: the beat waits for the next word (step 0).
          rxd_d.tail   = beat;
          rxc_d.tail_v = 1'b1;
        end else if (rxc_d.pend_v || rxc_d.hold_v) begin
          if (npush == 2'd0) begin
            push_a = beat;
          end else begin
            push_b = beat;
          end
          npush = npush + 2'd1;
        end else begin
          // A packet with no data byte has no beat to carry it: it is not
          // delivered and counts as a framing error in L0 (MAS, Receive).
          ferr = ferr || ctl_i.rx_l0;
        end
        rxc_d.in_pkt = 1'b0;
        rxc_d.pend_v = 1'b0;
        rxc_d.hold_v = 1'b0;
      end

      if (do_start) begin
        rxc_d.in_pkt    = 1'b1;
        rxd_d.pkt_dllp  = sym_b == common_pkg::SYM_SDP;
        rxd_d.pkt_bad   = 1'b0;
        rxd_d.pkt_first = 1'b1;
        rxd_d.len       = '0;
      end

      if (do_add) begin
        if (rxd_d.len != '1) begin
          rxd_d.len = rxd_d.len + pcie_mac_pkg::t_len'(1);
        end
        if (rxc_d.pend_v) begin
          // A byte follows the pending beat: it is not the last. Always the
          // cycle's first push: a pending beat and a held byte never
          // coexist, so an earlier step of this cycle formed no beat.
          beat      = '0;
          beat.data = rxd_d.pend;
          beat.sop  = rxd_d.pend_sop;
          beat.dllp = rxd_d.pkt_dllp;
          push_late = push_late || npush != 2'd0;
          push_a    = beat;
          npush     = 2'd1;
          rxc_d.pend_v = 1'b0;
        end
        if (rxc_d.hold_v) begin
          rxd_d.pend      = {sym_b, rxd_d.hold};
          rxc_d.pend_v    = 1'b1;
          rxd_d.pend_sop  = rxd_d.pkt_first;
          rxd_d.pkt_first = 1'b0;
          rxc_d.hold_v    = 1'b0;
        end else begin
          rxd_d.hold   = sym_b;
          rxc_d.hold_v = 1'b1;
        end
      end
    end
  end

  // The queue pops q0 every cycle it holds a beat; pushes go behind what is
  // left.
  assign cnt_left = (cnt_q == '0) ? '0 : cnt_q - pcie_mac_pkg::t_rxq_cnt'(1);
  assign cnt_sum  = {1'b0, cnt_left} + {1'b0, npush};

  always_comb begin
    rxq_d.q0 = rxq_q.q0;
    rxq_d.q1 = rxq_q.q1;
    if (cnt_left == '0) begin
      rxq_d.q0 = push_a;
      rxq_d.q1 = push_b;
    end else begin
      rxq_d.q0 = rxq_q.q1;
      rxq_d.q1 = push_a;
    end
    cnt_d = cnt_sum[1:0];
  end

  `COMMON_DFF_RST(cnt_q, cnt_d, clk_i, rst_i)
  `COMMON_DFF_RST(ferr_q, ferr, clk_i, rst_i)

  `COMMON_DFF_RST(txc_q, txc_d, clk_i, rst_i)
  `COMMON_DFF(txd_q, txd_d, clk_i)
  `COMMON_DFF_RST_VAL(rxc_q, rxc_d, clk_i, rst_i, pcie_mac_pkg::FR_RX_CTL_RESET)
  `COMMON_DFF(rxd_q, rxd_d, clk_i)
  `COMMON_DFF(rxq_q, rxq_d, clk_i)

  `COMMON_UNUSED_BITS(unused_rx_status, {rx_i.phy_status, rx_i.status[1:0]})

  logic rx_out_valid;   // a beat is presented

  assign rx_out_valid     = !rst_i && cnt_q != '0;
  assign pkt_rx_o.valid   = rx_out_valid;
  assign pkt_rx_o.data    = rxq_q.q0.data;
  assign pkt_rx_o.sop     = rx_out_valid && rxq_q.q0.sop;
  assign pkt_rx_o.eop     = rx_out_valid && rxq_q.q0.eop;
  assign pkt_rx_o.dllp    = rxq_q.q0.dllp;
  assign pkt_rx_o.err     = rx_out_valid && rxq_q.q0.eop && rxq_q.q0.err;
  assign pkt_rx_o.nullify = rx_out_valid && rxq_q.q0.eop && rxq_q.q0.nullify;
  assign rx_frame_err_o   = !rst_i && ferr_q;
  assign rx_idle_o        = rst_i ? 2'b00 : idle;

  // Two pushes at most per cycle, and the queue never overflows (MAS,
  // Receive: a two-beat cycle needs a pending beat, which only a cycle
  // without a push forms).
  `COMMON_ASSERT_NEVER(a_rx_queue_overflow, clk_i, rst_i, cnt_sum > 3'd2)
  `COMMON_ASSERT_NEVER(a_rx_pend_push_first, clk_i, rst_i, push_late)
  // A packet ended by EDB in [15:8] is closed while its beat waits a word.
  `COMMON_ASSERT_NEVER(a_rx_tail_closed, clk_i, rst_i, rxc_q.tail_v && rxc_q.in_pkt)
  `COMMON_ASSERT_NEVER(a_rx_err_and_nullify, clk_i, rst_i, pkt_rx_o.err && pkt_rx_o.nullify)
  `COMMON_ASSERT_NEVER(a_rx_flags_without_eop, clk_i, rst_i,
                       rx_out_valid && !rxq_q.q0.eop && (rxq_q.q0.err || rxq_q.q0.nullify))

endmodule

`default_nettype wire
