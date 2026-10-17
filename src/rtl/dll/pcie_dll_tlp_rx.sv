// TLP receive of pcie_dll (wiki/src/rtl/dll/MAS.md#tlp-receive): collects
// each TLP-tagged packet from the phy packet stream, writes its TLP DWs
// into the receive buffer as they arrive and computes its LCRC; at the
// eop the packet is checked, and the next cycle judged
// (wiki/src/arch/dll/RETRY.md#receiving-tlps): a good, expected, well-formed
// TLP that its credits cover is committed to the buffer, every other packet
// is rewound out of it. It keeps the receiver state (NEXT_RCV_SEQ,
// NAK_SCHEDULED, the Ack timer) and requests Acks and Naks from the
// transmit arbiter (RETRY.md, Ack and Nak scheduling). DLLP-tagged packets
// are not this block's: pcie_dll_dllp_rx takes them.
`default_nettype none

`include "common_macros.svh"

module pcie_dll_tlp_rx (
  input  logic                          clk_i,
  input  logic                          rst_i,
  input  logic                          flush_i,       // DL_Inactive or link down: discard
  input  pcie_dll_pkg::t_dl_state       state_i,
  input  pcie_dll_pkg::t_pkt_rx         pkt_rx_i,
  // Receive buffer
  input  logic                          buf_full_i,    // no slot for another DW
  output logic                          wr_en_o,       // write wr_word_o at the next free slot
  output pcie_dll_pkg::t_rx_word        wr_word_o,
  output logic                          commit_o,      // keep the TLP written since the last commit
  output logic                          rewind_o,      // drop it
  // Flow-control accounting (#214)
  output pcie_dll_pkg::t_rx_credit      chk_o,         // a TLP to store: does it fit?
  input  logic                          fit_i,         // its class's free credits cover it
  output pcie_dll_pkg::t_rx_credit      stored_o,      // a TLP stored
  output pcie_dll_pkg::t_rx_credit      malformed_o,   // a malformed TLP accepted
  // Control, errors, Ack and Nak
  output logic                          good_o,        // a TLP with a good LCRC (ends FC_INIT2)
  output logic                          bad_tlp_o,     // err_o bit 0, one cycle
  output logic                          malformed_err_o, // err_o bit 6, one cycle
  output pcie_dll_pkg::t_dllp_req       acknak_req_o,  // priority 1 and 2: a due Nak or Ack
  input  logic                          acknak_take_i  // the arbiter takes it at this edge
);

  localparam logic [7:0] BEATS_MAX = 8'd255;

  pcie_dll_pkg::t_tlp_asm      asm_q, asm_d;
  pcie_dll_pkg::t_tlp_chk      chk_q, chk_d;
  pcie_dll_pkg::t_acknak_state ak_q, ak_d;
  logic                        bad_q, bad_d;
  logic                        malf_q, malf_d;

  // Collection
  logic        beat_tlp;    // a beat of a TLP-tagged packet
  logic [7:0]  k;           // index of this beat in its packet (not sop)
  logic        release_b2;  // beat k-2 leaves the delay line into the LCRC
  logic        rel_tlp;     // ... and it is TLP bytes, not the sequence number
  logic        rel_hi;      // ... the second half of a TLP DW
  logic        wr_want;     // a TLP DW is complete
  logic [31:0] crc_rel;     // the LCRC register with beat k-2 added
  logic [31:0] crc_end;     // the LCRC register over every beat but the last two
  logic [31:0] lcrc_exp;    // the LCRC bytes that register gives
  logic [31:0] lcrc_rx;     // the last two beats
  logic [15:0] dw_hi;       // the beat that completes a DW

  // Judgement
  logic        live;        // FC_INIT2 or DL_Active: TLPs are received
  logic        short_pkt;   // below 6 bytes: no room for an LCRC
  logic        bad;         // flagged, too short, bad LCRC or EDB without the inverse
  logic        good;        // good LCRC
  logic [11:0] seq_dist;     // sequence number minus NEXT_RCV_SEQ
  logic        expected;
  logic        ahead;
  logic        dup;
  logic        malformed;
  logic        cand;        // expected and well-formed: store if it fits
  logic        store;
  logic        accept;      // NEXT_RCV_SEQ advances
  logic        nak_now;     // schedule a Nak
  logic        ack_time;    // the Ack timer has reached its limit
  logic [11:0] last_seq;    // NEXT_RCV_SEQ - 1

  // ------------------------------------------------------------ collect

  assign beat_tlp   = pkt_rx_i.valid && !pkt_rx_i.dllp && (pkt_rx_i.sop || asm_q.open);
  assign k          = asm_q.beats;
  assign release_b2 = beat_tlp && !pkt_rx_i.sop && k >= 8'd2;
  assign rel_tlp    = release_b2 && k >= 8'd3;
  // TLP beat t = k-3 leaves; t odd (k even) completes a DW.
  assign rel_hi     = rel_tlp && !k[0];
  assign wr_want    = rel_hi && asm_q.dws != pcie_dll_pkg::RX_MAX_TLP_DW;
  assign dw_hi      = asm_q.b2;
  assign crc_rel    = pcie_dll_pkg::lcrc_step(asm_q.crc, asm_q.b2);
  assign crc_end    = release_b2 ? crc_rel : asm_q.crc;
  assign lcrc_exp   = pcie_dll_pkg::lcrc_bytes(crc_end);
  assign lcrc_rx    = {pkt_rx_i.data, asm_q.b1};

  // A DW is written while the packet has had room for every earlier one,
  // so a stored TLP has no hole.
  assign wr_en_o   = wr_want && !buf_full_i && !asm_q.noroom && !flush_i;
  assign wr_word_o = '{data: {dw_hi, asm_q.lo}, sop: asm_q.dws == 6'd0, eop: pkt_rx_i.eop};

  always_comb begin
    asm_d = asm_q;
    chk_d = '0;
    if (beat_tlp) begin
      if (pkt_rx_i.sop) begin
        asm_d.beats  = 8'd1;
        asm_d.dws    = 6'd0;
        asm_d.noroom = 1'b0;
        asm_d.seq    = {pkt_rx_i.data[3:0], pkt_rx_i.data[15:8]};
        asm_d.crc    = 32'hFFFF_FFFF;
      end else begin
        if (k != BEATS_MAX) begin
          asm_d.beats = k + 8'd1;
        end
        if (release_b2) begin
          asm_d.crc = crc_rel;
        end
        if (rel_tlp && k[0]) begin
          asm_d.lo = asm_q.b2;
        end
        if (wr_want) begin
          asm_d.dws = asm_q.dws + 6'd1;
          if (asm_q.dws == 6'd0) begin
            asm_d.hdr = pcie_dll_pkg::tlp_hdr(asm_q.lo[6:0], dw_hi[1:0], dw_hi[15:8]);
          end
          if (buf_full_i) begin
            asm_d.noroom = 1'b1;
          end
        end
        asm_d.b2 = asm_q.b1;
      end
      asm_d.b1   = pkt_rx_i.data;
      asm_d.open = !pkt_rx_i.eop;
      if (pkt_rx_i.eop) begin
        chk_d.valid    = 1'b1;
        chk_d.beats    = asm_d.beats;
        chk_d.seq      = asm_d.seq;
        chk_d.hdr      = asm_d.hdr;
        chk_d.err      = pkt_rx_i.err;
        chk_d.nullify  = pkt_rx_i.nullify;
        chk_d.lcrc_ok  = lcrc_rx == lcrc_exp;
        chk_d.lcrc_inv = lcrc_rx == ~lcrc_exp;
        chk_d.noroom   = asm_d.noroom;
      end
    end
    if (flush_i) begin
      asm_d = '0;
      chk_d = '0;
    end
  end

  `COMMON_DFF_RST(asm_q, asm_d, clk_i, rst_i)
  `COMMON_DFF_RST(chk_q, chk_d, clk_i, rst_i)

  // -------------------------------------------------------------- judge

  assign live      = (state_i == pcie_dll_pkg::DL_INIT2 || state_i == pcie_dll_pkg::DL_ACTIVE)
                  && !flush_i;
  assign short_pkt = chk_q.beats < 8'd3;
  assign bad       = live && chk_q.valid
                  && (chk_q.err || short_pkt || (chk_q.nullify ? !chk_q.lcrc_inv : !chk_q.lcrc_ok));
  assign good      = live && chk_q.valid && !chk_q.err && !short_pkt && !chk_q.nullify
                  && chk_q.lcrc_ok;
  assign seq_dist  = chk_q.seq - ak_q.nrs;
  assign expected  = good && seq_dist == 12'd0;
  assign ahead     = good && seq_dist != 12'd0 && !seq_dist[11];
  assign dup       = good && seq_dist[11];
  assign malformed = expected && pcie_dll_pkg::tlp_malformed(chk_q.beats, chk_q.hdr.hdr4,
                                                             chk_q.hdr.with_data, chk_q.hdr.length);
  assign cand      = expected && !malformed;
  assign store     = cand && fit_i && !chk_q.noroom;
  assign accept    = malformed || store;
  assign nak_now   = bad || ahead;

  assign commit_o = store;
  assign rewind_o = chk_q.valid && !store;
  assign good_o   = good;

  // Events for the flow-control accounting: the class and credits DW 0
  // states; a malformed TLP shorter than one DW (below 5 beats) has none.
  // chk_o has its own process: fit_i answers it combinationally and store
  // depends on fit_i, so one process for all three would be a loop.
  always_comb begin
    chk_o       = pcie_dll_pkg::tlp_credit(chk_q.hdr.with_data, chk_q.hdr.typ, chk_q.hdr.length);
    chk_o.valid = cand;
  end

  always_comb begin
    stored_o    = chk_o;
    malformed_o = chk_o;
    stored_o.valid    = store;
    malformed_o.valid = malformed;
    if (chk_q.beats < 8'd5) begin
      malformed_o.cred = '0;
    end
  end

  // --------------------------------------------------- receiver state

  assign ack_time = ak_q.timer_on && ak_q.timer == pcie_dll_pkg::ACK_LATENCY;
  assign last_seq = ak_q.nrs - 12'd1;

  always_comb begin
    ak_d = ak_q;
    if (ak_q.timer_on && ak_q.timer != pcie_dll_pkg::ACK_LATENCY) begin
      ak_d.timer = ak_q.timer + 6'd1;
    end
    // Sending an Ack or Nak covers every TLP received so far.
    if (acknak_take_i) begin
      ak_d.nak_due  = 1'b0;
      ak_d.ack_due  = 1'b0;
      ak_d.timer_on = 1'b0;
      ak_d.timer    = 6'd0;
    end
    if (accept) begin
      ak_d.nrs       = ak_q.nrs + 12'd1;
      ak_d.nak_sched = 1'b0;
      if (!ak_d.timer_on) begin
        ak_d.timer_on = 1'b1;
        ak_d.timer    = 6'd0;
      end
    end
    if (nak_now && !ak_q.nak_sched) begin
      ak_d.nak_sched = 1'b1;
      ak_d.nak_due   = 1'b1;
    end
    if (dup) begin
      ak_d.ack_due = 1'b1;
    end
    if (flush_i) begin
      ak_d = '0;
    end
  end

  `COMMON_DFF_RST(ak_q, ak_d, clk_i, rst_i)

  assign acknak_req_o.valid = state_i == pcie_dll_pkg::DL_ACTIVE
                           && (ak_q.nak_due || ak_q.ack_due || ack_time);
  assign acknak_req_o.body  = {last_seq[7:0], 4'h0, last_seq[11:8], 8'h00,
                               ak_q.nak_due ? pcie_dll_pkg::DLLP_NAK : pcie_dll_pkg::DLLP_ACK};

  // ------------------------------------------------------------ errors

  assign bad_d  = nak_now;   // Bad TLP: every packet that schedules a Nak
  assign malf_d = malformed;

  `COMMON_DFF_RST(bad_q, bad_d, clk_i, rst_i)
  `COMMON_DFF_RST(malf_q, malf_d, clk_i, rst_i)

  assign bad_tlp_o       = bad_q;
  assign malformed_err_o = malf_q;

  `COMMON_ASSERT_NEVER(a_one_verdict, clk_i, rst_i, bad && good)
  `COMMON_ASSERT_NEVER(a_take_without_req, clk_i, rst_i, acknak_take_i && !acknak_req_o.valid)

endmodule

`default_nettype wire
