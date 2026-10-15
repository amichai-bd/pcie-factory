`default_nettype none

// Board wrapper: active-low key as reset, switches in, LEDs out.
module xy_b_board (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic [7:0] sw_i,
    output logic [7:0] led_o
);
  xy_b u_b (.clk_i(clk_i), .rst_i(!rst_ni), .d_i(sw_i), .q_o(led_o));
endmodule

`default_nettype wire
