`include "counter_defs.svh"

`ifndef COUNTER_FIXTURE
`COUNTER_FIXTURE_define_missing_from_filelist
`endif

module counter #(
    parameter int W = `COUNTER_WIDTH
) (
    input  logic         clk,
    input  logic         rst,
    input  logic         en,
    output logic [W-1:0] q
);
  logic [W-1:0] nxt;

  inc #(.W(W)) u_inc (.a(q), .y(nxt));

  always_ff @(posedge clk) begin
    if (rst) q <= '0;
    else if (en) q <= nxt;
  end

  // en is held low during reset. Only tests/test_negative.py breaks this,
  // to prove that simulation builds enable assertions (--assert).
  always_ff @(posedge clk) begin
    if (rst) assert (!en) else $fatal(1, "en_low_in_reset failed");
  end
endmodule
