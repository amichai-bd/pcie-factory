`default_nettype none

`include "common_macros.svh"

// Registered incrementer.
module xy_a (
    input  logic       clk_i,
    input  logic       rst_i,
    input  logic [7:0] d_i,
    output logic [7:0] q_o
);
  logic [7:0] inc_d;

  xy_a_x u_inc (.a_i(d_i), .y_o(inc_d));

  `COMMON_DFF_RST(q_o, inc_d, clk_i, rst_i)
endmodule

`default_nettype wire
