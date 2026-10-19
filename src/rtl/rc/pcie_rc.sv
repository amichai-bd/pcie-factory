// PCI Express root-port stack: pcie_rc_tl and pcie_rc_seq over pcie_link
// in the downstream role. Interfaces and behavior: wiki/src/arch/rc/HAS.md
// and TL_SEQ.md; structure: wiki/src/rtl/rc/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The stack integration arrives in wave 3.
`default_nettype none

module pcie_rc #(
  parameter int unsigned FC_PH           = 8,              // posted header credits advertised, 1..127
  parameter int unsigned FC_PD           = 32,             // posted data credits, 8..2047
  parameter int unsigned FC_NPH          = 8,              // non-posted header credits, 1..127
  parameter int unsigned FC_NPD          = 8,              // non-posted data credits, 2..2047
  parameter int unsigned TIMEOUT_DIV     = 1,              // 1..1024, passed to link; 1 for silicon
  parameter int unsigned ERR_TIMEOUT_DIV = 1,              // 1..1024, passed to link; 1 for silicon
  parameter int unsigned CPL_TIMEOUT_DIV = 1,              // 1..1024, divides the rc_tl completion timeout
  parameter int unsigned HOST_MEM_ADDR_W = 12,             // 8..16: host memory is 2**HOST_MEM_ADDR_W bytes
  parameter logic [31:0] HOST_MEM_BASE   = 32'h0000_0000,  // system address of host memory, size aligned
  parameter logic [31:0] MSI_ADDR        = 32'hFEE0_0000,  // system address of the 4 KiB MSI window
  parameter logic [15:0] VENDOR_ID       = 16'h0000,       // placeholder, claims no assigned vendor
  parameter logic [15:0] DEVICE_ID       = 16'h0000,       // placeholder
  parameter logic [7:0]  REVISION_ID     = 8'h00
) (
  // Clock and reset
  input  logic        clk_i,              // core clock, 125 MHz
  input  logic        rst_i,              // synchronous to clk_i, active high // cdc-domain: clk_i

  // Sequencer
  input  logic        start_i,            // a rising edge starts a run when none is in progress // cdc-domain: clk_i
  output logic        done_o,             // the last run has ended
  output logic        pass_o,             // with done_o: the run passed
  output logic [15:0] fail_step_o,        // with done_o and not pass_o: ROM index of the failing step

  // Data link status
  output logic        dl_up_o,
  output logic [1:0]  dl_state_o,         // 0 DL_Inactive, 1 FC_INIT1, 2 FC_INIT2, 3 DL_Active
  output logic [6:0]  dl_err_o,           // one-cycle error pulses, bit order in the dll HAS

  // Physical link status
  output logic        link_up_o,
  output logic [5:0]  ltssm_state_o,
  output logic        rcv_err_o,          // one-cycle pulse per receiver error

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0] ser_tx_data_o,      // [9:0] first symbol, bit 0 first on the line
  output logic        ser_tx_elec_idle_o,
  input  logic        ser_tx_rxdet_i,     // far-end receiver termination present // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic        ser_rx_clk_i,       // recovered clock, 125 MHz nominal
  input  logic [19:0] ser_rx_data_i,      // bit 0 earliest; no symbol alignment // cdc-domain: ser_rx_clk_i
  input  logic        ser_rx_elec_idle_i  // the line is in electrical idle // cdc-domain: ser_rx_clk_i
);

  // Inputs and parameters the shell does not use yet; the wave-3
  // integration consumes them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, rst_i, start_i, ser_tx_rxdet_i,
                           FC_PH[0], FC_PD[0], FC_NPH[0], FC_NPD[0], TIMEOUT_DIV[0],
                           ERR_TIMEOUT_DIV[0], CPL_TIMEOUT_DIV[0], HOST_MEM_ADDR_W[0],
                           HOST_MEM_BASE, MSI_ADDR, VENDOR_ID, DEVICE_ID, REVISION_ID};
  logic unused_rx_inputs;
  assign unused_rx_inputs = ^{ser_rx_clk_i, ser_rx_data_i, ser_rx_elec_idle_i};

  // Reset values from the HAS: no run has ended, link down.
  assign done_o             = 1'b0;
  assign pass_o             = 1'b0;
  assign fail_step_o        = 16'd0;
  assign dl_up_o            = 1'b0;
  assign dl_state_o         = 2'd0;
  assign dl_err_o           = 7'd0;
  assign link_up_o          = 1'b0;
  assign ltssm_state_o      = 6'd0;
  assign rcv_err_o          = 1'b0;
  assign ser_tx_data_o      = 20'd0;
  assign ser_tx_elec_idle_o = 1'b1;

endmodule

`default_nettype wire
