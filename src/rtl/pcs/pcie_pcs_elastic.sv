// SKP elastic buffer from ser_rx_clk_i to clk_i: SKP removal and packing on
// the write side, a 16-word common_cdc_fifo, SKP addition, start and
// recentring on the read side (wiki/src/rtl/pcs/MAS.md#elastic-buffer).
`default_nettype none

`include "common_macros.svh"

module pcie_pcs_elastic (
  // clk_i side (read).
  input  logic                     clk_i,
  input  logic                     rst_i,           // cdc-domain: clk_i
  // ser_rx_clk_i side (write).
  input  logic                     ser_rx_clk_i,
  input  logic                     ser_rx_valid_i,  // cdc-domain: ser_rx_clk_i
  input  pcie_pcs_pkg::t_rx_pair   ser_rx_i,        // cdc-domain: ser_rx_clk_i
  // Read side outputs, combinational from clk_i registers.
  output logic                     rx_valid_o,
  output pcie_pcs_pkg::t_rx_pair   rx_o,
  output pcie_pcs_pkg::t_eb_status rx_status_o,
  output logic [pcie_pcs_pkg::EB_FILL_W-1:0] fill_o,
  // The RX-domain reset (crossing 2 of the pcs HAS), for the rest of the
  // ser_rx_clk_i domain.
  output logic                     ser_rx_rst_o
);

  // ----------------------------------------------------- RX-domain reset

  // Asserts with rst_i, with no clock edge; releases on the second
  // ser_rx_clk_i edge after rst_i falls. Every write-side flop takes it
  // asynchronously.
  logic rx_rst;   // the RX-domain reset

  `COMMON_RESET_SYNC(u_rx_rst, rx_rst, rst_i, ser_rx_clk_i)

  assign ser_rx_rst_o = rx_rst;

  // ------------------------------------------------------------ write side

  pcie_pcs_pkg::t_eb_wr   w_q;
  pcie_pcs_pkg::t_eb_wr   w_d;
  pcie_pcs_pkg::t_sym10   w_sym0;
  pcie_pcs_pkg::t_sym10   w_sym1;
  logic                   w_lock;       // the aligner's lock for the whole word
  logic                   fill_high_rx; // synchronized fill high
  logic                   w_hold_live;  // the packing register keeps its symbol this cycle
  logic                   w_flush;      // drop it: lock starts
  pcie_pcs_pkg::t_eb_sym  w_new0;
  pcie_pcs_pkg::t_eb_sym  w_new1;
  logic                   w_drop;       // unlocked word dropped (recentring)
  logic                   w_take;       // word taken in
  logic                   w_rm0;        // sym0 is a removed SKP
  logic                   w_rm1;        // sym1 is a removed SKP
  logic                   w_wr;         // a packed word is ready
  logic                   w_push;
  logic                   w_ready;
  pcie_pcs_pkg::t_eb_sym  w_first;      // the packed word's symbols
  pcie_pcs_pkg::t_eb_sym  w_second;
  pcie_pcs_pkg::t_eb_word w_word;
  logic                   w_split;      // two locked symbols of different generations

  assign w_sym0 = ser_rx_i.sym0.code;
  assign w_sym1 = ser_rx_i.sym1.code;
  assign w_lock = ser_rx_i.sym0.lock;

  always_comb begin
    w_d    = w_q;
    w_drop = ser_rx_valid_i & ~w_lock & fill_high_rx & (w_q.holdoff == '0);
    if (w_drop) begin
      w_d.holdoff = pcie_pcs_pkg::HOLDOFF_W'(pcie_pcs_pkg::EB_HOLDOFF - 1);
    end else if (w_q.holdoff != '0) begin
      w_d.holdoff = w_q.holdoff - 1'b1;
    end else begin
      w_d.holdoff = '0;
    end
    w_take = ser_rx_valid_i & ~w_drop;
    // Remove the first SKP after a locked COM while fill high is seen; at
    // most one of the two symbols can be such a SKP.
    w_rm0  = w_take & w_lock & fill_high_rx & w_q.prev_com & pcie_pcs_pkg::is_skp(w_sym0);
    w_rm1  = w_take & w_lock & fill_high_rx & pcie_pcs_pkg::is_com(w_sym0)
           & pcie_pcs_pkg::is_skp(w_sym1);
    w_new0 = '{ovf: 1'b0, rem: w_q.pend_rem, idle: ser_rx_i.sym0.idle, gen: ser_rx_i.sym0.gen,
               lock: ser_rx_i.sym0.lock, sym: w_sym0};
    w_new1 = '{ovf: 1'b0, rem: w_rm0, idle: ser_rx_i.sym1.idle, gen: ser_rx_i.sym1.gen,
               lock: ser_rx_i.sym1.lock, sym: w_sym1};

    // Lock start: a locked word that follows a held unlocked symbol starts a
    // FIFO word of its own. The unlocked symbol is dropped, so the first
    // locked word is not packed with it and tagged unlocked.
    w_flush     = w_take & w_q.hold_valid & w_lock & ~w_q.hold.lock;
    w_hold_live = w_q.hold_valid & ~w_flush;

    w_wr     = 1'b0;
    w_first  = w_q.hold;
    w_second = w_new0;
    if (w_take) begin
      w_d.prev_com = pcie_pcs_pkg::is_com(w_sym1);
      w_d.pend_rem = w_rm1;
      if (!w_hold_live) begin
        w_d.hold_valid = 1'b0;
        if (w_rm0) begin
          w_d.hold_valid = 1'b1;
          w_d.hold       = w_new1;
        end else if (w_rm1) begin
          w_d.hold_valid = 1'b1;
          w_d.hold       = w_new0;
        end else begin
          w_wr     = 1'b1;
          w_first  = w_new0;
          w_second = w_new1;
        end
      end else begin
        w_wr = 1'b1;
        if (w_rm0) begin
          w_second       = w_new1;
          w_d.hold_valid = 1'b0;
        end else if (w_rm1) begin
          w_second       = w_new0;
          w_d.hold_valid = 1'b0;
        end else begin
          w_second       = w_new0;
          w_d.hold       = w_new1;
        end
      end
    end
    // Each symbol keeps its own lock, so a lock end may split a word: the
    // last locked symbol, then an unlocked one. Two locked symbols of
    // different generations (a same-cycle relock) are both tagged unlocked
    // with the newer generation.
    w_split  = w_first.lock & w_second.lock & (w_first.gen != w_second.gen);
    w_word = '{ovf:  w_q.pend_ovf,
               rem:  {w_second.rem, w_first.rem},
               idle: {w_second.idle, w_first.idle},
               gen:  w_second.lock ? w_second.gen : w_first.gen,
               lock: {w_second.lock & ~w_split, w_first.lock & ~w_split},
               sym1: w_second.sym,
               sym0: w_first.sym};
    // A word offered while the FIFO is full is lost; the next one written
    // carries the overflow flag.
    w_push = w_wr & ~rx_rst;
    if (w_push) begin
      w_d.pend_ovf = ~w_ready;
    end
  end

  `COMMON_DFF_ARST_VAL(w_q, w_d, ser_rx_clk_i, rx_rst, '0)

  `COMMON_ASSERT_NEVER(a_one_removal, ser_rx_clk_i, rx_rst, w_rm0 & w_rm1)
  `COMMON_ASSERT_NEVER(a_rem_after_com, ser_rx_clk_i, rx_rst, w_rm0 & w_q.pend_rem)
  // The aligner tags both symbols of a word with one lock, generation and
  // idle flag, except an EIOS tail word (sym0 the locked last IDL, sym1
  // idle), and never locks an idle symbol.
  `COMMON_ASSERT_NEVER(a_word_one_lock, ser_rx_clk_i, rx_rst,
                       ser_rx_valid_i & ((ser_rx_i.sym1.gen != ser_rx_i.sym0.gen)
                                       | (((ser_rx_i.sym1.lock != ser_rx_i.sym0.lock)
                                           | (ser_rx_i.sym1.idle != ser_rx_i.sym0.idle))
                                          & ~(ser_rx_i.sym0.lock & ~ser_rx_i.sym0.idle
                                              & ~ser_rx_i.sym1.lock & ser_rx_i.sym1.idle))))
  `COMMON_ASSERT_NEVER(a_idle_unlocked, ser_rx_clk_i, rx_rst,
                       ser_rx_valid_i & ser_rx_i.sym0.idle & ser_rx_i.sym0.lock)
  // Lock start: a FIFO word never holds an unlocked symbol before a locked one.
  `COMMON_ASSERT_NEVER(a_no_unlocked_then_locked, ser_rx_clk_i, rx_rst,
                       w_push & ~w_word.lock[0] & w_word.lock[1])

  // ------------------------------------------------------------------ FIFO

  pcie_pcs_pkg::t_eb_word              head;        // oldest visible word
  pcie_pcs_pkg::t_eb_word              head_next;   // the word after the head
  logic [pcie_pcs_pkg::EB_ADDR_W:0]    vis;         // visible words
  logic                                r_head_valid;
  logic                                r_pop;

  common_cdc_fifo #(
    .WIDTH ($bits(pcie_pcs_pkg::t_eb_word)),
    .ADDR_W(pcie_pcs_pkg::EB_ADDR_W)
  ) u_fifo (
    .src_clk_i      (ser_rx_clk_i),
    .src_rst_i      (rx_rst),
    .src_valid_i    (w_push),
    .src_ready_o    (w_ready),
    .src_data_i     (w_word),
    .dst_clk_i      (clk_i),
    .dst_rst_i      (rst_i),
    .dst_valid_o    (r_head_valid),
    .dst_ready_i    (r_pop),
    .dst_data_o     (head),
    .dst_fill_o     (vis),
    .dst_next_data_o(head_next)
  );

  // ------------------------------------------------------------- read side

  pcie_pcs_pkg::t_eb_rd                r_q;
  pcie_pcs_pkg::t_eb_rd                r_d;
  logic [pcie_pcs_pkg::HOLDOFF_W-1:0]  r_holdoff_dec;
  logic                                fill_high_q;
  logic                                fill_high_d;
  logic [pcie_pcs_pkg::EB_FILL_W-1:0]  fill;
  logic                                r_started;
  logic                                r_low;
  logic                                r_recentre;
  logic                                r_udf;
  logic                                r_go;
  pcie_pcs_pkg::t_eb_sym               head0;
  pcie_pcs_pkg::t_eb_sym               head1;
  pcie_pcs_pkg::t_eb_sym               next0;
  pcie_pcs_pkg::t_eb_sym               c0;      // the next three symbols in order
  pcie_pcs_pkg::t_eb_sym               c1;
  pcie_pcs_pkg::t_eb_sym               c2;
  logic                                v1;
  logic                                v2;
  pcie_pcs_pkg::t_eb_sym               o0;      // the two symbols sent
  pcie_pcs_pkg::t_eb_sym               o1;
  logic                                r_add0;  // copy of c1 sent after a COM in o0
  logic                                r_add1;  // copy of c2 owed after a COM in o1
  logic                                r_rem1;  // c2's removal reported on o1's COM
  logic                                r_two;   // two symbols consumed
  logic                                r_flush; // drop the leftover: lock starts
  logic                                r_left_live;

  assign fill = {vis, 1'b0} + pcie_pcs_pkg::EB_FILL_W'(r_q.left_valid)
              + pcie_pcs_pkg::EB_FILL_W'(r_q.dup);

  always_comb begin
    r_d   = r_q;
    head0 = '{ovf: head.ovf, rem: head.rem[0], idle: head.idle[0], gen: head.gen,
              lock: head.lock[0], sym: head.sym0};
    head1 = '{ovf: 1'b0, rem: head.rem[1], idle: head.idle[1], gen: head.gen,
              lock: head.lock[1], sym: head.sym1};
    next0 = '{ovf: head_next.ovf, rem: head_next.rem[0], idle: head_next.idle[0],
              gen: head_next.gen, lock: head_next.lock[0], sym: head_next.sym0};
    // Lock start: a locked head word that follows an unlocked leftover
    // symbol is sent from its first symbol. The leftover is dropped, so the
    // first locked word is not paired with it.
    r_flush     = r_q.left_valid & ~r_q.dup & ~r_q.rem_ack & r_head_valid & head0.lock
                & ~r_q.left.lock;
    r_left_live = r_q.left_valid & ~r_flush;
    if (r_left_live) begin
      c0 = r_q.left;
      c1 = head0;
      c2 = head1;
      v1 = r_head_valid;
      v2 = r_head_valid;
    end else begin
      c0 = head0;
      c1 = head1;
      c2 = next0;
      v1 = r_head_valid;
      v2 = vis >= (pcie_pcs_pkg::EB_ADDR_W + 1)'(2);
    end
    if (r_q.rem_ack) begin
      c0.rem = 1'b0;
    end

    r_started     = r_q.started | (fill >= pcie_pcs_pkg::EB_FILL_W'(pcie_pcs_pkg::EB_NOMINAL));
    r_low         = fill <= pcie_pcs_pkg::EB_FILL_W'(pcie_pcs_pkg::EB_LOW);
    fill_high_d   = fill >= pcie_pcs_pkg::EB_FILL_W'(pcie_pcs_pkg::EB_HIGH);
    r_holdoff_dec = (r_q.holdoff != '0) ? r_q.holdoff - 1'b1 : '0;
    // Unlocked recentring: skip a cycle while the next symbol to send is
    // unlocked and the fill is low, at most once per EB_HOLDOFF cycles (a
    // locked leftover is never held back behind an unlocked head).
    r_recentre    = r_started & r_head_valid & ~c0.lock & r_low & (r_holdoff_dec == '0);
    r_udf         = r_started & ~r_recentre & (fill < pcie_pcs_pkg::EB_FILL_W'(2));
    r_go          = r_started & ~r_recentre & ~r_udf;
    r_d.started   = r_started;
    if (r_started) begin
      r_d.holdoff = r_recentre ? pcie_pcs_pkg::HOLDOFF_W'(pcie_pcs_pkg::EB_HOLDOFF)
                               : r_holdoff_dec;
    end

    // The two symbols sent this cycle.
    r_add0 = 1'b0;
    r_add1 = 1'b0;
    r_rem1 = 1'b0;
    o0     = c0;
    o1     = c1;
    r_two  = 1'b1;
    if (r_q.dup) begin
      // The copy owed from the last cycle, then the symbol itself.
      o0     = '{ovf: 1'b0, rem: 1'b0, idle: c0.idle, gen: c0.gen, lock: c0.lock, sym: c0.sym};
      o1     = c0;
      r_two  = 1'b0;
    end else begin
      if ((c0.lock && pcie_pcs_pkg::is_com(c0.sym)) && v1 && !c1.rem
          && pcie_pcs_pkg::is_skp(c1.sym) && r_low) begin
        // Add a copy of the SKP right after the COM.
        r_add0 = 1'b1;
        o1     = '{ovf: 1'b0, rem: 1'b0, idle: c1.idle, gen: c1.gen, lock: c1.lock, sym: c1.sym};
        r_two  = 1'b0;
      end else if ((c1.lock && pcie_pcs_pkg::is_com(c1.sym)) && v2) begin
        // A COM in the second slot: look at the symbol after it.
        if (c2.rem) begin
          r_rem1 = 1'b1;
        end else if (pcie_pcs_pkg::is_skp(c2.sym) && r_low) begin
          r_add1 = 1'b1;
        end
      end
    end

    // Consume one or two symbols: the leftover first, then the head word.
    r_pop = 1'b0;
    if (r_go) begin
      r_d.dup     = r_add1;
      r_d.rem_ack = r_rem1;
      r_d.left    = head1;
      if (r_left_live) begin
        r_pop          = r_two;
        r_d.left_valid = r_two;
      end else begin
        r_pop          = 1'b1;
        r_d.left_valid = ~r_two;
      end
    end

    rx_valid_o  = r_go | r_udf;
    rx_o        = '{sym1: '{idle: o1.idle, lock: o1.lock, gen: o1.gen, code: o1.sym},
                    sym0: '{idle: o0.idle, lock: o0.lock, gen: o0.gen, code: o0.sym}};
    if (!r_go) begin
      rx_o = '0;
    end
    rx_status_o = '{skp_add: r_go & (r_add0 | r_add1),
                    skp_rem: r_go & (o0.rem | o1.rem | r_rem1),
                    ovf:     r_go & (o0.ovf | o1.ovf),
                    udf:     r_udf};
    fill_o      = fill;
  end

  `COMMON_DFF_RST(r_q, r_d, clk_i, rst_i)
  `COMMON_DFF_RST(fill_high_q, fill_high_d, clk_i, rst_i)

  // Crossing 3 of the pcs HAS: fill high, a level from a clk_i flop.
  `COMMON_METAFLOP(u_sync_fill_high, fill_high_rx, fill_high_q, ser_rx_clk_i, rx_rst)

  // The word after the head is only looked at for its first symbol, and
  // only the symbol and removed flag of c2; the write side never sets a
  // symbol's own overflow flag (the word's flag is w_q.pend_ovf).
  `COMMON_UNUSED_BITS(unused_bits, {head_next.sym1, head_next.rem[1], head_next.idle[1],
                                    head_next.lock[1], c2.ovf, c2.gen, c2.idle,
                                    c2.lock, w_first.ovf, w_second.ovf})

  `COMMON_ASSERT_NEVER(a_pop_empty, clk_i, rst_i, r_pop & ~r_head_valid)
  `COMMON_ASSERT_NEVER(a_dup_needs_symbol, clk_i, rst_i, r_q.dup & ~r_q.left_valid & ~r_head_valid)
  `COMMON_ASSERT_NEVER(a_send_needs_symbols, clk_i, rst_i, r_go & ~r_q.dup & ~r_add0 & ~v1)
  `COMMON_ASSERT_NEVER(a_add_only_low, clk_i, rst_i, rx_status_o.skp_add & ~r_low)
  `COMMON_ASSERT_NEVER(a_owed_copy_is_skp, clk_i, rst_i,
                       r_q.dup & r_go & ~pcie_pcs_pkg::is_skp(c0.sym))

  // The thresholds of RECEIVE.md (Elastic buffer) as that page writes them,
  // independent of the package constants that build them: fill high (which
  // the write side removes on) exactly at a visible fill of 9 or more; the
  // start exactly at 8; an addition only at 7 or less, and at 7 or less a
  // possible addition is made, after a COM in either slot.
  logic add_due0;
  logic add_due1;

  assign add_due0 = r_go & ~r_q.dup & (fill <= pcie_pcs_pkg::EB_FILL_W'(7)) & c0.lock & pcie_pcs_pkg::is_com(c0.sym)
                  & v1 & ~c1.rem & pcie_pcs_pkg::is_skp(c1.sym);
  assign add_due1 = r_go & ~r_q.dup & (fill <= pcie_pcs_pkg::EB_FILL_W'(7)) & c1.lock & pcie_pcs_pkg::is_com(c1.sym)
                  & v2 & ~c2.rem & pcie_pcs_pkg::is_skp(c2.sym);

  `COMMON_ASSERT_NEVER(a_fill_high_at_9, clk_i, rst_i, fill_high_d != (fill >= pcie_pcs_pkg::EB_FILL_W'(9)))
  `COMMON_ASSERT_NEVER(a_start_at_8, clk_i, rst_i, ~r_q.started & (r_started != (fill >= pcie_pcs_pkg::EB_FILL_W'(8))))
  `COMMON_ASSERT_NEVER(a_add_at_7_or_less, clk_i, rst_i, rx_status_o.skp_add & (fill > pcie_pcs_pkg::EB_FILL_W'(7)))
  `COMMON_ASSERT_NEVER(a_add_when_due, clk_i, rst_i, (add_due0 & ~r_add0) | (add_due1 & ~r_add1))

endmodule

`default_nettype wire
