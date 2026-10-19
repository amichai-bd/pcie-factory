// Registered output stage with a skid slot
// (wiki/src/rtl/common/common_skid_stage.md): out, the beat offered
// downstream, and skid, which takes the upstream beat in a cycle where out
// is held. Upstream is ready while skid is empty, a register, so out_ready_i
// reaches no upstream logic; a downstream that takes a beat every cycle
// keeps skid empty, so beats still flow with no gap. Every output comes
// from flops, except out_take_o, which also reads out_ready_i.
`default_nettype none

`include "common_macros.svh"

module common_skid_stage #(
  parameter int unsigned WIDTH = 32
) (
  input  logic             clk_i,
  input  logic             rst_i,
  input  logic             in_valid_i,
  output logic             in_ready_o,
  input  logic [WIDTH-1:0] in_data_i,
  output logic             out_valid_o,
  input  logic             out_ready_i,
  output logic [WIDTH-1:0] out_data_o,
  output logic             out_take_o,    // out taken at this edge
  output logic             out_new_o      // out offered for the first cycle (not held from the last)
);

  `COMMON_ASSERT_PARAM(g_width_check, WIDTH >= 1, "common_skid_stage: WIDTH must be at least 1")

  logic             out_valid_q, out_valid_d;
  logic [WIDTH-1:0] out_q, out_d;
  logic             skid_valid_q, skid_valid_d;
  logic [WIDTH-1:0] skid_q, skid_d;
  logic             held_q, held_d;       // out was offered and not taken at the last edge

  logic in_take;

  assign in_ready_o = !skid_valid_q;
  assign in_take    = in_valid_i && in_ready_o;
  assign out_take_o = out_valid_q && out_ready_i;
  assign out_new_o  = out_valid_q && !held_q;

  always_comb begin
    out_valid_d  = out_valid_q;
    out_d        = out_q;
    skid_valid_d = skid_valid_q;
    skid_d       = skid_q;
    held_d       = out_valid_q && !out_ready_i;
    if (!out_valid_q || out_take_o) begin
      // out is free at this edge: the skid beat first, else the upstream one.
      if (skid_valid_q) begin
        out_valid_d  = 1'b1;
        out_d        = skid_q;
        skid_valid_d = 1'b0;
      end else begin
        out_valid_d  = in_take;
        out_d        = in_data_i;
      end
    end else if (in_take) begin
      skid_valid_d = 1'b1;
      skid_d       = in_data_i;
    end
  end

  `COMMON_DFF_RST(out_valid_q, out_valid_d, clk_i, rst_i)
  `COMMON_DFF_RST(out_q, out_d, clk_i, rst_i)
  `COMMON_DFF_RST(skid_valid_q, skid_valid_d, clk_i, rst_i)
  `COMMON_DFF_RST(skid_q, skid_d, clk_i, rst_i)
  `COMMON_DFF_RST(held_q, held_d, clk_i, rst_i)

  assign out_valid_o = out_valid_q;
  assign out_data_o  = out_q;

  // An offered beat never changes until taken; skid fills only while out
  // is held.
  `COMMON_ASSERT_STABLE_WHEN(a_out_held, clk_i, rst_i,
                             out_valid_q && !out_ready_i, {out_valid_q, out_q})
  `COMMON_ASSERT_NEVER(a_skid_unheld, clk_i, rst_i, skid_valid_q && !out_valid_q)

endmodule

`default_nettype wire
