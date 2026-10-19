`default_nettype none

// Pin checker of the gpio unit DV (wiki/src/dv/gpio/DV_PLAN.md): one
// adapter's transmit pins (HAS, pins and transfers and reset values).
// At each clk_i edge it looks at the pins as the previous edge left them:
// after a reset edge all three are 0; otherwise the control and data pins
// changed only if the forwarded clock fell at that edge, or that edge was
// the first after reset (the first transfer starts with the clock still
// low).
module pcie_gpio_pin_check (
  input  logic       clk_i,
  input  logic       rst_i,
  input  logic       tclk_i,
  input  logic       ctrl_i,
  input  logic [3:0] data_i
);

  logic       tclk_q;
  logic       ctrl_q;
  logic [3:0] data_q;
  logic       rst_q;
  logic       rst_qq;
  logic       moved;

  assign moved = (ctrl_i != ctrl_q) || (data_i != data_q);

  always_ff @(posedge clk_i) begin
    if (rst_q) begin
      assert (!tclk_i && !ctrl_i && data_i == 4'd0)
        else $fatal(1, "%m: transmit pins not at their reset values after a reset edge");
    end else if (!rst_qq) begin
      assert (!moved || (tclk_q && !tclk_i))
        else $fatal(1, "%m: a control or data pin changed where the forwarded clock did not fall");
    end
    tclk_q <= tclk_i;
    ctrl_q <= ctrl_i;
    data_q <= data_i;
    rst_q  <= rst_i;
    rst_qq <= rst_q;
  end

endmodule

`default_nettype wire
