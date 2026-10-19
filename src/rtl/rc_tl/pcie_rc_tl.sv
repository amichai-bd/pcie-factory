// PCI Express root-port transaction layer, a sub-IP of pcie_rc: requester,
// Type 1 header, completer over host memory, MSI capture and error record.
// Interfaces and behavior: wiki/src/rtl/rc_tl/MAS.md, which implements
// wiki/src/arch/rc/HAS.md and TL_SEQ.md.
//
// Built so far: the front end (u_cmd), the Type 1 header (u_hdr), #374;
// the requester (u_req), the transmit arbiter (u_tx) and the completion
// side of the receive parser (u_rx), #373. The MSI capture and credit
// release outputs hold their reset values, and received TLPs other than
// completions are taken and dropped; the inbound blocks arrive with #375
// (MAS, Leaf blocks).
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

  // The link reset: rst_i, or the link down (MAS, Reset and link down).
  logic lrst;

  assign lrst = rst_i || !dl_up_i;

  pcie_rc_tl_pkg::t_hdr_st  hdr_st;
  pcie_rc_tl_pkg::t_hdr_acc hdr_acc;
  logic [31:0]              hdr_rd_data;
  pcie_rc_tl_pkg::t_req_cmd req_cmd;
  pcie_rc_tl_pkg::t_res     req_res;
  logic [pcie_rc_tl_pkg::ERR_W-1:0] req_err;
  pcie_rc_tl_pkg::t_tx_tlp  req_tx;
  pcie_rc_tl_pkg::t_cpl_rx  rx_cpl;
  logic                     tx_grant;
  logic                     tx_np_sop;
  logic                     tx_p_eop;

  pcie_rc_tl_cmd #(
    .HOST_MEM_ADDR_W (HOST_MEM_ADDR_W)
  ) u_cmd (
    .clk_i         (clk_i),
    .rst_i         (rst_i),
    .cmd_valid_i   (cmd_valid_i),
    .cmd_ready_o   (cmd_ready_o),
    .cmd_i         (cmd_i),
    .dl_up_i       (dl_up_i),
    .hdr_st_i      (hdr_st),
    .hdr_rd_data_i (hdr_rd_data),
    .hdr_acc_o     (hdr_acc),
    .req_o         (req_cmd),
    .res_i         (req_res),
    .err_ev_i      (req_err),
    .rsp_valid_o   (rsp_valid_o),
    .rsp_o         (rsp_o)
  );

  pcie_rc_tl_hdr #(
    .VENDOR_ID   (VENDOR_ID),
    .DEVICE_ID   (DEVICE_ID),
    .REVISION_ID (REVISION_ID)
  ) u_hdr (
    .clk_i     (clk_i),
    .rst_i     (rst_i),
    .acc_i     (hdr_acc),
    .rd_data_o (hdr_rd_data),
    .st_o      (hdr_st)
  );

  pcie_rc_tl_req #(
    .CPL_TIMEOUT_DIV (CPL_TIMEOUT_DIV)
  ) u_req (
    .clk_i     (clk_i),
    .rst_i     (rst_i),
    .dl_up_i   (dl_up_i),
    .cmd_i     (req_cmd),
    .pri_bus_i (hdr_st.pri_bus),
    .tx_o      (req_tx),
    .grant_i   (tx_grant),
    .np_sop_i  (tx_np_sop),
    .p_eop_i   (tx_p_eop),
    .cpl_i     (rx_cpl),
    .res_o     (req_res),
    .err_o     (req_err)
  );

  pcie_rc_tl_tx u_tx (
    .clk_i          (clk_i),
    .rst_i          (lrst),
    .fc_ph_avail_i  (fc_ph_avail_i),
    .fc_pd_avail_i  (fc_pd_avail_i),
    .fc_nph_avail_i (fc_nph_avail_i),
    .fc_npd_avail_i (fc_npd_avail_i),
    .req_i          (req_tx),
    .grant_o        (tx_grant),
    .np_sop_o       (tx_np_sop),
    .p_eop_o        (tx_p_eop),
    .tlp_tx_valid_o (tlp_tx_valid_o),
    .tlp_tx_ready_i (tlp_tx_ready_i),
    .tlp_tx_data_o  (tlp_tx_data_o),
    .tlp_tx_sop_o   (tlp_tx_sop_o),
    .tlp_tx_eop_o   (tlp_tx_eop_o)
  );

  pcie_rc_tl_rx u_rx (
    .clk_i          (clk_i),
    .rst_i          (lrst),
    .tlp_rx_valid_i (tlp_rx_valid_i),
    .tlp_rx_data_i  (tlp_rx_data_i),
    .tlp_rx_sop_i   (tlp_rx_sop_i),
    .tlp_rx_eop_i   (tlp_rx_eop_i),
    .cpl_o          (rx_cpl)
  );

  // Inputs and parameters no block uses yet; #375 consumes them.
  `COMMON_UNUSED_BITS(unused_inputs, {msi_ready_i, fc_cplh_avail_i, fc_cpld_avail_i,
                           FC_NPH[0], HOST_MEM_BASE, MSI_ADDR})

  // Reset values of the blocks not built yet (MAS): no capture, nothing
  // released; every received beat taken out of reset.
  assign msi_valid_o    = 1'b0;
  assign msi_o          = '0;
  assign tlp_rx_ready_o = !rst_i;
  assign fc_rel_p_o     = 1'b0;
  assign fc_rel_pd_o    = 4'd0;
  assign fc_rel_np_o    = 1'b0;
  assign fc_rel_npd_o   = 4'd0;

endmodule

`default_nettype wire
