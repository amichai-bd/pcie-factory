// pcie_gpio transmit path (wiki/src/rtl/gpio/MAS.md#transmit): the accept
// side of the dll packet stream, the transmit buffer and the slot
// serializer that drives the six transmit pins.
`default_nettype none

`include "common_macros.svh"

module pcie_gpio_tx #(
  parameter int unsigned CLK_DIV = 2  // even, 2..64: clk_i cycles per transfer
) (
  input  logic                    clk_i,
  input  logic                    rst_i,

  // What to send between packets (the link state, registered).
  input  pcie_gpio_pkg::t_tx_mode mode_i,
  output pcie_gpio_pkg::t_tx_sent sent_o,          // at a slot start: the word sent

  // dll transmit stream (HAS, transmit).
  input  logic                    pkt_tx_valid_i,
  output logic                    pkt_tx_ready_o,
  input  logic [15:0]             pkt_tx_data_i,
  input  logic                    pkt_tx_sop_i,
  input  logic                    pkt_tx_eop_i,
  input  logic                    pkt_tx_dllp_i,

  // Transmit pins: flop outputs.
  output logic                    gpio_tx_clk_o,
  output logic                    gpio_tx_ctrl_o,
  output logic [3:0]              gpio_tx_data_o
);

  localparam int unsigned DIV_W    = 6;               // CLK_DIV - 1 <= 63
  localparam int unsigned OCC_W    = pcie_gpio_pkg::TX_ADDR_W + 2;
  // Ready between packets: room for a maximum packet.
  localparam int unsigned ROOM_MAX = pcie_gpio_pkg::TX_DEPTH - pcie_gpio_pkg::MAX_PKT_BEATS;

  `COMMON_ASSERT_PARAM(g_tx_depth, pcie_gpio_pkg::TX_DEPTH >= pcie_gpio_pkg::MAX_PKT_BEATS
                       && pcie_gpio_pkg::TX_DEPTH <= 2 ** pcie_gpio_pkg::TX_ADDR_W,
                       "TX_DEPTH must hold a maximum packet and fit the buffer RAM")

  // ------------------------------------------------------------ accept side

  // In a packet: its sop was taken and its eop not yet. Dropping: the rest
  // of the packet is taken and discarded (valid fell inside it, or the link
  // left up).
  logic in_pkt_q;
  logic in_pkt_d;
  logic drop_q;
  logic drop_d;
  logic take;
  logic room;
  logic push;
  pcie_gpio_pkg::t_slot push_slot;
  pcie_gpio_pkg::t_code sop_code;
  logic [OCC_W-1:0] occ;          // buffered beats, the head included

  assign room           = occ <= OCC_W'(ROOM_MAX);
  assign pkt_tx_ready_o = in_pkt_q | (mode_i.up & room);
  assign take           = pkt_tx_valid_i & pkt_tx_ready_o;
  assign sop_code       = pkt_tx_eop_i
                        ? (pkt_tx_dllp_i ? pcie_gpio_pkg::CODE_ONE_DLLP : pcie_gpio_pkg::CODE_ONE_TLP)
                        : (pkt_tx_dllp_i ? pcie_gpio_pkg::CODE_SOP_DLLP : pcie_gpio_pkg::CODE_SOP_TLP);

  always_comb begin
    in_pkt_d       = in_pkt_q;
    drop_d         = drop_q;
    push           = 1'b0;
    push_slot.code = pcie_gpio_pkg::CODE_MID;
    push_slot.data = pkt_tx_data_i;
    if (!in_pkt_q) begin
      // Between packets ready needs up, so a beat taken is a sop and goes in.
      if (take) begin
        push           = 1'b1;
        push_slot.code = sop_code;
        in_pkt_d       = !pkt_tx_eop_i;
        drop_d         = 1'b0;
      end
    end else if (pkt_tx_valid_i) begin
      push           = mode_i.up & !drop_q;
      push_slot.code = pkt_tx_eop_i ? pcie_gpio_pkg::CODE_EOP : pcie_gpio_pkg::CODE_MID;
      if (pkt_tx_eop_i) begin
        in_pkt_d = 1'b0;
        drop_d   = 1'b0;
      end else if (!mode_i.up) begin
        drop_d = 1'b1;
      end
    end else begin
      // Valid fell inside the packet: an ABORT ends it in place of its eop.
      push           = mode_i.up & !drop_q;
      push_slot.code = pcie_gpio_pkg::CODE_ABORT;
      push_slot.data = 16'd0;
      drop_d         = 1'b1;
    end
  end

  `COMMON_DFF_RST(in_pkt_q, in_pkt_d, clk_i, rst_i)
  `COMMON_DFF_RST(drop_q, drop_d, clk_i, rst_i)

  // --------------------------------------------------------- transmit buffer

  // Runs only while up: leaving up discards every buffered beat.
  logic                                  buf_rst;
  logic                                  pop;
  logic                                  head_valid;
  pcie_gpio_pkg::t_slot                  head;
  logic                                  buf_empty;
  logic                                  buf_full;
  logic [pcie_gpio_pkg::TX_ADDR_W:0]     ram_used;

  assign buf_rst = rst_i | ~mode_i.up;
  assign occ     = OCC_W'(ram_used) + OCC_W'(head_valid);

  common_sdp_ram_fifo #(
    .WIDTH  ($bits(pcie_gpio_pkg::t_slot)),
    .ADDR_W (pcie_gpio_pkg::TX_ADDR_W)
  ) u_buf (
    .clk_i        (clk_i),
    .rst_i        (buf_rst),
    .push_i       (push),
    .push_data_i  (push_slot),
    .pop_i        (pop),
    .head_valid_o (head_valid),
    .head_o       (head),
    .empty_o      (buf_empty),
    .full_o       (buf_full),
    .ram_used_o   (ram_used)
  );

  // ------------------------------------------------------------- serializer

  // div_q counts the clk_i cycles of a transfer; a transfer starts at the
  // edge where it is 0 and the forwarded clock rises at CLK_DIV/2. xfer_q is
  // the transfer that starts next (0 starts a slot).
  logic [DIV_W-1:0]     div_q;
  logic [DIV_W-1:0]     div_d;
  logic [1:0]           xfer_q;
  logic [1:0]           xfer_d;
  logic                 xfer_start;
  logic                 clk_rise;
  logic                 slot_start;
  pcie_gpio_pkg::t_slot slot_q;       // the slot on the pins
  pcie_gpio_pkg::t_slot slot_d;
  pcie_gpio_pkg::t_slot next_slot;    // chosen at a slot start
  pcie_gpio_pkg::t_slot cur_slot;     // the slot whose transfer starts now
  logic                 pins_open_q;  // a packet's sop is on the pins, its end not yet
  logic                 pins_open_d;
  logic                 tx_clk_q;
  logic                 tx_clk_d;
  logic                 ctrl_q;
  logic                 ctrl_d;
  logic [3:0]           data_q;
  logic [3:0]           data_d;
  logic [3:0]           code_bits;    // control pin per transfer: marker, then the code

  assign xfer_start = div_q == '0;
  assign clk_rise   = div_q == DIV_W'(CLK_DIV / 2);
  assign slot_start = xfer_start & (xfer_q == 2'd0);
  assign div_d      = (div_q == DIV_W'(CLK_DIV - 1)) ? '0 : div_q + 1'b1;
  assign xfer_d     = xfer_start ? xfer_q + 2'd1 : xfer_q;

  // The next slot (HAS, slots and transmit; LINK_UP.md, states): an ABORT
  // first when the link left up inside a packet on the pins, else a
  // buffered beat or IDLE while up, else HOT_RESET or TRAIN.
  always_comb begin
    next_slot.code = pcie_gpio_pkg::CODE_IDLE;
    next_slot.data = pcie_gpio_pkg::WORD_TRAIN;
    pop            = 1'b0;
    sent_o         = '0;
    if (pins_open_q && !mode_i.up) begin
      next_slot.code = pcie_gpio_pkg::CODE_ABORT;
      next_slot.data = 16'd0;
    end else if (mode_i.up && head_valid) begin
      next_slot = head;
      pop       = slot_start;
    end else if (mode_i.up) begin
      next_slot.data = pcie_gpio_pkg::WORD_IDLE;
    end else if (mode_i.hot_reset) begin
      next_slot.data   = pcie_gpio_pkg::WORD_HOT_RESET;
      sent_o.hot_reset = slot_start;
    end else if (mode_i.bit8) begin
      next_slot.data = pcie_gpio_pkg::WORD_TRAIN_OK;
    end else begin
      sent_o.train_clear = slot_start;
    end
  end

  always_comb begin
    pins_open_d = pins_open_q;
    if (slot_start) begin
      unique case (next_slot.code)
        pcie_gpio_pkg::CODE_SOP_TLP,
        pcie_gpio_pkg::CODE_SOP_DLLP: pins_open_d = 1'b1;
        pcie_gpio_pkg::CODE_EOP,
        pcie_gpio_pkg::CODE_ABORT,
        pcie_gpio_pkg::CODE_ONE_TLP,
        pcie_gpio_pkg::CODE_ONE_DLLP: pins_open_d = 1'b0;
        default:                      pins_open_d = pins_open_q;
      endcase
    end
  end

  assign slot_d    = slot_start ? next_slot : slot_q;
  assign cur_slot  = slot_start ? next_slot : slot_q;
  assign code_bits = {cur_slot.code, 1'b1};

  // Pins change only at a transfer start, as the forwarded clock falls.
  always_comb begin
    ctrl_d   = ctrl_q;
    data_d   = data_q;
    tx_clk_d = tx_clk_q;
    if (xfer_start) begin
      ctrl_d   = code_bits[xfer_q];
      data_d   = cur_slot.data[{xfer_q, 2'b00} +: 4];
      tx_clk_d = 1'b0;
    end else if (clk_rise) begin
      tx_clk_d = 1'b1;
    end
  end

  `COMMON_DFF_RST(div_q, div_d, clk_i, rst_i)
  `COMMON_DFF_RST(xfer_q, xfer_d, clk_i, rst_i)
  `COMMON_DFF_EN(slot_q, slot_d, clk_i, slot_start)
  `COMMON_DFF_RST(pins_open_q, pins_open_d, clk_i, rst_i)
  `COMMON_DFF_RST(tx_clk_q, tx_clk_d, clk_i, rst_i)
  `COMMON_DFF_RST(ctrl_q, ctrl_d, clk_i, rst_i)
  `COMMON_DFF_RST(data_q, data_d, clk_i, rst_i)

  assign gpio_tx_clk_o  = tx_clk_q;
  assign gpio_tx_ctrl_o = ctrl_q;
  assign gpio_tx_data_o = data_q;

  `COMMON_UNUSED_BITS(unused_buf, {buf_empty, buf_full})

  // Invariants (MAS, transmit).
  // Between packets the dll offers only a sop; inside one, never a sop.
  `COMMON_ASSERT_NEVER(a_sop_between, clk_i, rst_i, take & ~in_pkt_q & ~pkt_tx_sop_i)
  `COMMON_ASSERT_NEVER(a_no_sop_inside, clk_i, rst_i, in_pkt_q & pkt_tx_valid_i & pkt_tx_sop_i)
  // A packet never outgrows the buffer: room for a maximum packet at its sop.
  `COMMON_ASSERT_NEVER(a_buf_room, clk_i, rst_i, push & (occ >= OCC_W'(pcie_gpio_pkg::TX_DEPTH)))
  // Cut-through: inside a packet on the pins a beat is always buffered.
  `COMMON_ASSERT_NEVER(a_beat_ready, clk_i, rst_i,
                       slot_start & mode_i.up & pins_open_q & ~head_valid)

endmodule

`default_nettype wire
