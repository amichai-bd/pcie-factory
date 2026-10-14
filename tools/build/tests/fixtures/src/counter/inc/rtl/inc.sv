module inc #(
    parameter int W = 8
) (
    input  logic [W-1:0] a,
    output logic [W-1:0] y
);
  assign y = a + 1'b1;
endmodule
