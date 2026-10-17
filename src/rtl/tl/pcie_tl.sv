// PCI Express transaction layer of the Endpoint: TLP parsing and routing,
// completions, requester tags and completion timeout, TLP transmit.
// Interfaces and behavior: wiki/src/arch/tl/HAS.md and its pages;
// structure: wiki/src/rtl/tl/MAS.md.
//
// Wave-1 shell: the ports of the HAS with every output tied to its reset
// value. The receive path, the transmit path and the completion tracker
// arrive in wave 2.
`default_nettype none

module pcie_tl #(
  parameter int unsigned FC_PH           = 8,   // posted header credits advertised, 1..127
  parameter int unsigned FC_PD           = 32,  // posted data credits, 8..2047
  parameter int unsigned FC_NPH          = 8,   // non-posted header credits, 1..127
  parameter int unsigned FC_NPD          = 8,   // non-posted data credits, 2..2047
  parameter int unsigned CPL_TIMEOUT_DIV = 1,   // 1..1024, divides the completion timeout; 1 for silicon
  parameter int unsigned BAR0_ADDR_W     = 12   // 12..28: BAR0 is 2**BAR0_ADDR_W bytes
) (
  // Clock and reset
  input  logic                   clk_i,             // core clock, 125 MHz
  input  logic                   rst_i,             // synchronous to clk_i, active high // cdc-domain: clk_i

  // Link: upper TLP stream, transmit
  output logic                   tlp_tx_valid_o,
  input  logic                   tlp_tx_ready_i,    // cdc-domain: clk_i
  output logic [31:0]            tlp_tx_data_o,     // one DW, [7:0] the first byte
  output logic                   tlp_tx_sop_o,
  output logic                   tlp_tx_eop_o,

  // Link: upper TLP stream, receive
  input  logic                   tlp_rx_valid_i,    // cdc-domain: clk_i
  output logic                   tlp_rx_ready_o,    // 1 whenever dl_up_i is 1
  input  logic [31:0]            tlp_rx_data_i,     // cdc-domain: clk_i
  input  logic                   tlp_rx_sop_i,      // cdc-domain: clk_i
  input  logic                   tlp_rx_eop_i,      // cdc-domain: clk_i

  // Link: credits available and data link status
  input  logic [7:0]             fc_ph_avail_i,     // all ones: infinite // cdc-domain: clk_i
  input  logic [11:0]            fc_pd_avail_i,     // cdc-domain: clk_i
  input  logic [7:0]             fc_nph_avail_i,    // cdc-domain: clk_i
  input  logic [11:0]            fc_npd_avail_i,    // cdc-domain: clk_i
  input  logic [7:0]             fc_cplh_avail_i,   // cdc-domain: clk_i
  input  logic [11:0]            fc_cpld_avail_i,   // cdc-domain: clk_i
  input  logic                   dl_up_i,           // 0: tl held in reset // cdc-domain: clk_i

  // Link: credit release (the link input arrives with #303)
  output logic                   fc_rel_p_o,        // one posted request left tl: 1 header credit
  output logic [3:0]             fc_rel_pd_o,       // and these posted data credits, 0..8
  output logic                   fc_rel_np_o,       // one non-posted request left tl: 1 header credit
  output logic [3:0]             fc_rel_npd_o,      // and these non-posted data credits, 0..8

  // cfg: register access
  output logic                   reg_req_valid_o,
  input  logic                   reg_req_ready_i,   // cdc-domain: clk_i
  output logic                   reg_req_bar2_o,    // 0 configuration, 1 BAR2
  output logic                   reg_req_write_o,
  output logic [9:0]             reg_req_addr_o,    // DW index
  output logic [3:0]             reg_req_be_o,
  output logic [31:0]            reg_req_data_o,
  output logic [7:0]             reg_req_bus_o,     // configuration write: Bus Number
  output logic [4:0]             reg_req_dev_o,     // configuration write: Device Number
  input  logic                   reg_cpl_valid_i,   // cdc-domain: clk_i
  input  logic [31:0]            reg_cpl_data_i,    // cdc-domain: clk_i

  // cfg: register state
  input  logic [7:0]             bus_num_i,         // cdc-domain: clk_i
  input  logic [4:0]             dev_num_i,         // cdc-domain: clk_i
  input  logic                   mem_space_en_i,    // cdc-domain: clk_i
  input  logic                   bus_master_en_i,   // cdc-domain: clk_i
  input  logic [1:0]             d_state_i,         // 0 D0, 3 D3hot // cdc-domain: clk_i
  input  logic [31:0]            bar0_base_i,       // cdc-domain: clk_i
  input  logic [31:0]            bar2_base_i,       // cdc-domain: clk_i
  input  logic                   cpl_timeout_dis_i, // 1: the completion timeout never fires // cdc-domain: clk_i

  // cfg: error events and messages
  output logic [8:0]             tl_err_o,          // one-cycle pulses, bit order in the TL_CFG page
  output logic                   tl_np_pending_o,   // an rq read awaits its final completion or timeout
  input  logic                   msg_valid_i,       // cdc-domain: clk_i
  output logic                   msg_ready_o,
  input  logic [1:0]             msg_kind_i,        // 0 interrupt write, 1 ERR_COR, 2 ERR_NONFATAL, 3 ERR_FATAL // cdc-domain: clk_i
  input  logic [63:0]            msg_addr_i,        // cdc-domain: clk_i
  input  logic [31:0]            msg_data_i,        // cdc-domain: clk_i

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
  output logic                   rc_done_o          // with eop: the read is finished
);

  // Inputs and parameters the shell does not use yet; the wave-2 RTL
  // consumes them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, rst_i, tlp_tx_ready_i,
                           tlp_rx_valid_i, tlp_rx_data_i, tlp_rx_sop_i, tlp_rx_eop_i,
                           fc_ph_avail_i, fc_pd_avail_i, fc_nph_avail_i, fc_npd_avail_i,
                           fc_cplh_avail_i, fc_cpld_avail_i, dl_up_i,
                           reg_req_ready_i, reg_cpl_valid_i, reg_cpl_data_i,
                           bus_num_i, dev_num_i, mem_space_en_i, bus_master_en_i, d_state_i,
                           bar0_base_i, bar2_base_i, cpl_timeout_dis_i,
                           msg_valid_i, msg_kind_i, msg_addr_i, msg_data_i,
                           cq_ready_i,
                           cc_valid_i, cc_sop_i, cc_eop_i, cc_data_i, cc_handle_i,
                           rq_valid_i, rq_sop_i, rq_eop_i, rq_data_i, rq_write_i, rq_addr_i,
                           rq_len_i, rq_first_be_i, rq_last_be_i, rq_tag_i,
                           rc_ready_i,
                           FC_PH[0], FC_PD[0], FC_NPH[0], FC_NPD[0], CPL_TIMEOUT_DIV[0]};

  // Reset values from the HAS: rst_i high or dl_up_i low.
  assign tlp_tx_valid_o  = 1'b0;
  assign tlp_tx_data_o   = 32'd0;
  assign tlp_tx_sop_o    = 1'b0;
  assign tlp_tx_eop_o    = 1'b0;
  assign tlp_rx_ready_o  = 1'b0;
  assign fc_rel_p_o      = 1'b0;
  assign fc_rel_pd_o     = 4'd0;
  assign fc_rel_np_o     = 1'b0;
  assign fc_rel_npd_o    = 4'd0;
  assign reg_req_valid_o = 1'b0;
  assign reg_req_bar2_o  = 1'b0;
  assign reg_req_write_o = 1'b0;
  assign reg_req_addr_o  = 10'd0;
  assign reg_req_be_o    = 4'd0;
  assign reg_req_data_o  = 32'd0;
  assign reg_req_bus_o   = 8'd0;
  assign reg_req_dev_o   = 5'd0;
  assign tl_err_o        = 9'd0;
  assign tl_np_pending_o = 1'b0;
  assign msg_ready_o     = 1'b0;
  assign cq_valid_o      = 1'b0;
  assign cq_sop_o        = 1'b0;
  assign cq_eop_o        = 1'b0;
  assign cq_data_o       = 32'd0;
  assign cq_write_o      = 1'b0;
  assign cq_addr_o       = '0;
  assign cq_len_o        = 11'd0;
  assign cq_first_be_o   = 4'd0;
  assign cq_last_be_o    = 4'd0;
  assign cq_handle_o     = 3'd0;
  assign cc_ready_o      = 1'b0;
  assign rq_ready_o      = 1'b0;
  assign rc_valid_o      = 1'b0;
  assign rc_sop_o        = 1'b0;
  assign rc_eop_o        = 1'b0;
  assign rc_data_o       = 32'd0;
  assign rc_tag_o        = 3'd0;
  assign rc_status_o     = 2'd0;
  assign rc_done_o       = 1'b0;

endmodule

`default_nettype wire
