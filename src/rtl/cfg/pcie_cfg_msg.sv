// pcie_cfg messages and interrupts: the application's request and its
// resolution, the MSI pending flag, the MSI-X pending bits, the offer on
// msg_* by priority, its withdrawal, and the take
// (wiki/src/arch/cfg/HAS.md#messages, #interrupts;
// wiki/src/rtl/cfg/MAS.md#messages-and-interrupts).
//
// Edge order: an offer is chosen from the state before the edge (an MSI-X
// write first looks up its table entry at an earlier edge); at the edge,
// the take, then the register write (ctrl_d_i, mask_d_i), then the error
// events, then the application's request apply, and an offer or a waiting
// request that may no longer go out is withdrawn or turned pending.
`default_nettype none

`include "common_macros.svh"

module pcie_cfg_msg #(
  parameter int unsigned MSIX_TABLE_SIZE = 8
) (
  input  logic                       clk_i,
  input  logic                       rst_i,
  input  pcie_cfg_pkg::t_ctrl        ctrl_q_i,
  input  pcie_cfg_pkg::t_ctrl        ctrl_d_i,
  input  logic [MSIX_TABLE_SIZE-1:0] mask_q_i,
  input  logic [MSIX_TABLE_SIZE-1:0] mask_d_i,
  input  pcie_cfg_pkg::t_err_due     due_i,
  input  pcie_cfg_pkg::t_entry       entry_i,      // the looked-up table entry
  input  logic                       bar2_take_i,  // a BAR2 access taken at this edge
  // Messages (to tl)
  output logic                       msg_valid_o,
  input  logic                       msg_ready_i,
  output logic [1:0]                 msg_kind_o,
  output logic [63:0]                msg_addr_o,
  output logic [31:0]                msg_data_o,
  // Interrupts (from the application)
  input  logic                       irq_valid_i,
  output logic                       irq_ready_o,
  input  logic [5:0]                 irq_vector_i,
  output logic                       irq_done_o,
  output logic [1:0]                 irq_status_o,
  // To the other blocks
  output logic [MSIX_TABLE_SIZE-1:0] pba_o,
  output logic                       look_o,
  output logic [5:0]                 look_vec_o,
  output pcie_cfg_pkg::t_err_due     take_err_o,
  output logic                       sse_set_o
);

  localparam int unsigned N = MSIX_TABLE_SIZE;

  pcie_cfg_pkg::t_offer offer_q, offer_d, cand;
  logic                 offer_valid_q, offer_valid_d;
  pcie_cfg_pkg::t_app   app_q, app_d;
  logic                 app_valid_q, app_valid_d;
  logic                 msi_pend_q, msi_pend_d;
  logic [N-1:0]         pba_q, pba_d;
  logic                 look_valid_q, look_valid_d;
  logic [5:0]           look_vec_q, look_vec_d;
  logic                 irq_ready_q, irq_ready_d;
  logic                 done_q, done_d;
  logic [1:0]           status_q, status_d;

  logic                 take;
  logic                 take_app;
  logic                 take_pmsi;
  logic [N-1:0]         clr_take;
  logic                 d0_q, d0_d;
  logic                 allowed_q, allowed_d;
  logic [N-1:0]         open_x_q, open_x_d;
  logic [63:0]          open_x_q64;
  logic [63:0]          open_x_d64;
  pcie_cfg_pkg::t_app   app_new;
  logic                 msi_open_q;
  logic [N-1:0]         ready_x;          // pending and allowed to go now
  logic [5:0]           first_x;
  logic                 cand_valid;
  logic                 cand_look;        // the candidate needs its table entry
  logic                 look_hit;
  logic                 load;
  logic                 kept;
  logic                 app_offered;
  logic                 app_live;
  logic                 app_send_d;
  logic                 app_kept_d;
  logic                 convert;
  logic                 irq_take;
  logic [N-1:0]         pba_t;
  logic [63:0]          pba_t64;
  logic                 msi_pend_t;
  logic [N-1:0]         set_pba;
  logic                 set_msi;
  logic                 irq_to_app;
  logic [N-1:0]         app_bit;
  logic [N-1:0]         irq_bit;

  // The take, apart from the block below: Signaled System Error and the
  // error take feed the other blocks, whose outputs that block reads.
  always_comb begin
    // ---- the take (msg_valid_o with msg_ready_i)
    take        = offer_valid_q && msg_ready_i;
    take_app    = take && offer_q.src == pcie_cfg_pkg::SRC_APP;
    take_pmsi   = take && offer_q.src == pcie_cfg_pkg::SRC_PEND_MSI;
    clr_take    = (take && offer_q.src == pcie_cfg_pkg::SRC_PEND_X)
                ? N'(64'd1 << offer_q.vector) : '0;
    take_err_o  = '0;
    if (take && offer_q.src == pcie_cfg_pkg::SRC_ERR) begin
      take_err_o.cor      = offer_q.kind == pcie_cfg_pkg::MSG_ERR_COR;
      take_err_o.nonfatal = offer_q.kind == pcie_cfg_pkg::MSG_ERR_NONFATAL;
      take_err_o.fatal    = offer_q.kind == pcie_cfg_pkg::MSG_ERR_FATAL;
    end
    sse_set_o   = ctrl_q_i.serr && (take_err_o.nonfatal || take_err_o.fatal);
  end

  always_comb begin
    // ---- when an interrupt may go out (before and after this edge)
    d0_q       = ctrl_q_i.d_state == pcie_cfg_pkg::D0;
    d0_d       = ctrl_d_i.d_state == pcie_cfg_pkg::D0;
    allowed_q  = ctrl_q_i.bme && d0_q;
    allowed_d  = ctrl_d_i.bme && d0_d;
    open_x_q   = {N{ctrl_q_i.msix_en && !ctrl_q_i.msix_fmask && allowed_q}} & ~mask_q_i;
    open_x_d   = {N{ctrl_d_i.msix_en && !ctrl_d_i.msix_fmask && allowed_d}} & ~mask_d_i;
    open_x_q64 = 64'(open_x_q);
    open_x_d64 = 64'(open_x_d);
    msi_open_q = ctrl_q_i.msi_en && !ctrl_q_i.msix_en && allowed_q;

    // ---- the candidate offer, by priority, from the state before the edge
    ready_x = pba_q & open_x_q;
    first_x = '0;
    for (int i = N - 1; i >= 0; i--) begin
      if (ready_x[i]) begin
        first_x = 6'(i);
      end
    end
    cand       = '0;
    cand_valid = 1'b0;
    cand_look  = 1'b0;
    if (d0_q) begin
      cand_valid = 1'b1;
      if (due_i.fatal) begin
        cand.kind = pcie_cfg_pkg::MSG_ERR_FATAL;
        cand.src  = pcie_cfg_pkg::SRC_ERR;
      end else if (due_i.nonfatal) begin
        cand.kind = pcie_cfg_pkg::MSG_ERR_NONFATAL;
        cand.src  = pcie_cfg_pkg::SRC_ERR;
      end else if (due_i.cor) begin
        cand.kind = pcie_cfg_pkg::MSG_ERR_COR;
        cand.src  = pcie_cfg_pkg::SRC_ERR;
      end else if (app_valid_q) begin
        cand.kind   = pcie_cfg_pkg::MSG_IRQ;
        cand.src    = pcie_cfg_pkg::SRC_APP;
        cand.msix   = app_q.msix;
        cand.vector = app_q.vector;
        cand_look   = app_q.msix;
      end else if (msi_pend_q && msi_open_q) begin
        cand.kind = pcie_cfg_pkg::MSG_IRQ;
        cand.src  = pcie_cfg_pkg::SRC_PEND_MSI;
      end else if (|ready_x) begin
        cand.kind   = pcie_cfg_pkg::MSG_IRQ;
        cand.src    = pcie_cfg_pkg::SRC_PEND_X;
        cand.msix   = 1'b1;
        cand.vector = first_x;
        cand_look   = 1'b1;
      end else begin
        cand_valid = 1'b0;
      end
    end
    if (cand.kind == pcie_cfg_pkg::MSG_IRQ) begin
      if (cand.msix) begin
        cand.addr = {entry_i.addr_hi, entry_i.addr_lo};
        cand.data = entry_i.data;
      end else begin
        cand.addr = ctrl_q_i.msi_addr;
        cand.data = {16'd0, ctrl_q_i.msi_data};
      end
    end

    // ---- loading the offer; an MSI-X write needs its entry looked up first
    look_hit   = look_valid_q && look_vec_q == cand.vector;
    load       = !offer_valid_q && cand_valid && (!cand_look || look_hit);
    look_o     = !offer_valid_q && cand_valid && cand_look && !look_hit && !bar2_take_i;
    look_vec_o = cand.vector;
    look_valid_d = look_o || (look_valid_q && !bar2_take_i && !load);
    look_vec_d   = look_o ? cand.vector : look_vec_q;

    // ---- withdrawal: kept while it may still go out (TL_CFG, Withdrawal)
    offer_d = load ? cand : offer_q;
    kept    = d0_d && (offer_d.src == pcie_cfg_pkg::SRC_ERR ||
                       (ctrl_d_i.bme && (offer_d.msix ? ctrl_d_i.msix_en : ctrl_d_i.msi_en)));
    offer_valid_d = (offer_valid_q ? !take : load) && kept;

    // ---- the application's request: withdrawn or no longer sendable
    app_offered = (offer_valid_q && offer_q.src == pcie_cfg_pkg::SRC_APP) ||
                  (load && cand.src == pcie_cfg_pkg::SRC_APP);
    app_live    = app_valid_q && !take_app;
    app_send_d  = app_q.msix ? open_x_d64[app_q.vector]
                             : (ctrl_d_i.msi_en && !ctrl_d_i.msix_en && allowed_d);
    app_kept_d  = allowed_d && (app_q.msix ? ctrl_d_i.msix_en : ctrl_d_i.msi_en);
    convert     = app_live && (app_offered ? !app_kept_d : !app_send_d);
    app_bit     = N'(64'd1 << app_q.vector);

    // ---- a new request (irq_ready_o 1 means no request is open)
    irq_take   = irq_valid_i && irq_ready_q;
    pba_t      = pba_q & ~clr_take;
    pba_t64    = 64'(pba_t);
    msi_pend_t = msi_pend_q && !take_pmsi;
    irq_bit    = N'(64'd1 << irq_vector_i);

    set_pba    = '0;
    set_msi    = 1'b0;
    irq_to_app = 1'b0;
    done_d     = 1'b0;
    status_d   = status_q;
    if (take_app) begin
      done_d   = 1'b1;
      status_d = pcie_cfg_pkg::IRQ_SENT;
    end
    if (convert) begin
      done_d   = 1'b1;
      status_d = pcie_cfg_pkg::IRQ_PENDING;
      if (app_q.msix) begin
        set_pba = app_bit;
      end else if (ctrl_d_i.msi_en) begin
        set_msi = 1'b1;
      end else if (ctrl_d_i.msix_en) begin
        set_pba = app_bit;
      end else begin
        status_d = pcie_cfg_pkg::IRQ_DROPPED;
      end
    end
    if (irq_take) begin
      if (ctrl_d_i.msix_en) begin
        if (pba_t64[irq_vector_i] || !open_x_d64[irq_vector_i]) begin
          set_pba  = irq_bit;
          done_d   = 1'b1;
          status_d = pcie_cfg_pkg::IRQ_PENDING;
        end else begin
          irq_to_app = 1'b1;
        end
      end else if (ctrl_d_i.msi_en) begin
        if (msi_pend_t || !allowed_d) begin
          set_msi  = 1'b1;
          done_d   = 1'b1;
          status_d = pcie_cfg_pkg::IRQ_PENDING;
        end else begin
          irq_to_app = 1'b1;
        end
      end else begin
        done_d   = 1'b1;
        status_d = pcie_cfg_pkg::IRQ_DROPPED;
      end
    end

    app_valid_d = (app_live && !convert) || irq_to_app;
    app_new.msix   = ctrl_d_i.msix_en;
    app_new.vector = irq_vector_i;
    app_d       = irq_to_app ? app_new : app_q;
    pba_d       = pba_t | set_pba;
    msi_pend_d  = (msi_pend_t || set_msi) && ctrl_d_i.msi_en;
    irq_ready_d = !(app_valid_d || irq_take || done_d);
  end

  `COMMON_DFF_EN(offer_q, offer_d, clk_i, load)
  `COMMON_DFF_RST(offer_valid_q, offer_valid_d, clk_i, rst_i)
  `COMMON_DFF_EN(app_q, app_d, clk_i, irq_to_app)
  `COMMON_DFF_RST(app_valid_q, app_valid_d, clk_i, rst_i)
  `COMMON_DFF_RST(msi_pend_q, msi_pend_d, clk_i, rst_i)
  `COMMON_DFF_RST(pba_q, pba_d, clk_i, rst_i)
  `COMMON_DFF_RST(look_valid_q, look_valid_d, clk_i, rst_i)
  `COMMON_DFF_EN(look_vec_q, look_vec_d, clk_i, look_o)
  `COMMON_DFF_RST(irq_ready_q, irq_ready_d, clk_i, rst_i)
  `COMMON_DFF_RST(done_q, done_d, clk_i, rst_i)
  `COMMON_DFF_EN(status_q, status_d, clk_i, done_d)

  assign msg_valid_o  = offer_valid_q;
  assign msg_kind_o   = offer_q.kind;
  assign msg_addr_o   = offer_q.addr;
  assign msg_data_o   = offer_q.data;
  assign irq_ready_o  = irq_ready_q;
  assign irq_done_o   = done_q;
  assign irq_status_o = status_q;
  assign pba_o        = pba_q;

  `COMMON_UNUSED_BITS(unused_ctrl, {ctrl_q_i.mse, ctrl_q_i.per, ctrl_q_i.cere, ctrl_q_i.nfere,
                                    ctrl_q_i.fere, ctrl_q_i.urre, ctrl_d_i.mse, ctrl_d_i.per,
                                    ctrl_d_i.serr, ctrl_d_i.msi_addr, ctrl_d_i.msi_data,
                                    ctrl_d_i.cere, ctrl_d_i.nfere, ctrl_d_i.fere, ctrl_d_i.urre})

  // The application obligation (APP, Interrupts): a vector in the table.
  `COMMON_ASSERT_NEVER(a_irq_vector, clk_i, rst_i,
                       irq_take && ({2'd0, irq_vector_i} >= 8'(MSIX_TABLE_SIZE)))
  // No message outside D0; a request is open only while irq_ready_o is 0.
  `COMMON_ASSERT_NEVER(a_no_offer_outside_d0, clk_i, rst_i, offer_valid_q && !d0_q)
  `COMMON_ASSERT_NEVER(a_ready_idle, clk_i, rst_i, irq_ready_q && (app_valid_q || done_q))
  // A waiting request may go out (the edge it could not, it turned pending).
  `COMMON_ASSERT_NEVER(a_app_sendable, clk_i, rst_i,
                       app_valid_q && !(offer_valid_q && offer_q.src == pcie_cfg_pkg::SRC_APP) &&
                       !(app_q.msix ? open_x_q64[app_q.vector] : msi_open_q))
  // An offer holds its fields until it is taken or withdrawn.
  `COMMON_ASSERT(a_offer_stable, clk_i, rst_i,
                 offer_valid_q && !msg_ready_i |=> !offer_valid_q || $stable(offer_q))
  // The MSI pending flag exists only under MSI Enable.
  `COMMON_ASSERT_NEVER(a_msi_pend_en, clk_i, rst_i, msi_pend_q && !ctrl_q_i.msi_en)

endmodule

`default_nettype wire
