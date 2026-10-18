// Completer request stream of pcie_tl (wiki/src/rtl/tl/MAS.md#cq-stream):
// offers BAR0 writes from the posted queue and BAR0 reads from the cq line
// on cq (wiki/src/arch/core/APP.md#completer-requests), a read only with a
// free handle and once every posted request received before it has left,
// a write past a read that waits for a handle
// (wiki/src/arch/tl/ORDERING.md#ordering).
`default_nettype none

`include "common_macros.svh"

module pcie_tl_cq #(
  parameter int unsigned BAR0_ADDR_W = 12
) (
  input  logic                         clk_i,
  input  logic                         rst_i,
  // Posted queue head and data
  input  logic                         pq_valid_i,
  input  pcie_tl_pkg::t_pq_ent         pq_head_i,
  output logic                         pq_pop_o,
  input  logic                         pd_valid_i,
  input  logic [31:0]                  pd_head_i,
  output logic                         pd_pop_o,
  // cq line head
  input  logic                         rd_valid_i,
  input  pcie_tl_pkg::t_cq_ent         rd_head_i,
  input  logic                         rd_ok_i,
  output logic                         rd_pop_o,
  // Handles: a read taken, and a handle freed by the completion former (#291)
  output logic                         rd_take_o,
  output pcie_tl_pkg::t_hd_ctx         rd_ctx_o,
  input  logic                         hd_free_i,
  input  logic [2:0]                   hd_free_idx_i,
  // cq stream (APP)
  output logic                         cq_valid_o,
  input  logic                         cq_ready_i,
  output logic                         cq_sop_o,
  output logic                         cq_eop_o,
  output logic [31:0]                  cq_data_o,
  output logic                         cq_write_o,
  output logic [BAR0_ADDR_W-1:0]       cq_addr_o,
  output logic [10:0]                  cq_len_o,
  output logic [3:0]                   cq_first_be_o,
  output logic [3:0]                   cq_last_be_o,
  output logic [2:0]                   cq_handle_o
);

  pcie_tl_pkg::t_cq_state        st_q, st_d;
  logic [pcie_tl_pkg::HANDLES-1:0] busy_q, busy_d;

  logic       r_ok;            // a read may start: eligible, a handle free
  logic       w_ok;            // a BAR0 write is at the posted head
  logic       cur_rd;
  logic       cur_wr;
  logic [2:0] handle;
  logic       take;
  logic       last;
  logic [27:0] addr_full;

  assign r_ok   = rd_valid_i && rd_ok_i && busy_q != '1;
  assign w_ok   = pq_valid_i && pq_head_i.kind == pcie_tl_pkg::PK_CQ;
  // An eligible read is older than every waiting write; once a packet is
  // offered it stays until its eop is taken.
  assign cur_rd = st_q.busy ? !st_q.wr : r_ok;
  assign cur_wr = st_q.busy ? st_q.wr : (!r_ok && w_ok);
  assign handle = st_q.busy ? st_q.handle : pcie_tl_pkg::lowest_free(busy_q);

  assign cq_valid_o = cur_rd || (cur_wr && pd_valid_i);
  assign last       = cur_rd || st_q.beat == pq_head_i.dw_len - 6'd1;
  assign take       = cq_valid_o && cq_ready_i;

  assign cq_sop_o      = cur_rd || st_q.beat == 6'd0;
  assign cq_eop_o      = last;
  assign cq_data_o     = pd_head_i;
  assign cq_write_o    = cur_wr;
  assign addr_full     = cur_wr ? {pq_head_i.addr, 2'b00} : {rd_head_i.addr, 2'b00};
  assign cq_addr_o     = addr_full[BAR0_ADDR_W-1:0];
  assign cq_len_o      = cur_wr ? {5'd0, pq_head_i.dw_len} : rd_head_i.dw_len;
  assign cq_first_be_o = cur_wr ? pq_head_i.fbe : rd_head_i.fbe;
  assign cq_last_be_o  = cur_wr ? pq_head_i.lbe : rd_head_i.lbe;
  assign cq_handle_o   = handle;

  always_comb begin
    st_d = st_q;
    if (!st_q.busy) begin
      st_d.wr     = cur_wr;
      st_d.handle = handle;
    end
    st_d.busy = (cur_rd || cur_wr) && !(take && last);
    if (take) begin
      st_d.beat = last ? 6'd0 : st_q.beat + 6'd1;
    end
    busy_d = busy_q;
    if (hd_free_i) begin
      busy_d[hd_free_idx_i] = 1'b0;
    end
    if (take && cur_rd) begin
      busy_d[handle] = 1'b1;
    end
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)
  `COMMON_DFF_RST(busy_q, busy_d, clk_i, rst_i)

  assign pq_pop_o  = take && cur_wr && last;
  assign pd_pop_o  = take && cur_wr;
  assign rd_pop_o  = take && cur_rd;
  assign rd_take_o = take && cur_rd;

  always_comb begin
    rd_ctx_o        = '0;
    rd_ctx_o.handle = handle;
    rd_ctx_o.id     = rd_head_i.id;
    rd_ctx_o.addr   = rd_head_i.addr[9:0];
    rd_ctx_o.dw_len = rd_head_i.dw_len;
    rd_ctx_o.fbe    = rd_head_i.fbe;
    rd_ctx_o.lbe    = rd_head_i.lbe;
  end

  `COMMON_UNUSED_BITS(unused_heads, {addr_full, pq_head_i.rel_d, pq_head_i.ep, rd_head_i.ep})

  // A handle is freed only while in use, and never the one taken now.
  `COMMON_ASSERT_NEVER(a_free_idle_handle, clk_i, rst_i, hd_free_i && !busy_q[hd_free_idx_i])
  // A read starts only with a free handle.
  `COMMON_ASSERT_NEVER(a_read_without_handle, clk_i, rst_i, take && cur_rd && busy_q[handle])
  // The write being offered stays at the posted head until its eop.
  `COMMON_ASSERT_NEVER(a_write_head_lost, clk_i, rst_i,
                       st_q.busy && st_q.wr && !(pq_valid_i && pq_head_i.kind == pcie_tl_pkg::PK_CQ))
  `COMMON_ASSERT_NEVER(a_read_head_lost, clk_i, rst_i, st_q.busy && !st_q.wr && !rd_valid_i)

endmodule

`default_nettype wire
