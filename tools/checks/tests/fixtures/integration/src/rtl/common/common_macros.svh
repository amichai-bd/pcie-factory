// Fixture vocabulary header: the only file that may hold always_ff and <=.
`ifndef COMMON_MACROS_SVH
`define COMMON_MACROS_SVH
`define COMMON_DFF_RST(Q, D, CLK, RESET) \
  always_ff @(posedge (CLK)) begin \
    if (RESET) begin \
      Q <= '0; \
    end else begin \
      Q <= D; \
    end \
  end
`endif
