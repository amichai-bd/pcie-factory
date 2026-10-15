// 8b/10b physical coding sublayer: PIPE (16-bit, clk_i) above, the
// 2x10-bit symbol interface below. Interfaces and behavior:
// wiki/src/arch/pcs/HAS.md; structure: wiki/src/rtl/pcs/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. Wave-2 sub-IPs are instantiated as they arrive, their outputs held
// unused until the top (#85) connects them; the crossing cells come with it.
`default_nettype none

module pcie_pcs (
  // Clock and reset
  input  logic        clk_i,                // core clock and PCLK, 125 MHz
  input  logic        rst_i,                // cdc-domain: clk_i

  // PIPE, MAC to PCS (clk_i)
  input  logic        pipe_reset_ni,        // cdc-domain: clk_i
  input  logic [15:0] pipe_tx_data_i,       // cdc-domain: clk_i
  input  logic [1:0]  pipe_tx_datak_i,      // cdc-domain: clk_i
  input  logic        pipe_tx_detect_rx_i,  // cdc-domain: clk_i
  input  logic        pipe_tx_elec_idle_i,  // cdc-domain: clk_i
  input  logic        pipe_tx_compliance_i, // cdc-domain: clk_i
  input  logic        pipe_rx_polarity_i,   // cdc-domain: clk_i
  input  logic [1:0]  pipe_power_down_i,    // cdc-domain: clk_i

  // PIPE, PCS to MAC (clk_i)
  output logic [15:0] pipe_rx_data_o,       // [7:0] is the earlier byte
  output logic [1:0]  pipe_rx_datak_o,
  output logic        pipe_rx_valid_o,
  output logic        pipe_phy_status_o,
  output logic        pipe_rx_elec_idle_o,
  output logic [2:0]  pipe_rx_status_o,

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0] ser_tx_data_o,        // [9:0] first symbol, bit 0 first on the line
  output logic        ser_tx_elec_idle_o,
  input  logic        ser_tx_rxdet_i,       // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic        ser_rx_clk_i,         // recovered clock, 125 MHz nominal
  input  logic [19:0] ser_rx_data_i,        // cdc-domain: ser_rx_clk_i
  input  logic        ser_rx_elec_idle_i    // cdc-domain: ser_rx_clk_i
);

  // Transmit encoder (pcs_enc8b10b), reset with the PCS clk_i side. Its
  // outputs are not connected to ser_tx_* until the top (#85).
  logic        tx_rst;
  logic        tx_valid;
  logic        enc_valid;
  logic [19:0] enc_data;

  assign tx_rst   = rst_i || !pipe_reset_ni;
  assign tx_valid = (pipe_power_down_i == 2'd0) && !pipe_tx_elec_idle_i;  // P0, not idle

  pcie_pcs_enc8b10b u_enc (
    .clk_i        (clk_i),
    .rst_i        (tx_rst),
    .valid_i      (tx_valid),
    .data_i       (pipe_tx_data_i),
    .datak_i      (pipe_tx_datak_i),
    .compliance_i (pipe_tx_compliance_i),
    .valid_o      (enc_valid),
    .data_o       (enc_data)
  );

  // Inputs and sub-IP outputs the shell does not use yet; the wave-2 RTL
  // consumes them.
  logic unused_inputs;
  assign unused_inputs = ^{pipe_tx_detect_rx_i, pipe_rx_polarity_i,
                           ser_tx_rxdet_i, ser_rx_clk_i,
                           ser_rx_data_i, ser_rx_elec_idle_i, enc_valid,
                           enc_data};

  // Reset values from the HAS: PhyStatus high, no received data, receiver
  // and transmitter in electrical idle.
  assign pipe_rx_data_o      = 16'd0;
  assign pipe_rx_datak_o     = 2'd0;
  assign pipe_rx_valid_o     = 1'b0;
  assign pipe_phy_status_o   = 1'b1;
  assign pipe_rx_elec_idle_o = 1'b1;
  assign pipe_rx_status_o    = 3'd0;
  assign ser_tx_data_o       = 20'd0;
  assign ser_tx_elec_idle_o  = 1'b1;

endmodule

`default_nettype wire
