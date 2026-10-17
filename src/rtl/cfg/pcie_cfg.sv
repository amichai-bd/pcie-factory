// PCI Express configuration space: Type 0 header, capabilities, MSI-X
// table and pending bits, error status and messages, interrupt resolution.
// Interfaces and behavior: wiki/src/arch/cfg/HAS.md and its register and
// capability pages; the tl side: wiki/src/arch/core/TL_CFG.md; structure:
// wiki/src/rtl/cfg/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The register file arrives in wave 2.
`default_nettype none

module pcie_cfg #(
  parameter int unsigned BAR0_ADDR_W      = 12,        // 12..28: BAR0 is 2**BAR0_ADDR_W bytes
  parameter int unsigned MSIX_TABLE_SIZE  = 8,         // 1..64 MSI-X vectors
  parameter logic [15:0] VENDOR_ID        = 16'h0000,  // placeholder, claims no assigned vendor
  parameter logic [15:0] DEVICE_ID        = 16'h0000,  // placeholder
  parameter logic [15:0] SUBSYS_VENDOR_ID = 16'h0000,  // placeholder
  parameter logic [15:0] SUBSYS_ID        = 16'h0000,  // placeholder
  parameter logic [23:0] CLASS_CODE       = 24'hFF0000, // base class FFh: fits no defined class
  parameter logic [7:0]  REVISION_ID      = 8'h00
) (
  // Clock and reset
  input  logic        clk_i,            // core clock, 125 MHz
  input  logic        rst_i,            // synchronous, active high; core: rst_i or data link down // cdc-domain: clk_i

  // Register access (from tl)
  input  logic        reg_req_valid_i,  // cdc-domain: clk_i
  output logic        reg_req_ready_o,
  input  logic        reg_req_bar2_i,   // 0 configuration space, 1 BAR2 memory // cdc-domain: clk_i
  input  logic        reg_req_write_i,  // cdc-domain: clk_i
  input  logic [9:0]  reg_req_addr_i,   // DW index // cdc-domain: clk_i
  input  logic [3:0]  reg_req_be_i,     // cdc-domain: clk_i
  input  logic [31:0] reg_req_data_i,   // byte 0 in [7:0] // cdc-domain: clk_i
  input  logic [7:0]  reg_req_bus_i,    // configuration write: Bus Number // cdc-domain: clk_i
  input  logic [4:0]  reg_req_dev_i,    // configuration write: Device Number // cdc-domain: clk_i
  output logic        reg_cpl_valid_o,
  output logic [31:0] reg_cpl_data_o,

  // Register state (to tl; the last three also to the application)
  output logic [7:0]  bus_num_o,
  output logic [4:0]  dev_num_o,
  output logic [31:0] bar0_base_o,
  output logic [31:0] bar2_base_o,
  output logic        mem_space_en_o,
  output logic        bus_master_en_o,
  output logic [1:0]  d_state_o,        // 0 D0, 3 D3hot

  // Error events
  input  logic [8:0]  tl_err_i,         // one-cycle pulses, bit order in TL_CFG // cdc-domain: clk_i
  input  logic [6:0]  dl_err_i,         // one-cycle pulses, bit order in the dll HAS // cdc-domain: clk_i
  input  logic        rcv_err_i,        // one-cycle pulse per receiver error // cdc-domain: clk_i

  // Messages (to tl)
  output logic        msg_valid_o,
  input  logic        msg_ready_i,      // cdc-domain: clk_i
  output logic [1:0]  msg_kind_o,       // 0 interrupt write, 1 ERR_COR, 2 ERR_NONFATAL, 3 ERR_FATAL
  output logic [63:0] msg_addr_o,
  output logic [31:0] msg_data_o,

  // Interrupts (from the application)
  input  logic        irq_valid_i,      // cdc-domain: clk_i
  output logic        irq_ready_o,
  input  logic [5:0]  irq_vector_i,     // cdc-domain: clk_i
  output logic        irq_done_o,
  output logic [1:0]  irq_status_o,     // 0 sent, 1 pending, 2 dropped

  // Function status (to the application)
  output logic [2:0]  max_payload_o,    // Device Control encoding, 0 is 128 bytes
  output logic [2:0]  max_read_req_o    // Device Control encoding, reset 2 (512 bytes)
);

  // Inputs and parameters the shell does not use yet; the wave-2 register
  // file consumes them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, rst_i, reg_req_valid_i, reg_req_bar2_i, reg_req_write_i,
                           reg_req_addr_i, reg_req_be_i, reg_req_data_i, reg_req_bus_i,
                           reg_req_dev_i, tl_err_i, dl_err_i, rcv_err_i, msg_ready_i,
                           irq_valid_i, irq_vector_i,
                           BAR0_ADDR_W[0], MSIX_TABLE_SIZE[0], VENDOR_ID, DEVICE_ID,
                           SUBSYS_VENDOR_ID, SUBSYS_ID, CLASS_CODE, REVISION_ID};

  // Reset values from the HAS.
  assign reg_req_ready_o = 1'b0;
  assign reg_cpl_valid_o = 1'b0;
  assign reg_cpl_data_o  = 32'd0;
  assign bus_num_o       = 8'd0;
  assign dev_num_o       = 5'd0;
  assign bar0_base_o     = 32'd0;
  assign bar2_base_o     = 32'd0;
  assign mem_space_en_o  = 1'b0;
  assign bus_master_en_o = 1'b0;
  assign d_state_o       = 2'd0;
  assign msg_valid_o     = 1'b0;
  assign msg_kind_o      = 2'd0;
  assign msg_addr_o      = 64'd0;
  assign msg_data_o      = 32'd0;
  assign irq_ready_o     = 1'b0;
  assign irq_done_o      = 1'b0;
  assign irq_status_o    = 2'd0;
  assign max_payload_o   = 3'd0;
  assign max_read_req_o  = 3'd2;

endmodule

`default_nettype wire
