`default_nettype none

// Incrementer.
module xy_blk_sub #(
    parameter int W = 8
) (
    input  logic [W-1:0] a_i,
    output logic [W-1:0] y_o
);
  assign y_o = a_i + W'(1);
endmodule

`default_nettype wire
