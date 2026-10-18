// Register-access engine of pcie_tl (wiki/src/rtl/tl/MAS.md#register-access):
// serves the cfg line (configuration requests, BAR2 reads, Unsupported
// Requests) and BAR2 writes from the posted queue on the tl-cfg register
// access (wiki/src/arch/core/TL_CFG.md#register-access), one DW access at a
// time, and hands each result to the completion former as a record.
`default_nettype none

`include "common_macros.svh"

module pcie_tl_reg (
  input  logic                        clk_i,
  input  logic                        rst_i,
  // cfg line head
  input  logic                        cfg_valid_i,
  input  pcie_tl_pkg::t_cfg_ent       cfg_head_i,
  input  logic                        cfg_ok_i,
  output logic                        cfg_pop_o,
  // Posted queue head and data (BAR2 writes)
  input  logic                        pq_valid_i,
  input  pcie_tl_pkg::t_pq_ent        pq_head_i,
  output logic                        pq_pop_o,
  input  logic                        pd_valid_i,
  input  logic [31:0]                 pd_head_i,
  output logic                        pd_pop_o,
  // Completions (pcie_tl_cpl): records and room
  input  logic                        cpl_room_i,     // room for one more record
  output logic                        cpl_rec_valid_o,
  output pcie_tl_pkg::t_cpl_rec       cpl_rec_o,
  // tl-cfg register access
  output logic                        reg_req_valid_o,
  input  logic                        reg_req_ready_i,
  output logic                        reg_req_bar2_o,
  output logic                        reg_req_write_o,
  output logic [9:0]                  reg_req_addr_o,
  output logic [3:0]                  reg_req_be_o,
  output logic [31:0]                 reg_req_data_o,
  output logic [7:0]                  reg_req_bus_o,
  output logic [4:0]                  reg_req_dev_o,
  input  logic                        reg_cpl_valid_i,
  input  logic [31:0]                 reg_cpl_data_i
);

  pcie_tl_pkg::t_reg_state st_q, st_d;
  pcie_tl_pkg::t_cfg_ent   cur_q, cur_d;    // the request served

  logic        start_np;       // an eligible cfg-line request with an access starts
  logic        ur_now;         // an eligible Unsupported Request is handed on
  logic        start_p;        // a BAR2 write starts
  logic        is_wr;
  logic        is_cfg;
  logic        last_acc;
  logic        can_offer;
  logic        take;
  logic [9:0]  acc_addr;
  logic [3:0]  acc_be;
  logic [10:0] idx_inc;

  assign ur_now   = st_q.st == pcie_tl_pkg::R_IDLE && cfg_valid_i && cfg_ok_i
                 && cfg_head_i.kind == pcie_tl_pkg::CK_UR && cpl_room_i;
  assign start_np = st_q.st == pcie_tl_pkg::R_IDLE && cfg_valid_i && cfg_ok_i
                 && cfg_head_i.kind != pcie_tl_pkg::CK_UR;
  // An eligible cfg-line request is older than every waiting posted write.
  assign start_p  = st_q.st == pcie_tl_pkg::R_IDLE && !(cfg_valid_i && cfg_ok_i)
                 && pq_valid_i && pq_head_i.kind == pcie_tl_pkg::PK_BAR2;

  assign is_cfg   = cur_q.kind == pcie_tl_pkg::CK_CFG_RD || cur_q.kind == pcie_tl_pkg::CK_CFG_WR;
  assign is_wr    = st_q.posted || cur_q.kind == pcie_tl_pkg::CK_CFG_WR;
  assign last_acc = st_q.idx == cur_q.dw_len - 11'd1;
  assign idx_inc  = st_q.idx + 11'd1;
  // A BAR2 write offers its DW once it is at the data head; any other
  // access only with room for the record its result makes.
  assign can_offer = st_q.posted ? pd_valid_i : cpl_room_i;
  assign reg_req_valid_o = st_q.st == pcie_tl_pkg::R_OFFER && (st_q.up || can_offer);
  assign take      = reg_req_valid_o && reg_req_ready_i;

  // The DW access: configuration register, or BAR2 DW index plus one per
  // DW with the byte enables of that DW.
  always_comb begin
    acc_addr = cur_q.reg_num + st_q.idx[9:0];
    if (is_cfg || cur_q.dw_len == 11'd1 || st_q.idx == 11'd0) begin
      acc_be = cur_q.fbe;
    end else if (last_acc) begin
      acc_be = cur_q.lbe;
    end else begin
      acc_be = 4'hF;
    end
  end

  assign reg_req_bar2_o  = !is_cfg;
  assign reg_req_write_o = is_wr;
  assign reg_req_addr_o  = is_cfg ? cur_q.reg_num : acc_addr;
  assign reg_req_be_o    = acc_be;
  assign reg_req_data_o  = st_q.posted ? pd_head_i : (is_wr ? cur_q.wdata : 32'd0);
  assign reg_req_bus_o   = (is_cfg && is_wr) ? cur_q.bus : 8'd0;
  assign reg_req_dev_o   = (is_cfg && is_wr) ? cur_q.dev : 5'd0;

  // A posted BAR2 write as a cfg-line-shaped request.
  pcie_tl_pkg::t_cfg_ent pw;
  always_comb begin
    pw         = '0;
    pw.kind    = pcie_tl_pkg::CK_BAR2_RD;   // a BAR2 access; st_q.posted makes it a write
    pw.reg_num = pq_head_i.addr[9:0];
    pw.dw_len  = {5'd0, pq_head_i.dw_len};
    pw.fbe     = pq_head_i.fbe;
    pw.lbe     = pq_head_i.lbe;
  end

  always_comb begin
    st_d  = st_q;
    cur_d = cur_q;
    case (st_q.st)
      pcie_tl_pkg::R_IDLE: begin
        st_d.idx = 11'd0;
        st_d.up  = 1'b0;
        if (start_np) begin
          cur_d       = cfg_head_i;
          st_d.posted = 1'b0;
          st_d.st     = pcie_tl_pkg::R_OFFER;
        end else if (start_p) begin
          cur_d       = pw;
          st_d.posted = 1'b1;
          st_d.st     = pcie_tl_pkg::R_OFFER;
        end
      end
      pcie_tl_pkg::R_OFFER: begin
        st_d.up = reg_req_valid_o && !reg_req_ready_i;
        if (take) begin
          st_d.st = pcie_tl_pkg::R_WAIT;
        end
      end
      pcie_tl_pkg::R_WAIT: begin
        if (reg_cpl_valid_i) begin
          if (last_acc || is_cfg) begin
            st_d.st = pcie_tl_pkg::R_IDLE;
          end else begin
            st_d.idx = idx_inc;
            st_d.st  = pcie_tl_pkg::R_OFFER;
          end
        end
      end
      default: st_d.st = pcie_tl_pkg::R_IDLE;
    endcase
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)
  `COMMON_DFF(cur_q, cur_d, clk_i)

  // A request leaves tl when cfg takes its last access (ORDERING.md).
  assign cfg_pop_o = ur_now || (take && !st_q.posted && (is_cfg || last_acc));
  assign pq_pop_o  = take && st_q.posted && last_acc;
  assign pd_pop_o  = take && st_q.posted;

  // Records: an Unsupported Request at once; a configuration access or a
  // BAR2 read DW when cfg finishes it.
  always_comb begin
    cpl_rec_valid_o = ur_now || (st_q.st == pcie_tl_pkg::R_WAIT && reg_cpl_valid_i && !st_q.posted);
    cpl_rec_o       = '0;
    if (ur_now) begin
      cpl_rec_o.kind    = pcie_tl_pkg::CR_UR;
      cpl_rec_o.id      = cfg_head_i.id;
      cpl_rec_o.mem     = cfg_head_i.mem;
      cpl_rec_o.locked  = cfg_head_i.locked;
      cpl_rec_o.addr    = cfg_head_i.mem ? {5'd0, cfg_head_i.addr_lo} : 12'd0;
      cpl_rec_o.dw_len  = cfg_head_i.dw_len;
      cpl_rec_o.fbe     = cfg_head_i.fbe;
      cpl_rec_o.lbe     = cfg_head_i.lbe;
    end else begin
      case (cur_q.kind)
        pcie_tl_pkg::CK_CFG_WR: cpl_rec_o.kind = pcie_tl_pkg::CR_CFG_WR;
        pcie_tl_pkg::CK_CFG_RD: cpl_rec_o.kind = pcie_tl_pkg::CR_CFG_RD;
        default:                cpl_rec_o.kind = pcie_tl_pkg::CR_BAR2_RD;
      endcase
      cpl_rec_o.id      = cur_q.id;
      cpl_rec_o.addr    = is_cfg ? 12'd0 : {cur_q.reg_num, 2'b00};
      cpl_rec_o.dw_len  = cur_q.dw_len;
      cpl_rec_o.fbe     = cur_q.fbe;
      cpl_rec_o.lbe     = cur_q.lbe;
      cpl_rec_o.dw_idx  = st_q.idx;
      cpl_rec_o.dw_last = is_cfg || last_acc;
      cpl_rec_o.data    = reg_cpl_data_i;
    end
  end

  `COMMON_UNUSED_BITS(unused_ent, {cur_q.ep, cur_q.addr_lo, cur_q.mem, cur_q.locked, cur_q.rel_d,
                                   pq_head_i.ep, pq_head_i.rel_d, pq_head_i.addr[25:10]})

  // One access at a time (TL_CFG): cfg finishes only an access it took,
  // never while one is offered or none is outstanding.
  `COMMON_ASSERT_NEVER(a_cpl_without_access, clk_i, rst_i,
                       reg_cpl_valid_i && st_q.st != pcie_tl_pkg::R_WAIT)
  // Fields are held while valid is 1.
  `COMMON_ASSERT_NEVER(a_offer_dropped, clk_i, rst_i, st_q.up && !reg_req_valid_o)

endmodule

`default_nettype wire
