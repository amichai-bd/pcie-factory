// pcie_rc_tl MSI capture (u_msi) (wiki/src/rtl/rc_tl/INBOUND.md#msi-capture):
// four t_rc_msi entries in flops, in arrival order, the oldest offered on
// msi_o while any is held. A capture arriving full is dropped and sets
// error record bit 12; a take at the same edge makes room. msi_valid_o
// comes from flops and rst_i only, never from msi_ready_i, which rc_seq
// may drive from msi_valid_o (TL_SEQ.md, MSI capture). Reset by rst only.
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_msi (
  input  logic                clk_i,
  input  logic                rst_i,       // rst only: the capture survives link down
  input  logic                push_i,      // a capture at this edge
  input  common_pkg::t_rc_msi msi_i,
  output logic                drop_o,      // it was dropped (error record bit 12)
  output logic                msi_valid_o,
  input  logic                msi_ready_i,
  output common_pkg::t_rc_msi msi_o
);

  localparam int unsigned DEPTH = pcie_rc_tl_pkg::MSI_DEPTH;

  pcie_rc_tl_pkg::t_msi_st st_q, st_d;

  logic       pop;
  logic [2:0] held;          // entries left after the pop

  assign pop    = (st_q.cnt != 3'd0) && msi_ready_i;
  assign held   = st_q.cnt - {2'd0, pop};
  assign drop_o = push_i && (held == 3'(DEPTH));

  always_comb begin
    st_d = st_q;
    if (pop) begin
      // The oldest leaves; the others move up, the vacated entry is 0.
      for (int i = 0; i < DEPTH - 1; i++) begin
        st_d.ent[i] = st_q.ent[i + 1];
      end
      st_d.ent[DEPTH - 1] = '0;
    end
    st_d.cnt = held;
    if (push_i && !drop_o) begin
      st_d.ent[held[1:0]] = msi_i;
      st_d.cnt = held + 3'd1;
    end
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)

  // rst_i holds msi_valid at 0 at once (TL_SEQ.md, Signals).
  assign msi_valid_o = !rst_i && (st_q.cnt != 3'd0);
  assign msi_o       = rst_i ? '0 : st_q.ent[0];

  `COMMON_ASSERT_NEVER(a_cnt_range, clk_i, rst_i, st_q.cnt > 3'(DEPTH))
  // An empty capture offers 0.
  `COMMON_ASSERT(a_empty_zero, clk_i, rst_i, (st_q.cnt != 3'd0) || (st_q.ent[0] == '0))

endmodule

`default_nettype wire
