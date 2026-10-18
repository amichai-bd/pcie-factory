// Completer completions of pcie_tl (wiki/src/rtl/tl/MAS.md#completions):
// keeps each BAR0 read's context under its handle, takes the application's
// data on cc and frees the handle at the packet's eop, turns the register
// engine's records into completions, and queues both through one
// completion former each (wiki/src/arch/tl/COMPLETIONS.md#completer-completions).
`default_nettype none

`include "common_macros.svh"

module pcie_tl_cpl (
  input  logic                        clk_i,
  input  logic                        rst_i,
  // Read handles, from the cq stream
  input  logic                        rd_take_i,
  input  pcie_tl_pkg::t_hd_ctx        rd_ctx_i,
  output logic                        hd_free_o,
  output logic [2:0]                  hd_free_idx_o,
  // cc stream (APP)
  input  logic                        cc_valid_i,
  output logic                        cc_ready_o,
  input  logic                        cc_sop_i,
  input  logic                        cc_eop_i,
  input  logic [31:0]                 cc_data_i,
  input  logic [2:0]                  cc_handle_i,
  // Records, from the register engine
  input  logic                        cpl_rec_valid_i,
  input  pcie_tl_pkg::t_cpl_rec       cpl_rec_i,
  output logic                        cpl_room_o,
  // Transmit epochs of the two completion lines, and their arming
  input  pcie_tl_pkg::t_ep            ep_cc_i,
  input  pcie_tl_pkg::t_ep            ep_cf_i,
  output logic                        arm_cc_o,
  output logic                        arm_cf_o,
  // Completion queues, to the transmit arbiter
  output logic                        cc_desc_valid_o,
  output pcie_tl_pkg::t_cpl_desc      cc_desc_o,
  input  logic                        cc_desc_pop_i,
  output logic                        cc_data_valid_o,
  output logic [31:0]                 cc_data_o,
  input  logic                        cc_data_pop_i,
  output logic                        cf_desc_valid_o,
  output pcie_tl_pkg::t_cpl_desc      cf_desc_o,
  input  logic                        cf_desc_pop_i,
  output logic                        cf_data_valid_o,
  output logic [31:0]                 cf_data_o,
  input  logic                        cf_data_pop_i
);

  // ------------------------------------------------------ read handles
  // A memory with one write port (the read taken on cq) and a
  // combinational read (the handle cc answers).
  pcie_tl_pkg::t_cpl_ctx hctx_mem [pcie_tl_pkg::HANDLES];
  pcie_tl_pkg::t_cpl_ctx hctx_in;
  logic [2:0]            hctx_waddr;
  logic [pcie_tl_pkg::HANDLES-1:0] live_q, live_d;   // handles taken and not yet answered

  always_comb begin
    hctx_in        = '0;
    hctx_in.id     = rd_ctx_i.id;
    hctx_in.alo    = rd_ctx_i.addr[4:0];
    hctx_in.dw_len = rd_ctx_i.dw_len;
    hctx_in.fbe    = rd_ctx_i.fbe;
    hctx_in.lbe    = rd_ctx_i.lbe;
  end
  assign hctx_waddr = rd_ctx_i.handle;

  `COMMON_MEM_WRITE(hctx_mem, clk_i, rd_take_i, hctx_waddr, hctx_in)

  // ------------------------------------------------------ cc stream
  pcie_tl_pkg::t_cc_state cc_q, cc_d;
  logic                   cc_take;
  logic [2:0]             cc_hd;
  logic                   cc_room;
  logic                   cc_read_last;
  logic [10:0]            cc_k;
  logic                   cc_push;
  pcie_tl_pkg::t_ep       cc_ep;
  pcie_tl_pkg::t_cpl_ctx  cc_ctx;
  pcie_tl_pkg::t_cpl_desc nd_none;

  assign cc_hd      = cc_q.busy ? cc_q.hd : cc_handle_i;
  assign cc_ctx     = hctx_mem[cc_hd];
  assign cc_ready_o = cc_room;
  assign cc_take    = cc_valid_i && cc_ready_o;
  // Its completions wait for every posted request taken before its sop
  // beat (APP ordering): the epoch then.
  assign cc_ep      = cc_q.busy ? cc_q.ep : ep_cc_i;
  assign arm_cc_o   = cc_take && !cc_q.busy;
  assign nd_none    = '0;

  always_comb begin
    cc_d = cc_q;
    if (cc_take) begin
      cc_d.busy = !cc_eop_i;
      cc_d.hd   = cc_hd;
      cc_d.ep   = cc_ep;
    end
  end

  `COMMON_DFF_RST(cc_q, cc_d, clk_i, rst_i)

  assign hd_free_o     = cc_take && cc_eop_i;
  assign hd_free_idx_o = cc_hd;

  always_comb begin
    live_d = live_q;
    if (hd_free_o) begin
      live_d[cc_hd] = 1'b0;
    end
    if (rd_take_i) begin
      live_d[hctx_waddr] = 1'b1;
    end
  end

  `COMMON_DFF_RST(live_q, live_d, clk_i, rst_i)

  pcie_tl_cplf u_cc (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .beat_i       (cc_take),
    .data_i       (cc_data_i),
    .ctx_i        (cc_ctx),
    .nd_i         (1'b0),
    .nd_desc_i    (nd_none),
    .ep_i         (cc_ep),
    .room_o       (cc_room),
    .read_last_o  (cc_read_last),
    .k_o          (cc_k),
    .push_o       (cc_push),
    .desc_valid_o (cc_desc_valid_o),
    .desc_o       (cc_desc_o),
    .desc_pop_i   (cc_desc_pop_i),
    .data_valid_o (cc_data_valid_o),
    .data_o       (cc_data_o),
    .data_pop_i   (cc_data_pop_i)
  );

  // ------------------------------------------------- register records
  // A configuration write or an Unsupported Request is one completion
  // without data; a configuration read one DW (Byte Count 4, Lower Address
  // 0); a BAR2 read's DWs are split like a BAR0 read's.
  logic                   cf_beat;
  logic                   cf_nd;
  pcie_tl_pkg::t_cpl_ctx  cf_ctx;
  pcie_tl_pkg::t_cpl_desc cf_nd_desc;
  logic                   cf_read_last;
  logic [10:0]            cf_k;
  logic                   cf_push;
  logic [12:0]            ur_total;
  logic                   rec_ur;
  logic                   rec_wr;
  logic                   rec_bar2;

  assign rec_ur   = cpl_rec_i.kind == pcie_tl_pkg::CR_UR;
  assign rec_wr   = cpl_rec_i.kind == pcie_tl_pkg::CR_CFG_WR;
  assign rec_bar2 = cpl_rec_i.kind == pcie_tl_pkg::CR_BAR2_RD;
  assign cf_nd    = cpl_rec_valid_i && (rec_ur || rec_wr);
  assign cf_beat  = cpl_rec_valid_i && !(rec_ur || rec_wr);
  assign ur_total = pcie_tl_pkg::byte_total(cpl_rec_i.dw_len, cpl_rec_i.fbe, cpl_rec_i.lbe);

  always_comb begin
    cf_ctx        = '0;
    cf_ctx.id     = cpl_rec_i.id;
    cf_ctx.fixed4 = !rec_bar2;
    cf_ctx.alo    = rec_bar2 ? cpl_rec_i.addr[6:2] : 5'd0;
    cf_ctx.dw_len = rec_bar2 ? cpl_rec_i.dw_len : 11'd1;
    cf_ctx.fbe    = cpl_rec_i.fbe;
    cf_ctx.lbe    = rec_bar2 ? cpl_rec_i.lbe : 4'd0;
    // Unsupported Request of a memory read: Byte Count T and Lower Address
    // B[6:0]; of anything else, and a configuration write: 4 and 0.
    cf_nd_desc        = '0;
    cf_nd_desc.status = rec_ur ? pcie_tl_pkg::CS_UR : pcie_tl_pkg::CS_SC;
    cf_nd_desc.locked = rec_ur && cpl_rec_i.locked;
    cf_nd_desc.bc     = (rec_ur && cpl_rec_i.mem) ? ur_total[11:0] : 12'd4;
    cf_nd_desc.la     = (rec_ur && cpl_rec_i.mem)
                      ? {cpl_rec_i.addr[6:2], pcie_tl_pkg::be_lo(cpl_rec_i.fbe)} : 7'd0;
    cf_nd_desc.id     = cpl_rec_i.id;
  end

  assign arm_cf_o = cf_push;

  pcie_tl_cplf u_cf (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .beat_i       (cf_beat),
    .data_i       (cpl_rec_i.data),
    .ctx_i        (cf_ctx),
    .nd_i         (cf_nd),
    .nd_desc_i    (cf_nd_desc),
    .ep_i         (ep_cf_i),
    .room_o       (cpl_room_o),
    .read_last_o  (cf_read_last),
    .k_o          (cf_k),
    .push_o       (cf_push),
    .desc_valid_o (cf_desc_valid_o),
    .desc_o       (cf_desc_o),
    .desc_pop_i   (cf_desc_pop_i),
    .data_valid_o (cf_data_valid_o),
    .data_o       (cf_data_o),
    .data_pop_i   (cf_data_pop_i)
  );

  `COMMON_UNUSED_BITS(unused_rec, {cpl_rec_i.dw_last, cpl_rec_i.dw_idx[10:0], cpl_rec_i.addr[11:7],
                                   cpl_rec_i.addr[1:0], cc_push, cc_k, rd_ctx_i.addr[9:5],
                                   ur_total[12]})

  // The application answers a handle tl offered and took (APP), one
  // packet at a time, with exactly its read's DWs.
  `COMMON_ASSERT_NEVER(a_cc_handle_idle, clk_i, rst_i, cc_take && !cc_q.busy && !live_q[cc_hd])
  `COMMON_ASSERT_NEVER(a_cc_sop, clk_i, rst_i, cc_take && (cc_sop_i == cc_q.busy))
  `COMMON_ASSERT_NEVER(a_cc_eop, clk_i, rst_i, cc_take && (cc_eop_i != cc_read_last))
  // The register engine's records follow the former's DW count and its room.
  `COMMON_ASSERT_NEVER(a_rec_order, clk_i, rst_i,
                       cf_beat && rec_bar2 && (cpl_rec_i.dw_idx != cf_k
                                               || cpl_rec_i.dw_last != cf_read_last))

endmodule

`default_nettype wire
