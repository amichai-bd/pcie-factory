// TLP transmit of pcie_dll (wiki/src/rtl/dll/MAS.md#tlp-transmit): takes
// TLPs from the upper stream, gives each the next sequence number and its
// LCRC, stores it in the replay buffer (a common_sdp_ram of 256 words)
// and offers it to the transmit arbiter as priorities 5 (replay) and 6
// (new TLP); acts on received Ack and Nak, runs the replay timer, REPLAY_NUM
// and its rollover hold (wiki/src/arch/dll/RETRY.md). The credit gate is
// an input (#214).
`default_nettype none

`include "common_macros.svh"

module pcie_dll_tlp_tx #(
  parameter int unsigned REPLAY_TIMEOUT = 356   // replay timeout, clk_i cycles in L0 (pcie_dll)
) (
  input  logic                      clk_i,
  input  logic                      rst_i,
  input  logic                      flush_i,        // DL_Inactive: clear everything
  input  pcie_dll_pkg::t_dl_state   state_i,
  input  logic [5:0]                ltssm_state_i,  // L0 is 0x20..0x2F
  // Upper transmit stream
  input  logic                      tlp_valid_i,
  input  pcie_dll_pkg::t_tlp_beat   tlp_i,
  output logic                      tlp_ready_o,
  input  logic                      credit_ok_i,    // the partner's credits cover the sop beat's TLP (#214)
  // Received DLLPs (Ack and Nak)
  input  pcie_dll_pkg::t_rx_dllp    rx_dllp_i,
  // Transmit arbiter
  output pcie_dll_pkg::t_tlp_src    src_o,          // priorities 5 and 6
  input  logic                      take_i,         // the arbiter starts the offered TLP at this edge
  input  logic                      beat_i,         // the phy takes a TLP beat at this edge
  input  logic                      abort_i,        // the TLP offered is withdrawn before its sop was taken
  output logic                      hold_o,         // rollover hold, or a rollover now: no TLP starts
  // Errors (err_o bits 4:2 of the HAS) and the retrain request
  output logic [2:0]                err_o,          // {DL protocol error, rollover, replay timeout}
  output logic                      retrain_req_o
);

  localparam int unsigned SLOTS = 2 ** pcie_dll_pkg::RBUF_SLOT_W;
  // REPLAY_TIMER counts 0 to REPLAY_TIMEOUT - 1: 9 bits at the default 356.
  localparam int unsigned RT_W  = $clog2(REPLAY_TIMEOUT);
  localparam logic [RT_W-1:0] RT_LAST = RT_W'(REPLAY_TIMEOUT - 1);

  pcie_dll_pkg::t_retry_state st_q, st_d;
  pcie_dll_pkg::t_tlp_stream  sm_q, sm_d;
  logic [7:0]                 end_q [SLOTS];   // word after each held TLP, by seq mod 64
  logic [7:0]                 end_d [SLOTS];
  logic [RT_W-1:0]            rt_q, rt_d;      // REPLAY_TIMER (st_q.rt_run: running)
  logic [2:0]                 err_q, err_d;
  logic                       retrain_q, retrain_d;

  // ------------------------------------------------------------ upper stream

  logic        active;       // DL_Active
  logic        in_l0;        // ltssm_state_i is an L0 code
  logic [11:0] held;         // TLPs holding a sequence number, not acknowledged
  logic        lock;         // the TLP on the phy stream has been acknowledged
  logic [7:0]  used_words;
  logic [11:0] used_tlps;
  logic [13:0] used_bytes;   // framed bytes in the replay buffer
  logic [13:0] need_bytes;   // used + the offered TLP + 6
  logic [10:0] sop_pdw;      // payload DWs the offered sop beat's header states
  logic [12:0] sop_bytes;    // the bytes of its TLP
  logic        room_ok;
  logic        beat_take;    // the upper stream's beat is taken at this edge
  logic        sop_take;
  logic        eop_take;
  logic [15:0] seq_beat;     // the sequence-number beat of the TLP being taken
  logic [31:0] crc_in;
  logic [31:0] crc_lo;
  logic [31:0] crc_next;

  assign active = state_i == pcie_dll_pkg::DL_ACTIVE;
  assign in_l0  = ltssm_state_i[5:4] == 2'b10;
  assign held   = st_q.seq_alloc - st_q.ackd - 12'd1;
  // Each held TLP takes n + 4 bytes of words (TLP and LCRC) and counts
  // n + 6 (its sequence number too), as the HAS sizes the buffer. A TLP an
  // Ack removed while it is on the phy stream keeps its words, and so does
  // every TLP after it, until its eop is taken (RETRY.md, replay buffer):
  // the next words written would otherwise overwrite it.
  assign lock       = sm_q.active && (sm_q.seq - st_q.ackd - 12'd1) >= 12'd2048;
  assign used_words = lock ? st_q.wr_addr - sm_q.start : st_q.wr_addr - st_q.base;
  assign used_tlps  = lock ? st_q.seq_alloc - sm_q.seq : held;
  assign used_bytes = {4'd0, used_words, 2'b00} + {1'b0, used_tlps, 1'b0};
  // A 3- or 4-DW header (Fmt bit 0, byte 0 bit 5) and the payload (Fmt
  // bit 1, byte 0 bit 6; Length in byte 2 bits 1:0 and byte 3).
  assign sop_pdw    = pcie_dll_pkg::tlp_payload_dw(tlp_i.data[6], {tlp_i.data[17:16],
                                                                   tlp_i.data[31:24]});
  assign sop_bytes  = (tlp_i.data[5] ? 13'd16 : 13'd12) + {sop_pdw, 2'b00};
  assign need_bytes = used_bytes + {1'b0, sop_bytes} + 14'd6;
  assign room_ok    = need_bytes <= 14'(pcie_dll_pkg::RBUF_BYTES);

  // Ready on a sop beat only in DL_Active, between TLPs (the LCRC word of
  // the last one written), with room and credits; then every cycle to eop.
  assign tlp_ready_o = active && (st_q.taking
                       || (tlp_i.sop && !st_q.lcrc_due && room_ok && credit_ok_i));
  assign beat_take   = tlp_valid_i && tlp_ready_o;
  assign sop_take    = beat_take && !st_q.taking;
  assign eop_take    = beat_take && tlp_i.eop;
  // The LCRC register (seeded with all ones) over the 2 sequence-number
  // bytes at the sop, then each DW as two 16-bit steps.
  assign seq_beat    = {st_q.seq_alloc[7:0], 4'h0, st_q.seq_alloc[11:8]};
  assign crc_in      = sop_take ? pcie_dll_pkg::lcrc_step(32'hFFFF_FFFF, seq_beat) : st_q.crc;
  assign crc_lo      = pcie_dll_pkg::lcrc_step(crc_in, tlp_i.data[15:0]);
  assign crc_next    = pcie_dll_pkg::lcrc_step(crc_lo, tlp_i.data[31:16]);

  // ------------------------------------------------------------ replay buffer

  logic        wr_en;
  logic [7:0]  wr_addr;
  logic [31:0] wr_data;
  logic [31:0] lcrc_word;    // the LCRC bytes from the register, byte 0 in [7:0]
  logic        rd_en;
  logic [7:0]  rd_addr;
  logic [31:0] rd_data;
  logic [7:0]  wr_end;       // word after the TLP whose LCRC is written now
  logic        end_we;

  assign lcrc_word = pcie_dll_pkg::lcrc_bytes(st_q.crc);
  assign wr_en     = beat_take || st_q.lcrc_due;
  assign wr_addr   = st_q.wr_addr;
  assign wr_data   = st_q.lcrc_due ? lcrc_word : tlp_i.data;
  assign wr_end    = st_q.wr_addr + 8'd1;
  assign end_we    = st_q.lcrc_due;

  common_sdp_ram #(
    .WIDTH  (32),
    .ADDR_W (pcie_dll_pkg::RBUF_ADDR_W),
    .LANES  (1)
  ) u_rbuf (
    .clk_i      (clk_i),
    .wr_en_i    (wr_en),
    .wr_lanes_i (1'b1),
    .wr_addr_i  (wr_addr),
    .wr_data_i  (wr_data),
    .rd_en_i    (rd_en),
    .rd_addr_i  (rd_addr),
    .rd_data_o  (rd_data)
  );

  always_comb begin
    end_d = end_q;
    end_d[st_q.seq_done[pcie_dll_pkg::RBUF_SLOT_W-1:0]] = wr_end;
  end

  `COMMON_DFF_ARRAY_EN(end_q, end_d, clk_i, end_we)

  // ------------------------------------------------------------ Ack and Nak

  logic        acknak;       // an Ack or Nak in DL_Active
  logic [11:0] ack_seq;
  logic        in_range;
  logic        proto_err;
  logic        progress;     // it acknowledges at least one TLP
  logic        nak;
  logic [7:0]  ack_end;      // word after TLP ack_seq: the new base

  assign acknak    = rx_dllp_i.valid && active && (rx_dllp_i.kind == pcie_dll_pkg::RX_ACK
                                                || rx_dllp_i.kind == pcie_dll_pkg::RX_NAK);
  assign ack_seq   = rx_dllp_i.seq;
  // ACKD_SEQ or a TLP sent and not yet acknowledged (RETRY.md, received Ack
  // and Nak): (NTS - 1 - S) must not exceed (NTS - 1 - ACKD_SEQ).
  assign in_range  = (st_q.nts - 12'd1 - ack_seq) <= (st_q.nts - 12'd1 - st_q.ackd);
  assign proto_err = acknak && !in_range;
  assign progress  = acknak && in_range && ack_seq != st_q.ackd;
  assign nak       = acknak && in_range && rx_dllp_i.kind == pcie_dll_pkg::RX_NAK;
  assign ack_end   = end_q[ack_seq[pcie_dll_pkg::RBUF_SLOT_W-1:0]];

  // ------------------------------------------------------------ TLP source

  logic        have;         // a TLP to send: the replay's next, or a new complete one
  logic [11:0] cand_seq;
  logic [7:0]  cand_addr;
  logic [7:0]  cand_end;
  logic        tlp_eop_beat; // the phy takes a TLP's last beat at this edge
  logic        timer_at_end; // the replay timer reaches REPLAY_TIMEOUT at this edge
  logic        roll_now;     // a rollover at this edge, whatever the arbiter takes

  assign have      = st_q.rp_active || st_q.nts != st_q.seq_done;
  assign cand_seq  = st_q.rp_active ? st_q.rp_seq : st_q.nts;
  assign cand_addr = st_q.rp_active ? st_q.rp_addr : st_q.nts_addr;
  assign cand_end  = end_q[cand_seq[pcie_dll_pkg::RBUF_SLOT_W-1:0]];

  assign src_o.valid = active && have && !st_q.hold;
  // A rollover in this cycle that no take can cause: a Nak or the timer
  // starting a replay with REPLAY_NUM at 3, no forward progress, no replay
  // running and sent TLPs unacknowledged. (A take that ends a replay and
  // starts a pending one happens only with the stream free, so no sop is
  // waiting then.) The arbiter then stops offering a waiting TLP sop at
  // this very edge, so none starts ahead of the rollover replay. Its one
  // direct input is ltssm_state_i (in_l0, so the timer expires exactly
  // after REPLAY_TIMEOUT L0 cycles, never earlier); the rest are registers and the
  // registered received DLLP. pkt_tx_valid_o depends on ltssm_state_i
  // through it, in the rollover cycle only (MAS, Timing).
  assign timer_at_end = st_q.rt_run && in_l0 && rt_q == RT_LAST;
  assign roll_now     = (nak || timer_at_end || st_q.rp_pending) && !progress
                     && !st_q.rp_active && st_q.replay_num == 2'd3
                     && st_q.nts != st_q.ackd + 12'd1;
  assign hold_o       = st_q.hold || roll_now;
  assign src_o.data  = sm_q.sop ? {sm_q.seq[7:0], 4'h0, sm_q.seq[11:8]}
                     : (sm_q.half ? rd_data[31:16] : rd_data[15:0]);
  assign src_o.eop   = !sm_q.sop && sm_q.half && sm_q.words == 6'd1;
  assign tlp_eop_beat = beat_i && src_o.eop;

  // ------------------------------------------------------------ state

  logic        expire;       // the replay timer reaches REPLAY_TIMEOUT at this edge
  logic        replay_req;
  logic        rollover;

  always_comb begin
    st_d         = st_q;
    rt_d         = rt_q;
    expire       = 1'b0;
    rollover     = 1'b0;

    // Upper stream into the replay buffer; the LCRC word one edge after eop.
    if (beat_take) begin
      st_d.wr_addr = st_q.wr_addr + 8'd1;
      st_d.crc     = crc_next;
    end
    if (sop_take) begin
      st_d.seq_alloc = st_q.seq_alloc + 12'd1;
      st_d.taking    = 1'b1;
    end
    if (eop_take) begin
      st_d.taking   = 1'b0;
      st_d.lcrc_due = 1'b1;
    end
    if (st_q.lcrc_due) begin
      st_d.lcrc_due = 1'b0;
      st_d.wr_addr  = wr_end;
      st_d.seq_done = st_q.seq_done + 12'd1;
    end

    // Replay timer: counts in L0 while running; at REPLAY_TIMEOUT it stops
    // and requests a replay (timer_at_end, the one compare).
    if (st_q.rt_run && in_l0) begin
      if (timer_at_end) begin
        expire      = 1'b1;
        st_d.rt_run = 1'b0;
        rt_d        = '0;
      end else begin
        rt_d = rt_q + RT_W'(1);
      end
    end

    // The arbiter starts a TLP: the replay's next or the next new one.
    if (take_i) begin
      if (st_q.rp_active) begin
        st_d.rp_seq  = st_q.rp_seq + 12'd1;
        st_d.rp_addr = cand_end;
        if (st_q.rp_seq + 12'd1 == st_q.rp_end) begin
          st_d.rp_active = 1'b0;
        end
      end else begin
        st_d.nts      = st_q.nts + 12'd1;
        st_d.nts_addr = cand_end;
      end
    end

    // Forward progress: TLPs ACKD_SEQ + 1 to S leave the buffer.
    if (progress) begin
      st_d.ackd       = ack_seq;
      st_d.base       = ack_end;
      st_d.replay_num = 2'd0;
      st_d.rt_run     = ack_seq != st_q.nts - 12'd1;   // sent TLPs remain
      rt_d            = '0;
      // The running replay skips what the Ack removed.
      if (st_d.rp_active && (ack_seq - st_d.rp_seq) < 12'd2048) begin
        st_d.rp_seq  = ack_seq + 12'd1;
        st_d.rp_addr = ack_end;
        if (ack_seq + 12'd1 == st_d.rp_end) begin
          st_d.rp_active = 1'b0;
        end
      end
    end

    // A replay: requested by a Nak or the timer, or held from earlier;
    // starts once no replay runs and only with sent TLPs unacknowledged.
    replay_req = nak || expire || st_q.rp_pending;
    if (replay_req) begin
      if (st_d.rp_active) begin
        st_d.rp_pending = 1'b1;
      end else begin
        st_d.rp_pending = 1'b0;
        if (st_d.nts != st_d.ackd + 12'd1) begin
          st_d.rp_active  = 1'b1;
          st_d.rp_seq     = st_d.ackd + 12'd1;
          st_d.rp_addr    = st_d.base;
          st_d.rp_end     = st_d.nts;
          st_d.rt_run     = 1'b0;
          rt_d            = '0;
          // REPLAY_NUM after this edge's Ack or Nak (0 on progress).
          if (st_d.replay_num == 2'd3) begin
            rollover        = 1'b1;
            st_d.replay_num = 2'd0;
            st_d.hold       = 1'b1;
            st_d.hold_left  = 1'b0;
            st_d.hold_cnt   = 10'd0;
          end else begin
            st_d.replay_num = st_d.replay_num + 2'd1;
          end
        end
      end
    end

    // The timer starts, when stopped, as a TLP's last beat is taken.
    if (tlp_eop_beat && !st_d.rt_run) begin
      st_d.rt_run = 1'b1;
      rt_d        = '0;
    end

    // Rollover hold: until the LTSSM has left L0 and come back, or 1024
    // cycles if it never left.
    if (st_q.hold && !rollover) begin
      st_d.hold_cnt = st_q.hold_cnt + 10'd1;
      if (!in_l0) begin
        st_d.hold_left = 1'b1;
      end else if (st_q.hold_left || st_q.hold_cnt == pcie_dll_pkg::HOLD_LAST) begin
        st_d.hold = 1'b0;
      end
    end

    if (flush_i) begin
      st_d = pcie_dll_pkg::RETRY_RESET;
      rt_d = '0;
    end
  end

  `COMMON_DFF_RST_VAL(st_q, st_d, clk_i, rst_i, pcie_dll_pkg::RETRY_RESET)
  `COMMON_DFF_RST(rt_q, rt_d, clk_i, rst_i)

  // ------------------------------------------------------------ stream

  // The TLP on the phy stream: the sequence-number beat, then each word
  // of the replay buffer low half first. A word is read at the take (the
  // first) or as the high half before it is taken. A started TLP goes on
  // to its eop under flush; only an abort (sop not taken) drops it.
  always_comb begin
    sm_d    = sm_q;
    rd_en   = 1'b0;
    rd_addr = sm_q.rd_addr;
    if (take_i) begin
      sm_d.active  = 1'b1;
      sm_d.sop     = 1'b1;
      sm_d.half    = 1'b0;
      sm_d.seq     = cand_seq;
      sm_d.start   = cand_addr;
      sm_d.rd_addr = cand_addr + 8'd1;
      sm_d.words   = 6'(cand_end - cand_addr);
      rd_en        = 1'b1;
      rd_addr      = cand_addr;
    end else if (beat_i) begin
      if (sm_q.sop) begin
        sm_d.sop = 1'b0;
      end else if (!sm_q.half) begin
        sm_d.half = 1'b1;
      end else begin
        sm_d.half  = 1'b0;
        sm_d.words = sm_q.words - 6'd1;
        if (sm_q.words == 6'd1) begin
          sm_d.active = 1'b0;
        end else begin
          rd_en        = 1'b1;
          sm_d.rd_addr = sm_q.rd_addr + 8'd1;
        end
      end
    end
    if (abort_i) begin
      sm_d = '0;
    end
  end

  `COMMON_DFF_RST(sm_q, sm_d, clk_i, rst_i)

  // ------------------------------------------------------------ errors

  assign err_d     = flush_i ? 3'b000 : {proto_err, rollover, expire};
  assign retrain_d = rollover && !flush_i;

  `COMMON_DFF_RST(err_q, err_d, clk_i, rst_i)
  `COMMON_DFF_RST(retrain_q, retrain_d, clk_i, rst_i)

  assign err_o         = err_q;
  assign retrain_req_o = retrain_q;

  // Only the kind and sequence number of a received DLLP matter here, and
  // only whether the LTSSM is in L0.
  `COMMON_UNUSED_BITS(unused_rx_dllp, {rx_dllp_i.cls, rx_dllp_i.val})
  `COMMON_UNUSED_BITS(unused_ltssm, {ltssm_state_i[3:0]})

  // ------------------------------------------------------------ assertions

  // At most 56 TLPs fit the 1024-byte buffer, so the 2047-TLP limit of the
  // transmit rule (HAS, transmit) and the 64 end-address slots both hold.
  `COMMON_ASSERT_NEVER(a_held_limit, clk_i, rst_i, held > 12'd56)
  `COMMON_ASSERT_NEVER(a_roll_now, clk_i, rst_i, roll_now && !flush_i && !rollover)
  `COMMON_ASSERT_NEVER(a_one_beat_tlp, clk_i, rst_i, sop_take && tlp_i.eop)
  `COMMON_ASSERT_NEVER(a_take_without_tlp, clk_i, rst_i, take_i && !src_o.valid)
  `COMMON_ASSERT_NEVER(a_beat_without_tlp, clk_i, rst_i, beat_i && !sm_q.active)
  // A held TLP is 3 to 36 DWs and its LCRC: 4 to 37 words.
  `COMMON_ASSERT_NEVER(a_tlp_words, clk_i, rst_i,
                       take_i && (6'(cand_end - cand_addr) < 6'd4
                                  || 6'(cand_end - cand_addr) > 6'd37))

endmodule

`default_nettype wire
