// First-word-fall-through queue (wiki/src/rtl/common/ram/common_sdp_ram_fifo.md):
// entries in a common_sdp_ram, the head in the RAM's read register.
// A push at edge E shows at the head two edges later when the queue was
// empty; a pop and a refill share an edge, so the head can be taken every
// cycle. It holds 2**ADDR_W entries in the RAM plus the head.
`default_nettype none

`include "common_macros.svh"

module common_sdp_ram_fifo #(
  parameter int unsigned WIDTH  = 8,
  parameter int unsigned ADDR_W = 1
) (
  input  logic             clk_i,
  input  logic             rst_i,
  input  logic             push_i,
  input  logic [WIDTH-1:0] push_data_i,
  input  logic             pop_i,         // take the head (head_valid_o 1)
  output logic             head_valid_o,
  output logic [WIDTH-1:0] head_o,
  output logic             empty_o,       // no entry at all, head or RAM
  output logic             full_o,        // no room for a push
  output logic [ADDR_W:0]  ram_used_o     // entries in the RAM, the head not counted
);

  localparam int unsigned PTR_W = ADDR_W + 1;

  logic [PTR_W-1:0]  wr_q, wr_d;     // next RAM slot to write
  logic [PTR_W-1:0]  rd_q, rd_d;     // next RAM slot to load into the head
  logic              hv_q, hv_d;     // the head register holds an entry

  logic [PTR_W-1:0]  in_ram;
  logic              load;           // read the next RAM entry into the head at this edge
  logic [ADDR_W-1:0] wr_addr;
  logic [ADDR_W-1:0] rd_addr;
  logic [PTR_W-1:0]  wr_inc;
  logic [PTR_W-1:0]  rd_inc;

  assign in_ram  = wr_q - rd_q;
  assign full_o  = in_ram[ADDR_W];
  assign empty_o = !hv_q && in_ram == '0;
  // An entry written at an earlier edge is in the RAM: the read at this edge
  // never meets this edge's write (wr_q differs from rd_q).
  assign load    = in_ram != '0 && (!hv_q || pop_i);
  assign wr_addr = wr_q[ADDR_W-1:0];
  assign rd_addr = rd_q[ADDR_W-1:0];
  assign wr_inc  = wr_q + PTR_W'(1);
  assign rd_inc  = rd_q + PTR_W'(1);

  always_comb begin
    wr_d = push_i ? wr_inc : wr_q;
    rd_d = load ? rd_inc : rd_q;
    hv_d = load || (hv_q && !pop_i);
  end

  `COMMON_DFF_RST(wr_q, wr_d, clk_i, rst_i)
  `COMMON_DFF_RST(rd_q, rd_d, clk_i, rst_i)
  `COMMON_DFF_RST(hv_q, hv_d, clk_i, rst_i)

  // The RAM's read register is the head: it holds while no load.
  common_sdp_ram #(
    .WIDTH  (WIDTH),
    .ADDR_W (ADDR_W),
    .LANES  (1)
  ) u_ram (
    .clk_i      (clk_i),
    .wr_en_i    (push_i),
    .wr_lanes_i (1'b1),
    .wr_addr_i  (wr_addr),
    .wr_data_i  (push_data_i),
    .rd_en_i    (load),
    .rd_addr_i  (rd_addr),
    .rd_data_o  (head_o)
  );

  assign head_valid_o = hv_q;
  assign ram_used_o   = in_ram;

  `COMMON_ASSERT_NEVER(a_push_when_full, clk_i, rst_i, push_i && full_o)
  `COMMON_ASSERT_NEVER(a_pop_when_empty, clk_i, rst_i, pop_i && !hv_q)

endmodule

`default_nettype wire
