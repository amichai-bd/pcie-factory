// DV-only cycle model of the sky130 OpenRAM macro sky130_sram_1rw1r_64x256_8
// (256 x 64 bits, port 0 read or write with byte mask, port 1 read), used
// to lint and simulate the common RAM sky130 backend
// (wiki/src/rtl/common/ram/common_sdp_ram.md#sky130-backend). Never in an
// ASIC source list: the flow uses the macro's own LEF, Liberty and GDS
// views from the pinned ORFS image.
//
// Written from the macro's port list and the behavior its OpenRAM model and
// Liberty view describe: every input is sampled at the rising clock edge;
// a write lands in the selected bytes; a read returns the addressed word
// for the cycle after its edge only. A cycle without a read drives a fixed
// junk pattern, so a consumer that relies on the output holding fails.
// A port 1 read of the word port 0 writes at the same edge is undefined for
// the macro and fatal here. It models no timing (the real read data
// arrives after the falling edge).
//
// Provenance: the pattern (a strict DV-only macro model that is fatal on
// every physical collision) is adapted from nand2anything
// src/common/dv/memory/n2a_ihp_edge_2p_model.sv (commit 8bb70d2), adapted
// with the owner's permission; the model itself is new.
`default_nettype none

module sky130_sram_1rw1r_64x256_8 (
  input  logic        clk0,
  input  logic        csb0,
  input  logic        web0,
  input  logic [7:0]  wmask0,
  input  logic [7:0]  addr0,
  input  logic [63:0] din0,
  output logic [63:0] dout0,
  input  logic        clk1,
  input  logic        csb1,
  input  logic [7:0]  addr1,
  output logic [63:0] dout1
);

  localparam logic [63:0] JUNK = 64'hDEAD_BEEF_DEAD_BEEF;

  logic [63:0] mem [256];

  always_ff @(posedge clk0) begin
    if (!csb0 && !web0) begin
      for (int b = 0; b < 8; b++) begin
        if (wmask0[b]) begin
          mem[addr0][b*8 +: 8] <= din0[b*8 +: 8];
        end
      end
    end
    dout0 <= (!csb0 && web0) ? mem[addr0] : JUNK;
  end

  always_ff @(posedge clk1) begin
    if (!csb1 && !csb0 && !web0 && addr1 == addr0) begin
      $fatal(1, "sky130_sram_1rw1r_64x256_8: port 1 reads word %0d while port 0 writes it", addr1);
    end
    dout1 <= !csb1 ? mem[addr1] : JUNK;
  end

endmodule

`default_nettype wire
