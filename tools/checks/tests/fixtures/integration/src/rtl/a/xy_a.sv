`default_nettype none

// Registered incrementer.
module xy_a (
    input  logic       clk_i,
    input  logic       rst_i,
    input  logic [7:0] d_i,
    output logic [7:0] q_o
);
  logic [7:0] inc_d;

  xy_a_x u_inc (.a_i(d_i), .y_o(inc_d));

  always_ff @(posedge clk_i) begin
    if (rst_i) q_o <= '0;
    else q_o <= inc_d;
  end
endmodule

`default_nettype wire
