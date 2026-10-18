// pcie_cfg configuration space: the Type 0 header and the capability
// registers of wiki/src/arch/cfg/REGISTERS.md and CAPS.md, their writes
// with byte enables, the RW1C status bits, the bus and device number
// capture, the captured slot power limit and the read of any configuration DW
// (wiki/src/rtl/cfg/MAS.md#configuration-space).
`default_nettype none

`include "common_macros.svh"

module pcie_cfg_regs #(
  parameter int unsigned BAR0_ADDR_W      = 12,
  parameter int unsigned MSIX_TABLE_SIZE  = 8,
  parameter logic [15:0] VENDOR_ID        = 16'h0000,
  parameter logic [15:0] DEVICE_ID        = 16'h0000,
  parameter logic [15:0] SUBSYS_VENDOR_ID = 16'h0000,
  parameter logic [15:0] SUBSYS_ID        = 16'h0000,
  parameter logic [23:0] CLASS_CODE       = 24'hFF0000,
  parameter logic [7:0]  REVISION_ID      = 8'h00
) (
  input  logic                clk_i,
  input  logic                rst_i,
  // A configuration write taken at this edge, and the access fields.
  input  logic                wr_i,
  input  logic [9:0]          addr_i,
  input  logic [3:0]          be_i,
  input  logic [31:0]         data_i,
  input  logic [7:0]          bus_i,
  input  logic [4:0]          dev_i,
  // Status bits set at this edge (errors, Signaled System Error).
  input  pcie_cfg_pkg::t_sts  sts_set_i,
  input  logic                np_pending_i,
  // The slot power event: Captured Slot Power Limit loaded at this edge.
  input  logic                slot_pwr_valid_i,
  input  pcie_cfg_pkg::t_slot_pwr slot_pwr_i,
  // The configuration DW at addr_i, as the registers hold it.
  output logic [31:0]         rd_data_o,
  output pcie_cfg_pkg::t_ctrl ctrl_q_o,
  output pcie_cfg_pkg::t_ctrl ctrl_d_o,
  output logic [7:0]          bus_num_o,
  output logic [4:0]          dev_num_o,
  output logic [31:0]         bar0_base_o,
  output logic [31:0]         bar2_base_o,
  output logic                cpl_timeout_dis_o,
  output logic [2:0]          max_payload_o,
  output logic [2:0]          max_read_req_o
);

  localparam logic [31:0] RW_BAR0     = ~((32'd1 << BAR0_ADDR_W) - 32'd1);
  localparam logic [31:0] VAL_ID      = {DEVICE_ID, VENDOR_ID};
  localparam logic [31:0] VAL_CLASS   = {CLASS_CODE, REVISION_ID};
  localparam logic [31:0] VAL_SUBSYS  = {SUBSYS_ID, SUBSYS_VENDOR_ID};
  localparam logic [10:0] TABLE_SIZE  = 11'(MSIX_TABLE_SIZE - 1);
  localparam logic [31:0] VAL_MSIX    = pcie_cfg_pkg::VAL_MSIX | {5'd0, TABLE_SIZE, 16'd0};

  // One register per DW with RW bits, each holding only its RW bits (the
  // read-only bits are added at the read).
  logic [31:0] cmd_q,     cmd_d;
  logic [31:0] cls_q,     cls_d;
  logic [31:0] bar0_q,    bar0_d;
  logic [31:0] bar2_q,    bar2_d;
  logic [31:0] intline_q, intline_d;
  logic [31:0] pmcsr_q,   pmcsr_d;
  logic [31:0] msictl_q,  msictl_d;
  logic [31:0] msilo_q,   msilo_d;
  logic [31:0] msihi_q,   msihi_d;
  logic [31:0] msidat_q,  msidat_d;
  logic [31:0] msix_q,    msix_d;
  logic [31:0] devctl_q,  devctl_d;
  logic [31:0] lnkctl_q,  lnkctl_d;
  logic [31:0] devctl2_q, devctl2_d;
  pcie_cfg_pkg::t_sts sts_q, sts_d;
  logic [7:0]  bus_q, bus_d;
  logic [4:0]  dev_q, dev_d;
  pcie_cfg_pkg::t_slot_pwr spl_q;

  logic [31:0] pm_rw;       // PowerState is not written by a D1 or D2 value
  logic [31:0] clr_hdr;     // Status bits written 1
  logic [31:0] clr_dev;     // Device Status bits written 1

  always_comb begin
    pm_rw = (data_i[1:0] == 2'd1 || data_i[1:0] == 2'd2) ? 32'd0 : pcie_cfg_pkg::RW_PM_CSR;
    cmd_d     = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_CMD)
              ? pcie_cfg_pkg::wr_rw(cmd_q, data_i, be_i, pcie_cfg_pkg::RW_CMD) : cmd_q;
    cls_d     = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_CLS)
              ? pcie_cfg_pkg::wr_rw(cls_q, data_i, be_i, pcie_cfg_pkg::RW_BYTE0) : cls_q;
    bar0_d    = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_BAR0)
              ? pcie_cfg_pkg::wr_rw(bar0_q, data_i, be_i, RW_BAR0) : bar0_q;
    bar2_d    = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_BAR2)
              ? pcie_cfg_pkg::wr_rw(bar2_q, data_i, be_i, pcie_cfg_pkg::RW_BAR2) : bar2_q;
    intline_d = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_INTLINE)
              ? pcie_cfg_pkg::wr_rw(intline_q, data_i, be_i, pcie_cfg_pkg::RW_BYTE0) : intline_q;
    pmcsr_d   = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_PM_CSR)
              ? pcie_cfg_pkg::wr_rw(pmcsr_q, data_i, be_i, pm_rw) : pmcsr_q;
    msictl_d  = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_MSI_CTL)
              ? pcie_cfg_pkg::wr_rw(msictl_q, data_i, be_i, pcie_cfg_pkg::RW_MSI_CTL) : msictl_q;
    msilo_d   = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_MSI_LO)
              ? pcie_cfg_pkg::wr_rw(msilo_q, data_i, be_i, pcie_cfg_pkg::RW_MSI_LO) : msilo_q;
    msihi_d   = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_MSI_HI)
              ? pcie_cfg_pkg::wr_rw(msihi_q, data_i, be_i, pcie_cfg_pkg::RW_ALL) : msihi_q;
    msidat_d  = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_MSI_DAT)
              ? pcie_cfg_pkg::wr_rw(msidat_q, data_i, be_i, pcie_cfg_pkg::RW_MSI_DAT) : msidat_q;
    msix_d    = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_MSIX)
              ? pcie_cfg_pkg::wr_rw(msix_q, data_i, be_i, pcie_cfg_pkg::RW_MSIX) : msix_q;
    devctl_d  = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_DEVCTL)
              ? pcie_cfg_pkg::wr_rw(devctl_q, data_i, be_i, pcie_cfg_pkg::RW_DEVCTL) : devctl_q;
    lnkctl_d  = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_LNKCTL)
              ? pcie_cfg_pkg::wr_rw(lnkctl_q, data_i, be_i, pcie_cfg_pkg::RW_LNKCTL) : lnkctl_q;
    devctl2_d = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_DEVCTL2)
              ? pcie_cfg_pkg::wr_rw(devctl2_q, data_i, be_i, pcie_cfg_pkg::RW_DEVCTL2) : devctl2_q;

    // Every configuration write captures the bus and device number.
    bus_d = wr_i ? bus_i : bus_q;
    dev_d = wr_i ? dev_i : dev_q;
  end

  // The RW1C status bits, apart from the block above: their set inputs
  // depend on its outputs through the other blocks.
  always_comb begin
    // RW1C: a write of 1 clears; an event at the same edge sets (it comes
    // after the write in the edge order, MAS Decisions).
    clr_hdr = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_CMD)
            ? pcie_cfg_pkg::wr_w1c(data_i, be_i, 32'hF100_0000) : 32'd0;
    clr_dev = pcie_cfg_pkg::hit(wr_i, addr_i, pcie_cfg_pkg::DW_DEVCTL)
            ? pcie_cfg_pkg::wr_w1c(data_i, be_i, 32'h000F_0000) : 32'd0;
    sts_d.dpe  = (sts_q.dpe  & ~clr_hdr[31]) | sts_set_i.dpe;
    sts_d.sse  = (sts_q.sse  & ~clr_hdr[30]) | sts_set_i.sse;
    sts_d.rma  = (sts_q.rma  & ~clr_hdr[29]) | sts_set_i.rma;
    sts_d.rta  = (sts_q.rta  & ~clr_hdr[28]) | sts_set_i.rta;
    sts_d.mdpe = (sts_q.mdpe & ~clr_hdr[24]) | sts_set_i.mdpe;
    sts_d.urd  = (sts_q.urd  & ~clr_dev[19]) | sts_set_i.urd;
    sts_d.fed  = (sts_q.fed  & ~clr_dev[18]) | sts_set_i.fed;
    sts_d.nfed = (sts_q.nfed & ~clr_dev[17]) | sts_set_i.nfed;
    sts_d.ced  = (sts_q.ced  & ~clr_dev[16]) | sts_set_i.ced;

  end

  `COMMON_DFF_RST(cmd_q, cmd_d, clk_i, rst_i)
  `COMMON_DFF_RST(cls_q, cls_d, clk_i, rst_i)
  `COMMON_DFF_RST(bar0_q, bar0_d, clk_i, rst_i)
  `COMMON_DFF_RST(bar2_q, bar2_d, clk_i, rst_i)
  `COMMON_DFF_RST(intline_q, intline_d, clk_i, rst_i)
  `COMMON_DFF_RST(pmcsr_q, pmcsr_d, clk_i, rst_i)
  `COMMON_DFF_RST(msictl_q, msictl_d, clk_i, rst_i)
  `COMMON_DFF_RST(msilo_q, msilo_d, clk_i, rst_i)
  `COMMON_DFF_RST(msihi_q, msihi_d, clk_i, rst_i)
  `COMMON_DFF_RST(msidat_q, msidat_d, clk_i, rst_i)
  `COMMON_DFF_RST(msix_q, msix_d, clk_i, rst_i)
  `COMMON_DFF_RST_VAL(devctl_q, devctl_d, clk_i, rst_i, pcie_cfg_pkg::RST_DEVCTL)
  `COMMON_DFF_RST(lnkctl_q, lnkctl_d, clk_i, rst_i)
  `COMMON_DFF_RST(devctl2_q, devctl2_d, clk_i, rst_i)
  `COMMON_DFF_RST(sts_q, sts_d, clk_i, rst_i)
  `COMMON_DFF_RST(bus_q, bus_d, clk_i, rst_i)
  `COMMON_DFF_RST(dev_q, dev_d, clk_i, rst_i)
  // Captured Slot Power Limit: read-only to software, loaded by the event.
  `COMMON_DFF_RST_EN(spl_q, slot_pwr_i, clk_i, slot_pwr_valid_i, rst_i, '0)

  // The register state, before and after this edge's write.
  always_comb begin
    ctrl_q_o.mse        = cmd_q[1];
    ctrl_q_o.bme        = cmd_q[2];
    ctrl_q_o.per        = cmd_q[6];
    ctrl_q_o.serr       = cmd_q[8];
    ctrl_q_o.d_state    = pmcsr_q[1:0];
    ctrl_q_o.msi_en     = msictl_q[16];
    ctrl_q_o.msi_addr   = {msihi_q, msilo_q[31:2], 2'b00};
    ctrl_q_o.msi_data   = msidat_q[15:0];
    ctrl_q_o.msix_en    = msix_q[31];
    ctrl_q_o.msix_fmask = msix_q[30];
    ctrl_q_o.cere       = devctl_q[0];
    ctrl_q_o.nfere      = devctl_q[1];
    ctrl_q_o.fere       = devctl_q[2];
    ctrl_q_o.urre       = devctl_q[3];
  end

  always_comb begin
    ctrl_d_o.mse        = cmd_d[1];
    ctrl_d_o.bme        = cmd_d[2];
    ctrl_d_o.per        = cmd_d[6];
    ctrl_d_o.serr       = cmd_d[8];
    ctrl_d_o.d_state    = pmcsr_d[1:0];
    ctrl_d_o.msi_en     = msictl_d[16];
    ctrl_d_o.msi_addr   = {msihi_d, msilo_d[31:2], 2'b00};
    ctrl_d_o.msi_data   = msidat_d[15:0];
    ctrl_d_o.msix_en    = msix_d[31];
    ctrl_d_o.msix_fmask = msix_d[30];
    ctrl_d_o.cere       = devctl_d[0];
    ctrl_d_o.nfere      = devctl_d[1];
    ctrl_d_o.fere       = devctl_d[2];
    ctrl_d_o.urre       = devctl_d[3];
  end

  assign bus_num_o         = bus_q;
  assign dev_num_o         = dev_q;
  assign bar0_base_o       = bar0_q;
  assign bar2_base_o       = bar2_q;
  assign cpl_timeout_dis_o = devctl2_q[4];
  assign max_payload_o     = devctl_q[7:5];
  assign max_read_req_o    = devctl_q[14:12];

  // Read: the whole DW whatever the byte enables; unlisted DWs read 0.
  logic [31:0] sts_hdr;
  logic [31:0] sts_dev;
  assign sts_hdr = {sts_q.dpe, sts_q.sse, sts_q.rma, sts_q.rta, 3'd0, sts_q.mdpe, 24'd0};
  assign sts_dev = {10'd0, np_pending_i, 1'b0, sts_q.urd, sts_q.fed, sts_q.nfed, sts_q.ced, 16'd0};

  always_comb begin
    case (addr_i)
      pcie_cfg_pkg::DW_ID:      rd_data_o = VAL_ID;
      pcie_cfg_pkg::DW_CMD:     rd_data_o = cmd_q | sts_hdr | pcie_cfg_pkg::VAL_STS_CAP;
      pcie_cfg_pkg::DW_CLASS:   rd_data_o = VAL_CLASS;
      pcie_cfg_pkg::DW_CLS:     rd_data_o = cls_q;
      pcie_cfg_pkg::DW_BAR0:    rd_data_o = bar0_q;
      pcie_cfg_pkg::DW_BAR2:    rd_data_o = bar2_q;
      pcie_cfg_pkg::DW_SUBSYS:  rd_data_o = VAL_SUBSYS;
      pcie_cfg_pkg::DW_CAPPTR:  rd_data_o = pcie_cfg_pkg::VAL_CAPPTR;
      pcie_cfg_pkg::DW_INTLINE: rd_data_o = intline_q;
      pcie_cfg_pkg::DW_PM_CAP:  rd_data_o = pcie_cfg_pkg::VAL_PM_CAP;
      pcie_cfg_pkg::DW_PM_CSR:  rd_data_o = pmcsr_q | pcie_cfg_pkg::VAL_PM_CSR;
      pcie_cfg_pkg::DW_MSI_CTL: rd_data_o = msictl_q | pcie_cfg_pkg::VAL_MSI_CTL;
      pcie_cfg_pkg::DW_MSI_LO:  rd_data_o = msilo_q;
      pcie_cfg_pkg::DW_MSI_HI:  rd_data_o = msihi_q;
      pcie_cfg_pkg::DW_MSI_DAT: rd_data_o = msidat_q;
      pcie_cfg_pkg::DW_MSIX:    rd_data_o = msix_q | VAL_MSIX;
      pcie_cfg_pkg::DW_MSIX_TB: rd_data_o = pcie_cfg_pkg::VAL_MSIX_TB;
      pcie_cfg_pkg::DW_MSIX_PB: rd_data_o = pcie_cfg_pkg::VAL_MSIX_PB;
      pcie_cfg_pkg::DW_PCIE:    rd_data_o = pcie_cfg_pkg::VAL_PCIE;
      pcie_cfg_pkg::DW_DEVCAP:  rd_data_o = pcie_cfg_pkg::VAL_DEVCAP
                                          | {4'd0, spl_q.scale, spl_q.value, 18'd0};
      pcie_cfg_pkg::DW_DEVCTL:  rd_data_o = devctl_q | sts_dev;
      pcie_cfg_pkg::DW_LNKCAP:  rd_data_o = pcie_cfg_pkg::VAL_LNKCAP;
      pcie_cfg_pkg::DW_LNKCTL:  rd_data_o = lnkctl_q | pcie_cfg_pkg::VAL_LNKSTS;
      pcie_cfg_pkg::DW_DEVCAP2: rd_data_o = pcie_cfg_pkg::VAL_DEVCAP2;
      pcie_cfg_pkg::DW_DEVCTL2: rd_data_o = devctl2_q;
      pcie_cfg_pkg::DW_LNKCAP2: rd_data_o = pcie_cfg_pkg::VAL_LNKCAP2;
      pcie_cfg_pkg::DW_LNKCTL2: rd_data_o = pcie_cfg_pkg::VAL_LNKCTL2;
      default:                  rd_data_o = 32'd0;
    endcase
  end

  // Parameter ranges (HAS, Parameters).
  `COMMON_ASSERT_PARAM(g_bar0_addr_w_check, BAR0_ADDR_W >= 12 && BAR0_ADDR_W <= 28,
                       "pcie_cfg: BAR0_ADDR_W must be 12..28")
  `COMMON_ASSERT_PARAM(g_msix_table_size_check, MSIX_TABLE_SIZE >= 1 && MSIX_TABLE_SIZE <= 64,
                       "pcie_cfg: MSIX_TABLE_SIZE must be 1..64")
  `COMMON_ASSERT_PARAM(g_vendor_id_check, VENDOR_ID != 16'hFFFF,
                       "pcie_cfg: VENDOR_ID must not be 16'hFFFF")
  `COMMON_ASSERT_PARAM(g_device_id_check, DEVICE_ID != 16'hFFFF,
                       "pcie_cfg: DEVICE_ID must not be 16'hFFFF")
  `COMMON_ASSERT_PARAM(g_subsys_vendor_id_check, SUBSYS_VENDOR_ID != 16'hFFFF,
                       "pcie_cfg: SUBSYS_VENDOR_ID must not be 16'hFFFF")
  `COMMON_ASSERT_PARAM(g_subsys_id_check, SUBSYS_ID != 16'hFFFF,
                       "pcie_cfg: SUBSYS_ID must not be 16'hFFFF")

  // The registers hold only their RW bits; PowerState never holds D1 or D2.
  `COMMON_ASSERT_NEVER(a_rw_only, clk_i, rst_i,
                       (cmd_q & ~pcie_cfg_pkg::RW_CMD) != 32'd0 ||
                       (devctl_q & ~pcie_cfg_pkg::RW_DEVCTL) != 32'd0 ||
                       (bar0_q & ~RW_BAR0) != 32'd0)
  `COMMON_ASSERT_NEVER(a_pstate_d0_d3, clk_i, rst_i,
                       pmcsr_q[1:0] != pcie_cfg_pkg::D0 && pmcsr_q[1:0] != pcie_cfg_pkg::D3HOT)

  // Only the RW1C bits of the write-1 masks are used.
  `COMMON_UNUSED_BITS(unused_clr, {clr_hdr[27:25], clr_hdr[23:0], clr_dev[31:20], clr_dev[15:0]})

endmodule

`default_nettype wire
