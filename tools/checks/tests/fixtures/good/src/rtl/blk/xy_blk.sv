`default_nettype none

// Counter: increments while en_i is high.
module xy_blk (
    input  logic              clk_i,
    input  logic              rst_i,
    input  logic              en_i,
    output xy_blk_pkg::t_count count_o
);
  xy_blk_pkg::t_count next_d;
  xy_blk_pkg::t_count wrapped;
  xy_blk_pkg::t_count count_q;

  xy_blk_sub #(.W(xy_blk_pkg::W)) u_inc (.a_i(count_q), .y_o(next_d));
  assign wrapped = xy_blk_pkg::wrap(next_d);
  common_flop #(.W(xy_blk_pkg::W)) u_q (
      .clk_i(clk_i), .rst_i(rst_i), .en_i(en_i), .d_i(wrapped), .q_o(count_q)
  );

  always_comb begin
    count_o = count_q;
  end
endmodule

`default_nettype wire
