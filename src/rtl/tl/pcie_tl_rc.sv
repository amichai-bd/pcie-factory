// rc stream of pcie_tl (wiki/src/rtl/tl/MAS.md#rc-stream): the packets the
// tracker queues, held until the application takes them, each offered
// once every posted request received before it has left tl
// (wiki/src/arch/core/APP.md#requester-completions,
// wiki/src/arch/tl/ORDERING.md#ordering).
`default_nettype none

`include "common_macros.svh"

module pcie_tl_rc (
  input  logic                    clk_i,
  input  logic                    rst_i,
  // From the tracker
  input  logic                    desc_push_i,
  input  pcie_tl_pkg::t_rc_desc   desc_i,
  input  logic                    data_push_i,
  input  logic [31:0]             data_i,
  // The rc line (receive queues): its epoch for a packet queued now, and
  // the oldest posted request held
  input  pcie_tl_pkg::t_ep        rc_ep_i,
  input  logic                    p_none_i,
  input  logic                    p_known_i,
  input  pcie_tl_pkg::t_ep        p_head_ep_i,
  // rc stream (APP)
  output logic                    rc_valid_o,
  input  logic                    rc_ready_i,
  output logic                    rc_sop_o,
  output logic                    rc_eop_o,
  output logic [31:0]             rc_data_o,
  output logic [2:0]              rc_tag_o,
  output logic [1:0]              rc_status_o,
  output logic                    rc_done_o,
  // A read's rc_done beat taken: its tag is free (to the tracker)
  output logic                    free_o,
  output logic [2:0]              free_tag_o
);

  pcie_tl_pkg::t_rc_desc desc_in;
  pcie_tl_pkg::t_rc_desc hd;
  logic                  hd_valid;
  logic                  hd_pop;
  logic                  hq_empty;
  logic                  hq_full;
  logic [pcie_tl_pkg::RC_HQ_AW:0] hq_used;
  logic                  dd_valid;
  logic [31:0]           dd;
  logic                  dd_pop;
  logic                  dq_empty;
  logic                  dq_full;
  logic [pcie_tl_pkg::RC_DQ_AW:0] dq_used;

  // A packet takes the rc line's epoch when it is queued.
  always_comb begin
    desc_in    = desc_i;
    desc_in.ep = rc_ep_i;
  end

  pcie_tl_fifo #(
    .WIDTH  ($bits(pcie_tl_pkg::t_rc_desc)),
    .ADDR_W (pcie_tl_pkg::RC_HQ_AW)
  ) u_hq (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (desc_push_i),
    .push_data_i  (desc_in),
    .pop_i        (hd_pop),
    .head_valid_o (hd_valid),
    .head_o       (hd),
    .empty_o      (hq_empty),
    .full_o       (hq_full),
    .ram_used_o   (hq_used)
  );

  pcie_tl_fifo #(
    .WIDTH  (32),
    .ADDR_W (pcie_tl_pkg::RC_DQ_AW)
  ) u_dq (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (data_push_i),
    .push_data_i  (data_i),
    .pop_i        (dd_pop),
    .head_valid_o (dd_valid),
    .head_o       (dd),
    .empty_o      (dq_empty),
    .full_o       (dq_full),
    .ram_used_o   (dq_used)
  );

  // The head packet may go once every posted request received before it
  // has left; once it may, it stays free to go (APP: an offer stays up).
  pcie_tl_pkg::t_rc_state st_q, st_d;
  logic                   ok;
  logic                   is_st;
  logic                   take;

  assign ok     = st_q.ok || p_none_i
               || (p_known_i && pcie_tl_pkg::ep_after(p_head_ep_i, hd.ep));
  assign is_st  = hd.dlen == 6'd0;
  assign rc_valid_o  = hd_valid && ok && (is_st || dd_valid);
  assign rc_sop_o    = st_q.beat == 6'd0;
  assign rc_eop_o    = is_st || st_q.beat == hd.dlen - 6'd1;
  assign rc_data_o   = is_st ? 32'd0 : dd;
  assign rc_tag_o    = hd.tag;
  assign rc_status_o = hd.status;
  assign rc_done_o   = hd.done && rc_eop_o;

  assign take   = rc_valid_o && rc_ready_i;
  assign hd_pop = take && rc_eop_o;
  assign dd_pop = take && !is_st;

  always_comb begin
    st_d    = st_q;
    st_d.ok = hd_valid && ok && !hd_pop;
    if (take) begin
      st_d.beat = rc_eop_o ? 6'd0 : st_q.beat + 6'd1;
    end
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)

  assign free_o     = hd_pop && hd.done;
  assign free_tag_o = hd.tag;

  `COMMON_UNUSED_BITS(unused_rc, {hq_empty, hq_used, dq_empty, dq_used})

  // The tracker queues at most 3 packets and 32 DWs per tag (MAS, rc
  // stream), so neither queue fills.
  `COMMON_ASSERT_NEVER(a_rc_hq_overflow, clk_i, rst_i, desc_push_i && hq_full)
  `COMMON_ASSERT_NEVER(a_rc_dq_overflow, clk_i, rst_i, data_push_i && dq_full)
  `COMMON_ASSERT_NEVER(a_rc_dlen, clk_i, rst_i, desc_push_i && desc_i.dlen > 6'd32)

endmodule

`default_nettype wire
