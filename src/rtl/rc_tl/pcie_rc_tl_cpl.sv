// pcie_rc_tl completer (u_cpl) (wiki/src/rtl/rc_tl/INBOUND.md#completer):
// the non-posted queue (a common_sdp_ram_fifo), and the completer, which
// takes its head when idle and releases its credits in the next cycle. A
// drop leaves at once; an Unsupported Request becomes one completion
// without data; a read becomes its completions, each read from host memory
// one DW per granted cycle into the completion data queue (a second
// common_sdp_ram_fifo), its bytes outside First and Last BE forced to 0, and
// offered to the transmit arbiter when all its DWs are there. The next
// completion starts when the link has taken this one's eop.
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_cpl #(
  parameter int unsigned FC_NPH = 8   // 1..127, checked by the top
) (
  input  logic                      clk_i,
  input  logic                      rst_i,         // lrst: rst_i or dl_up_i 0
  input  logic [7:0]                pri_bus_i,     // Primary Bus Number: the Completer ID
  // The non-posted queue's input, from u_rx
  input  logic                      np_push_i,
  input  pcie_rc_tl_pkg::t_np_ent   np_i,
  // Host memory
  output logic                      rd_req_o,
  output logic [pcie_rc_tl_pkg::HADW_W-1:0] rd_adw_o,
  input  logic                      rd_gnt_i,
  input  logic                      rd_vld_i,      // the read granted at the last edge
  input  logic [31:0]               rd_data_i,
  // The transmit arbiter
  output pcie_rc_tl_pkg::t_cpl_tx   tx_o,
  input  logic                      tx_grant_i,
  input  logic                      tx_pop_i,      // a data DW taken from the data queue
  output logic [31:0]               tx_data_o,     // the data queue's head
  input  logic                      tx_eop_i,      // the link took the completion's eop
  // Non-posted release, from flops
  output logic                      fc_rel_np_o,
  output logic [3:0]                fc_rel_npd_o
);

  // 2**NPQ_AW entries in the RAM plus the head: at least FC_NPH + 1, so a
  // credit-correct endpoint never fills it.
  localparam int unsigned NPQ_AW = (FC_NPH <= 2) ? 1 : $clog2(FC_NPH);

  // ------------------------------------------------------ the non-posted queue
  logic                    np_hv;
  pcie_rc_tl_pkg::t_np_ent np_head;
  logic                    np_pop;
  logic                    np_empty;
  logic                    np_full;
  logic [NPQ_AW:0]         np_used;

  common_sdp_ram_fifo #(
    .WIDTH  ($bits(pcie_rc_tl_pkg::t_np_ent)),
    .ADDR_W (NPQ_AW)
  ) u_npq (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (np_push_i),
    .push_data_i  (np_i),
    .pop_i        (np_pop),
    .head_valid_o (np_hv),
    .head_o       (np_head),
    .empty_o      (np_empty),
    .full_o       (np_full),
    .ram_used_o   (np_used)
  );

  // ------------------------------------------------------ the completer
  pcie_rc_tl_pkg::t_cpl_ctl cc_q, cc_d;

  logic [12:0] tot;          // the head's total bytes T
  logic [1:0]  blo;          // its first byte's index in its DW
  logic [11:0] sb;           // the current completion's first byte, in 4 KiB
  logic [12:0] bc;
  logic [9:0]  dw0_len;
  logic        rd_first;     // the DW returned is the read's first
  logic        rd_last;      // ... its last
  logic [31:0] mask;
  logic        push;
  logic [5:0]  k_next;

  assign np_pop = np_hv && !cc_q.act;
  assign tot    = common_pkg::byte_total(np_head.dw_n, np_head.fbe, np_head.lbe);
  assign blo    = common_pkg::be_lo(np_head.fbe);

  // Byte Count: T for the first completion, B + T - S for one starting at
  // S (4096 sent as 0); Lower Address S[6:0], B for the first.
  assign sb     = {cc_q.dwa, cc_q.beg ? common_pkg::be_lo(cc_q.ent.fbe) : 2'b00};
  assign bc     = cc_q.fin - {1'b0, sb};

  // The read's bytes that F and L do not enable are forced to 0.
  assign rd_first = cc_q.ret == cc_q.ent.dw_n;
  assign rd_last  = cc_q.ret == 11'd1;
  assign mask     = (rd_first ? common_pkg::be_mask(cc_q.ent.fbe) : 32'hFFFF_FFFF)
                  & ((rd_last && !rd_first) ? common_pkg::be_mask(cc_q.ent.lbe) : 32'hFFFF_FFFF);
  assign push     = rd_vld_i && cc_q.act;

  // No read is asked for under lrst, so none returns to a cleared completer.
  assign rd_req_o = cc_q.act && cc_q.iss != 6'd0 && !rst_i;
  assign rd_adw_o = cc_q.radw;
  assign k_next   = pcie_rc_tl_pkg::cpl_dws(cc_q.rest, cc_q.dwa[3:0] + cc_q.k[3:0]);

  always_comb begin
    cc_d = cc_q;
    if (rd_gnt_i) begin
      cc_d.iss  = cc_q.iss - 6'd1;
      cc_d.radw = cc_q.radw + pcie_rc_tl_pkg::HADW_W'(1);
    end
    if (push) begin
      cc_d.got = cc_q.got - 6'd1;
      cc_d.ret = cc_q.ret - 11'd1;
    end
    if (tx_grant_i) begin
      cc_d.gnt = 1'b1;
    end
    if (tx_eop_i) begin
      cc_d.gnt = 1'b0;
      if (cc_q.rest == 11'd0) begin
        cc_d.act = 1'b0;
      end else begin
        // The next completion starts where this one ended, on a DW.
        cc_d.dwa  = cc_q.dwa + {4'd0, cc_q.k};
        cc_d.beg  = 1'b0;
        cc_d.k    = k_next;
        cc_d.iss  = k_next;
        cc_d.got  = k_next;
        cc_d.rest = cc_q.rest - {5'd0, k_next};
      end
    end
    if (np_pop) begin
      // A drop leaves at once; a read's first completion starts.
      cc_d     = '0;
      cc_d.act = np_head.kind != pcie_rc_tl_pkg::NP_DROP;
      cc_d.ent = np_head;
      cc_d.dwa = np_head.adw[9:0];
      cc_d.beg = 1'b1;
      cc_d.fin = {1'b0, np_head.adw[9:0], blo} + tot;
      cc_d.radw = np_head.adw;
      cc_d.ret = np_head.dw_n;
      if (np_head.kind == pcie_rc_tl_pkg::NP_READ) begin
        cc_d.k    = pcie_rc_tl_pkg::cpl_dws(np_head.dw_n, np_head.adw[3:0]);
        cc_d.iss  = cc_d.k;
        cc_d.got  = cc_d.k;
        cc_d.rest = np_head.dw_n - {5'd0, cc_d.k};
      end
    end
  end

  `COMMON_DFF_RST(cc_q, cc_d, clk_i, rst_i)

  // ------------------------------------------------------ the completion
  // Header (INBOUND.md, Completer): CplD with the current DWs, or Cpl (the
  // locked kind for a locked read) with Unsupported Request status; TC and
  // Attr bits 1:0 of the request, Completer ID {Primary Bus Number, 0, 0},
  // BCM 0, the request's Requester ID and Tag. An Unsupported Request of a
  // memory read has Byte Count T and Lower Address B, else 4 and 0.
  logic        rd;
  logic [11:0] bcf;
  logic [6:0]  la;

  assign rd      = cc_q.ent.kind == pcie_rc_tl_pkg::NP_READ;
  assign dw0_len = rd ? {4'd0, cc_q.k} : 10'd0;
  assign bcf     = (rd || cc_q.ent.mem) ? bc[11:0] : 12'd4;
  assign la      = (rd || cc_q.ent.mem) ? sb[6:0] : 7'd0;

  always_comb begin
    tx_o       = '0;
    tx_o.valid = cc_q.act && !cc_q.gnt && cc_q.got == 6'd0;
    tx_o.dw_n  = rd ? cc_q.k : 6'd0;
    tx_o.dw0   = pcie_rc_tl_pkg::cpl_dw0(rd ? 3'b010 : 3'b000,
                                         (cc_q.ent.kind == pcie_rc_tl_pkg::NP_URLK)
                                           ? common_pkg::T_CPLLK : common_pkg::T_CPL,
                                         cc_q.ent.tc, cc_q.ent.attr, dw0_len);
    tx_o.dw1   = pcie_rc_tl_pkg::cpl_dw1({pri_bus_i, 8'd0},
                                         rd ? pcie_rc_tl_pkg::CS_SC : pcie_rc_tl_pkg::CS_UR, bcf);
    tx_o.dw2   = pcie_rc_tl_pkg::cpl_dw2(cc_q.ent.rid, cc_q.ent.tag, la);
  end

  // ------------------------------------------------------ the data queue
  logic       dq_hv;
  logic       dq_empty;
  logic       dq_full;
  logic [5:0] dq_used;

  common_sdp_ram_fifo #(
    .WIDTH  (32),
    .ADDR_W (5)
  ) u_dq (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (push),
    .push_data_i  (rd_data_i & mask),
    .pop_i        (tx_pop_i),
    .head_valid_o (dq_hv),
    .head_o       (tx_data_o),
    .empty_o      (dq_empty),
    .full_o       (dq_full),
    .ram_used_o   (dq_used)
  );

  // ------------------------------------------------------ release
  // A non-posted TLP leaves when the completer takes it from the queue:
  // its release pulses in the next cycle; releases stop under lrst at once.
  logic       rel_q;
  logic [3:0] rel_dc_q;

  `COMMON_DFF_RST(rel_q, np_pop, clk_i, rst_i)
  `COMMON_DFF_RST(rel_dc_q, np_head.dc, clk_i, rst_i)

  assign fc_rel_np_o  = rel_q && !rst_i;
  assign fc_rel_npd_o = (rel_q && !rst_i) ? rel_dc_q : 4'd0;

  `COMMON_UNUSED_BITS(unused_q, {np_empty, np_full, np_used, dq_empty, dq_full, dq_used,
                                 cc_q.ent.dc, bc[12]})

  // A completion's data DWs are all in the data queue when it is offered:
  // every pop finds a head; nothing comes back for an idle completer.
  `COMMON_ASSERT_NEVER(a_pop_empty, clk_i, rst_i, tx_pop_i && !dq_hv)
  `COMMON_ASSERT_NEVER(a_ret_idle, clk_i, rst_i, rd_vld_i && !cc_q.act)
  `COMMON_ASSERT_NEVER(a_grant_unoffered, clk_i, rst_i, tx_grant_i && !tx_o.valid)

endmodule

`default_nettype wire
