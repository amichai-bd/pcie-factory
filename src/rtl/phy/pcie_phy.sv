// Gen1 x1 physical-layer stack: pcie_mac and pcie_pcs joined by PIPE.
// Interfaces and behavior: wiki/src/arch/phy/HAS.md; structure:
// wiki/src/rtl/phy/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The mac and pcs instances arrive with the stack integration.
`default_nettype none

module pcie_phy #(
  parameter int unsigned TIMEOUT_DIV = 1  // divides every LTSSM timeout; 1 for silicon
) (
  // Clocks, reset and strap
  input  logic        clk_i,             // core clock and PCLK, 125 MHz
  input  logic        rst_i,             // synchronous to clk_i, active high
  input  logic        port_role_i,       // 0 downstream, 1 upstream; static outside reset

  // Upper framed-packet stream, transmit (upper layer to link)
  input  logic        pkt_tx_valid_i,
  output logic        pkt_tx_ready_o,
  input  logic [15:0] pkt_tx_data_i,     // [7:0] is the earlier byte
  input  logic        pkt_tx_sop_i,
  input  logic        pkt_tx_eop_i,
  input  logic        pkt_tx_dllp_i,     // tag: 0 TLP, 1 DLLP; held for the packet
  input  logic        pkt_tx_nullify_i,  // on the eop beat of a TLP: end with EDB

  // Upper framed-packet stream, receive (link to upper layer)
  output logic        pkt_rx_valid_o,
  output logic [15:0] pkt_rx_data_o,     // [7:0] is the earlier byte
  output logic        pkt_rx_sop_o,
  output logic        pkt_rx_eop_o,
  output logic        pkt_rx_dllp_o,
  output logic        pkt_rx_err_o,      // on the eop beat: discard the packet

  // Link control and status
  output logic        link_up_o,
  output logic [5:0]  ltssm_state_o,
  output logic        rcv_err_o,         // one-cycle pulse per receiver error
  input  logic        hot_reset_req_i,
  input  logic        disable_req_i,
  input  logic        loopback_req_i,
  input  logic [1:0]  pm_state_req_i,    // 0 L0, 1 L0s allowed, 2 L1, 3 L2

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0] ser_tx_data_o,     // [9:0] first symbol, bit 0 first on the line
  output logic        ser_tx_elec_idle_o,
  input  logic        ser_tx_rxdet_i,    // far-end receiver termination present

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic        ser_rx_clk_i,      // recovered clock, 125 MHz nominal
  input  logic [19:0] ser_rx_data_i,     // bit 0 earliest; no symbol alignment
  input  logic        ser_rx_elec_idle_i
);

  // Inputs the shell does not use yet; the stack integration consumes them.
  logic unused_inputs;
  assign unused_inputs = ^{rst_i, port_role_i, pkt_tx_valid_i, pkt_tx_data_i,
                           pkt_tx_sop_i, pkt_tx_eop_i, pkt_tx_dllp_i,
                           pkt_tx_nullify_i, hot_reset_req_i, disable_req_i,
                           loopback_req_i, pm_state_req_i, ser_tx_rxdet_i,
                           ser_rx_clk_i, ser_rx_data_i, ser_rx_elec_idle_i,
                           clk_i, TIMEOUT_DIV[0]};

  // Reset values from the HAS: no packet traffic, link down in Detect.Quiet
  // (code 0), transmitter in electrical idle.
  assign pkt_tx_ready_o     = 1'b0;
  assign pkt_rx_valid_o     = 1'b0;
  assign pkt_rx_data_o      = 16'd0;
  assign pkt_rx_sop_o       = 1'b0;
  assign pkt_rx_eop_o       = 1'b0;
  assign pkt_rx_dllp_o      = 1'b0;
  assign pkt_rx_err_o       = 1'b0;
  assign link_up_o          = 1'b0;
  assign ltssm_state_o      = 6'd0;
  assign rcv_err_o          = 1'b0;
  assign ser_tx_data_o      = 20'd0;
  assign ser_tx_elec_idle_o = 1'b1;

endmodule

`default_nettype wire
