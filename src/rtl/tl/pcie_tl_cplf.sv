// Completion former of pcie_tl (wiki/src/rtl/tl/MAS.md#completion-former):
// one per completion source. It takes a read's DWs in address order and
// splits them into completions on the 64-byte read completion boundary,
// the fewest the rules allow, with their Byte Count and Lower Address
// (wiki/src/arch/tl/COMPLETIONS.md#completer-completions), and takes
// completions without data formed outside. A completion's DWs, the bytes
// the request did not enable as 0, go to its data queue as they arrive;
// its descriptor follows with its last DW, so a queued descriptor always
// has all its data behind it.
`default_nettype none

`include "common_macros.svh"

module pcie_tl_cplf #(
  parameter int unsigned DATA_AW = 6,   // data queue: 2**DATA_AW DWs in RAM, at least CPL_MAX
  parameter int unsigned DESC_AW = 2    // descriptor queue: 2**DESC_AW in RAM
) (
  input  logic                    clk_i,
  input  logic                    rst_i,
  // A read's DWs, in address order, with the read's context
  input  logic                    beat_i,       // a DW is given (only while room_o)
  input  logic [31:0]             data_i,
  input  pcie_tl_pkg::t_cpl_ctx   ctx_i,
  // A completion without data (only while room_o and no completion is open)
  input  logic                    nd_i,
  input  pcie_tl_pkg::t_cpl_desc  nd_desc_i,    // its epoch field is ignored
  input  pcie_tl_pkg::t_ep        ep_i,         // the epoch of a completion queued now
  output logic                    room_o,       // a DW, or a completion without data, may be given
  output logic                    read_last_o,  // the next DW given is its read's last
  output logic [10:0]             k_o,          // the DW index of the next DW given
  output logic                    push_o,       // a completion is queued at this edge
  // Queues, to the transmit arbiter
  output logic                    desc_valid_o,
  output pcie_tl_pkg::t_cpl_desc  desc_o,
  input  logic                    desc_pop_i,
  output logic                    data_valid_o,
  output logic [31:0]             data_o,
  input  logic                    data_pop_i
);

  `COMMON_ASSERT_PARAM(g_data_aw_check, (1 << DATA_AW) >= 2 * pcie_tl_pkg::CPL_MAX,
                       "pcie_tl_cplf: the data queue must hold two longest completions")

  pcie_tl_pkg::t_cplf_state st_q, st_d;

  logic                   first;        // the next DW is its read's first
  logic [1:0]             lo;
  logic [10:0]            rest;         // DWs of the read from the next one
  logic [4:0]             s_lo;         // DW address bits 6:2 of the next DW
  logic [5:0]             n_start;      // DWs of a completion starting at the next DW
  logic [12:0]            total;
  logic [11:0]            bc_start;
  logic [6:0]             la_start;
  logic [12:0]            rb_after;     // rb after a completion starting now
  logic                   start;
  logic                   cpl_last;     // this DW ends its completion
  logic [3:0]             m_first;
  logic [3:0]             m_last;
  logic [31:0]            dmask;
  logic [31:0]            dpush;
  logic                   desc_push;
  pcie_tl_pkg::t_cpl_desc desc_in;
  logic                   desc_full;
  logic                   desc_empty;
  logic [DESC_AW:0]       desc_used;
  logic                   data_full;
  logic                   data_empty;
  logic [DATA_AW:0]       data_used;
  logic                   data_room;

  assign first    = st_q.k == 11'd0;
  assign lo       = common_pkg::be_lo(ctx_i.fbe);
  assign rest     = ctx_i.dw_len - st_q.k;
  assign s_lo     = ctx_i.alo + st_q.k[4:0];
  // The rest in one completion when it fits in 128 bytes; else up to the
  // highest 64-byte boundary within 128 bytes (COMPLETIONS.md, splitting).
  assign n_start  = (rest <= 11'(pcie_tl_pkg::CPL_MAX)) ? rest[5:0] : 6'd32 - {2'b00, s_lo[3:0]};
  assign total    = common_pkg::byte_total(ctx_i.dw_len, ctx_i.fbe, ctx_i.lbe);
  assign bc_start = ctx_i.fixed4 ? 12'd4 : (first ? total[11:0] : st_q.rb[11:0]);
  assign la_start = ctx_i.fixed4 ? 7'd0 : (first ? {ctx_i.alo, lo} : {s_lo, 2'b00});
  // The bytes from the next completion's first byte to the read's end:
  // the first starts at the first enabled byte, a later one on its DW.
  assign rb_after = (first ? total : st_q.rb) - {5'd0, n_start, 2'b00} + (first ? 13'(lo) : 13'd0);

  assign start       = beat_i && st_q.left == 6'd0;
  assign cpl_last    = beat_i && (start ? n_start == 6'd1 : st_q.left == 6'd1);
  assign read_last_o = st_q.k == ctx_i.dw_len - 11'd1;
  assign k_o         = st_q.k;

  // Bytes the request did not enable are sent as 0: the first DW by F, the
  // last of a longer read by L.
  assign m_first = first ? ctx_i.fbe : 4'hF;
  assign m_last  = (read_last_o && ctx_i.dw_len != 11'd1) ? ctx_i.lbe : 4'hF;
  assign dmask   = common_pkg::be_mask(m_first & m_last);
  assign dpush   = data_i & dmask;

  always_comb begin
    st_d = st_q;
    if (beat_i) begin
      st_d.k = read_last_o ? 11'd0 : st_q.k + 11'd1;
      if (start) begin
        st_d.left = n_start - 6'd1;
        st_d.n    = n_start;
        st_d.bc   = bc_start;
        st_d.la   = la_start;
        st_d.rb   = rb_after;
      end else begin
        st_d.left = st_q.left - 6'd1;
      end
    end
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)

  // A completion is queued with its last DW, or at once without data.
  assign desc_push = cpl_last || nd_i;
  assign push_o    = desc_push;

  always_comb begin
    desc_in = nd_desc_i;
    if (!nd_i) begin
      desc_in.status = pcie_tl_pkg::CS_SC;
      desc_in.locked = 1'b0;
      desc_in.bc     = start ? bc_start : st_q.bc;
      desc_in.la     = start ? la_start : st_q.la;
      desc_in.id     = ctx_i.id;
      desc_in.dlen   = start ? n_start : st_q.n;
    end
    desc_in.ep = ep_i;
  end

  // Room at a completion's first DW for the longest completion, so its
  // later DWs and its descriptor always fit.
  assign data_room = data_used <= (DATA_AW + 1)'((1 << DATA_AW) - pcie_tl_pkg::CPL_MAX);
  assign room_o    = st_q.left != 6'd0 || (data_room && !desc_full);

  common_sdp_ram_fifo #(
    .WIDTH  (32),
    .ADDR_W (DATA_AW)
  ) u_data (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (beat_i),
    .push_data_i  (dpush),
    .pop_i        (data_pop_i),
    .head_valid_o (data_valid_o),
    .head_o       (data_o),
    .empty_o      (data_empty),
    .full_o       (data_full),
    .ram_used_o   (data_used)
  );

  common_sdp_ram_fifo #(
    .WIDTH  ($bits(pcie_tl_pkg::t_cpl_desc)),
    .ADDR_W (DESC_AW)
  ) u_desc (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (desc_push),
    .push_data_i  (desc_in),
    .pop_i        (desc_pop_i),
    .head_valid_o (desc_valid_o),
    .head_o       (desc_o),
    .empty_o      (desc_empty),
    .full_o       (desc_full),
    .ram_used_o   (desc_used)
  );

  `COMMON_UNUSED_BITS(unused_q, {data_full, data_empty, desc_empty, desc_used, nd_desc_i.ep})

  `COMMON_ASSERT_NEVER(a_beat_no_room, clk_i, rst_i, beat_i && !room_o)
  `COMMON_ASSERT_NEVER(a_nd_open, clk_i, rst_i, nd_i && (beat_i || st_q.left != 6'd0 || !room_o))
  // A read ends with its own last DW, never inside a completion.
  `COMMON_ASSERT_NEVER(a_read_end_open, clk_i, rst_i, beat_i && read_last_o && !cpl_last)

endmodule

`default_nettype wire
