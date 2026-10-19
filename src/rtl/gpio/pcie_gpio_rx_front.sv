// pcie_gpio receive front end (wiki/src/rtl/gpio/MAS.md#receive-front-end):
// the gpio_rx_clk_i domain. It captures each transfer on the rising edge
// of the partner's forwarded clock, cuts the transfers into slots at its
// slot phase, hunts for the phase while the link state is not aligned, and
// carries each slot into clk_i through a common_cdc_fifo.
`default_nettype none

`include "common_macros.svh"

module pcie_gpio_rx_front (
  input  logic                    clk_i,
  input  logic                    rst_i,           // cdc-domain: clk_i
  input  logic                    aligned_i,       // the link state's alignment flop // cdc-domain: clk_i
  input  logic                    gpio_rx_clk_i,
  input  logic                    gpio_rx_ctrl_i,  // cdc-domain: gpio_rx_clk_i
  input  logic [3:0]              gpio_rx_data_i,  // cdc-domain: gpio_rx_clk_i
  output logic                    slot_valid_o,    // clk_i: a slot this cycle
  output pcie_gpio_pkg::t_rx_slot slot_o           // clk_i
);

  // --------------------------------------------------------- receive reset

  // Asserts with rst_i, with no clock edge (the partner's clock may be
  // stopped); releases on the second gpio_rx_clk_i edge after rst_i falls
  // (HAS, clocks and resets). Every flop of the domain takes it
  // asynchronously.
  logic rx_rst;

  `COMMON_RESET_SYNC(u_rx_rst, rx_rst, rst_i, gpio_rx_clk_i)

  // The alignment flag, synchronized: 0 hunts for the slot phase.
  logic rx_lock;

  `COMMON_METAFLOP_ARST(u_lock_sync, rx_lock, aligned_i, gpio_rx_clk_i, rx_rst)

  // ------------------------------------------------------- capture and cut

  // The last four transfers, oldest at transfer position 0, in the layout of
  // a slot; ph_q is the position of the newest one in its slot.
  pcie_gpio_pkg::t_rx_slot win_q;
  pcie_gpio_pkg::t_rx_slot win_d;
  logic [1:0]              ph_q;
  logic [1:0]              ph_d;
  logic                    win_marker;  // the window reads 1, 0, 0, 0 on the control pin
  logic                    boundary;    // the window is a slot
  logic                    jump;        // hunting: a marker window at another phase
  pcie_gpio_pkg::t_rx_slot push_slot;
  logic                    push_ready;

  assign win_marker = win_q.ctrl == 4'b0001;
  assign jump       = ~rx_lock & win_marker & (ph_q != 2'd3);
  assign boundary   = (ph_q == 2'd3) | jump;
  assign ph_d       = boundary ? 2'd0 : ph_q + 2'd1;

  always_comb begin
    win_d.new_phase = 1'b0;
    win_d.ctrl      = {gpio_rx_ctrl_i, win_q.ctrl[3:1]};
    win_d.data      = {gpio_rx_data_i, win_q.data[15:4]};
  end

  always_comb begin
    push_slot           = win_q;
    push_slot.new_phase = jump;
  end

  `COMMON_DFF_ARST_VAL(win_q, win_d, gpio_rx_clk_i, rx_rst, '0)
  `COMMON_DFF_ARST_VAL(ph_q, ph_d, gpio_rx_clk_i, rx_rst, 2'd0)

  // ------------------------------------------------------------- crossing

  // At most one slot per 4 gpio_rx_clk_i edges once locked, against one
  // taken per clk_i cycle: the FIFO never fills (HAS, clock contract).
  logic [pcie_gpio_pkg::RX_ADDR_W:0] fill;
  pcie_gpio_pkg::t_rx_slot           next_slot;

  common_cdc_fifo #(
    .WIDTH          ($bits(pcie_gpio_pkg::t_rx_slot)),
    .ADDR_W         (pcie_gpio_pkg::RX_ADDR_W),
    .SRC_ASYNC_RESET(1'b1)
  ) u_fifo (
    .src_clk_i      (gpio_rx_clk_i),
    .src_rst_i      (rx_rst),
    .src_valid_i    (boundary),
    .src_ready_o    (push_ready),
    .src_data_i     (push_slot),
    .dst_clk_i      (clk_i),
    .dst_rst_i      (rst_i),
    .dst_valid_o    (slot_valid_o),
    .dst_ready_i    (1'b1),
    .dst_data_o     (slot_o),
    .dst_fill_o     (fill),
    .dst_next_data_o(next_slot)
  );

  `COMMON_UNUSED_BITS(unused_fifo, {fill, next_slot})

  // The receive buffer never fills (HAS, clock contract).
  `COMMON_ASSERT_NEVER_ARST(a_rx_no_overflow, gpio_rx_clk_i, rx_rst, boundary & ~push_ready)

endmodule

`default_nettype wire
