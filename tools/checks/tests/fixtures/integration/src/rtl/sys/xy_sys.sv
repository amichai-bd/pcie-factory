`default_nettype none

// Integration: a, then b.
module xy_sys (
    input  logic       clk_i,
    input  logic       rst_i,
    input  logic [7:0] d_i,
    output logic [7:0] q_o
);
  logic [7:0] mid_q;

  xy_a u_a (.clk_i(clk_i), .rst_i(rst_i), .d_i(d_i), .q_o(mid_q));
  xy_b u_b (.clk_i(clk_i), .rst_i(rst_i), .d_i(mid_q), .q_o(q_o));
endmodule

`default_nettype wire
