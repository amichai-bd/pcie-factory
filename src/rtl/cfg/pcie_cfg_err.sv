// pcie_cfg errors: each event pulse sets its status bits, takes its class
// (correctable, non-fatal, fatal, with advisory events as correctable) and
// decides on an error message from the Device Control and Command enables
// as they stand after this edge's write; one due flag per message kind
// (wiki/src/arch/cfg/HAS.md#errors, wiki/src/rtl/cfg/MAS.md#errors).
`default_nettype none

`include "common_macros.svh"

module pcie_cfg_err (
  input  logic                   clk_i,
  input  logic                   rst_i,
  input  logic [8:0]             tl_err_i,
  input  logic [6:0]             dl_err_i,
  input  logic                   rcv_err_i,
  input  pcie_cfg_pkg::t_ctrl    ctrl_d_i,    // the enables after this edge's write
  input  pcie_cfg_pkg::t_err_due take_i,      // the error message taken at this edge
  output pcie_cfg_pkg::t_sts     sts_set_o,   // sse is the message block's
  output pcie_cfg_pkg::t_err_due due_o
);

  logic ur_nf;        // tl_err 0: posted Unsupported Request, non-fatal
  logic ur_cor;       // tl_err 1: non-posted Unsupported Request, advisory
  logic nf_plain;     // non-fatal events with no UR gate
  logic cor_plain;    // correctable events with no UR gate
  logic fatal;
  logic d0;
  logic nf_en;
  logic fatal_en;
  pcie_cfg_pkg::t_err_due want;
  pcie_cfg_pkg::t_err_due due_q, due_d;

  always_comb begin
    ur_nf     = tl_err_i[0];
    ur_cor    = tl_err_i[1];
    nf_plain  = tl_err_i[3] | tl_err_i[4] | tl_err_i[6];
    cor_plain = tl_err_i[5] | (|dl_err_i[3:0]) | rcv_err_i;
    fatal     = tl_err_i[2] | (|dl_err_i[6:4]);

    sts_set_o      = '0;
    sts_set_o.dpe  = tl_err_i[3] | tl_err_i[4];
    sts_set_o.mdpe = tl_err_i[4] & ctrl_d_i.per;
    sts_set_o.rma  = tl_err_i[7];
    sts_set_o.rta  = tl_err_i[8];
    sts_set_o.urd  = ur_nf | ur_cor;
    sts_set_o.ced  = cor_plain | ur_cor;
    sts_set_o.nfed = nf_plain | ur_nf;
    sts_set_o.fed  = fatal;

    // Message decision; nothing is decided outside D0.
    d0            = ctrl_d_i.d_state == pcie_cfg_pkg::D0;
    nf_en         = ctrl_d_i.nfere | ctrl_d_i.serr;
    fatal_en      = ctrl_d_i.fere | ctrl_d_i.serr;
    want.cor      = ctrl_d_i.cere & (cor_plain | (ur_cor & ctrl_d_i.urre));
    want.nonfatal = nf_en & (nf_plain | (ur_nf & ctrl_d_i.urre));
    want.fatal    = fatal_en & fatal;

    // A decided message merges with one of its kind still due; leaving D0
    // discards every due message.
    due_d = ((due_q & ~take_i) | want) & {3{d0}};
  end

  `COMMON_DFF_RST(due_q, due_d, clk_i, rst_i)

  assign due_o = due_q;

  `COMMON_UNUSED_BITS(unused_ctrl, {ctrl_d_i.mse, ctrl_d_i.bme, ctrl_d_i.msi_en, ctrl_d_i.msi_addr,
                                    ctrl_d_i.msi_data, ctrl_d_i.msix_en, ctrl_d_i.msix_fmask})

  // A take clears only a due kind.
  `COMMON_ASSERT_NEVER(a_take_due, clk_i, rst_i, (take_i & ~due_q) != 3'd0)

endmodule

`default_nettype wire
