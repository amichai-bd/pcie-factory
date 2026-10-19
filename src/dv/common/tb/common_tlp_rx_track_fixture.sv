`default_nettype none

// Test top for common_tlp_rx_track
// (wiki/src/dv/common/common_tlp_rx_track.md): the tracker on documented
// ports, its t_tlp_rx_pos output split into one port per field.
module common_tlp_rx_track_fixture (
  input  logic       clk_i,
  input  logic       rst_i,
  input  logic       take_i,
  input  logic       sop_i,
  input  logic       eop_i,
  input  logic       hdr4_i,
  output logic       sop_o,
  output logic       mid_o,
  output logic       hdr_last_o,
  output logic [2:0] en_dw_o,
  output logic       busy_o,
  output logic       hdr4_o,
  output logic [5:0] beat_o,
  output logic [5:0] hdr_end_o
);

  common_pkg::t_tlp_rx_pos pos;

  common_tlp_rx_track u_dut (
    .clk_i  (clk_i),
    .rst_i  (rst_i),
    .take_i (take_i),
    .sop_i  (sop_i),
    .eop_i  (eop_i),
    .hdr4_i (hdr4_i),
    .pos_o  (pos)
  );

  assign sop_o      = pos.sop;
  assign mid_o      = pos.mid;
  assign hdr_last_o = pos.hdr_last;
  assign en_dw_o    = pos.en_dw;
  assign busy_o     = pos.busy;
  assign hdr4_o     = pos.hdr4;
  assign beat_o     = pos.beat;
  assign hdr_end_o  = pos.hdr_end;

endmodule

`default_nettype wire
