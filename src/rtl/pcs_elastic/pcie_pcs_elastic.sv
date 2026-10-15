// SKP elastic buffer from ser_rx_clk_i to clk_i: SKP removal and packing on
// the write side, a 16-word common_cdc_fifo, SKP addition, start and
// recentring on the read side (wiki/src/rtl/pcs_elastic/MAS.md).
`default_nettype none

`include "common_macros.svh"

module pcie_pcs_elastic (
  // clk_i side (read).
  input  logic        clk_i,
  input  logic        rst_i,            // cdc-domain: clk_i
  // ser_rx_clk_i side (write).
  input  logic        ser_rx_clk_i,
  input  logic        ser_rx_valid_i,   // cdc-domain: ser_rx_clk_i
  input  logic [19:0] ser_rx_sym_i,     // cdc-domain: ser_rx_clk_i
  input  logic        ser_rx_lock_i,    // cdc-domain: ser_rx_clk_i
  input  logic        ser_rx_gen_i,     // cdc-domain: ser_rx_clk_i
  // Read side outputs, combinational from clk_i registers.
  output logic        rx_valid_o,
  output logic [19:0] rx_sym_o,
  output logic [1:0]  rx_lock_o,
  output logic [1:0]  rx_gen_o,
  output logic        rx_skp_add_o,
  output logic        rx_skp_rem_o,
  output logic        rx_ovf_o,
  output logic        rx_udf_o,
  output logic [5:0]  fill_o,
  // The RX-domain reset (crossing 2 of the pcs HAS), for the rest of the
  // ser_rx_clk_i domain.
  output logic        ser_rx_rst_o
);

  // Buffer sizes and fill levels, in symbols unless noted
  // (wiki/src/arch/pcs/RECEIVE.md, Elastic buffer).
  localparam int unsigned EB_ADDR_W  = 4;   // 16 words of two symbols
  localparam int unsigned EB_NOMINAL = 8;   // start level and nominal fill
  localparam int unsigned EB_HIGH    = 9;   // fill high at or above this
  localparam int unsigned EB_LOW     = 7;   // add a SKP at or below this
  localparam int unsigned EB_HOLDOFF = 8;   // recentring: at most once per 8 cycles
  localparam int unsigned FILL_W     = 6;   // visible fill, 0 to 34 symbols
  localparam int unsigned HOLDOFF_W  = 4;

  // ----------------------------------------------------- RX-domain reset

  // Asserts with rst_i, with no clock edge; releases on the second
  // ser_rx_clk_i edge after rst_i falls. Every write-side flop takes it
  // asynchronously.
  logic rx_rst;   // the RX-domain reset

  `COMMON_RESET_SYNC(u_rx_rst, rx_rst, rst_i, ser_rx_clk_i)

  assign ser_rx_rst_o = rx_rst;

  // ------------------------------------------------------------ write side

  pcie_pcs_elastic_pkg::t_sym10                         w_sym0;
  pcie_pcs_elastic_pkg::t_sym10                         w_sym1;
  logic                                                 fill_high_rx;  // synchronized fill high
  logic [HOLDOFF_W-1:0]           w_holdoff_q;
  logic [HOLDOFF_W-1:0]           w_holdoff_d;
  logic                                                 w_prev_com_q;  // last symbol taken was a COM
  logic                                                 w_prev_com_d;
  logic                                                 w_pend_rem_q;  // next kept symbol follows a removed SKP
  logic                                                 w_pend_rem_d;
  logic                                                 w_pend_ovf_q;  // next written word follows a lost one
  logic                                                 w_pend_ovf_d;
  logic                                                 w_hold_valid_q;  // packing register holds a symbol
  logic                                                 w_hold_valid_d;
  pcie_pcs_elastic_pkg::t_eb_sym                        w_hold_q;
  pcie_pcs_elastic_pkg::t_eb_sym                        w_hold_d;
  pcie_pcs_elastic_pkg::t_eb_sym                        w_new0;
  pcie_pcs_elastic_pkg::t_eb_sym                        w_new1;
  logic                                                 w_drop;   // unlocked word dropped (recentring)
  logic                                                 w_take;   // word taken in
  logic                                                 w_rm0;    // sym0 is a removed SKP
  logic                                                 w_rm1;    // sym1 is a removed SKP
  logic                                                 w_wr;     // a packed word is ready
  logic                                                 w_push;
  logic                                                 w_ready;
  pcie_pcs_elastic_pkg::t_eb_sym                        w_first;  // the packed word's symbols
  pcie_pcs_elastic_pkg::t_eb_sym                        w_second;
  pcie_pcs_elastic_pkg::t_eb_word                       w_word;

  assign w_sym0 = ser_rx_sym_i[9:0];
  assign w_sym1 = ser_rx_sym_i[19:10];

  always_comb begin
    w_drop = ser_rx_valid_i & ~ser_rx_lock_i & fill_high_rx & (w_holdoff_q == '0);
    if (w_drop) begin
      w_holdoff_d = HOLDOFF_W'(EB_HOLDOFF - 1);
    end else if (w_holdoff_q != '0) begin
      w_holdoff_d = w_holdoff_q - 1'b1;
    end else begin
      w_holdoff_d = '0;
    end
    w_take = ser_rx_valid_i & ~w_drop;
    // Remove the first SKP after a locked COM while fill high is seen; at
    // most one of the two symbols can be such a SKP.
    w_rm0  = w_take & ser_rx_lock_i & fill_high_rx & w_prev_com_q
           & pcie_pcs_elastic_pkg::is_skp(w_sym0);
    w_rm1  = w_take & ser_rx_lock_i & fill_high_rx & pcie_pcs_elastic_pkg::is_com(w_sym0)
           & pcie_pcs_elastic_pkg::is_skp(w_sym1);
    w_new0 = '{ovf: 1'b0, rem: w_pend_rem_q, gen: ser_rx_gen_i, lock: ser_rx_lock_i, sym: w_sym0};
    w_new1 = '{ovf: 1'b0, rem: w_rm0, gen: ser_rx_gen_i, lock: ser_rx_lock_i, sym: w_sym1};

    w_prev_com_d   = w_prev_com_q;
    w_pend_rem_d   = w_pend_rem_q;
    w_hold_valid_d = w_hold_valid_q;
    w_hold_d       = w_hold_q;
    w_wr           = 1'b0;
    w_first        = w_hold_q;
    w_second       = w_new0;
    if (w_take) begin
      w_prev_com_d = pcie_pcs_elastic_pkg::is_com(w_sym1);
      w_pend_rem_d = w_rm1;
      if (!w_hold_valid_q) begin
        if (w_rm0) begin
          w_hold_valid_d = 1'b1;
          w_hold_d       = w_new1;
        end else if (w_rm1) begin
          w_hold_valid_d = 1'b1;
          w_hold_d       = w_new0;
        end else begin
          w_wr     = 1'b1;
          w_first  = w_new0;
          w_second = w_new1;
        end
      end else begin
        w_wr = 1'b1;
        if (w_rm0) begin
          w_second       = w_new1;
          w_hold_valid_d = 1'b0;
        end else if (w_rm1) begin
          w_second       = w_new0;
          w_hold_valid_d = 1'b0;
        end else begin
          w_second       = w_new0;
          w_hold_d       = w_new1;
        end
      end
    end
    // A word whose symbols differ in lock or generation is tagged unlocked
    // with the newer symbol's generation.
    w_word = '{ovf:  w_pend_ovf_q,
               rem:  {w_second.rem, w_first.rem},
               gen:  w_second.gen,
               lock: w_first.lock & w_second.lock & (w_first.gen == w_second.gen),
               sym1: w_second.sym,
               sym0: w_first.sym};
    // A word offered while the FIFO is full is lost; the next one written
    // carries the overflow flag.
    w_push       = w_wr & ~rx_rst;
    w_pend_ovf_d = w_pend_ovf_q;
    if (w_push) begin
      w_pend_ovf_d = ~w_ready;
    end
  end

  `COMMON_DFF_ARST_VAL(w_holdoff_q, w_holdoff_d, ser_rx_clk_i, rx_rst, '0)
  `COMMON_DFF_ARST_VAL(w_prev_com_q, w_prev_com_d, ser_rx_clk_i, rx_rst, '0)
  `COMMON_DFF_ARST_VAL(w_pend_rem_q, w_pend_rem_d, ser_rx_clk_i, rx_rst, '0)
  `COMMON_DFF_ARST_VAL(w_pend_ovf_q, w_pend_ovf_d, ser_rx_clk_i, rx_rst, '0)
  `COMMON_DFF_ARST_VAL(w_hold_valid_q, w_hold_valid_d, ser_rx_clk_i, rx_rst, '0)
  `COMMON_DFF_ARST_VAL(w_hold_q, w_hold_d, ser_rx_clk_i, rx_rst, '0)

  `COMMON_ASSERT_NEVER(a_one_removal, ser_rx_clk_i, rx_rst, w_rm0 & w_rm1)
  `COMMON_ASSERT_NEVER(a_rem_after_com, ser_rx_clk_i, rx_rst, w_rm0 & w_pend_rem_q)

  // ------------------------------------------------------------------ FIFO

  pcie_pcs_elastic_pkg::t_eb_word                       head;   // oldest visible word
  pcie_pcs_elastic_pkg::t_eb_word                       head_next;   // the word after the head
  logic [EB_ADDR_W:0]             vis;    // visible words
  logic                                                 r_head_valid;
  logic                                                 r_pop;

  common_cdc_fifo #(
    .WIDTH ($bits(pcie_pcs_elastic_pkg::t_eb_word)),
    .ADDR_W(EB_ADDR_W)
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

  logic                                                 r_started_q;
  logic                                                 r_started;
  logic                                                 r_left_valid_q;  // leftover register holds a symbol
  logic                                                 r_left_valid_d;
  pcie_pcs_elastic_pkg::t_eb_sym                        r_left_q;
  pcie_pcs_elastic_pkg::t_eb_sym                        r_left_d;
  logic                                                 r_dup_q;   // send the next symbol twice
  logic                                                 r_dup_d;
  logic                                                 r_rem_ack_q;  // next symbol's removed flag already reported
  logic                                                 r_rem_ack_d;
  logic [HOLDOFF_W-1:0]           r_holdoff_q;
  logic [HOLDOFF_W-1:0]           r_holdoff_d;
  logic [HOLDOFF_W-1:0]           r_holdoff_dec;
  logic                                                 fill_high_q;
  logic                                                 fill_high_d;
  logic [FILL_W-1:0]              fill;
  logic                                                 r_low;
  logic                                                 r_recentre;
  logic                                                 r_udf;
  logic                                                 r_go;
  pcie_pcs_elastic_pkg::t_eb_sym                        head0;
  pcie_pcs_elastic_pkg::t_eb_sym                        head1;
  pcie_pcs_elastic_pkg::t_eb_sym                        next0;
  pcie_pcs_elastic_pkg::t_eb_sym                        c0;   // the next three symbols in order
  pcie_pcs_elastic_pkg::t_eb_sym                        c1;
  pcie_pcs_elastic_pkg::t_eb_sym                        c2;
  logic                                                 v1;
  logic                                                 v2;
  pcie_pcs_elastic_pkg::t_eb_sym                        o0;   // the two symbols sent
  pcie_pcs_elastic_pkg::t_eb_sym                        o1;
  logic                                                 r_add0;  // copy of c1 sent after a COM in o0
  logic                                                 r_add1;  // copy of c2 owed after a COM in o1
  logic                                                 r_rem1;  // c2's removal reported on o1's COM
  logic                                                 r_two;   // two symbols consumed

  assign fill = {vis, 1'b0} + FILL_W'(r_left_valid_q)
              + FILL_W'(r_dup_q);

  always_comb begin
    head0 = '{ovf: head.ovf, rem: head.rem[0], gen: head.gen, lock: head.lock, sym: head.sym0};
    head1 = '{ovf: 1'b0, rem: head.rem[1], gen: head.gen, lock: head.lock, sym: head.sym1};
    next0 = '{ovf: head_next.ovf, rem: head_next.rem[0], gen: head_next.gen, lock: head_next.lock, sym: head_next.sym0};
    if (r_left_valid_q) begin
      c0 = r_left_q;
      c1 = head0;
      c2 = head1;
      v1 = r_head_valid;
      v2 = r_head_valid;
    end else begin
      c0 = head0;
      c1 = head1;
      c2 = next0;
      v1 = r_head_valid;
      v2 = vis >= (EB_ADDR_W + 1)'(2);
    end
    if (r_rem_ack_q) begin
      c0.rem = 1'b0;
    end

    r_started     = r_started_q | (fill >= FILL_W'(EB_NOMINAL));
    r_low         = fill <= FILL_W'(EB_LOW);
    fill_high_d   = fill >= FILL_W'(EB_HIGH);
    r_holdoff_dec = (r_holdoff_q != '0) ? r_holdoff_q - 1'b1 : '0;
    // Unlocked recentring: skip a cycle while the head word is unlocked and
    // the fill is low, at most once per EB_HOLDOFF cycles.
    r_recentre    = r_started & r_head_valid & ~head.lock & r_low & (r_holdoff_dec == '0);
    r_udf         = r_started & ~r_recentre & (fill < FILL_W'(2));
    r_go          = r_started & ~r_recentre & ~r_udf;
    r_holdoff_d   = r_holdoff_q;
    if (r_started) begin
      r_holdoff_d = r_recentre
                  ? HOLDOFF_W'(EB_HOLDOFF)
                  : r_holdoff_dec;
    end

    // The two symbols sent this cycle.
    r_add0 = 1'b0;
    r_add1 = 1'b0;
    r_rem1 = 1'b0;
    o0     = c0;
    o1     = c1;
    r_two  = 1'b1;
    if (r_dup_q) begin
      // The copy owed from the last cycle, then the symbol itself.
      o0     = '{ovf: 1'b0, rem: 1'b0, gen: c0.gen, lock: c0.lock, sym: c0.sym};
      o1     = c0;
      r_two  = 1'b0;
    end else begin
      if ((c0.lock && pcie_pcs_elastic_pkg::is_com(c0.sym)) && v1 && !c1.rem
          && pcie_pcs_elastic_pkg::is_skp(c1.sym) && r_low) begin
        // Add a copy of the SKP right after the COM.
        r_add0 = 1'b1;
        o1     = '{ovf: 1'b0, rem: 1'b0, gen: c1.gen, lock: c1.lock, sym: c1.sym};
        r_two  = 1'b0;
      end else if ((c1.lock && pcie_pcs_elastic_pkg::is_com(c1.sym)) && v2) begin
        // A COM in the second slot: look at the symbol after it.
        if (c2.rem) begin
          r_rem1 = 1'b1;
        end else if (pcie_pcs_elastic_pkg::is_skp(c2.sym) && r_low) begin
          r_add1 = 1'b1;
        end
      end
    end

    // Consume one or two symbols: the leftover first, then the head word.
    r_pop          = 1'b0;
    r_left_valid_d = r_left_valid_q;
    r_left_d       = r_left_q;
    r_dup_d        = r_dup_q;
    r_rem_ack_d    = r_rem_ack_q;
    if (r_go) begin
      r_dup_d     = r_add1;
      r_rem_ack_d = r_rem1;
      if (r_left_valid_q) begin
        r_pop          = r_two;
        r_left_valid_d = r_two;
        r_left_d       = head1;
      end else begin
        r_pop          = 1'b1;
        r_left_valid_d = ~r_two;
        r_left_d       = head1;
      end
    end

    rx_valid_o   = r_go | r_udf;
    rx_udf_o     = r_udf;
    rx_sym_o     = r_go ? {o1.sym, o0.sym} : '0;
    rx_lock_o    = r_go ? {o1.lock, o0.lock} : '0;
    rx_gen_o     = r_go ? {o1.gen, o0.gen} : '0;
    rx_skp_add_o = r_go & (r_add0 | r_add1);
    rx_skp_rem_o = r_go & (o0.rem | o1.rem | r_rem1);
    rx_ovf_o     = r_go & (o0.ovf | o1.ovf);
    fill_o       = fill;
  end

  `COMMON_DFF_RST(r_started_q, r_started, clk_i, rst_i)
  `COMMON_DFF_RST(r_left_valid_q, r_left_valid_d, clk_i, rst_i)
  `COMMON_DFF_RST(r_left_q, r_left_d, clk_i, rst_i)
  `COMMON_DFF_RST(r_dup_q, r_dup_d, clk_i, rst_i)
  `COMMON_DFF_RST(r_rem_ack_q, r_rem_ack_d, clk_i, rst_i)
  `COMMON_DFF_RST(r_holdoff_q, r_holdoff_d, clk_i, rst_i)
  `COMMON_DFF_RST(fill_high_q, fill_high_d, clk_i, rst_i)

  // Crossing 3 of the pcs HAS: fill high, a level from a clk_i flop.
  `COMMON_METAFLOP(u_sync_fill_high, fill_high_rx, fill_high_q, ser_rx_clk_i, rx_rst)

  // The word after the head is only looked at for its first symbol, and
  // only the symbol and removed flag of c2; the write side never sets a
  // symbol's own overflow flag (the word's flag is w_pend_ovf_q).
  `COMMON_UNUSED_BITS(unused_bits, {head_next.sym1, head_next.rem[1], c2.ovf, c2.gen,
                                    c2.lock, w_first.ovf, w_second.ovf})

  `COMMON_ASSERT_NEVER(a_pop_empty, clk_i, rst_i, r_pop & ~r_head_valid)
  `COMMON_ASSERT_NEVER(a_dup_needs_symbol, clk_i, rst_i, r_dup_q & ~r_left_valid_q & ~r_head_valid)
  `COMMON_ASSERT_NEVER(a_send_needs_symbols, clk_i, rst_i, r_go & ~r_dup_q & ~r_add0 & ~v1)
  `COMMON_ASSERT_NEVER(a_add_only_low, clk_i, rst_i, rx_skp_add_o & ~r_low)
  `COMMON_ASSERT_NEVER(a_owed_copy_is_skp, clk_i, rst_i,
                       r_dup_q & r_go & ~pcie_pcs_elastic_pkg::is_skp(c0.sym))

endmodule

`default_nettype wire
