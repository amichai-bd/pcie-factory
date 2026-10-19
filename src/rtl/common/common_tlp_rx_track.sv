// Receive beat tracker (wiki/src/rtl/common/common_tlp_rx_track.md): on a
// TLP stream that delivers whole TLPs one DW per beat and never stalls,
// says where each beat taken sits in its TLP: a sop, a later beat, the
// last header DW, header DW 0 to 2; it counts the TLP's beats, saturating
// at 63, and keeps its header size. Its assertions state the whole-TLP
// contract of the stream.
`default_nettype none

`include "common_macros.svh"

module common_tlp_rx_track (
  input  logic                     clk_i,
  input  logic                     rst_i,
  input  logic                     take_i,   // a beat taken at this edge
  input  logic                     sop_i,    // with take_i: it starts a TLP
  input  logic                     eop_i,    // with take_i: it ends its TLP
  input  logic                     hdr4_i,   // with a sop: the TLP has a 4-DW header
  output common_pkg::t_tlp_rx_pos  pos_o
);

  common_pkg::t_tlp_rx_trk st_q, st_d;

  logic       take_sop;
  logic       take_mid;
  logic [5:0] hdr_end;
  logic [5:0] beat_inc;

  assign take_sop = take_i && sop_i;
  assign take_mid = take_i && !sop_i && st_q.busy;
  assign hdr_end  = st_q.hdr4 ? 6'd3 : 6'd2;
  assign beat_inc = (st_q.beat == 6'h3F) ? st_q.beat : st_q.beat + 6'd1;

  always_comb begin
    st_d = st_q;
    if (take_sop) begin
      st_d.busy = 1'b1;
      st_d.beat = 6'd1;
      st_d.hdr4 = hdr4_i;
    end else if (take_mid) begin
      st_d.beat = beat_inc;
    end
    if (take_i && eop_i) begin
      st_d.busy = 1'b0;
    end
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)

  always_comb begin
    pos_o          = '0;
    pos_o.sop      = take_sop;
    pos_o.mid      = take_mid;
    pos_o.hdr_last = take_mid && st_q.beat == hdr_end;
    pos_o.en_dw[0] = take_sop;
    pos_o.en_dw[1] = take_mid && st_q.beat == 6'd1;
    pos_o.en_dw[2] = take_mid && st_q.beat == 6'd2;
    pos_o.busy     = st_q.busy;
    pos_o.hdr4     = st_q.hdr4;
    pos_o.beat     = st_q.beat;
    pos_o.hdr_end  = hdr_end;
  end

  // The stream delivers whole TLPs: a sop starts one, no beat comes outside
  // one, and the eop is no earlier than the last header DW.
  `COMMON_ASSERT_NEVER(a_beat_outside_tlp, clk_i, rst_i, take_i && !sop_i && !st_q.busy)
  `COMMON_ASSERT_NEVER(a_sop_inside_tlp, clk_i, rst_i, take_sop && st_q.busy)
  `COMMON_ASSERT_NEVER(a_short_tlp, clk_i, rst_i,
                       take_i && eop_i && (sop_i || st_q.beat < hdr_end))

endmodule

`default_nettype wire
