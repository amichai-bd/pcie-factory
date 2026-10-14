`default_nettype none

module xy_blk (
    input  logic clk_i,
    output logic q_o
);
  always_ff @(posedge clk_i) q_o <= !q_o;
endmodule

`default_nettype wire
