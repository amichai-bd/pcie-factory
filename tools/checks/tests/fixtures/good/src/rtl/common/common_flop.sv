`default_nettype none

`include "common_macros.svh"

// Enabled register with synchronous active-high reset.
module common_flop #(
    parameter int W = 8
) (
    input  logic         clk_i,
    input  logic         rst_i,
    input  logic         en_i,
    input  logic [W-1:0] d_i,
    output logic [W-1:0] q_o
);
  `COMMON_DFF_RST_EN(q_o, d_i, clk_i, en_i, rst_i, '0)
endmodule

`default_nettype wire
