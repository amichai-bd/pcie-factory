// pcie_rc_seq program ROM: the image PROGRAM selects, read at pc_i with a
// registered output, so the FPGA flow may map it to block memory. An index
// at or above ROM_STEPS reads as FAIL, the all-zero word
// (wiki/src/rtl/rc_seq/MAS.md#programs).
`default_nettype none

`include "common_macros.svh"

module pcie_rc_seq_rom #(
  parameter int unsigned PROGRAM = 1     // program id (MAS: Programs)
) (
  input  logic                   clk_i,
  input  logic [15:0]            pc_i,    // step index
  output pcie_rc_seq_pkg::t_step word_o   // the step at the pc_i of the previous edge
);

  localparam pcie_rc_seq_pkg::t_prog PRG = pcie_rc_seq_pkg::program_of(PROGRAM);

  // t_prog is the packed {rom, n}: its fields are taken by part-select,
  // because Quartus Lite rejects a member select of a struct localparam
  // in a constant expression (error 10742, "hierarchical identifier";
  // #379). The layout check keeps the part-selects equal to the members.
  localparam int unsigned N_W = 16;
  localparam logic [N_W-1:0]        PRG_N = PRG[N_W-1:0];
  localparam pcie_rc_seq_pkg::t_rom IMAGE = PRG[$bits(pcie_rc_seq_pkg::t_prog)-1:N_W];

  `COMMON_ASSERT_PARAM(g_prog_layout, $bits(pcie_rc_seq_pkg::t_prog) == $bits(pcie_rc_seq_pkg::t_rom) + N_W,
                       "pcie_rc_seq_rom: t_prog is not {rom, n[15:0]}")
  `COMMON_ASSERT_PARAM(g_program_fits, PRG_N <= 16'(pcie_rc_seq_pkg::ROM_STEPS),
                       "pcie_rc_seq_rom: the program is longer than the ROM")

  logic [pcie_rc_seq_pkg::ROM_AW-1:0] rom_idx;
  logic                               in_rom;
  pcie_rc_seq_pkg::t_step             word_d;
  pcie_rc_seq_pkg::t_step             word_q;

  assign rom_idx = pc_i[pcie_rc_seq_pkg::ROM_AW-1:0];
  assign in_rom  = (pc_i >> pcie_rc_seq_pkg::ROM_AW) == 16'd0;
  assign word_d  = in_rom ? IMAGE[rom_idx] : '0;

  `COMMON_DFF(word_q, word_d, clk_i)

  assign word_o = word_q;

endmodule

`default_nettype wire
