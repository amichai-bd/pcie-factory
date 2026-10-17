// DLLP receive of pcie_dll (wiki/src/rtl/dll/MAS.md#dllp-receive): collects
// each packet tagged DLLP from the phy packet stream and, at its eop,
// checks it (phy flag, 6-byte length, CRC-16; wiki/src/arch/dll/DLLP.md,
// receiving DLLPs). A good DLLP leaves decoded on rx_dllp_o, a bad one
// pulses bad_dllp_o; both one cycle after the eop beat. TLP-tagged packets
// are not this block's: the TLP receive path (#213) takes them.
`default_nettype none

`include "common_macros.svh"

module pcie_dll_dllp_rx (
  input  logic                   clk_i,
  input  logic                   rst_i,
  input  logic                   flush_i,     // DL_Inactive or link down: discard
  input  pcie_dll_pkg::t_pkt_rx  pkt_rx_i,
  output pcie_dll_pkg::t_rx_dllp rx_dllp_o,   // a good DLLP, for one cycle
  output logic                   bad_dllp_o   // a bad DLLP, for one cycle
);

  pcie_dll_pkg::t_rx_asm  asm_q, asm_d;
  pcie_dll_pkg::t_rx_dllp dllp_q, dllp_d;
  logic                   bad_q, bad_d;

  logic        beat_dllp;  // a beat of a DLLP-tagged packet
  logic        at_end;     // the eop beat of a DLLP being collected
  logic        len_ok;     // the eop beat is the third: 6 bytes
  logic        crc_ok;     // bytes 4 and 5 match the CRC-16 of bytes 0 to 3

  // The tag is held from sop to eop; a TLP-tagged packet is ignored here.
  assign beat_dllp = pkt_rx_i.valid && pkt_rx_i.dllp && (pkt_rx_i.sop || asm_q.open);
  assign at_end    = beat_dllp && pkt_rx_i.eop;
  assign len_ok    = !pkt_rx_i.sop && asm_q.beats == 2'd2;
  assign crc_ok    = pcie_dll_pkg::crc16(asm_q.body) == pkt_rx_i.data;

  always_comb begin
    asm_d  = asm_q;
    dllp_d = '0;
    bad_d  = 1'b0;
    if (flush_i) begin
      asm_d = '0;
    end else if (beat_dllp) begin
      if (pkt_rx_i.sop) begin
        asm_d.beats           = 2'd1;
        asm_d.body[15:0]      = pkt_rx_i.data;
      end else begin
        if (asm_q.beats == 2'd1) begin
          asm_d.body[31:16] = pkt_rx_i.data;
        end
        if (asm_q.beats != 2'd3) begin
          asm_d.beats = asm_q.beats + 2'd1;
        end
      end
      asm_d.open = !pkt_rx_i.eop;
      if (at_end) begin
        if (!pkt_rx_i.err && len_ok && crc_ok) begin
          dllp_d = pcie_dll_pkg::dllp_decode(asm_q.body[7:0],
                                              {asm_q.body[13:8], asm_q.body[23:22]},
                                              {asm_q.body[19:16], asm_q.body[31:24]});
        end else begin
          bad_d = 1'b1;
        end
      end
    end
  end

  `COMMON_DFF_RST(asm_q, asm_d, clk_i, rst_i)
  `COMMON_DFF_RST(dllp_q, dllp_d, clk_i, rst_i)
  `COMMON_DFF_RST(bad_q, bad_d, clk_i, rst_i)

  assign rx_dllp_o  = dllp_q;
  assign bad_dllp_o = bad_q;

  // A DLLP cannot be nullified (the phy reports a DLLP ended by EDB as an
  // error, DLLP.md); the TLP path reads nullify.
  `COMMON_UNUSED_BITS(unused_rx, {pkt_rx_i.nullify})

  `COMMON_ASSERT_NEVER(a_good_and_bad, clk_i, rst_i, rx_dllp_o.valid && bad_dllp_o)

endmodule

`default_nettype wire
