// Testbench top of pcie_cfg (wiki/src/dv/cfg/DV_PLAN.md): the DUT with its
// parameters, every port passed through by name, plus cfg_params_o, the
// parameters as the cocotb environment reads them ({BAR0_ADDR_W,
// MSIX_TABLE_SIZE, VENDOR_ID, DEVICE_ID, SUBSYS_VENDOR_ID, SUBSYS_ID,
// CLASS_CODE, REVISION_ID}, BAR0_ADDR_W in the most significant bits). The
// DUT is driven only through its ports.
`default_nettype none

module pcie_cfg_tb #(
  // The IDs are int here so a regression entry can set them as plain
  // integers (-G); the DUT gets them at their HAS widths.
  parameter int unsigned BAR0_ADDR_W      = 12,
  parameter int unsigned MSIX_TABLE_SIZE  = 8,
  parameter int unsigned VENDOR_ID        = 0,
  parameter int unsigned DEVICE_ID        = 0,
  parameter int unsigned SUBSYS_VENDOR_ID = 0,
  parameter int unsigned SUBSYS_ID        = 0,
  parameter int unsigned CLASS_CODE       = 32'h00FF_0000,
  parameter int unsigned REVISION_ID      = 0
) (
  input  logic         clk_i,
  input  logic         rst_i,
  input  logic         reg_req_valid_i,
  output logic         reg_req_ready_o,
  input  logic         reg_req_bar2_i,
  input  logic         reg_req_write_i,
  input  logic [9:0]   reg_req_addr_i,
  input  logic [3:0]   reg_req_be_i,
  input  logic [31:0]  reg_req_data_i,
  input  logic [7:0]   reg_req_bus_i,
  input  logic [4:0]   reg_req_dev_i,
  output logic         reg_cpl_valid_o,
  output logic [31:0]  reg_cpl_data_o,
  output logic [7:0]   bus_num_o,
  output logic [4:0]   dev_num_o,
  output logic [31:0]  bar0_base_o,
  output logic [31:0]  bar2_base_o,
  output logic         cpl_timeout_dis_o,
  output logic         mem_space_en_o,
  output logic         bus_master_en_o,
  output logic [1:0]   d_state_o,
  input  logic         tl_np_pending_i,
  input  logic [8:0]   tl_err_i,
  input  logic [6:0]   dl_err_i,
  input  logic         rcv_err_i,
  output logic         msg_valid_o,
  input  logic         msg_ready_i,
  output logic [1:0]   msg_kind_o,
  output logic [63:0]  msg_addr_o,
  output logic [31:0]  msg_data_o,
  input  logic         irq_valid_i,
  output logic         irq_ready_o,
  input  logic [5:0]   irq_vector_i,
  output logic         irq_done_o,
  output logic [1:0]   irq_status_o,
  output logic [2:0]   max_payload_o,
  output logic [2:0]   max_read_req_o,
  output logic [111:0] cfg_params_o
);

  localparam logic [15:0] VID   = 16'(VENDOR_ID);
  localparam logic [15:0] DID   = 16'(DEVICE_ID);
  localparam logic [15:0] SSVID = 16'(SUBSYS_VENDOR_ID);
  localparam logic [15:0] SSID  = 16'(SUBSYS_ID);
  localparam logic [23:0] CLASS = 24'(CLASS_CODE);
  localparam logic [7:0]  REV   = 8'(REVISION_ID);

  assign cfg_params_o = {8'(BAR0_ADDR_W), 8'(MSIX_TABLE_SIZE), VID, DID, SSVID, SSID, CLASS, REV};

  pcie_cfg #(
    .BAR0_ADDR_W     (BAR0_ADDR_W),
    .MSIX_TABLE_SIZE (MSIX_TABLE_SIZE),
    .VENDOR_ID       (VID),
    .DEVICE_ID       (DID),
    .SUBSYS_VENDOR_ID(SSVID),
    .SUBSYS_ID       (SSID),
    .CLASS_CODE      (CLASS),
    .REVISION_ID     (REV)
  ) u_dut (
    .clk_i             (clk_i),
    .rst_i             (rst_i),
    .reg_req_valid_i   (reg_req_valid_i),
    .reg_req_ready_o   (reg_req_ready_o),
    .reg_req_bar2_i    (reg_req_bar2_i),
    .reg_req_write_i   (reg_req_write_i),
    .reg_req_addr_i    (reg_req_addr_i),
    .reg_req_be_i      (reg_req_be_i),
    .reg_req_data_i    (reg_req_data_i),
    .reg_req_bus_i     (reg_req_bus_i),
    .reg_req_dev_i     (reg_req_dev_i),
    .reg_cpl_valid_o   (reg_cpl_valid_o),
    .reg_cpl_data_o    (reg_cpl_data_o),
    .bus_num_o         (bus_num_o),
    .dev_num_o         (dev_num_o),
    .bar0_base_o       (bar0_base_o),
    .bar2_base_o       (bar2_base_o),
    .cpl_timeout_dis_o (cpl_timeout_dis_o),
    .mem_space_en_o    (mem_space_en_o),
    .bus_master_en_o   (bus_master_en_o),
    .d_state_o         (d_state_o),
    .tl_np_pending_i   (tl_np_pending_i),
    .tl_err_i          (tl_err_i),
    .dl_err_i          (dl_err_i),
    .rcv_err_i         (rcv_err_i),
    .msg_valid_o       (msg_valid_o),
    .msg_ready_i       (msg_ready_i),
    .msg_kind_o        (msg_kind_o),
    .msg_addr_o        (msg_addr_o),
    .msg_data_o        (msg_data_o),
    .irq_valid_i       (irq_valid_i),
    .irq_ready_o       (irq_ready_o),
    .irq_vector_i      (irq_vector_i),
    .irq_done_o        (irq_done_o),
    .irq_status_o      (irq_status_o),
    .max_payload_o     (max_payload_o),
    .max_read_req_o    (max_read_req_o)
  );

endmodule

`default_nettype wire
