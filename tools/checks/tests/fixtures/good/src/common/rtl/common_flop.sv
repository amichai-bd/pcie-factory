`default_nettype none

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
  always_ff @(posedge clk_i) begin
    if (rst_i) q_o <= '0;
    else if (en_i) q_o <= d_i;
  end
endmodule

`default_nettype wire
