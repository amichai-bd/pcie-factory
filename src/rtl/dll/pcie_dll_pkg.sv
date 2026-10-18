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

  // One value per class; named fields, read through fc_get and added to
  // through fc_charge.
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

  // --------------------------------------------------------- TLP receive

  // The TLP receive path and Ack/Nak scheduling (MAS, TLP receive; RETRY.md).
  localparam logic [31:0] LCRC_POLY     = 32'h04C1_1DB7;
  localparam logic [5:0]  RX_MAX_TLP_DW = 6'd36;   // 4-DW header and a 128-byte payload
  localparam logic [5:0]  ACK_LATENCY   = 6'd32;   // Ack timer limit, cycles
  // The receive-buffer reserve of an infinite field (FLOW_CONTROL.md,
  // receive buffer): 8 headers, 24 data credits.
  localparam int unsigned RX_INF_HDR    = 8;
  localparam int unsigned RX_INF_DATA   = 24;

  // The header fields of a TLP's DW 0 the dll reads (FLOW_CONTROL.md,
  // classes and credits): Fmt bits 5 and 6 of byte 0, Type, Length.
  typedef struct packed {
    logic       hdr4;       // Fmt bit 5: a 4-DW header
    logic       with_data;  // Fmt bit 6: a payload follows
    logic [4:0] typ;        // Type, byte 0 bits 4:0
    logic [9:0] length;     // Length in DWs, byte 2 bits 1:0 and byte 3; 0 is 1024
  } t_tlp_hdr;

  // A received TLP's class and the credits its header states, as the
  // receive side reports it to the flow-control accounting (MAS, TLP
  // receive, events).
  typedef struct packed {
    logic      valid;
    t_fc_class cls;
    t_fc_val   cred;   // header credits (0 or 1) and data credits
  } t_rx_credit;

  // One DW of the receive buffer with the TLP's first and last marks.
  typedef struct packed {
    logic [31:0] data;  // [7:0] is the earlier byte
    logic        sop;
    logic        eop;
  } t_rx_word;

  // TLP receive: the packet being collected. Beats pass a two-beat delay
  // line (b1, b2) so the last two, the LCRC, are never stored or summed.
  typedef struct packed {
    logic        open;    // a TLP-tagged packet's sop has arrived, not its eop
    logic [7:0]  beats;   // beats so far, saturating at 255
    logic [5:0]  dws;     // TLP DWs completed, at most RX_MAX_TLP_DW
    logic        noroom;  // a DW found the receive buffer full
    logic [11:0] seq;     // the sequence number, from the sop beat
    t_tlp_hdr    hdr;     // from TLP header DW 0
    logic [15:0] lo;      // first half of the DW being assembled
    logic [15:0] b1;      // the previous beat
    logic [15:0] b2;      // the beat before it
    logic [31:0] crc;     // LCRC register over the beats that left the delay line
  } t_tlp_asm;

  // A TLP packet as checked at its eop, judged in the next cycle.
  typedef struct packed {
    logic        valid;
    logic [7:0]  beats;    // packet length in beats, saturating at 255
    logic [11:0] seq;
    t_tlp_hdr    hdr;
    logic        err;      // flagged by phy
    logic        nullify;  // ended by EDB
    logic        lcrc_ok;  // the LCRC matches
    logic        lcrc_inv; // the LCRC is the inverse: a nullified TLP
    logic        noroom;   // not all DWs were stored
  } t_tlp_chk;

  // Receiver state and Ack/Nak scheduling (RETRY.md, receiver state).
  typedef struct packed {
    logic [11:0] nrs;        // NEXT_RCV_SEQ
    logic        nak_sched;  // NAK_SCHEDULED
    logic        nak_due;    // a Nak waits for the arbiter
    logic        ack_due;    // an Ack is due at once (a duplicate)
    logic        timer_on;   // the Ack timer runs
    logic [5:0]  timer;      // cycles since it started, saturating at ACK_LATENCY
  } t_acknak_state;

  // One 16-bit beat into the LCRC register: bytes [7:0] then [15:8], each
  // bit 0 first, polynomial 04C11DB7h (RETRY.md, LCRC).
  function automatic logic [31:0] lcrc_step(input logic [31:0] crc, input logic [15:0] beat);
    logic [31:0] r;
    logic        fb;
    r = crc;
    for (int unsigned i = 0; i < 16; i++) begin
      fb = r[31] ^ beat[i];
      r  = {r[30:0], 1'b0} ^ (fb ? LCRC_POLY : 32'h0000_0000);
    end
    return r;
  endfunction

  // The 4 LCRC bytes from the register, byte 0 in [7:0]: the remainder
  // complemented, its bit 31 first on the line, so byte k bit i holds
  // remainder bit 31-8k-i.
  function automatic logic [31:0] lcrc_bytes(input logic [31:0] crc);
    logic [31:0] r;
    for (int unsigned i = 0; i < 32; i++) begin
      r[i] = ~crc[31 - i];
    end
    return r;
  endfunction

  // The fields of a TLP header DW 0: byte 0 bits 6:0 (Fmt bits 6 and 5,
  // Type), byte 2 bits 1:0 and byte 3 (Length).
  function automatic t_tlp_hdr tlp_hdr(input logic [6:0] b0, input logic [1:0] b2,
                                       input logic [7:0] b3);
    t_tlp_hdr h;
    h.hdr4      = b0[5];
    h.with_data = b0[6];
    h.typ       = b0[4:0];
    h.length    = {b2, b3};
    return h;
  endfunction

  // Payload DWs a TLP header states: Length (0 is 1024) when Fmt says with
  // data, else 0 (FLOW_CONTROL.md, classes and credits).
  function automatic logic [10:0] tlp_payload_dw(input logic with_data, input logic [9:0] len);
    logic [10:0] r;
    r = 11'd0;
    if (with_data) begin
      r = len == 10'd0 ? 11'd1024 : {1'b0, len};
    end
    return r;
  endfunction

  // The class and credits a TLP header states: posted for a memory write
  // (Type 00000 with data) or a message (Type 10xxx), completion for Type
  // 01010 and 01011, otherwise non-posted; 1 header credit, and payload
  // DWs / 4 rounded up data credits.
  function automatic t_rx_credit tlp_credit(input logic with_data, input logic [4:0] typ,
                                            input logic [9:0] len);
    t_rx_credit  r;
    logic [10:0] pdw;
    pdw     = tlp_payload_dw(with_data, len);
    r.valid = 1'b1;
    if ((typ == 5'b00000 && with_data) || typ[4:3] == 2'b10) begin
      r.cls = FC_P;
    end else if (typ[4:1] == 4'b0101) begin
      r.cls = FC_CPL;
    end else begin
      r.cls = FC_NP;
    end
    r.cred.hdr  = 8'd1;
    r.cred.data = {3'd0, pdw[10:2]} + {11'd0, pdw[1:0] != 2'b00};
    return r;
  endfunction

  // A good-LCRC packet of `beats` beats is malformed (RETRY.md, receiving
  // TLPs): its length is not 2 more than a multiple of 4 (an even beat
  // count), the TLP is shorter than a 3-DW header (below 9 beats), its DWs
  // differ from what its header's Fmt and Length give, or its payload is
  // above 128 bytes.
  function automatic logic tlp_malformed(input logic [7:0] beats, input logic hdr4,
                                         input logic with_data, input logic [9:0] len);
    logic [10:0] want_dw;
    logic [10:0] got_dw;
    want_dw = (hdr4 ? 11'd4 : 11'd3) + tlp_payload_dw(with_data, len);
    got_dw  = {4'd0, beats[7:1]} - 11'd1;   // (beats - 3) / 2 for an odd count
    return !beats[0] || beats < 8'd9 || got_dw != want_dw || tlp_payload_dw(with_data, len) > 11'd32;
  endfunction

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
  // all ones for an infinite field, else CREDIT_LIMIT less
  // CREDITS_CONSUMED modulo 2^w; 0 otherwise.
  function automatic t_fc_val fc_show(input logic inf_hdr, input logic inf_data,
                                      input t_fc_val limit, input t_fc_val used,
                                      input logic on);
    t_fc_val r;
    r = '0;
    if (on) begin
      r.hdr  = inf_hdr ? 8'hFF : limit.hdr - used.hdr;
      r.data = inf_data ? 12'hFFF : limit.data - used.data;
    end
    return r;
  endfunction

  // Add credits c of class cls to v when en (credit counters, modulo 2^w).
  function automatic t_fc_vals fc_charge(input t_fc_vals v, input logic en,
                                         input t_fc_class cls, input t_fc_val c);
    t_fc_vals r;
    r = v;
    if (en) begin
      case (cls)
        FC_NP: begin
          r.np.hdr  = v.np.hdr + c.hdr;
          r.np.data = v.np.data + c.data;
        end
        FC_CPL: begin
          r.cpl.hdr  = v.cpl.hdr + c.hdr;
          r.cpl.data = v.cpl.data + c.data;
        end
        default: begin
          r.p.hdr  = v.p.hdr + c.hdr;
          r.p.data = v.p.data + c.data;
        end
      endcase
    end
    return r;
  endfunction

  // The transmit gate for one class: for each finite field,
  // (CREDIT_LIMIT - (CREDITS_CONSUMED + need)) modulo 2^w is at most
  // 2^(w-1); an infinite field always passes.
  function automatic logic fc_gate(input logic inf_hdr, input logic inf_data,
                                   input t_fc_val limit, input t_fc_val used,
                                   input t_fc_val need);
    logic [7:0]  left_hdr;
    logic [11:0] left_data;
    left_hdr  = limit.hdr - (used.hdr + need.hdr);
    left_data = limit.data - (used.data + need.data);
    return (inf_hdr || left_hdr <= 8'd128) && (inf_data || left_data <= 12'd2048);
  endfunction

  // The receive overflow check for one class: need is at most
  // (CREDITS_ALLOCATED - CREDITS_RECEIVED) modulo 2^w for both fields. An
  // infinite field's counters start from the reserve, so the same rule
  // checks the reserve's free space.
  function automatic logic fc_fits(input t_fc_val alloc, input t_fc_val rcvd,
                                   input t_fc_val need);
    logic [7:0]  free_hdr;
    logic [11:0] free_data;
    free_hdr  = alloc.hdr - rcvd.hdr;
    free_data = alloc.data - rcvd.data;
    return need.hdr <= free_hdr && need.data <= free_data;
  endfunction

  // CREDITS_ALLOCATED on entering DL_Inactive, from the advertised value:
  // the advertisement for a finite field, the reserve for an infinite one.
  function automatic t_fc_vals fc_rx_base(input t_fc_vals adv);
    t_fc_vals r;
    r.p.hdr    = adv.p.hdr == 8'd0 ? 8'(RX_INF_HDR) : adv.p.hdr;
    r.p.data   = adv.p.data == 12'd0 ? 12'(RX_INF_DATA) : adv.p.data;
    r.np.hdr   = adv.np.hdr == 8'd0 ? 8'(RX_INF_HDR) : adv.np.hdr;
    r.np.data  = adv.np.data == 12'd0 ? 12'(RX_INF_DATA) : adv.np.data;
    r.cpl.hdr  = adv.cpl.hdr == 8'd0 ? 8'(RX_INF_HDR) : adv.cpl.hdr;
    r.cpl.data = adv.cpl.data == 12'd0 ? 12'(RX_INF_DATA) : adv.cpl.data;
    return r;
  endfunction

  // What an UpdateFC carries: CREDITS_ALLOCATED, an infinite field
  // (advertised as 0) sent as 0.
  function automatic t_fc_vals fc_upd_vals(input t_fc_vals alloc, input t_fc_vals adv);
    t_fc_vals r;
    r.p.hdr    = adv.p.hdr == 8'd0 ? 8'd0 : alloc.p.hdr;
    r.p.data   = adv.p.data == 12'd0 ? 12'd0 : alloc.p.data;
    r.np.hdr   = adv.np.hdr == 8'd0 ? 8'd0 : alloc.np.hdr;
    r.np.data  = adv.np.data == 12'd0 ? 12'd0 : alloc.np.data;
    r.cpl.hdr  = adv.cpl.hdr == 8'd0 ? 8'd0 : alloc.cpl.hdr;
    r.cpl.data = adv.cpl.data == 12'd0 ? 12'd0 : alloc.cpl.data;
    return r;
  endfunction

  // Credit release (FLOW_CONTROL.md, credit release): the fields of c whose
  // advertisement adv is finite (fin 1) or infinite (fin 0); others 0.
  function automatic t_fc_val fc_keep(input t_fc_val c, input t_fc_val adv, input logic fin);
    t_fc_val r;
    r.hdr  = ((adv.hdr != 8'd0) == fin) ? c.hdr : 8'd0;
    r.data = ((adv.data != 12'd0) == fin) ? c.data : 12'd0;
    return r;
  endfunction

  // Credits held in the receive buffer never exceed its space: free
  // credits (CREDITS_ALLOCATED - CREDITS_RECEIVED) modulo 2^w stay at most
  // the base (advertisement or reserve). Per field of one class.
  function automatic logic fc_free_ok(input t_fc_val alloc, input t_fc_val rcvd,
                                      input t_fc_val base);
    logic [7:0]  free_hdr;
    logic [11:0] free_data;
    free_hdr  = alloc.hdr - rcvd.hdr;
    free_data = alloc.data - rcvd.data;
    return free_hdr <= base.hdr && free_data <= base.data;
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

endpackage

`default_nettype wire
