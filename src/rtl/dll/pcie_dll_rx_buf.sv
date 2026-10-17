// Receive buffer of pcie_dll (wiki/src/rtl/dll/MAS.md#receive-buffer): the
// stored TLPs, without sequence number and LCRC, in arrival order, one DW
// per word of a simple dual-port RAM, offered on the upper receive stream
// (wiki/src/arch/dll/HAS.md#receive). The TLP receive block writes each
// packet's DWs as they arrive and then commits or rewinds them, so only
// committed TLPs are ever read.
`default_nettype none

`include "common_macros.svh"

module pcie_dll_rx_buf #(
  parameter int unsigned ADDR_W = 9   // 2**ADDR_W DWs (MAS, receive buffer sizing)
) (
  input  logic                     clk_i,
  input  logic                     rst_i,
  input  logic                     flush_i,      // DL_Inactive: empty, abandon the TLP on the stream
  input  logic                     wr_en_i,
  input  pcie_dll_pkg::t_rx_word   wr_word_i,
  input  logic                     commit_i,     // keep everything written so far
  input  logic                     rewind_i,     // drop what was written since the last commit
  output logic                     full_o,       // no slot for another DW
  output logic                     rx_valid_o,
  input  logic                     rx_ready_i,
  output pcie_dll_pkg::t_rx_word   rx_word_o,
  output pcie_dll_pkg::t_rx_credit taken_o       // the upper layer took a TLP's eop beat
);

  localparam int unsigned PTR_W = ADDR_W + 1;   // the extra bit tells full from empty

  logic [PTR_W-1:0]  wr_q, wr_d;    // next slot to write
  logic [PTR_W-1:0]  cm_q, cm_d;    // end of the committed TLPs
  logic [PTR_W-1:0]  rd_q, rd_d;    // next slot to read
  logic              out_q, out_d;  // rx_word_o holds a beat not yet taken
  pcie_dll_pkg::t_rx_credit cur_q, cur_d;   // the TLP on the stream: class and credits

  logic [PTR_W-1:0]  used;
  logic              take;          // the upper layer takes the beat at this edge
  logic              rd_en;         // read the next committed DW at this edge
  logic [ADDR_W-1:0] wr_addr;
  logic [ADDR_W-1:0] rd_addr;
  logic [PTR_W-1:0]  wr_inc;
  logic [PTR_W-1:0]  rd_inc;
  logic [33:0]       wr_data;
  logic [33:0]       rd_data;
  logic              ram_wr;

  assign used    = wr_q - rd_q;
  assign full_o  = used[ADDR_W];
  assign take    = out_q && rx_ready_i;
  assign rd_en   = rd_q != cm_q && (!out_q || take) && !flush_i;
  assign ram_wr  = wr_en_i && !full_o;
  assign wr_addr = wr_q[ADDR_W-1:0];
  assign rd_addr = rd_q[ADDR_W-1:0];
  assign wr_inc  = wr_q + PTR_W'(1);
  assign rd_inc  = rd_q + PTR_W'(1);
  assign wr_data = wr_word_i;

  always_comb begin
    wr_d  = wr_q;
    cm_d  = cm_q;
    rd_d  = rd_q;
    out_d = out_q;
    cur_d = cur_q;
    if (rewind_i) begin
      wr_d = cm_q;
    end else if (ram_wr) begin
      wr_d = wr_inc;
    end
    if (commit_i) begin
      cm_d = wr_q;
    end
    if (rd_en) begin
      rd_d  = rd_inc;
      out_d = 1'b1;
    end else if (take) begin
      out_d = 1'b0;
    end
    if (take && rx_word_o.sop) begin
      // DW 0: Fmt bit 6 and Type in byte 0, Length in bytes 2 and 3.
      cur_d = pcie_dll_pkg::tlp_credit(rx_word_o.data[6], rx_word_o.data[4:0],
                                       {rx_word_o.data[17:16], rx_word_o.data[31:24]});
    end
    if (flush_i) begin
      wr_d  = '0;
      cm_d  = '0;
      rd_d  = '0;
      out_d = 1'b0;
    end
  end

  `COMMON_DFF_RST(wr_q, wr_d, clk_i, rst_i)
  `COMMON_DFF_RST(cm_q, cm_d, clk_i, rst_i)
  `COMMON_DFF_RST(rd_q, rd_d, clk_i, rst_i)
  `COMMON_DFF_RST(out_q, out_d, clk_i, rst_i)
  `COMMON_DFF_EN(cur_q, cur_d, clk_i, take)

  // The RAM's read register is the stream's output register: a read at an
  // edge offers the DW from the next cycle, and with rd_en low it holds.
  common_sdp_ram #(
    .WIDTH  (34),
    .ADDR_W (ADDR_W),
    .LANES  (1)
  ) u_ram (
    .clk_i      (clk_i),
    .wr_en_i    (ram_wr),
    .wr_lanes_i (1'b1),
    .wr_addr_i  (wr_addr),
    .wr_data_i  (wr_data),
    .rd_en_i    (rd_en),
    .rd_addr_i  (rd_addr),
    .rd_data_o  (rd_data)
  );

  assign rx_word_o  = rd_data;
  assign rx_valid_o = out_q;

  always_comb begin
    taken_o       = cur_q;
    taken_o.valid = take && rx_word_o.eop;
  end

  // The TLP receive block never writes into a full buffer or while it
  // commits or rewinds (MAS, receive buffer).
  `COMMON_ASSERT_NEVER(a_write_when_full, clk_i, rst_i, wr_en_i && full_o)
  `COMMON_ASSERT_NEVER(a_write_at_verdict, clk_i, rst_i, wr_en_i && (commit_i || rewind_i))
  `COMMON_ASSERT_NEVER(a_commit_and_rewind, clk_i, rst_i, commit_i && rewind_i)

endmodule

`default_nettype wire
