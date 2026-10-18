// PCI Express configuration space: Type 0 header, capabilities, MSI-X
// table and pending bits, error status and messages, interrupt resolution.
// Interfaces and behavior: wiki/src/arch/cfg/HAS.md and its register and
// capability pages; the tl side: wiki/src/arch/core/TL_CFG.md; structure:
// wiki/src/rtl/cfg/MAS.md.
//
// The access handshake and completion live here; the blocks are the
// configuration space (pcie_cfg_regs), the errors (pcie_cfg_err), BAR2 with
// the MSI-X table RAM (pcie_cfg_bar2) and the messages and interrupts
// (pcie_cfg_msg).
`default_nettype none

`include "common_macros.svh"

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
  output logic        cpl_timeout_dis_o, // Device Control 2 Completion Timeout Disable
  output logic        mem_space_en_o,
  output logic        bus_master_en_o,
  output logic [1:0]  d_state_o,        // 0 D0, 3 D3hot

  // Requester state (from tl)
  input  logic        tl_np_pending_i,  // an application non-posted request is outstanding // cdc-domain: clk_i

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

  logic                     reg_take;
  logic                     cfg_wr;
  logic                     cfg_rd;
  logic                     bar2_wr;
  logic                     bar2_rd;
  logic                     bar2_take;
  logic                     cpl_q;
  logic                     cpl_bar2_q;
  logic [31:0]              cfg_rd_data;
  logic [31:0]              cfg_rd_q;
  logic [31:0]              bar2_rd_data;
  pcie_cfg_pkg::t_ctrl      ctrl_q;
  pcie_cfg_pkg::t_ctrl      ctrl_d;
  pcie_cfg_pkg::t_sts       err_sts;
  pcie_cfg_pkg::t_sts       sts_set;
  pcie_cfg_pkg::t_err_due   due;
  pcie_cfg_pkg::t_err_due   take_err;
  pcie_cfg_pkg::t_entry     entry;
  logic                     sse_set;
  logic                     look;
  logic [5:0]               look_vec;
  logic [MSIX_TABLE_SIZE-1:0] mask_q;
  logic [MSIX_TABLE_SIZE-1:0] mask_d;
  logic [MSIX_TABLE_SIZE-1:0] pba;

  // ---- register access: one at a time, completed one cycle after its take;
  // ready whenever out of reset and no access is open (cpl_q marks the one
  // open access, in the cycle after its take)
  always_comb begin
    reg_take    = reg_req_valid_i && reg_req_ready_o;
    cfg_wr      = reg_take && !reg_req_bar2_i && reg_req_write_i;
    cfg_rd      = reg_take && !reg_req_bar2_i && !reg_req_write_i;
    bar2_wr     = reg_take && reg_req_bar2_i && reg_req_write_i;
    bar2_rd     = reg_take && reg_req_bar2_i && !reg_req_write_i;
    bar2_take   = reg_take && reg_req_bar2_i;
  end

  // The status bits set at this edge: the error events and Signaled
  // System Error (kept out of the block above, which feeds them).
  always_comb begin
    sts_set     = err_sts;
    sts_set.sse = sse_set;
  end

  `COMMON_DFF_RST(cpl_q, reg_take, clk_i, rst_i)
  `COMMON_DFF_EN(cpl_bar2_q, reg_req_bar2_i, clk_i, reg_take)
  `COMMON_DFF_EN(cfg_rd_q, cfg_rd_data, clk_i, cfg_rd)

  assign reg_req_ready_o = !rst_i && !cpl_q;
  assign reg_cpl_valid_o = cpl_q;
  assign reg_cpl_data_o  = cpl_bar2_q ? bar2_rd_data : cfg_rd_q;

  pcie_cfg_regs #(
    .BAR0_ADDR_W     (BAR0_ADDR_W),
    .MSIX_TABLE_SIZE (MSIX_TABLE_SIZE),
    .VENDOR_ID       (VENDOR_ID),
    .DEVICE_ID       (DEVICE_ID),
    .SUBSYS_VENDOR_ID(SUBSYS_VENDOR_ID),
    .SUBSYS_ID       (SUBSYS_ID),
    .CLASS_CODE      (CLASS_CODE),
    .REVISION_ID     (REVISION_ID)
  ) u_regs (
    .clk_i            (clk_i),
    .rst_i            (rst_i),
    .wr_i             (cfg_wr),
    .addr_i           (reg_req_addr_i),
    .be_i             (reg_req_be_i),
    .data_i           (reg_req_data_i),
    .bus_i            (reg_req_bus_i),
    .dev_i            (reg_req_dev_i),
    .sts_set_i        (sts_set),
    .np_pending_i     (tl_np_pending_i),
    .rd_data_o        (cfg_rd_data),
    .ctrl_q_o         (ctrl_q),
    .ctrl_d_o         (ctrl_d),
    .bus_num_o        (bus_num_o),
    .dev_num_o        (dev_num_o),
    .bar0_base_o      (bar0_base_o),
    .bar2_base_o      (bar2_base_o),
    .cpl_timeout_dis_o(cpl_timeout_dis_o),
    .max_payload_o    (max_payload_o),
    .max_read_req_o   (max_read_req_o)
  );

  pcie_cfg_err u_err (
    .clk_i    (clk_i),
    .rst_i    (rst_i),
    .tl_err_i (tl_err_i),
    .dl_err_i (dl_err_i),
    .rcv_err_i(rcv_err_i),
    .ctrl_d_i (ctrl_d),
    .take_i   (take_err),
    .sts_set_o(err_sts),
    .due_o    (due)
  );

  pcie_cfg_bar2 #(
    .MSIX_TABLE_SIZE(MSIX_TABLE_SIZE)
  ) u_bar2 (
    .clk_i     (clk_i),
    .rst_i     (rst_i),
    .wr_i      (bar2_wr),
    .rd_i      (bar2_rd),
    .addr_i    (reg_req_addr_i),
    .be_i      (reg_req_be_i),
    .data_i    (reg_req_data_i),
    .look_i    (look),
    .look_vec_i(look_vec),
    .pba_i     (pba),
    .mask_q_o  (mask_q),
    .mask_d_o  (mask_d),
    .entry_o   (entry),
    .rd_data_o (bar2_rd_data)
  );

  pcie_cfg_msg #(
    .MSIX_TABLE_SIZE(MSIX_TABLE_SIZE)
  ) u_msg (
    .clk_i       (clk_i),
    .rst_i       (rst_i),
    .ctrl_q_i    (ctrl_q),
    .ctrl_d_i    (ctrl_d),
    .mask_q_i    (mask_q),
    .mask_d_i    (mask_d),
    .due_i       (due),
    .entry_i     (entry),
    .bar2_take_i (bar2_take),
    .msg_valid_o (msg_valid_o),
    .msg_ready_i (msg_ready_i),
    .msg_kind_o  (msg_kind_o),
    .msg_addr_o  (msg_addr_o),
    .msg_data_o  (msg_data_o),
    .irq_valid_i (irq_valid_i),
    .irq_ready_o (irq_ready_o),
    .irq_vector_i(irq_vector_i),
    .irq_done_o  (irq_done_o),
    .irq_status_o(irq_status_o),
    .pba_o       (pba),
    .look_o      (look),
    .look_vec_o  (look_vec),
    .take_err_o  (take_err),
    .sse_set_o   (sse_set)
  );

  // Register-state outputs (TL_CFG, Register state).
  assign mem_space_en_o  = ctrl_q.mse;
  assign bus_master_en_o = ctrl_q.bme;
  assign d_state_o       = ctrl_q.d_state;

  // cfg takes no access while its last one is completing (holds by
  // construction of reg_req_ready_o; guards changes to it).
  `COMMON_ASSERT_NEVER(a_one_access, clk_i, rst_i, cpl_q && reg_take)

endmodule

`default_nettype wire
