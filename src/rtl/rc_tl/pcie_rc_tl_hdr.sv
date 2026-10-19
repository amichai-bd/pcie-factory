// pcie_rc_tl Type 1 header (u_hdr): the root port's 16-DW configuration
// header of wiki/src/arch/rc/HAS.md#type-1-header, read and written by the
// front end's HDR_* commands (TL_SEQ.md, Header access), and the header
// state the other blocks route by (wiki/src/rtl/rc_tl/MAS.md#type-1-header).
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_hdr #(
  parameter logic [15:0] VENDOR_ID   = 16'h0000,
  parameter logic [15:0] DEVICE_ID   = 16'h0000,
  parameter logic [7:0]  REVISION_ID = 8'h00
) (
  input  logic                     clk_i,
  input  logic                     rst_i,      // synchronous, active high: rst only
  input  pcie_rc_tl_pkg::t_hdr_acc acc_i,      // the DW read, and a write at this edge
  output logic [31:0]              rd_data_o,  // the DW at acc_i.idx; indices 16 to 1023 read 0
  output pcie_rc_tl_pkg::t_hdr_st  st_o        // from the flops
);

  // The DWs with RW fields, by DW index (rc HAS, Type 1 header).
  localparam logic [9:0] DW_ID    = 10'd0;   // Vendor ID, Device ID
  localparam logic [9:0] DW_CMD   = 10'd1;   // Command, Status
  localparam logic [9:0] DW_CLASS = 10'd2;   // Revision ID, Class Code
  localparam logic [9:0] DW_CLS   = 10'd3;   // Cache Line Size, Header Type
  localparam logic [9:0] DW_BUS   = 10'd6;   // the three bus numbers
  localparam logic [9:0] DW_MEM   = 10'd8;   // Memory Base, Memory Limit

  // The RW bits of each of those DWs; every other bit is a constant.
  localparam logic [31:0] RW_CMD = 32'h0000_0006;   // Bus Master, Memory Space Enable
  localparam logic [31:0] RW_CLS = 32'h0000_00FF;   // Cache Line Size
  localparam logic [31:0] RW_BUS = 32'h00FF_FFFF;   // Subordinate, Secondary, Primary
  localparam logic [31:0] RW_MEM = 32'hFFF0_FFF0;   // Memory Limit, Memory Base [15:4]

  // Read-only constants.
  localparam logic [23:0] CLASS_CODE  = 24'h060400;  // bridge, PCI-to-PCI
  localparam logic [7:0]  HEADER_TYPE = 8'h01;       // Type 1, single function

  // One register per DW with RW fields, holding only its RW bits; the
  // read-only bits are added at the read.
  logic [31:0] cmd_q, cmd_d;
  logic [31:0] cls_q, cls_d;
  logic [31:0] bus_q, bus_d;
  logic [31:0] mem_q, mem_d;

  always_comb begin
    cmd_d = cmd_q;
    cls_d = cls_q;
    bus_d = bus_q;
    mem_d = mem_q;
    if (acc_i.wr) begin
      case (acc_i.idx)
        DW_CMD:
          cmd_d = pcie_rc_tl_pkg::wr_rw(cmd_q, acc_i.data, acc_i.be, RW_CMD);
        DW_CLS:
          cls_d = pcie_rc_tl_pkg::wr_rw(cls_q, acc_i.data, acc_i.be, RW_CLS);
        DW_BUS:
          bus_d = pcie_rc_tl_pkg::wr_rw(bus_q, acc_i.data, acc_i.be, RW_BUS);
        DW_MEM:
          mem_d = pcie_rc_tl_pkg::wr_rw(mem_q, acc_i.data, acc_i.be, RW_MEM);
        default: begin
        end
      endcase
    end
  end

  `COMMON_DFF_RST(cmd_q, cmd_d, clk_i, rst_i)
  `COMMON_DFF_RST(cls_q, cls_d, clk_i, rst_i)
  `COMMON_DFF_RST(bus_q, bus_d, clk_i, rst_i)
  `COMMON_DFF_RST(mem_q, mem_d, clk_i, rst_i)

  // Read: the whole DW, every field included; unlisted DWs read 0.
  always_comb begin
    case (acc_i.idx)
      DW_ID:    rd_data_o = {DEVICE_ID, VENDOR_ID};
      DW_CMD:   rd_data_o = cmd_q;
      DW_CLASS: rd_data_o = {CLASS_CODE, REVISION_ID};
      DW_CLS:   rd_data_o = {8'h00, HEADER_TYPE, 8'h00, 8'h00}
                                              | cls_q;
      DW_BUS:   rd_data_o = bus_q;
      DW_MEM:   rd_data_o = mem_q;
      default:                      rd_data_o = 32'd0;
    endcase
  end

  always_comb begin
    st_o.mse       = cmd_q[1];
    st_o.bme       = cmd_q[2];
    st_o.pri_bus   = bus_q[7:0];
    st_o.sec_bus   = bus_q[15:8];
    st_o.mem_base  = mem_q[15:4];
    st_o.mem_limit = mem_q[31:20];
  end

  // Only the RW bits are ever 1 in the registers.
  `COMMON_ASSERT(a_hdr_rw_only, clk_i, rst_i,
                 ((cmd_q & ~RW_CMD) == 32'd0)
                 && ((cls_q & ~RW_CLS) == 32'd0)
                 && ((bus_q & ~RW_BUS) == 32'd0)
                 && ((mem_q & ~RW_MEM) == 32'd0))

endmodule

`default_nettype wire
