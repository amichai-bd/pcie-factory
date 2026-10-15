`default_nettype none

// Board wrapper: active-low key as reset, LEDs show the count.
module xy_blk_board (
    input  logic               clk_i,
    input  logic               rst_ni,
    output xy_blk_pkg::t_count led_o
);
  xy_blk u_blk (.clk_i(clk_i), .rst_i(!rst_ni), .en_i(1'b1), .count_o(led_o));
endmodule

`default_nettype wire
