// pcie_cfg BAR2: the MSI-X table (address and data DWs in a common RAM,
// the Mask bits in flops) and the read of any BAR2 DW, the pending bits
// included (wiki/src/arch/cfg/CAPS.md#bar2, wiki/src/rtl/cfg/MAS.md#bar2).
// The RAM read port serves a BAR2 read and the message block's entry
// lookup, never both at one edge.
`default_nettype none

`include "common_macros.svh"

module pcie_cfg_bar2 #(
  parameter int unsigned MSIX_TABLE_SIZE = 8
) (
  input  logic                       clk_i,
  input  logic                       rst_i,
  // A BAR2 write or read taken at this edge, and the access fields.
  input  logic                       wr_i,
  input  logic                       rd_i,
  input  logic [9:0]                 addr_i,
  input  logic [3:0]                 be_i,
  input  logic [31:0]                data_i,
  // Entry lookup for an interrupt write: entry_o shows it after this edge.
  input  logic                       look_i,
  input  logic [5:0]                 look_vec_i,
  input  logic [MSIX_TABLE_SIZE-1:0] pba_i,
  output logic [MSIX_TABLE_SIZE-1:0] mask_q_o,
  output logic [MSIX_TABLE_SIZE-1:0] mask_d_o,
  output pcie_cfg_pkg::t_entry       entry_o,
  // The DW read at the last BAR2 read edge; valid in the cycle after it.
  output logic [31:0]                rd_data_o
);

  localparam int unsigned ADDR_W = (MSIX_TABLE_SIZE <= 1) ? 1 : $clog2(MSIX_TABLE_SIZE);
  localparam logic [MSIX_TABLE_SIZE-1:0] MASK_RST = '1;
  localparam logic [7:0] ENTRIES = 8'(MSIX_TABLE_SIZE);

  logic [5:0]              entry;      // table entry the access addresses
  logic [1:0]              field;      // 0 address, 1 upper address, 2 data, 3 mask
  logic                    in_table;
  logic                    ram_field;  // the access is to a RAM DW
  logic                    ram_wr;
  logic [11:0]             ram_lanes;
  logic [95:0]             ram_wdata;
  logic                    ram_rd;
  logic [ADDR_W-1:0]       ram_waddr;
  logic [ADDR_W-1:0]       ram_raddr;
  logic [5:0]              rd_entry;
  logic [95:0]             ram_q;
  logic [MSIX_TABLE_SIZE-1:0] mask_q, mask_d;
  logic [63:0]             mask64;
  logic [63:0]             pba64;
  logic [31:0]             rd_d, rd_q;
  logic                    rd_ram_d, rd_ram_q;
  logic [1:0]              rd_field_q;

  always_comb begin
    entry     = addr_i[7:2];
    field     = addr_i[1:0];
    in_table  = (addr_i[9:8] == 2'd0) && ({2'd0, entry} < ENTRIES);
    ram_field = in_table && (field != 2'd3);
    ram_wr    = wr_i && ram_field;
    ram_wdata = {data_i, data_i, data_i};
    ram_lanes = 12'({8'd0, be_i} << {field, 2'b00});
    ram_waddr = entry[ADDR_W-1:0];

    // Mask bits: bit 0 of DW 3 of each entry, reset 1.
    mask_d = mask_q;
    for (int i = 0; i < int'(MSIX_TABLE_SIZE); i++) begin
      if (wr_i && in_table && field == 2'd3 && be_i[0] && entry == 6'(i)) begin
        mask_d[i] = data_i[0];
      end
    end

    // Read data that does not come from the RAM, as held before this edge.
    mask64 = 64'(mask_q);
    pba64  = 64'(pba_i);
    if (in_table && field == 2'd3) begin
      rd_d = {31'd0, mask64[entry]};
    end else if (addr_i == pcie_cfg_pkg::DW_PBA0) begin
      rd_d = pba64[31:0];
    end else if (addr_i == pcie_cfg_pkg::DW_PBA1) begin
      rd_d = pba64[63:32];
    end else begin
      rd_d = 32'd0;
    end
    rd_ram_d = ram_field;
  end

  // The read port: a BAR2 read, else the lookup (kept apart from the block
  // above, which the lookup's request depends on through mask_d_o).
  assign ram_rd    = (rd_i && ram_field) || look_i;
  assign rd_entry  = look_i ? look_vec_i : entry;
  assign ram_raddr = rd_entry[ADDR_W-1:0];

  common_sdp_ram #(
    .WIDTH (96),
    .ADDR_W(ADDR_W),
    .LANES (12)
  ) u_table (
    .clk_i     (clk_i),
    .wr_en_i   (ram_wr),
    .wr_lanes_i(ram_lanes),
    .wr_addr_i (ram_waddr),
    .wr_data_i (ram_wdata),
    .rd_en_i   (ram_rd),
    .rd_addr_i (ram_raddr),
    .rd_data_o (ram_q)
  );

  `COMMON_DFF_RST_VAL(mask_q, mask_d, clk_i, rst_i, MASK_RST)
  `COMMON_DFF_EN(rd_q, rd_d, clk_i, rd_i)
  `COMMON_DFF_EN(rd_ram_q, rd_ram_d, clk_i, rd_i)
  `COMMON_DFF_EN(rd_field_q, field, clk_i, rd_i)

  // The table word with Message Address bits 1:0 forced 0: they are
  // read-only 0, so the RAM stores them as written and the read clears them
  // (for a word never written as well).
  always_comb begin
    entry_o         = ram_q;
    entry_o.addr_lo = {ram_q[31:2], 2'b00};
    case (rd_field_q)
      2'd0:    rd_data_o = rd_ram_q ? entry_o.addr_lo : rd_q;
      2'd1:    rd_data_o = rd_ram_q ? entry_o.addr_hi : rd_q;
      2'd2:    rd_data_o = rd_ram_q ? entry_o.data : rd_q;
      default: rd_data_o = rd_q;
    endcase
  end

  assign mask_q_o = mask_q;
  `COMMON_UNUSED_BITS(unused_rd_entry, rd_entry)
  assign mask_d_o = mask_d;

  // The lookup never shares an edge with a BAR2 access (MAS, BAR2).
  `COMMON_ASSERT_NEVER(a_one_reader, clk_i, rst_i, look_i && (rd_i || wr_i))
  `COMMON_ASSERT_NEVER(a_look_range, clk_i, rst_i, look_i && ({2'd0, look_vec_i} >= ENTRIES))

endmodule

`default_nettype wire
