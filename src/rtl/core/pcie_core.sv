// PCI Express Endpoint: pcie_tl and pcie_cfg over pcie_link.
// Interfaces and behavior: wiki/src/arch/core/HAS.md, APP.md and
// TL_CFG.md; structure: wiki/src/rtl/core/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The stack integration arrives in wave 3.
`default_nettype none

module pcie_core #(
  parameter int unsigned FC_PH            = 8,         // posted header credits advertised, 1..127
  parameter int unsigned FC_PD            = 32,        // posted data credits, 8..2047
  parameter int unsigned FC_NPH           = 8,         // non-posted header credits, 1..127
  parameter int unsigned FC_NPD           = 8,         // non-posted data credits, 2..2047
  parameter int unsigned TIMEOUT_DIV      = 1,         // 1..1024, passed to link; 1 for silicon
  parameter int unsigned ERR_TIMEOUT_DIV  = 1,         // 1..1024, passed to link; 1 for silicon
  parameter int unsigned CPL_TIMEOUT_DIV  = 1,         // 1..1024, divides the tl completion timeout; 1 for silicon
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
  input  logic                   clk_i,             // core clock, 125 MHz
  input  logic                   rst_i,             // synchronous to clk_i, active high // cdc-domain: clk_i

  // Completer requests (host to application, BAR0)
  output logic                   cq_valid_o,
  input  logic                   cq_ready_i,        // cdc-domain: clk_i
  output logic                   cq_sop_o,
  output logic                   cq_eop_o,
  output logic [31:0]            cq_data_o,         // write data, [7:0] lowest address
  output logic                   cq_write_o,        // 1 write, 0 read
  output logic [BAR0_ADDR_W-1:0] cq_addr_o,         // byte offset of the first DW in BAR0
  output logic [10:0]            cq_len_o,          // DWs, 1..1024
  output logic [3:0]             cq_first_be_o,
  output logic [3:0]             cq_last_be_o,
  output logic [2:0]             cq_handle_o,       // read handle, 0..7

  // Completer completions (application to host)
  input  logic                   cc_valid_i,        // cdc-domain: clk_i
  output logic                   cc_ready_o,
  input  logic                   cc_sop_i,          // cdc-domain: clk_i
  input  logic                   cc_eop_i,          // cdc-domain: clk_i
  input  logic [31:0]            cc_data_i,         // cdc-domain: clk_i
  input  logic [2:0]             cc_handle_i,       // cdc-domain: clk_i

  // Requester requests (application as bus master)
  input  logic                   rq_valid_i,        // cdc-domain: clk_i
  output logic                   rq_ready_o,
  input  logic                   rq_sop_i,          // cdc-domain: clk_i
  input  logic                   rq_eop_i,          // cdc-domain: clk_i
  input  logic [31:0]            rq_data_i,         // cdc-domain: clk_i
  input  logic                   rq_write_i,        // cdc-domain: clk_i
  input  logic [63:0]            rq_addr_i,         // cdc-domain: clk_i
  input  logic [5:0]             rq_len_i,          // DWs, 1..32 // cdc-domain: clk_i
  input  logic [3:0]             rq_first_be_i,     // cdc-domain: clk_i
  input  logic [3:0]             rq_last_be_i,      // cdc-domain: clk_i
  input  logic [2:0]             rq_tag_i,          // cdc-domain: clk_i

  // Requester completions (host to application)
  output logic                   rc_valid_o,
  input  logic                   rc_ready_i,        // cdc-domain: clk_i
  output logic                   rc_sop_o,
  output logic                   rc_eop_o,
  output logic [31:0]            rc_data_o,
  output logic [2:0]             rc_tag_o,
  output logic [1:0]             rc_status_o,       // 0 success, 1 UR, 2 CA, 3 failed
  output logic                   rc_done_o,         // with eop: the read is finished

  // Interrupts
  input  logic                   irq_valid_i,       // cdc-domain: clk_i
  output logic                   irq_ready_o,
  input  logic [5:0]             irq_vector_i,      // cdc-domain: clk_i
  output logic                   irq_done_o,
  output logic [1:0]             irq_status_o,      // 0 sent, 1 pending, 2 dropped

  // Function status
  output logic                   bus_master_en_o,
  output logic                   mem_space_en_o,
  output logic [1:0]             d_state_o,         // 0 D0, 3 D3hot
  output logic [2:0]             max_payload_o,     // Device Control encoding, 0 is 128 bytes
  output logic [2:0]             max_read_req_o,    // Device Control encoding, reset 2 (512 bytes)

  // Data link status
  output logic                   dl_up_o,
  output logic [1:0]             dl_state_o,        // 0 DL_Inactive, 1 FC_INIT1, 2 FC_INIT2, 3 DL_Active
  output logic [6:0]             dl_err_o,          // one-cycle error pulses, bit order in the dll HAS

  // Physical link status
  output logic                   link_up_o,
  output logic [5:0]             ltssm_state_o,
  output logic                   rcv_err_o,         // one-cycle pulse per receiver error

  // Lower 2x10-bit symbol interface, transmit (clk_i)
  output logic [19:0]            ser_tx_data_o,     // [9:0] first symbol, bit 0 first on the line
  output logic                   ser_tx_elec_idle_o,
  input  logic                   ser_tx_rxdet_i,    // far-end receiver termination present // cdc-domain: clk_i

  // Lower 2x10-bit symbol interface, receive (ser_rx_clk_i)
  input  logic                   ser_rx_clk_i,      // recovered clock, 125 MHz nominal
  input  logic [19:0]            ser_rx_data_i,     // bit 0 earliest; no symbol alignment // cdc-domain: ser_rx_clk_i
  input  logic                   ser_rx_elec_idle_i // the line is in electrical idle // cdc-domain: ser_rx_clk_i
);

  // Inputs and parameters the shell does not use yet; the wave-3
  // integration consumes them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, rst_i, cq_ready_i,
                           cc_valid_i, cc_sop_i, cc_eop_i, cc_data_i, cc_handle_i,
                           rq_valid_i, rq_sop_i, rq_eop_i, rq_data_i, rq_write_i, rq_addr_i,
                           rq_len_i, rq_first_be_i, rq_last_be_i, rq_tag_i,
                           rc_ready_i, irq_valid_i, irq_vector_i, ser_tx_rxdet_i,
                           FC_PH[0], FC_PD[0], FC_NPH[0], FC_NPD[0], TIMEOUT_DIV[0],
                           ERR_TIMEOUT_DIV[0], CPL_TIMEOUT_DIV[0], MSIX_TABLE_SIZE[0],
                           VENDOR_ID, DEVICE_ID, SUBSYS_VENDOR_ID, SUBSYS_ID, CLASS_CODE,
                           REVISION_ID};
  logic unused_rx_inputs;
  assign unused_rx_inputs = ^{ser_rx_clk_i, ser_rx_data_i, ser_rx_elec_idle_i};

  // Reset values from the HAS: link down, function in reset.
  assign cq_valid_o         = 1'b0;
  assign cq_sop_o           = 1'b0;
  assign cq_eop_o           = 1'b0;
  assign cq_data_o          = 32'd0;
  assign cq_write_o         = 1'b0;
  assign cq_addr_o          = '0;
  assign cq_len_o           = 11'd0;
  assign cq_first_be_o      = 4'd0;
  assign cq_last_be_o       = 4'd0;
  assign cq_handle_o        = 3'd0;
  assign cc_ready_o         = 1'b0;
  assign rq_ready_o         = 1'b0;
  assign rc_valid_o         = 1'b0;
  assign rc_sop_o           = 1'b0;
  assign rc_eop_o           = 1'b0;
  assign rc_data_o          = 32'd0;
  assign rc_tag_o           = 3'd0;
  assign rc_status_o        = 2'd0;
  assign rc_done_o          = 1'b0;
  assign irq_ready_o        = 1'b0;
  assign irq_done_o         = 1'b0;
  assign irq_status_o       = 2'd0;
  assign bus_master_en_o    = 1'b0;
  assign mem_space_en_o     = 1'b0;
  assign d_state_o          = 2'd0;
  assign max_payload_o      = 3'd0;
  assign max_read_req_o     = 3'd2;
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
