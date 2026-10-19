// Testbench top of pcie_rc_tl (wiki/src/dv/rc_tl/DV_PLAN.md): the DUT with
// its parameters, every port passed through by name, plus params_o, the
// parameters the cocotb environment's model needs ({CPL_TIMEOUT_DIV,
// HOST_MEM_ADDR_W, REVISION_ID, DEVICE_ID, VENDOR_ID}, CPL_TIMEOUT_DIV in
// the most significant bits). The DUT is driven only through its ports.
`default_nettype none

module pcie_rc_tl_tb #(
  // Every parameter is int here so a regression entry can set it as a
  // plain integer (-G); the DUT gets it at its MAS width.
  parameter int unsigned FC_NPH          = 8,
  parameter int unsigned CPL_TIMEOUT_DIV = 1,
  parameter int unsigned HOST_MEM_ADDR_W = 12,
  parameter int unsigned HOST_MEM_BASE   = 32'h0000_0000,
  parameter int unsigned MSI_ADDR        = 32'hFEE0_0000,
  parameter int unsigned VENDOR_ID       = 0,
  parameter int unsigned DEVICE_ID       = 0,
  parameter int unsigned REVISION_ID     = 0
) (
  input  logic                clk_i,
  input  logic                rst_i,
  input  logic                cmd_valid_i,
  output logic                cmd_ready_o,
  input  common_pkg::t_rc_cmd cmd_i,
  output logic                rsp_valid_o,
  output common_pkg::t_rc_rsp rsp_o,
  output logic                msi_valid_o,
  input  logic                msi_ready_i,
  output common_pkg::t_rc_msi msi_o,
  output logic                tlp_tx_valid_o,
  input  logic                tlp_tx_ready_i,
  output logic [31:0]         tlp_tx_data_o,
  output logic                tlp_tx_sop_o,
  output logic                tlp_tx_eop_o,
  input  logic                tlp_rx_valid_i,
  output logic                tlp_rx_ready_o,
  input  logic [31:0]         tlp_rx_data_i,
  input  logic                tlp_rx_sop_i,
  input  logic                tlp_rx_eop_i,
  input  logic [7:0]          fc_ph_avail_i,
  input  logic [11:0]         fc_pd_avail_i,
  input  logic [7:0]          fc_nph_avail_i,
  input  logic [11:0]         fc_npd_avail_i,
  input  logic [7:0]          fc_cplh_avail_i,
  input  logic [11:0]         fc_cpld_avail_i,
  output logic                fc_rel_p_o,
  output logic [3:0]          fc_rel_pd_o,
  output logic                fc_rel_np_o,
  output logic [3:0]          fc_rel_npd_o,
  input  logic                dl_up_i,
  output logic [55:0]         params_o
);

  assign params_o = {11'(CPL_TIMEOUT_DIV), 5'(HOST_MEM_ADDR_W), 8'(REVISION_ID), 16'(DEVICE_ID), 16'(VENDOR_ID)};

  pcie_rc_tl #(
    .FC_NPH          (FC_NPH),
    .CPL_TIMEOUT_DIV (CPL_TIMEOUT_DIV),
    .HOST_MEM_ADDR_W (HOST_MEM_ADDR_W),
    .HOST_MEM_BASE   (32'(HOST_MEM_BASE)),
    .MSI_ADDR        (32'(MSI_ADDR)),
    .VENDOR_ID       (16'(VENDOR_ID)),
    .DEVICE_ID       (16'(DEVICE_ID)),
    .REVISION_ID     (8'(REVISION_ID))
  ) u_dut (
    .clk_i           (clk_i),
    .rst_i           (rst_i),
    .cmd_valid_i     (cmd_valid_i),
    .cmd_ready_o     (cmd_ready_o),
    .cmd_i           (cmd_i),
    .rsp_valid_o     (rsp_valid_o),
    .rsp_o           (rsp_o),
    .msi_valid_o     (msi_valid_o),
    .msi_ready_i     (msi_ready_i),
    .msi_o           (msi_o),
    .tlp_tx_valid_o  (tlp_tx_valid_o),
    .tlp_tx_ready_i  (tlp_tx_ready_i),
    .tlp_tx_data_o   (tlp_tx_data_o),
    .tlp_tx_sop_o    (tlp_tx_sop_o),
    .tlp_tx_eop_o    (tlp_tx_eop_o),
    .tlp_rx_valid_i  (tlp_rx_valid_i),
    .tlp_rx_ready_o  (tlp_rx_ready_o),
    .tlp_rx_data_i   (tlp_rx_data_i),
    .tlp_rx_sop_i    (tlp_rx_sop_i),
    .tlp_rx_eop_i    (tlp_rx_eop_i),
    .fc_ph_avail_i   (fc_ph_avail_i),
    .fc_pd_avail_i   (fc_pd_avail_i),
    .fc_nph_avail_i  (fc_nph_avail_i),
    .fc_npd_avail_i  (fc_npd_avail_i),
    .fc_cplh_avail_i (fc_cplh_avail_i),
    .fc_cpld_avail_i (fc_cpld_avail_i),
    .fc_rel_p_o      (fc_rel_p_o),
    .fc_rel_pd_o     (fc_rel_pd_o),
    .fc_rel_np_o     (fc_rel_np_o),
    .fc_rel_npd_o    (fc_rel_npd_o),
    .dl_up_i         (dl_up_i)
  );

endmodule

`default_nettype wire
