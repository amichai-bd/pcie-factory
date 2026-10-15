`default_nettype none

// Incrementer.
module xy_a_x (
    input  logic [7:0] a_i,
    output logic [7:0] y_o
);
  assign y_o = a_i + 8'd1;
endmodule

`default_nettype wire
