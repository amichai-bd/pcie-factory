// pcie_rc_tl receive parser (u_rx): takes every beat the link delivers and
// places it in its TLP with the shared beat tracker common_tlp_rx_track.
// Completions go to the requester, their header fields at the third header
// DW, then every later beat and the eop
// (wiki/src/rtl/rc_tl/REQUESTER.md#completion-matching). Every other TLP is
// judged at its last header DW: the receive checks, the inbound routing and
// poisoned (wiki/src/rtl/rc_tl/INBOUND.md#receive-checks); a posted write's
// data DWs go to host memory as they arrive, and at the eop the MSI
// capture, the non-posted queue entry, the error record events and the
// posted release (INBOUND.md, Credits).
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_rx #(
  parameter int unsigned HOST_MEM_ADDR_W = 12,              // 8..16, checked by the top
  parameter logic [31:0] HOST_MEM_BASE   = 32'h0000_0000,
  parameter logic [31:0] MSI_ADDR        = 32'hFEE0_0000
) (
  input  logic                    clk_i,
  input  logic                    rst_i,          // lrst: rst_i or dl_up_i 0
  input  logic                    tlp_rx_valid_i, // taken whenever valid (ready is 1)
  input  logic [31:0]             tlp_rx_data_i,
  input  logic                    tlp_rx_sop_i,
  input  logic                    tlp_rx_eop_i,
  input  pcie_rc_tl_pkg::t_hdr_st hdr_st_i,       // Bus Master Enable and the window
  output pcie_rc_tl_pkg::t_cpl_rx cpl_o,          // completion beats, to the requester
  // Inbound requests and messages
  output pcie_rc_tl_pkg::t_mem_wr wr_o,           // a host memory write, from a flop
  output logic                    msi_push_o,     // an MSI captured at this edge
  output common_pkg::t_rc_msi     msi_o,
  output logic                    np_push_o,      // a non-posted entry queued at this edge
  output pcie_rc_tl_pkg::t_np_ent np_o,
  output logic [pcie_rc_tl_pkg::ERR_W-1:0] err_o, // error record events at this edge
  output logic                    fc_rel_p_o,     // posted release, from flops
  output logic [3:0]              fc_rel_pd_o
);

  localparam int unsigned HW = HOST_MEM_ADDR_W;
  // Host memory in DWs; a request is inside when its DW offset plus its
  // Length is at most this.
  localparam logic [15:0] HOST_DWS = 16'(32'd1 << (HW - 2));

  common_pkg::t_tlp_rx_pos rp;      // the beat's place in its TLP
  logic                    cpl_q, cpl_d;   // the open TLP is a completion
  logic [31:0]             dw0_q;
  logic [31:0]             dw1_q;
  logic [31:0]             dw2_q;

  logic        take;
  logic        in_cpl;        // a beat after the sop of a completion
  logic [9:0]  lfield;
  logic [11:0] bcf;

  // While rst_i (lrst) is 1 nothing is parsed: a TLP in progress is
  // dropped with no event (MAS, Reset and link down). The tracker places
  // each beat and checks the dll's whole-TLP contract (dll HAS, upper TLP
  // stream); Fmt bit 0 (byte 0 bit 5) marks a 4-DW header.
  assign take = tlp_rx_valid_i && !rst_i;

  common_tlp_rx_track u_beats (
    .clk_i  (clk_i),
    .rst_i  (rst_i),
    .take_i (take),
    .sop_i  (tlp_rx_sop_i),
    .eop_i  (tlp_rx_eop_i),
    .hdr4_i (tlp_rx_data_i[5]),
    .pos_o  (rp)
  );

  assign in_cpl = rp.mid && cpl_q;
  assign cpl_d  = rp.sop ? common_pkg::is_cpl(tlp_rx_data_i[7:5], tlp_rx_data_i[4:0]) : cpl_q;

  `COMMON_DFF_RST(cpl_q, cpl_d, clk_i, rst_i)
  `COMMON_DFF_EN(dw0_q, tlp_rx_data_i, clk_i, rp.en_dw[0])
  `COMMON_DFF_EN(dw1_q, tlp_rx_data_i, clk_i, rp.en_dw[1])
  `COMMON_DFF_EN(dw2_q, tlp_rx_data_i, clk_i, rp.en_dw[2])

  // ------------------------------------------------------ completions
  // The header fields (TLP.md, Completions): DW 0 and 1 from registers,
  // DW 2 from the stream at the third header DW (a completion's header is
  // 3 DWs, so that beat is its last header DW).
  assign lfield = {dw0_q[17:16], dw0_q[31:24]};
  assign bcf    = {dw1_q[19:16], dw1_q[31:24]};

  always_comb begin
    cpl_o        = '0;
    cpl_o.hdr    = in_cpl && rp.beat == 6'd2;
    cpl_o.dat    = in_cpl && rp.beat > 6'd2;
    cpl_o.eop    = in_cpl && tlp_rx_eop_i;
    cpl_o.data   = tlp_rx_data_i;
    cpl_o.locked = dw0_q[4:0] == common_pkg::T_CPLLK;
    cpl_o.wdata  = dw0_q[6];
    cpl_o.ep     = dw0_q[22];
    cpl_o.status = dw1_q[23:21];
    cpl_o.dw_n   = !dw0_q[6] ? 11'd0 : (lfield == 10'd0) ? 11'd1024 : {1'b0, lfield};
    cpl_o.bc     = bcf;
    cpl_o.rid    = {tlp_rx_data_i[7:0], tlp_rx_data_i[15:8]};
    cpl_o.tag    = tlp_rx_data_i[23:16];
    cpl_o.la     = tlp_rx_data_i[30:24];
  end

  // ------------------------------------------------------ requests and messages
  // The header fields at the last header DW (TLP.md): DW 0 and 1 from
  // registers; the address DW is this beat, with DW 2 the upper address of
  // a 4-DW header.
  logic [2:0]  fmt;
  logic [4:0]  typ;
  logic [2:0]  tc;
  logic [1:0]  attr;
  logic        ep;
  logic [10:0] dw_n;          // Length, 0 read as 1024
  logic [3:0]  fbe;
  logic [3:0]  lbe;
  logic [7:0]  code;          // a message's code
  logic [31:0] aw;            // the address DW as an address word
  logic [31:2] adr;           // address bits 31:2
  logic        hi;            // a 4-DW address with bits 63:32 not 0

  assign fmt  = dw0_q[7:5];
  assign typ  = dw0_q[4:0];
  assign tc   = dw0_q[14:12];
  assign attr = dw0_q[21:20];
  assign ep   = dw0_q[22];
  assign dw_n = (lfield == 10'd0) ? 11'd1024 : {1'b0, lfield};
  assign fbe  = dw1_q[27:24];
  assign lbe  = dw1_q[31:28];
  assign code = dw1_q[31:24];
  assign aw   = common_pkg::be_word(tlp_rx_data_i);
  assign adr  = aw[31:2];
  assign hi   = rp.hdr4 && (dw2_q != 32'd0);

  logic is_mem;               // memory or locked memory request
  logic is_cfgio;             // configuration or I/O request
  logic is_msg;
  logic mal;                  // steps 1 and 3 to 6
  logic in_win;               // inside the memory window
  logic in_msi;               // inside the MSI window
  logic in_host;              // entirely inside host memory
  logic [15:0] host_end;      // the request's end, in host DWs
  common_pkg::t_cls cls;
  logic [3:0]  dc;

  assign is_mem   = (typ == common_pkg::T_MEM) || (typ == common_pkg::T_MEMLK);
  assign is_cfgio = (typ == common_pkg::T_IO) || (typ == common_pkg::T_CFG0)
                 || (typ == common_pkg::T_CFG1);
  assign is_msg   = typ[4:3] == 2'b10;

  // INBOUND.md, Receive checks, the endpoint's steps, in order.
  always_comb begin
    mal = !common_pkg::kind_known(fmt, typ);
    if (is_mem) begin
      mal = mal || !common_pkg::be_ok(dw_n, fbe, lbe, adr[2])
                || common_pkg::cross_4k(adr[11:2], dw_n);
    end
    if (is_cfgio) begin
      mal = mal || (lfield != 10'd1) || (lbe != 4'd0) || (tc != 3'd0) || (attr != 2'd0);
    end
    if (is_msg) begin
      mal = mal || ((tc != 3'd0) && common_pkg::msg_tc0(code));
    end
  end

  // The inbound routing (rc HAS): a request stays inside one 4 KiB block
  // (step 4), so the 1 MiB window and the 4 KiB MSI window hold it whole or
  // not at all; host memory may be smaller than 4 KiB, so its end is checked.
  assign host_end = 16'(adr[HW-1:2]) + {5'd0, dw_n};
  assign in_win   = !hi && (adr[31:20] >= hdr_st_i.mem_base) && (adr[31:20] <= hdr_st_i.mem_limit);
  assign in_msi   = !hi && (adr[31:12] == MSI_ADDR[31:12]);
  assign in_host  = !hi && (adr[31:HW] == HOST_MEM_BASE[31:HW]) && (host_end <= HOST_DWS);
  assign cls      = common_pkg::credit_class(fmt[1], typ);
  assign dc       = common_pkg::data_credits(fmt[1], lfield[5:0]);

  pcie_rc_tl_pkg::t_rx_req rq_q, rq_d;
  pcie_rc_tl_pkg::t_rx_req dec;      // the decision at the last header DW
  pcie_rc_tl_pkg::t_rx_req now;      // the TLP's decision at this beat
  logic serve;                       // routing serves a memory request from host memory
  logic msi;                         // routing captures it
  logic ur;                          // routing makes it an Unsupported Request

  always_comb begin
    dec           = '0;
    dec.on        = 1'b1;
    dec.rel       = cls == common_pkg::CLS_P;
    dec.np        = cls == common_pkg::CLS_NP;
    dec.dbeg      = 1'b1;
    dec.cnt       = dw_n[5:0];
    dec.moff      = adr[11:2];
    dec.ent.kind  = pcie_rc_tl_pkg::NP_UR;
    dec.ent.mem   = is_mem;
    dec.ent.rid   = {dw1_q[7:0], dw1_q[15:8]};
    dec.ent.tag   = dw1_q[23:16];
    dec.ent.tc    = tc;
    dec.ent.attr  = attr;
    dec.ent.adw   = adr[15:2];
    dec.ent.dw_n  = dw_n;
    dec.ent.fbe   = fbe;
    dec.ent.lbe   = lbe;
    dec.ent.dc    = dc;
    serve = 1'b0;
    msi   = 1'b0;
    ur    = 1'b0;
    if (mal) begin
      // Dropped, bit 6; a non-posted one through the queue as a drop.
      dec.err[pcie_rc_tl_pkg::ERR_MAL] = 1'b1;
      dec.ent.kind = pcie_rc_tl_pkg::NP_DROP;
    end else if (is_msg) begin
      // Messages are not gated by Bus Master Enable; poisoned: bit 5 only.
      if (ep) begin
        dec.err[pcie_rc_tl_pkg::ERR_POIS] = 1'b1;
      end else if (code == pcie_rc_tl_pkg::MSG_ERR_COR) begin
        dec.err[pcie_rc_tl_pkg::ERR_COR] = 1'b1;
      end else if (code == pcie_rc_tl_pkg::MSG_ERR_NFAT) begin
        dec.err[pcie_rc_tl_pkg::ERR_NFAT] = 1'b1;
      end else if (code == pcie_rc_tl_pkg::MSG_ERR_FAT) begin
        dec.err[pcie_rc_tl_pkg::ERR_FAT] = 1'b1;
      end else begin
        dec.err[pcie_rc_tl_pkg::ERR_MSG] = 1'b1;
      end
    end else begin
      // The inbound routing rows, the first match deciding: a locked,
      // I/O, configuration or atomic request is an Unsupported Request.
      if (typ != common_pkg::T_MEM || in_win || !hdr_st_i.bme) begin
        ur = 1'b1;
      end else if (fmt[1] && dw_n == 11'd1 && in_msi) begin
        msi = 1'b1;
      end else if (in_host) begin
        serve = 1'b1;
      end else begin
        ur = 1'b1;
      end
      if (typ == common_pkg::T_MEMLK) begin
        dec.ent.kind = pcie_rc_tl_pkg::NP_URLK;
      end
      // Then poisoned: Unsupported Request outranks it.
      if (ur) begin
        dec.err[pcie_rc_tl_pkg::ERR_IN_UR] = 1'b1;
      end else if (ep) begin
        dec.err[pcie_rc_tl_pkg::ERR_POIS] = 1'b1;
      end else begin
        dec.wr  = serve && fmt[1];
        dec.msi = msi;
        if (serve && !fmt[1]) begin
          dec.ent.kind = pcie_rc_tl_pkg::NP_READ;
        end
      end
    end
  end

  logic        hdr_last;      // the last header DW of a request or message
  logic        req_eop;       // its eop
  logic        dat;           // one of its data DWs (not a digest)
  logic [3:0]  lanes;

  assign hdr_last = rp.hdr_last && !cpl_q;
  assign now      = hdr_last ? dec : rq_q;
  assign req_eop  = rp.mid && !cpl_q && tlp_rx_eop_i && now.on;
  assign dat      = rp.mid && !cpl_q && !hdr_last && rq_q.on && rq_q.cnt != 6'd0;
  // Lanes by First BE, all four, then Last BE (INBOUND.md, Receive checks).
  assign lanes    = (rq_q.dbeg ? rq_q.ent.fbe : 4'hF)
                  & ((rq_q.cnt == 6'd1 && !rq_q.dbeg) ? rq_q.ent.lbe : 4'hF);

  always_comb begin
    rq_d = rq_q;
    if (hdr_last) begin
      rq_d = dec;
    end
    if (dat) begin
      rq_d.cnt  = rq_q.cnt - 6'd1;
      rq_d.dbeg = 1'b0;
      rq_d.ent.adw = rq_q.ent.adw + pcie_rc_tl_pkg::HADW_W'(1);
      if (rq_q.dbeg) begin
        rq_d.mdata = tlp_rx_data_i;
      end
    end
    if (req_eop) begin
      rq_d.on = 1'b0;
    end
  end

  `COMMON_DFF_RST(rq_q, rq_d, clk_i, rst_i)

  // A posted write DW is written at the edge after it is taken: the write
  // flop always wins host memory in that cycle.
  pcie_rc_tl_pkg::t_mem_wr wr_q, wr_d;

  always_comb begin
    wr_d       = '0;
    wr_d.valid = dat && rq_q.wr;
    wr_d.adw   = rq_q.ent.adw;
    wr_d.be    = lanes;
    wr_d.data  = tlp_rx_data_i;
  end

  `COMMON_DFF_RST(wr_q, wr_d, clk_i, rst_i)

  assign wr_o = wr_q;

  // At the eop: the capture (its data DW is this beat when it is the
  // first), the queue entry and the events; the posted release follows in
  // the next cycle.
  logic       rel_q, rel_d;
  logic [3:0] rel_dc_q, rel_dc_d;

  assign msi_push_o  = req_eop && now.msi;
  assign msi_o.offset = now.moff;
  assign msi_o.data  = (dat && rq_q.dbeg) ? tlp_rx_data_i : rq_q.mdata;
  assign np_push_o   = req_eop && now.np;
  assign np_o        = now.ent;
  assign err_o       = req_eop ? now.err : '0;
  assign rel_d       = req_eop && now.rel;
  assign rel_dc_d    = now.ent.dc;

  `COMMON_DFF_RST(rel_q, rel_d, clk_i, rst_i)
  `COMMON_DFF_RST(rel_dc_q, rel_dc_d, clk_i, rst_i)

  // Releases stop under lrst at once.
  assign fc_rel_p_o  = rel_q && !rst_i;
  assign fc_rel_pd_o = (rel_q && !rst_i) ? rel_dc_q : 4'd0;

  // The completion fields not checked (TLP.md, Completions): TC, Attr, TD,
  // AT, the Completer ID and BCM; the requester counts the data DWs, so a
  // digest DW after them is never data. The request fields not read: TD
  // (a digest DW follows the data and is never written), AT, Attr bit 2,
  // the address's reserved bits 1:0; the tracker's other views.
  `COMMON_UNUSED_BITS(unused_hdr, {dw0_q[15], dw0_q[11:8], dw0_q[23], dw0_q[19:18],
                                   dw1_q[20], rp.busy, rp.hdr_end,
                                   tlp_rx_data_i[31:30], aw[1:0]})
  // At the eop only what it does matters; the routing reads Bus Master
  // Enable and the window of the header state.
  `COMMON_UNUSED_BITS(unused_now, {now.on, now.wr, now.cnt, now.dbeg, now.mdata})
  `COMMON_UNUSED_BITS(unused_hdr_st, {hdr_st_i.mse, hdr_st_i.pri_bus, hdr_st_i.sec_bus})

  // A request with data carries at most 32 DWs (the dll's 128-byte limit).
  `COMMON_ASSERT_NEVER(a_wr_len, clk_i, rst_i, hdr_last && fmt[1] && !mal && dw_n > 11'd32)

endmodule

`default_nettype wire
