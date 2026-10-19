// PCI Express root-port transaction layer, a sub-IP of pcie_rc: requester,
// Type 1 header, completer over host memory, MSI capture and error record.
// Interfaces and behavior: wiki/src/rtl/rc_tl/MAS.md, which implements
// wiki/src/arch/rc/HAS.md and TL_SEQ.md.
//
// Wave-1 shell: the ports of the MAS with every output at its reset value;
// received beats are taken and dropped. The blocks arrive with #373 to #375.
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl #(
  parameter int unsigned FC_NPH          = 8,              // non-posted header credits advertised, 1..127
  parameter int unsigned CPL_TIMEOUT_DIV = 1,              // 1..1024, divides the completion timeout
  parameter int unsigned HOST_MEM_ADDR_W = 12,             // 8..16: host memory is 2**HOST_MEM_ADDR_W bytes
  parameter logic [31:0] HOST_MEM_BASE   = 32'h0000_0000,  // system address of host memory, size aligned
  parameter logic [31:0] MSI_ADDR        = 32'hFEE0_0000,  // system address of the 4 KiB MSI window
  parameter logic [15:0] VENDOR_ID       = 16'h0000,
  parameter logic [15:0] DEVICE_ID       = 16'h0000,
  parameter logic [7:0]  REVISION_ID     = 8'h00
) (
  input  logic                  clk_i,             // core clock, 125 MHz
  input  logic                  rst_i,             // synchronous, active high

  // Commands, results and MSI captures with rc_seq (TL_SEQ.md)
  input  logic                  cmd_valid_i,
  output logic                  cmd_ready_o,
  input  common_pkg::t_rc_cmd   cmd_i,
  output logic                  rsp_valid_o,       // one-cycle pulse per result beat, no ready
  output common_pkg::t_rc_rsp   rsp_o,
  output logic                  msi_valid_o,
  input  logic                  msi_ready_i,
  output common_pkg::t_rc_msi   msi_o,

  // link upper TLP stream (dll HAS, upper TLP stream)
  output logic                  tlp_tx_valid_o,
  input  logic                  tlp_tx_ready_i,
  output logic [31:0]           tlp_tx_data_o,
  output logic                  tlp_tx_sop_o,
  output logic                  tlp_tx_eop_o,
  input  logic                  tlp_rx_valid_i,
  output logic                  tlp_rx_ready_o,
  input  logic [31:0]           tlp_rx_data_i,
  input  logic                  tlp_rx_sop_i,
  input  logic                  tlp_rx_eop_i,

  // link credits available (all ones: infinite)
  input  logic [7:0]            fc_ph_avail_i,
  input  logic [11:0]           fc_pd_avail_i,
  input  logic [7:0]            fc_nph_avail_i,
  input  logic [11:0]           fc_npd_avail_i,
  input  logic [7:0]            fc_cplh_avail_i,
  input  logic [11:0]           fc_cpld_avail_i,

  // link credit release (FC_RELEASE 1)
  output logic                  fc_rel_p_o,
  output logic [3:0]            fc_rel_pd_o,
  output logic                  fc_rel_np_o,
  output logic [3:0]            fc_rel_npd_o,

  // link data link status
  input  logic                  dl_up_i
);

  localparam int unsigned OVL_SH = (HOST_MEM_ADDR_W > 12) ? HOST_MEM_ADDR_W : 12;

  `COMMON_ASSERT_PARAM(g_fc_nph_check, FC_NPH >= 1 && FC_NPH <= 127, "pcie_rc_tl: FC_NPH must be 1 to 127")
  `COMMON_ASSERT_PARAM(g_cpl_timeout_div_check, CPL_TIMEOUT_DIV >= 1 && CPL_TIMEOUT_DIV <= 1024, "pcie_rc_tl: CPL_TIMEOUT_DIV must be 1 to 1024")
  `COMMON_ASSERT_PARAM(g_host_mem_addr_w_check, HOST_MEM_ADDR_W >= 8 && HOST_MEM_ADDR_W <= 16, "pcie_rc_tl: HOST_MEM_ADDR_W must be 8 to 16")
  `COMMON_ASSERT_PARAM(g_host_mem_base_check, (HOST_MEM_BASE & ((32'd1 << HOST_MEM_ADDR_W) - 32'd1)) == 32'd0, "pcie_rc_tl: HOST_MEM_BASE must be aligned to the host memory size")
  `COMMON_ASSERT_PARAM(g_msi_addr_check, MSI_ADDR[11:0] == 12'd0, "pcie_rc_tl: MSI_ADDR must be 4 KiB aligned")
  // Both regions are aligned powers of two: they overlap exactly when their
  // bases agree above the larger one's size.
  `COMMON_ASSERT_PARAM(g_msi_overlap_check, (MSI_ADDR >> OVL_SH) != (HOST_MEM_BASE >> OVL_SH), "pcie_rc_tl: the MSI window must not overlap host memory")
  `COMMON_ASSERT_PARAM(g_vendor_id_check, VENDOR_ID != 16'hFFFF, "pcie_rc_tl: VENDOR_ID must not be FFFF")
  `COMMON_ASSERT_PARAM(g_device_id_check, DEVICE_ID != 16'hFFFF, "pcie_rc_tl: DEVICE_ID must not be FFFF")

  // Inputs and parameters the shell does not use yet; #373 to #375 consume
  // them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, cmd_valid_i, cmd_i, msi_ready_i, tlp_tx_ready_i,
                           tlp_rx_valid_i, tlp_rx_data_i, tlp_rx_sop_i, tlp_rx_eop_i,
                           fc_ph_avail_i, fc_pd_avail_i, fc_nph_avail_i, fc_npd_avail_i,
                           fc_cplh_avail_i, fc_cpld_avail_i, dl_up_i,
                           FC_NPH[0], CPL_TIMEOUT_DIV[0], HOST_MEM_ADDR_W[0],
                           HOST_MEM_BASE, MSI_ADDR, VENDOR_ID, DEVICE_ID, REVISION_ID};

  // Reset values (MAS): no command taken, no result, no capture, nothing
  // sent or released; every received beat taken out of reset.
  assign cmd_ready_o    = 1'b0;
  assign rsp_valid_o    = 1'b0;
  assign rsp_o          = '0;
  assign msi_valid_o    = 1'b0;
  assign msi_o          = '0;
  assign tlp_tx_valid_o = 1'b0;
  assign tlp_tx_data_o  = 32'd0;
  assign tlp_tx_sop_o   = 1'b0;
  assign tlp_tx_eop_o   = 1'b0;
  assign tlp_rx_ready_o = !rst_i;
  assign fc_rel_p_o     = 1'b0;
  assign fc_rel_pd_o    = 4'd0;
  assign fc_rel_np_o    = 1'b0;
  assign fc_rel_npd_o   = 4'd0;

endmodule

`default_nettype wire
