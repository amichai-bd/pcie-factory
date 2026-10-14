`default_nettype none

// Counter: increments while en_i is high.
module xy_blk
  import xy_blk_pkg::*;
(
    input  logic   clk_i,
    input  logic   rst_i,
    input  logic   en_i,
    output t_count count_o
);
  t_count next_d;
  t_count count_q;

  xy_blk_sub #(.W(W)) u_inc (.a_i(count_q), .y_o(next_d));
  common_flop #(.W(W)) u_q (
      .clk_i(clk_i), .rst_i(rst_i), .en_i(en_i), .d_i(wrap(next_d)), .q_o(count_q)
  );

  always_comb begin
    count_o = count_q;
  end
endmodule

`default_nettype wire
