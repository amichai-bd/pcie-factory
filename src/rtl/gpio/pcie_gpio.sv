// GPIO data-link-boundary adapter: the phy packet stream and link status
// that pcie_dll uses, carried to a partner pcie_gpio over 6 pins per
// direction (4 data, 1 control, 1 forwarded clock), one 16-bit beat per
// 4-transfer slot.
// Interfaces and behavior: wiki/src/arch/gpio/HAS.md; structure:
// wiki/src/rtl/gpio/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The adapter logic arrives with #378.
`default_nettype none

`include "common_macros.svh"

module pcie_gpio #(
  parameter int unsigned CLK_DIV = 2  // even, 2..64: clk_i cycles per transfer
) (
  // Clocks, reset and strap
  input  logic        clk_i,             // core clock
  input  logic        rst_i,             // synchronous to clk_i, active high // cdc-domain: clk_i
  input  logic        port_role_i,       // 0 downstream, 1 upstream; static outside reset // cdc-domain: clk_i

  // Upper packet stream, transmit (dll to adapter)
  input  logic        pkt_tx_valid_i,    // cdc-domain: clk_i
  output logic        pkt_tx_ready_o,
  input  logic [15:0] pkt_tx_data_i,     // two packet bytes, [7:0] first // cdc-domain: clk_i
  input  logic        pkt_tx_sop_i,      // cdc-domain: clk_i
  input  logic        pkt_tx_eop_i,      // cdc-domain: clk_i
  input  logic        pkt_tx_dllp_i,     // 0 TLP, 1 DLLP; held from sop to eop // cdc-domain: clk_i

  // Upper packet stream, receive (adapter to dll; no ready)
  output logic        pkt_rx_valid_o,
  output logic [15:0] pkt_rx_data_o,     // two packet bytes, [7:0] first
  output logic        pkt_rx_sop_o,
  output logic        pkt_rx_eop_o,
  output logic        pkt_rx_dllp_o,
  output logic        pkt_rx_err_o,      // with eop: the packet is bad or cut
  output logic        pkt_rx_nullify_o,  // always 0: no EDB on this transport

  // Link status and control
  output logic        link_up_o,
  output logic [5:0]  ltssm_state_o,     // 0x00, 0x04, 0x19 or 0x20 (mac encoding)
  output logic        rcv_err_o,         // one-cycle pulse per receive error
  input  logic        hot_reset_req_i,   // level, downstream role only // cdc-domain: clk_i

  // Transmit pins
  output logic        gpio_tx_clk_o,     // forwarded clock, clk_i / CLK_DIV
  output logic        gpio_tx_ctrl_o,
  output logic [3:0]  gpio_tx_data_o,

  // Receive pins (gpio_rx_clk_i)
  input  logic        gpio_rx_clk_i,     // the partner's forwarded clock
  input  logic        gpio_rx_ctrl_i,    // cdc-domain: gpio_rx_clk_i
  input  logic [3:0]  gpio_rx_data_i     // cdc-domain: gpio_rx_clk_i
);

  `COMMON_ASSERT_PARAM(g_bad_clk_div, CLK_DIV >= 2 && CLK_DIV <= 64 && CLK_DIV % 2 == 0,
                       "CLK_DIV must be even, 2..64")

  // Inputs the shell does not use yet; the adapter logic consumes them.
  logic unused_clk_inputs;
  logic unused_rx_inputs;
  assign unused_clk_inputs = ^{clk_i, rst_i, port_role_i, pkt_tx_valid_i, pkt_tx_data_i,
                               pkt_tx_sop_i, pkt_tx_eop_i, pkt_tx_dllp_i, hot_reset_req_i};
  assign unused_rx_inputs  = ^{gpio_rx_clk_i, gpio_rx_ctrl_i, gpio_rx_data_i};

  // Reset values from the HAS.
  assign pkt_tx_ready_o   = 1'b0;
  assign pkt_rx_valid_o   = 1'b0;
  assign pkt_rx_data_o    = 16'd0;
  assign pkt_rx_sop_o     = 1'b0;
  assign pkt_rx_eop_o     = 1'b0;
  assign pkt_rx_dllp_o    = 1'b0;
  assign pkt_rx_err_o     = 1'b0;
  assign pkt_rx_nullify_o = 1'b0;
  assign link_up_o        = 1'b0;
  assign ltssm_state_o    = 6'h00;
  assign rcv_err_o        = 1'b0;
  assign gpio_tx_clk_o    = 1'b0;
  assign gpio_tx_ctrl_o   = 1'b0;
  assign gpio_tx_data_o   = 4'd0;

endmodule

`default_nettype wire
