`default_nettype none

// Registered inverter.
module xy_b (
    input  logic       clk_i,
    input  logic       rst_i,
    input  logic [7:0] d_i,
    output logic [7:0] q_o
);
  always_ff @(posedge clk_i) begin
    if (rst_i) q_o <= '0;
    else q_o <= ~d_i;
  end
endmodule

`default_nettype wire
