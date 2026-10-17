// Types, constants and pure helpers private to pcie_dll
// (wiki/src/rtl/dll/MAS.md#package). DLLP formats and the CRC-16 are
// defined on wiki/src/arch/dll/DLLP.md.
`default_nettype none

package pcie_dll_pkg;

  // ------------------------------------------------------------- states

  // Data link control states, encoded as dl_state_o shows them
  // (HAS, Status).
  typedef enum logic [1:0] {
    DL_INACTIVE = 2'd0,
    DL_INIT1    = 2'd1,   // DL_Init, FC_INIT1
    DL_INIT2    = 2'd2,   // DL_Init, FC_INIT2
    DL_ACTIVE   = 2'd3
  } t_dl_state;

  // Flow-control classes, in InitFC group and UpdateFC round-robin order;
  // the code is also the class nibble of an FC DLLP type (byte 0 bits 5:4).
  typedef enum logic [1:0] {
    FC_P   = 2'd0,
    FC_NP  = 2'd1,
    FC_CPL = 2'd2
  } t_fc_class;

  // ------------------------------------------------------------- DLLPs

  // Byte 0 of each DLLP type this design sends or decodes (DLLP.md, types
  // used). The FC types carry the class in bits 5:4 and VC 0 in bits 2:0.
  localparam logic [7:0] DLLP_ACK      = 8'h00;
  localparam logic [7:0] DLLP_NAK      = 8'h10;
  localparam logic [7:0] DLLP_INITFC1  = 8'h40;
  localparam logic [7:0] DLLP_INITFC2  = 8'hC0;
  localparam logic [7:0] DLLP_UPDATEFC = 8'h80;

  localparam logic [15:0] CRC16_POLY = 16'h100B;  // x^16 + x^12 + x^3 + x + 1

  // One class's credit fields as an FC DLLP carries them: HdrFC and DataFC.
  typedef struct packed {
    logic [7:0]  hdr;
    logic [11:0] data;
  } t_fc_val;

  // One value per class; named fields, read and written through fc_get and
  // fc_set.
  typedef struct packed {
    t_fc_val cpl;
    t_fc_val np;
    t_fc_val p;
  } t_fc_vals;

  // A DLLP waiting for the transmit arbiter: its 4 body bytes, byte 0 in
  // [7:0]. The arbiter adds the CRC-16.
  typedef struct packed {
    logic        valid;
    logic [31:0] body;
  } t_dllp_req;

  // What a received good DLLP is, from byte 0.
  typedef enum logic [2:0] {
    RX_ACK      = 3'd0,
    RX_NAK      = 3'd1,
    RX_INITFC1  = 3'd2,
    RX_INITFC2  = 3'd3,
    RX_UPDATEFC = 3'd4,
    RX_OTHER    = 3'd5   // another type, or an FC DLLP for a VC other than 0
  } t_dllp_kind;

  // A received DLLP that passed the length and CRC-16 checks, for one cycle.
  typedef struct packed {
    logic        valid;
    t_dllp_kind  kind;
    t_fc_class   cls;    // FC kinds only
    t_fc_val     val;    // FC kinds only: HdrFC and DataFC
    logic [11:0] seq;    // Ack and Nak only: AckNak_Seq_Num
  } t_rx_dllp;

  // ------------------------------------------------------- packet stream

  // One beat of the phy upper packet stream, transmit side (valid and
  // ready stay single wires).
  typedef struct packed {
    logic [15:0] data;   // [7:0] is the earlier byte
    logic        sop;
    logic        eop;
    logic        dllp;   // 0 TLP, 1 DLLP; held from sop to eop
  } t_pkt_tx;

  // One beat of the phy upper packet stream, receive side.
  typedef struct packed {
    logic        valid;
    logic [15:0] data;   // [7:0] is the earlier byte
    logic        sop;
    logic        eop;
    logic        dllp;
    logic        err;    // with eop only
    logic        nullify;// with eop only
  } t_pkt_rx;

  // ------------------------------------------------------ block state

  // DLLP receive: the packet being collected (MAS, DLLP receive).
  typedef struct packed {
    logic        open;   // a DLLP-tagged packet's sop has arrived, not its eop
    logic [1:0]  beats;  // beats so far; 3 stands for 3 or more
    logic [31:0] body;   // bytes 0 to 3, byte 0 in [7:0]
  } t_rx_asm;

  // Control: the data link control state and the InitFC group being sent
  // (MAS, Control).
  typedef struct packed {
    t_dl_state  st;
    logic [1:0] grp_left;  // InitFC DLLPs of the group not yet started: 3 P next, 2 NP, 1 Cpl
    logic [6:0] grp_timer; // cycles since the group started, saturating at 127
    logic       done;      // FC_INIT2: an InitFC2, UpdateFC or good TLP arrived
  } t_ctrl_state;

  // Transmit: the packet on the stream (MAS, Transmit). A DLLP is held
  // here; a TLP's beats come from the TLP transmit block (t_tlp_src).
  typedef struct packed {
    logic        busy;   // a packet is on the stream, sop offered or accepted
    logic        tlp;    // the packet is a TLP
    logic [1:0]  beat;   // next beat to offer: 0, 1 or 2 (a TLP: 0 sop, 1 after it)
    logic [47:0] pkt;    // the 6 bytes, byte 0 in [7:0]
  } t_tx_state;

  // Flow control: one class's transmit-side limits (FLOW_CONTROL.md,
  // transmit accounting).
  typedef struct packed {
    logic    recorded;   // the first InitFC of this class has been recorded
    logic    inf_hdr;    // header credits infinite
    logic    inf_data;   // data credits infinite
    t_fc_val limit;      // CREDIT_LIMIT, header and data
  } t_fc_limit;

  // ------------------------------------------------------ TLP transmit

  // TLP transmit (MAS, TLP transmit; RETRY.md): the replay buffer is
  // 1024 bytes of framed TLPs, held as 256 words of 32 bits; the timers are
  // in clk_i cycles.
  localparam int unsigned RBUF_BYTES     = 1024;
  localparam int unsigned RBUF_ADDR_W    = 8;     // 256 words
  localparam int unsigned RBUF_SLOT_W    = 6;     // end-address table: seq mod 64 (at most 56 TLPs)
  localparam logic [8:0]  REPLAY_TIMEOUT = 9'd356;
  localparam logic [9:0]  HOLD_LAST      = 10'd1023;  // rollover hold ends at 1024 cycles

  // One beat of the upper transmit stream (valid and ready stay single
  // wires).
  typedef struct packed {
    logic [31:0] data;   // one DW, [7:0] is the earlier byte
    logic        sop;
    logic        eop;
  } t_tlp_beat;

  // The TLP source of the transmit arbiter (priorities 5 and 6): a TLP may
  // start, and the beat on the stream while the arbiter sends a TLP.
  typedef struct packed {
    logic        valid;  // a replayed or new TLP is ready to start
    logic [15:0] data;   // the current beat, [7:0] the earlier byte
    logic        eop;    // the current beat is the last
  } t_tlp_src;

  // TLP transmit: sequence numbers, replay buffer pointers, replay state,
  // the replay timer and the rollover hold (MAS, TLP transmit).
  typedef struct packed {
    logic [11:0] seq_alloc;   // next sequence number to assign
    logic [11:0] seq_done;    // next TLP whose LCRC is not yet stored
    logic [11:0] nts;         // NEXT_TRANSMIT_SEQ
    logic [11:0] ackd;        // ACKD_SEQ
    logic [7:0]  wr_addr;     // next replay-buffer word to write
    logic [7:0]  base;        // first word of the oldest TLP held
    logic [7:0]  nts_addr;    // first word of TLP nts
    logic        taking;      // an upper TLP is between its sop and eop
    logic        lcrc_due;    // the LCRC word is written at the next edge
    logic [31:0] crc;         // running LCRC register
    logic        rp_active;   // a replay is running
    logic        rp_pending;  // a replay was requested while one ran
    logic [11:0] rp_seq;      // next TLP the replay sends
    logic [11:0] rp_end;      // one past the last TLP the replay sends
    logic [7:0]  rp_addr;     // first word of TLP rp_seq
    logic [1:0]  replay_num;  // REPLAY_NUM
    logic        rt_run;      // replay timer running
    logic [8:0]  rt;          // REPLAY_TIMER
    logic        hold;        // rollover hold: no TLP starts
    logic        hold_left;   // the LTSSM has left L0 since the rollover
    logic [9:0]  hold_cnt;    // cycles since the rollover
  } t_retry_state;

  // DL_Inactive values of t_retry_state: ACKD_SEQ 4095, the rest 0.
  localparam t_retry_state RETRY_RESET = '{
    seq_alloc:  12'd0,
    seq_done:   12'd0,
    nts:        12'd0,
    ackd:       12'hFFF,
    wr_addr:    8'd0,
    base:       8'd0,
    nts_addr:   8'd0,
    taking:     1'b0,
    lcrc_due:   1'b0,
    crc:        32'd0,
    rp_active:  1'b0,
    rp_pending: 1'b0,
    rp_seq:     12'd0,
    rp_end:     12'd0,
    rp_addr:    8'd0,
    replay_num: 2'd0,
    rt_run:     1'b0,
    rt:         9'd0,
    hold:       1'b0,
    hold_left:  1'b0,
    hold_cnt:   10'd0
  };

  // TLP transmit: the TLP on the phy stream, read from the replay buffer.
  typedef struct packed {
    logic        active;  // a TLP is being sent
    logic        sop;     // the sequence-number beat is next
    logic        half;    // 0 the low, 1 the high half of the word read
    logic [11:0] seq;     // its sequence number
    logic [7:0]  start;   // its first word
    logic [7:0]  rd_addr; // next word to read
    logic [5:0]  words;   // words still to send, the one read included
  } t_tlp_stream;

  // ---------------------------------------------------------- helpers

  // The 2 CRC-16 bytes of a DLLP whose body is body (byte 0 in [7:0]):
  // register seeded with all ones, each byte fed bit 0 first, remainder
  // complemented, its bit 15 first on the line. Returns {byte 5, byte 4}:
  // byte 4 holds remainder bits 15:8 and byte 5 bits 7:0, each reversed.
  function automatic logic [15:0] crc16(input logic [31:0] body);
    logic [15:0] r;
    logic [15:0] inv;
    logic [15:0] out;
    logic        fb;
    r = 16'hFFFF;
    for (int unsigned i = 0; i < 32; i++) begin
      fb = r[15] ^ body[i];
      r  = {r[14:0], 1'b0} ^ (fb ? CRC16_POLY : 16'h0000);
    end
    inv = ~r;
    for (int unsigned i = 0; i < 8; i++) begin
      out[i]     = inv[15 - i];   // byte 4: remainder bit 15 in bit 0
      out[8 + i] = inv[7 - i];    // byte 5: remainder bit 7 in bit 0
    end
    return out;
  endfunction

  // The body of an InitFC1, InitFC2 or UpdateFC DLLP: base is the type's
  // DLLP_* code, cls goes in bits 5:4 of byte 0, VC 0 (DLLP.md, fields).
  function automatic logic [31:0] fc_body(input logic [7:0] base, input t_fc_class cls,
                                          input t_fc_val v);
    logic [7:0] b0;
    logic [7:0] b1;
    logic [7:0] b2;
    logic [7:0] b3;
    b0 = base | {2'b00, cls, 4'h0};
    b1 = {2'b00, v.hdr[7:2]};
    b2 = {v.hdr[1:0], 2'b00, v.data[11:8]};
    b3 = v.data[7:0];
    return {b3, b2, b1, b0};
  endfunction

  // Decode a good DLLP from byte 0 (b0) and its two 12-bit-or-less fields:
  // bytes 1-2 bits 5:0 and 7:6 (hdr, HdrFC) and bytes 2-3 bits 3:0 and 7:0
  // (cnt, DataFC or AckNak_Seq_Num); reserved bits are not read (DLLP.md,
  // types used and fields).
  function automatic t_rx_dllp dllp_decode(input logic [7:0] b0, input logic [7:0] hdr,
                                           input logic [11:0] cnt);
    t_rx_dllp d;
    d.valid    = 1'b1;
    d.kind     = RX_OTHER;
    d.cls      = FC_P;
    d.val.hdr  = hdr;
    d.val.data = cnt;
    d.seq      = cnt;
    if (b0 == DLLP_ACK) begin
      d.kind = RX_ACK;
    end else if (b0 == DLLP_NAK) begin
      d.kind = RX_NAK;
    end else if (b0[5:4] != 2'b11 && b0[3:0] == 4'h0) begin
      // FC types: bits 7:6 the kind, 5:4 the class, 3 zero, 2:0 VC 0.
      d.cls = t_fc_class'(b0[5:4]);
      case (b0[7:6])
        2'b01:   d.kind = RX_INITFC1;
        2'b11:   d.kind = RX_INITFC2;
        2'b10:   d.kind = RX_UPDATEFC;
        default: d.kind = RX_OTHER;
      endcase
    end
    return d;
  endfunction

  // One class's field of a per-class value.
  function automatic t_fc_val fc_get(input t_fc_vals v, input t_fc_class cls);
    t_fc_val r;
    case (cls)
      FC_NP:   r = v.np;
      FC_CPL:  r = v.cpl;
      default: r = v.p;
    endcase
    return r;
  endfunction

  // Flow control: for class cls, record the first InitFC1 or InitFC2 (kind,
  // class dcls, value v, valid dv) in FC_INIT1 (0 is infinite); an UpdateFC
  // in FC_INIT2 or DL_Active sets every finite field's limit
  // (FLOW_CONTROL.md, transmit accounting).
  function automatic t_fc_limit fc_record(input t_fc_limit lim, input logic dv,
                                          input t_dllp_kind kind, input t_fc_class dcls,
                                          input t_fc_val v, input t_fc_class cls,
                                          input t_dl_state st);
    t_fc_limit r;
    r = lim;
    if (dv && dcls == cls) begin
      if ((kind == RX_INITFC1 || kind == RX_INITFC2) && st == DL_INIT1 && !lim.recorded) begin
        r.recorded = 1'b1;
        r.inf_hdr  = v.hdr == 8'd0;
        r.inf_data = v.data == 12'd0;
        r.limit    = v;
      end else if (kind == RX_UPDATEFC && (st == DL_INIT2 || st == DL_ACTIVE)) begin
        if (!lim.inf_hdr) begin
          r.limit.hdr = v.hdr;
        end
        if (!lim.inf_data) begin
          r.limit.data = v.data;
        end
      end
    end
    return r;
  endfunction

  // Flow control: one class's credits available, shown in DL_Active (on):
  // all ones for an infinite field, else the limit less what was consumed
  // (nothing before #214); 0 otherwise.
  function automatic t_fc_val fc_show(input logic inf_hdr, input logic inf_data,
                                      input t_fc_val limit, input logic on);
    t_fc_val r;
    r = '0;
    if (on) begin
      r.hdr  = inf_hdr ? 8'hFF : limit.hdr;
      r.data = inf_data ? 12'hFFF : limit.data;
    end
    return r;
  endfunction

  // UpdateFC refresh: cycles since a class's last UpdateFC, cleared when
  // one is sent, saturating at REFRESH_LAST (the cycle it falls due).
  localparam logic [10:0] REFRESH_LAST = 11'd2047;

  function automatic logic [10:0] refresh_count(input logic [10:0] c, input logic sent);
    logic [10:0] r;
    r = c;
    if (sent) begin
      r = 11'd0;
    end else if (c != REFRESH_LAST) begin
      r = c + 11'd1;
    end
    return r;
  endfunction

  // The next class in round-robin order P, NP, Cpl.
  function automatic t_fc_class fc_next(input t_fc_class cls);
    t_fc_class r;
    case (cls)
      FC_P:    r = FC_NP;
      FC_NP:   r = FC_CPL;
      default: r = FC_P;
    endcase
    return r;
  endfunction

  // LCRC (RETRY.md, LCRC): CRC-32, polynomial 04C11DB7h, register seeded
  // with all ones, each byte fed bit 0 first. Kept bit-reversed (the
  // reflected form, polynomial EDB88320h), so the complement of the
  // register is the LCRC with byte 0 in [7:0], the zlib.crc32 convention.
  localparam logic [31:0] LCRC_POLY_REV = 32'hEDB88320;
  localparam logic [31:0] LCRC_SEED     = 32'hFFFFFFFF;

  // The register after the 2 sequence-number bytes of TLP seq: byte 0 is
  // bits 11:8 with bits 7:4 0, byte 1 bits 7:0.
  function automatic logic [31:0] lcrc_seq(input logic [11:0] seq);
    logic [31:0] r;
    logic [15:0] d;
    logic        fb;
    r = LCRC_SEED;
    d = {seq[7:0], 4'h0, seq[11:8]};
    for (int unsigned i = 0; i < 16; i++) begin
      fb = r[0] ^ d[i];
      r  = {1'b0, r[31:1]} ^ (fb ? LCRC_POLY_REV : 32'h0);
    end
    return r;
  endfunction

  // The register after one more DW (byte 0 in [7:0]): 32 bit-serial
  // steps, unrolled into an XOR network.
  function automatic logic [31:0] lcrc_dw(input logic [31:0] crc, input logic [31:0] d);
    logic [31:0] r;
    logic        fb;
    r = crc;
    for (int unsigned i = 0; i < 32; i++) begin
      fb = r[0] ^ d[i];
      r  = {1'b0, r[31:1]} ^ (fb ? LCRC_POLY_REV : 32'h0);
    end
    return r;
  endfunction

  // The bytes a TLP holds by the Fmt bits 1:0 and Length of its first DW
  // (FLOW_CONTROL.md, classes and credits): a 3- or 4-DW header (Fmt bit 0)
  // and, when Fmt says with data (bit 1), Length DWs of payload (Length 0
  // is 1024).
  function automatic logic [12:0] tlp_bytes(input logic [1:0] fmt, input logic [9:0] len);
    logic [10:0] dws;
    dws = (len == 10'd0) ? 11'd1024 : {1'b0, len};
    return (fmt[0] ? 13'd16 : 13'd12) + (fmt[1] ? {dws, 2'b00} : 13'd0);
  endfunction

endpackage

`default_nettype wire
