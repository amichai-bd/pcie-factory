`default_nettype none

`include "common_macros.svh"

// Registered inverter.
module xy_b (
    input  logic       clk_i,
    input  logic       rst_i,
    input  logic [7:0] d_i,
    output logic [7:0] q_o
);
  logic [7:0] inv_d;

  assign inv_d = ~d_i;
  `COMMON_DFF_RST(q_o, inv_d, clk_i, rst_i)
endmodule

`default_nettype wire
