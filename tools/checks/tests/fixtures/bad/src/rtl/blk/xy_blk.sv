// Bad fixture: no `default_nettype, plain always, wire, port suffixes,
// an inline lint pragma, and an unused input (Verilator UNUSEDSIGNAL).
module xy_blk (
    input  logic       clk,
    input  logic [3:0] d_i,
    output logic       q_o
);
  wire unused_w;
  /* verilator lint_off UNDRIVEN */
  always @* q_o = d_i[0];
endmodule
