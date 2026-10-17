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

  // Transmit: the DLLP on the stream (MAS, Transmit).
  typedef struct packed {
    logic        busy;   // a packet is on the stream, sop offered or accepted
    logic [1:0]  beat;   // next beat to offer: 0, 1 or 2
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

  // Credit accounting (FLOW_CONTROL.md, transmit and receive accounting).

  // What a TLP costs: its class and its header and data credits.
  typedef struct packed {
    t_fc_class cls;
    t_fc_val   val;
  } t_fc_cost;

  // One beat of an upper TLP stream as flow control watches it: taken
  // (valid and ready both 1), first and last beat, the DW.
  typedef struct packed {
    logic        take;
    logic        sop;
    logic        eop;
    logic [31:0] data;   // [7:0] is the earlier byte
  } t_tlp_beat;

  // The receive decision on one TLP from the TLP receive path (#213): a
  // good-LCRC TLP with the expected sequence number, for one cycle.
  typedef struct packed {
    logic        valid;
    logic        malformed;  // accepted as malformed: its credits return at once
    logic        no_hdr;     // malformed and under 4 bytes: it counts no credits
    logic [31:0] dw0;        // header DW0 (byte 0 in [7:0]), don't care if no_hdr
  } t_fc_rx_tlp;

  // Receive space of an infinite field (FLOW_CONTROL.md, receive buffer).
  localparam logic [7:0]  RX_RESERVE_HDR  = 8'd8;
  localparam logic [11:0] RX_RESERVE_DATA = 12'd24;

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

  // The credits a TLP costs, from header byte 0 (with_data: Fmt bit 6;
  // typ: Type, bits 4:0) and the Length field (len: byte 2 bits 1:0, then
  // byte 3): the class by Fmt and Type, 1 header credit, and Length in DWs
  // (0 is 1024) divided by 4 and rounded up when Fmt says with data
  // (FLOW_CONTROL.md, classes and credits). The other Fmt bits do not
  // matter. Callers slice header DW0 (byte 0 in [7:0]).
  function automatic t_fc_cost tlp_cost(input logic with_data, input logic [4:0] typ,
                                        input logic [9:0] len);
    t_fc_cost    r;
    logic [10:0] len_dw;
    len_dw = {1'b0, len};
    if (len_dw == 11'd0) begin
      len_dw = 11'd1024;
    end
    if ((typ == 5'b00000 && with_data) || typ[4:3] == 2'b10) begin
      r.cls = FC_P;       // memory write, messages
    end else if (typ == 5'b01010 || typ == 5'b01011) begin
      r.cls = FC_CPL;     // completions
    end else begin
      r.cls = FC_NP;
    end
    r.val.hdr  = 8'd1;
    r.val.data = with_data ? {3'd0, 9'((len_dw + 11'd3) >> 2)} : 12'd0;
    return r;
  endfunction

  // Add cost c to its class in v when en (credit counters, modulo 2^w).
  function automatic t_fc_vals fc_charge(input t_fc_vals v, input logic en,
                                         input t_fc_cost c);
    t_fc_vals r;
    r = v;
    if (en) begin
      case (c.cls)
        FC_NP: begin
          r.np.hdr  = v.np.hdr + c.val.hdr;
          r.np.data = v.np.data + c.val.data;
        end
        FC_CPL: begin
          r.cpl.hdr  = v.cpl.hdr + c.val.hdr;
          r.cpl.data = v.cpl.data + c.val.data;
        end
        default: begin
          r.p.hdr  = v.p.hdr + c.val.hdr;
          r.p.data = v.p.data + c.val.data;
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
    r.p.hdr    = adv.p.hdr == 8'd0 ? RX_RESERVE_HDR : adv.p.hdr;
    r.p.data   = adv.p.data == 12'd0 ? RX_RESERVE_DATA : adv.p.data;
    r.np.hdr   = adv.np.hdr == 8'd0 ? RX_RESERVE_HDR : adv.np.hdr;
    r.np.data  = adv.np.data == 12'd0 ? RX_RESERVE_DATA : adv.np.data;
    r.cpl.hdr  = adv.cpl.hdr == 8'd0 ? RX_RESERVE_HDR : adv.cpl.hdr;
    r.cpl.data = adv.cpl.data == 12'd0 ? RX_RESERVE_DATA : adv.cpl.data;
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
