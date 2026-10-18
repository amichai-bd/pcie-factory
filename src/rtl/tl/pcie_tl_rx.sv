// Receive parser of pcie_tl (wiki/src/rtl/tl/MAS.md#receive-parser): takes
// every beat the link delivers, judges each request at its last header DW
// by the checks and routing of wiki/src/arch/tl/ROUTING.md, stores the data
// of BAR0 and BAR2 writes, and in the cycle after the TLP's eop pushes its
// entry into the posted queue or a non-posted line and pulses its error
// event. Completions go beat by beat to the tracker slot, a digest DW
// included (the tracker reads Length DWs of data).
`default_nettype none

`include "common_macros.svh"

module pcie_tl_rx #(
  parameter int unsigned BAR0_ADDR_W = 12
) (
  input  logic                    clk_i,
  input  logic                    rst_i,          // rst_i or dl_up_i 0
  input  logic                    tlp_rx_valid_i, // taken whenever valid (ready is 1)
  input  logic [31:0]             tlp_rx_data_i,
  input  logic                    tlp_rx_sop_i,
  input  logic                    tlp_rx_eop_i,
  input  logic                    mem_space_en_i,
  input  logic [1:0]              d_state_i,
  input  logic [31:0]             bar0_base_i,
  input  logic [31:0]             bar2_base_i,
  output logic                    pq_push_o,      // epochs are the queue's
  output pcie_tl_pkg::t_pq_ent    pq_ent_o,
  output logic                    cq_push_o,
  output pcie_tl_pkg::t_cq_ent    cq_ent_o,
  output logic                    cfg_push_o,
  output pcie_tl_pkg::t_cfg_ent   cfg_ent_o,
  output logic                    pd_push_o,      // a data DW of a BAR0 or BAR2 write
  output logic [31:0]             pd_data_o,
  output logic                    np_mal_o,       // a malformed non-posted TLP left at once
  output logic [3:0]              np_mal_d_o,     // its data credits
  output logic [3:0]              err_o,          // tl_err_o bits 3:0
  output pcie_tl_pkg::t_rx_beat   cpl_beat_o      // completion beats, to the tracker (#292)
);

  pcie_tl_pkg::t_rx_state st_q, st_d;
  pcie_tl_pkg::t_rx_cur   cur_q, cur_d;
  logic [31:0]            dw0_q;
  logic [31:0]            dw1_q;
  logic [31:0]            dw2_q;

  logic                   take;
  logic                   take_sop;
  logic                   take_mid;       // a beat after the sop
  logic [5:0]             hdr_end;        // beat index of the last header DW
  logic                   hdr_last;
  logic [5:0]             data_idx;       // payload DW index of a beat after the header
  logic                   data_beat;
  logic                   first_data;
  logic                   en_dw0;
  logic                   en_dw1;
  logic                   en_dw2;
  logic [31:0]            hdw2;
  logic [31:0]            hdw3;
  pcie_tl_pkg::t_hdr      h;
  pcie_tl_pkg::t_rx_cur   judged;
  logic [5:0]             beat_inc;

  assign take     = tlp_rx_valid_i;
  assign take_sop = take && tlp_rx_sop_i;
  assign take_mid = take && !tlp_rx_sop_i && st_q.busy;
  assign hdr_end  = st_q.hdr4 ? 6'd3 : 6'd2;
  assign hdr_last = take_mid && st_q.beat == hdr_end;
  // Payload beats: the Length DWs after the header of a TLP with data. A
  // digest DW after them (TD 1) is never stored (TLP.md, TD).
  assign data_idx   = st_q.beat - hdr_end - 6'd1;
  assign data_beat  = take_mid && st_q.beat > hdr_end && cur_q.write
                   && {5'd0, data_idx} < cur_q.dw_len;
  assign first_data = data_beat && data_idx == 6'd0;
  assign en_dw0   = take_sop;
  assign en_dw1   = take_mid && st_q.beat == 6'd1;
  assign en_dw2   = take_mid && st_q.beat == 6'd2;
  assign beat_inc = (st_q.beat == 6'h3F) ? st_q.beat : st_q.beat + 6'd1;

  `COMMON_DFF_EN(dw0_q, tlp_rx_data_i, clk_i, en_dw0)
  `COMMON_DFF_EN(dw1_q, tlp_rx_data_i, clk_i, en_dw1)
  `COMMON_DFF_EN(dw2_q, tlp_rx_data_i, clk_i, en_dw2)

  // The header at its last DW: DW 0 to 2 from registers, the last from the
  // stream.
  assign hdw2 = st_q.hdr4 ? dw2_q : tlp_rx_data_i;
  assign hdw3 = tlp_rx_data_i;
  assign h    = pcie_tl_pkg::hdr_decode(dw0_q, dw1_q, hdw2, hdw3);

  // ------------------------------------------------- checks and routing
  logic       known;
  logic       is_mem;
  logic       is_cfgio;
  logic       is_msg;
  logic       posted;
  logic       bar_on;
  logic       hit0;
  logic       hit2;
  logic [7:0] code;

  // Address bits a BAR compares: 31 down to its size (HAS, BAR decode).
  localparam logic [31:0] BAR0_MASK = ~((32'd1 << BAR0_ADDR_W) - 32'd1);
  localparam logic [31:0] BAR2_MASK = 32'hFFFF_F000;

  assign known    = pcie_tl_pkg::kind_known(h.fmt, h.typ);
  assign is_mem   = h.typ == pcie_tl_pkg::T_MEM || h.typ == pcie_tl_pkg::T_MEMLK;
  assign is_cfgio = h.typ == pcie_tl_pkg::T_IO || h.typ == pcie_tl_pkg::T_CFG0
                 || h.typ == pcie_tl_pkg::T_CFG1;
  assign is_msg   = h.typ[4:3] == 2'b10;
  assign code     = {h.lbe, h.fbe};
  // BAR decode (ROUTING.md): memory space on, D0, address below 4 GiB.
  assign bar_on   = mem_space_en_i && d_state_i == pcie_tl_pkg::D0 && h.addr[63:32] == 32'd0;
  assign hit0     = bar_on && ((h.addr[31:0] ^ bar0_base_i) & BAR0_MASK) == 32'd0;
  assign hit2     = bar_on && ((h.addr[31:0] ^ bar2_base_i) & BAR2_MASK) == 32'd0;

  always_comb begin
    judged         = '0;
    judged.cls     = pcie_tl_pkg::credit_class(h.fmt[1], h.typ);
    judged.rel_d   = pcie_tl_pkg::data_credits(h.fmt[1], h.dw_len[5:0]);
    judged.id.rid  = h.rid;
    judged.id.tag  = h.tag;
    judged.id.tc   = h.tc;
    judged.id.attr = h.attr;
    judged.addr    = h.addr[27:2];
    judged.reg_num = h.reg_num;
    judged.dw_len  = h.dw_len;
    judged.fbe     = h.fbe;
    judged.lbe     = h.lbe;
    judged.mem     = is_mem;
    judged.locked  = h.typ == pcie_tl_pkg::T_MEMLK;
    judged.write   = h.fmt[1];
    judged.bus     = h.bus;
    judged.dev     = h.dev;
    posted         = judged.cls == pcie_tl_pkg::CLS_P;
    // Steps 1 to 6 (ROUTING.md, checks): the first that fails decides.
    if (!known) begin
      judged.out = pcie_tl_pkg::OUT_MAL;
    end else if (h.typ == pcie_tl_pkg::T_CPL || h.typ == pcie_tl_pkg::T_CPLLK) begin
      judged.out = pcie_tl_pkg::OUT_CPL;
    end else if (is_mem && (!pcie_tl_pkg::be_ok(h.dw_len, h.fbe, h.lbe, h.addr[2])
                            || pcie_tl_pkg::cross_4k(h.addr[11:2], h.dw_len))) begin
      judged.out = pcie_tl_pkg::OUT_MAL;
    end else if (is_cfgio && (h.dw_len != 11'd1 || h.lbe != 4'd0 || h.tc != 3'd0
                              || h.attr != 2'd0)) begin
      judged.out = pcie_tl_pkg::OUT_MAL;
    end else if (is_msg && pcie_tl_pkg::msg_tc0(code) && h.tc != 3'd0) begin
      judged.out = pcie_tl_pkg::OUT_MAL;
    end else begin
      // Step 7: route.
      if (h.typ == pcie_tl_pkg::T_MEM && h.at == 2'd0 && hit0) begin
        judged.out = pcie_tl_pkg::OUT_CQ;
      end else if (h.typ == pcie_tl_pkg::T_MEM && h.at == 2'd0 && hit2) begin
        judged.out = pcie_tl_pkg::OUT_BAR2;
      end else if (h.typ == pcie_tl_pkg::T_CFG0 && h.fn == 3'd0) begin
        judged.out = pcie_tl_pkg::OUT_CFG;
      end else if (is_msg && pcie_tl_pkg::msg_silent(code)) begin
        judged.out = pcie_tl_pkg::OUT_DROP;
      end else if (is_msg && code == pcie_tl_pkg::MSG_PME_TURN_OFF) begin
        judged.out = pcie_tl_pkg::OUT_PME;
      end else begin
        judged.out = pcie_tl_pkg::OUT_UR;
      end
      // Step 8: poisoned, unless already an Unsupported Request.
      if (judged.out == pcie_tl_pkg::OUT_UR) begin
        judged.err[pcie_tl_pkg::E_UR_P]  = posted;
        judged.err[pcie_tl_pkg::E_UR_NP] = !posted;
      end else if (h.ep) begin
        judged.out = posted ? pcie_tl_pkg::OUT_DROP : pcie_tl_pkg::OUT_UR;
        judged.err[pcie_tl_pkg::E_POIS_REQ] = 1'b1;
      end
    end
    if (judged.out == pcie_tl_pkg::OUT_MAL) begin
      judged.err[pcie_tl_pkg::E_MAL] = 1'b1;
    end
  end

  // --------------------------------------------------------- state
  always_comb begin
    st_d      = st_q;
    st_d.push = take && tlp_rx_eop_i;
    if (take_sop) begin
      st_d.busy = 1'b1;
      st_d.beat = 6'd1;
      st_d.hdr4 = tlp_rx_data_i[5];
      st_d.cpl  = pcie_tl_pkg::is_cpl(tlp_rx_data_i[7:5], tlp_rx_data_i[4:0]);
    end else if (take_mid) begin
      st_d.beat = beat_inc;
    end
    if (take && tlp_rx_eop_i) begin
      st_d.busy = 1'b0;
    end
  end

  always_comb begin
    cur_d = cur_q;
    if (hdr_last) begin
      cur_d = judged;
    end
    if (first_data) begin
      cur_d.wdata = tlp_rx_data_i;
    end
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)
  `COMMON_DFF(cur_q, cur_d, clk_i)

  // ------------------------------------------------------------ outputs
  logic push_p;
  logic push_np;

  assign pd_push_o = data_beat && cur_q.cls == pcie_tl_pkg::CLS_P
                  && (cur_q.out == pcie_tl_pkg::OUT_CQ || cur_q.out == pcie_tl_pkg::OUT_BAR2);
  assign pd_data_o = tlp_rx_data_i;

  assign push_p  = st_q.push && cur_q.out != pcie_tl_pkg::OUT_CPL && cur_q.cls == pcie_tl_pkg::CLS_P;
  assign push_np = st_q.push && cur_q.out != pcie_tl_pkg::OUT_CPL && cur_q.cls == pcie_tl_pkg::CLS_NP;

  always_comb begin
    pq_push_o     = push_p;
    pq_ent_o      = '0;
    pq_ent_o.addr = cur_q.addr;
    pq_ent_o.dw_len = cur_q.dw_len[5:0];
    pq_ent_o.fbe  = cur_q.fbe;
    pq_ent_o.lbe  = cur_q.lbe;
    pq_ent_o.rel_d = cur_q.rel_d;
    case (cur_q.out)
      pcie_tl_pkg::OUT_CQ:   pq_ent_o.kind = pcie_tl_pkg::PK_CQ;
      pcie_tl_pkg::OUT_BAR2: pq_ent_o.kind = pcie_tl_pkg::PK_BAR2;
      pcie_tl_pkg::OUT_PME:  pq_ent_o.kind = pcie_tl_pkg::PK_PME;
      default:               pq_ent_o.kind = pcie_tl_pkg::PK_DISCARD;
    endcase

    cq_push_o     = push_np && cur_q.out == pcie_tl_pkg::OUT_CQ;
    cq_ent_o      = '0;
    cq_ent_o.id   = cur_q.id;
    cq_ent_o.addr = cur_q.addr;
    cq_ent_o.dw_len = cur_q.dw_len;
    cq_ent_o.fbe  = cur_q.fbe;
    cq_ent_o.lbe  = cur_q.lbe;

    cfg_push_o    = push_np && (cur_q.out == pcie_tl_pkg::OUT_CFG || cur_q.out == pcie_tl_pkg::OUT_BAR2
                                || cur_q.out == pcie_tl_pkg::OUT_UR);
    cfg_ent_o     = '0;
    cfg_ent_o.id  = cur_q.id;
    cfg_ent_o.reg_num = (cur_q.out == pcie_tl_pkg::OUT_CFG) ? cur_q.reg_num : cur_q.addr[9:0];
    cfg_ent_o.addr_lo = {cur_q.addr[4:0], 2'b00};
    cfg_ent_o.dw_len = cur_q.dw_len;
    cfg_ent_o.fbe = cur_q.fbe;
    cfg_ent_o.lbe = cur_q.lbe;
    cfg_ent_o.mem = cur_q.mem;
    cfg_ent_o.locked = cur_q.locked;
    cfg_ent_o.wdata = cur_q.wdata;
    cfg_ent_o.bus = cur_q.bus;
    cfg_ent_o.dev = cur_q.dev;
    cfg_ent_o.rel_d = cur_q.rel_d;
    case (cur_q.out)
      pcie_tl_pkg::OUT_CFG:  cfg_ent_o.kind = cur_q.write ? pcie_tl_pkg::CK_CFG_WR : pcie_tl_pkg::CK_CFG_RD;
      pcie_tl_pkg::OUT_BAR2: cfg_ent_o.kind = pcie_tl_pkg::CK_BAR2_RD;
      default:               cfg_ent_o.kind = pcie_tl_pkg::CK_UR;
    endcase
  end

  assign np_mal_o   = push_np && cur_q.out == pcie_tl_pkg::OUT_MAL;
  assign np_mal_d_o = cur_q.rel_d;
  assign err_o      = st_q.push ? cur_q.err : 4'd0;

  always_comb begin
    cpl_beat_o.valid = take && (tlp_rx_sop_i ? pcie_tl_pkg::is_cpl(tlp_rx_data_i[7:5], tlp_rx_data_i[4:0])
                                             : st_q.cpl && st_q.busy);
    cpl_beat_o.data  = tlp_rx_data_i;
    cpl_beat_o.sop   = tlp_rx_sop_i;
    cpl_beat_o.eop   = tlp_rx_eop_i;
  end

  // Fields the entries do not carry.
  `COMMON_UNUSED_BITS(unused_hdr, {h.addr[1:0], h.b1_other, h.td})

  // The dll delivers whole TLPs (dll HAS, upper TLP stream): a sop starts
  // one, no beat comes outside one, the eop is no earlier than the last
  // header DW, and no TLP has more than 32 data DWs (8 data credits) and
  // a digest DW.
  `COMMON_ASSERT_NEVER(a_beat_outside_tlp, clk_i, rst_i, take && !tlp_rx_sop_i && !st_q.busy)
  `COMMON_ASSERT_NEVER(a_sop_inside_tlp, clk_i, rst_i, take_sop && st_q.busy)
  `COMMON_ASSERT_NEVER(a_short_tlp, clk_i, rst_i,
                       take && tlp_rx_eop_i && (tlp_rx_sop_i || st_q.beat < hdr_end))
  `COMMON_ASSERT_NEVER(a_rel_data_max, clk_i, rst_i, hdr_last && judged.rel_d > 4'd8)

endmodule

`default_nettype wire
