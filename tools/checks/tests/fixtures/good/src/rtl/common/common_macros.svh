// Fixture vocabulary header: the only file that may hold always_ff and <=.
`ifndef COMMON_MACROS_SVH
`define COMMON_MACROS_SVH
`define COMMON_DFF_RST_EN(Q, D, CLK, ENABLE, RESET, VALUE) \
  always_ff @(posedge (CLK)) begin \
    if (RESET) begin \
      Q <= VALUE; \
    end else if (ENABLE) begin \
      Q <= D; \
    end \
  end
`endif
