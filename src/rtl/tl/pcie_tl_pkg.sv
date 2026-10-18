// Package of pcie_tl (wiki/src/rtl/tl/MAS.md#package): the header field
// codes, the structs the blocks exchange, the block state types and the
// pure helpers of the receive checks (wiki/src/arch/tl/ROUTING.md) and the
// credit rule (wiki/src/arch/tl/HAS.md#credit-release).
`default_nettype none

package pcie_tl_pkg;

  // ------------------------------------------------------------ constants

  // Ordering lines (MAS, receive queues): a posted entry carries one epoch
  // per line; an entry of a line waits for every posted entry of an epoch
  // not after its own.
  localparam int unsigned NLINE    = 3;
  localparam int unsigned LINE_CQ  = 0;   // non-posted, cq line (BAR0 reads)
  localparam int unsigned LINE_CFG = 1;   // non-posted, cfg line
  localparam int unsigned LINE_RC  = 2;   // received completions (#292)
  localparam int unsigned EP_W     = 9;   // epoch counter: 2**(EP_W-1) above FC_PH and FC_NPH + 1

  localparam int unsigned HANDLES  = 8;   // completer read handles (COMPLETIONS.md)

  // Fmt (byte 0 bits 7:5) and Type (bits 4:0) (TLP.md, kinds)
  localparam logic [4:0] T_MEM      = 5'h00;
  localparam logic [4:0] T_MEMLK    = 5'h01;
  localparam logic [4:0] T_IO       = 5'h02;
  localparam logic [4:0] T_CFG0     = 5'h04;
  localparam logic [4:0] T_CFG1     = 5'h05;
  localparam logic [4:0] T_CPL      = 5'h0A;
  localparam logic [4:0] T_CPLLK    = 5'h0B;
  localparam logic [4:0] T_FETCHADD = 5'h0C;
  localparam logic [4:0] T_SWAP     = 5'h0D;
  localparam logic [4:0] T_CAS      = 5'h0E;

  // Message codes (ROUTING.md, messages)
  localparam logic [7:0] MSG_UNLOCK       = 8'h00;
  localparam logic [7:0] MSG_PM_AS_NAK    = 8'h14;
  localparam logic [7:0] MSG_PME_TURN_OFF = 8'h19;
  localparam logic [7:0] MSG_SET_SLOT_PWR = 8'h50;
  localparam logic [7:0] MSG_VDM1         = 8'h7F;

  localparam logic [1:0] D0 = 2'd0;

  // tl_err_o bits (TL_CFG error events)
  localparam int unsigned E_UR_P     = 0;
  localparam int unsigned E_UR_NP    = 1;
  localparam int unsigned E_MAL      = 2;
  localparam int unsigned E_POIS_REQ = 3;

  // ---------------------------------------------------------------- types

  // A TLP's credit class, as the dll counts it (dll FLOW_CONTROL classes).
  typedef enum logic [1:0] {
    CLS_P   = 2'd0,
    CLS_NP  = 2'd1,
    CLS_CPL = 2'd2
  } t_cls;

  // Receive outcome (ROUTING.md, outcomes).
  typedef enum logic [2:0] {
    OUT_CQ   = 3'd0,   // BAR0 memory request, to cq
    OUT_BAR2 = 3'd1,   // BAR2 memory request, to cfg
    OUT_CFG  = 3'd2,   // Type 0 configuration request, to cfg
    OUT_UR   = 3'd3,   // Unsupported Request (or a poisoned non-posted request)
    OUT_DROP = 3'd4,   // dropped (a silent message or a poisoned posted request)
    OUT_PME  = 3'd5,   // PME_Turn_Off: answered with PME_TO_Ack
    OUT_MAL  = 3'd6,   // malformed
    OUT_CPL  = 3'd7    // completion, to the tracker
  } t_out;

  // Posted queue entry kind.
  typedef enum logic [1:0] {
    PK_CQ      = 2'd0,   // BAR0 write: leaves at its cq eop beat
    PK_BAR2    = 2'd1,   // BAR2 write: leaves when cfg takes its last access
    PK_DISCARD = 2'd2,   // leaves at the head: Unsupported Request, dropped, malformed, poisoned
    PK_PME     = 2'd3    // leaves at the head and asks for PME_TO_Ack
  } t_pkind;

  // cfg line entry kind.
  typedef enum logic [1:0] {
    CK_CFG_RD  = 2'd0,
    CK_CFG_WR  = 2'd1,
    CK_BAR2_RD = 2'd2,
    CK_UR      = 2'd3    // answered with an Unsupported Request completion, no access
  } t_ckind;

  // Completion record to the completion former (#291).
  typedef enum logic [1:0] {
    CR_UR      = 2'd0,
    CR_CFG_WR  = 2'd1,
    CR_CFG_RD  = 2'd2,
    CR_BAR2_RD = 2'd3
  } t_crkind;

  typedef logic [EP_W-1:0] t_ep;
  typedef t_ep [NLINE-1:0] t_eps;

  // Requester context a completion needs (COMPLETIONS.md): Requester ID,
  // all 8 Tag bits, TC, Attr bits 1:0.
  typedef struct packed {
    logic [15:0] rid;
    logic [7:0]  tag;
    logic [2:0]  tc;
    logic [1:0]  attr;
  } t_req_id;

  // Header of a received request, decoded from its DWs (TLP.md).
  typedef struct packed {
    logic [2:0]  fmt;
    logic [4:0]  typ;
    logic [2:0]  tc;
    logic [4:0]  b1_other;   // byte 1 bits 7 and 3:0 (Attr bit 2, TH): ignored
    logic        td;         // not read: the digest DW after the data is dropped (TLP.md, TD)
    logic        ep;
    logic [1:0]  attr;       // Attr bits 1:0
    logic [1:0]  at;
    logic [10:0] dw_len;     // DWs, 1 to 1024 (Length 0 is 1024)
    logic [15:0] rid;
    logic [7:0]  tag;
    logic [3:0]  lbe;
    logic [3:0]  fbe;
    logic [63:0] addr;       // memory: byte address; bits 1:0 are ignored
    logic [7:0]  bus;        // configuration: bytes 8, 9 and 10, 11
    logic [4:0]  dev;
    logic [2:0]  fn;
    logic [9:0]  reg_num;    // Extended Register Number, Register Number
  } t_hdr;

  // The TLP being received, judged at its last header DW (rx state).
  typedef struct packed {
    t_out        out;
    logic [3:0]  err;        // tl_err_o bits 3:0
    t_cls        cls;
    logic [3:0]  rel_d;      // data credits the dll counted, 0 to 8
    t_req_id     id;
    logic [25:0] addr;       // address bits 27:2 (BAR0 offset, BAR2 DW index in [9:0])
    logic [9:0]  reg_num;
    logic [10:0] dw_len;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
    logic        mem;        // a memory request (locked or not)
    logic        locked;     // a locked memory read
    logic        write;      // Fmt "with data"
    logic [7:0]  bus;
    logic [4:0]  dev;
    logic [31:0] wdata;      // first data DW (a configuration write)
  } t_rx_cur;

  // Posted queue entry.
  typedef struct packed {
    t_pkind      kind;
    logic [25:0] addr;       // address bits 27:2
    logic [5:0]  dw_len;     // 1 to 32 (a write)
    logic [3:0]  fbe;
    logic [3:0]  lbe;
    logic [3:0]  rel_d;
    t_eps        ep;         // its epoch on each line
  } t_pq_ent;

  // Non-posted cq line entry: a BAR0 read.
  typedef struct packed {
    t_ep         ep;
    t_req_id     id;
    logic [25:0] addr;       // address bits 27:2
    logic [10:0] dw_len;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
  } t_cq_ent;

  // Non-posted cfg line entry.
  typedef struct packed {
    t_ep         ep;
    t_ckind      kind;
    t_req_id     id;
    logic [9:0]  reg_num;    // configuration: register; BAR2: first DW index
    logic [6:0]  addr_lo;    // memory: address bits 6:0 (Lower Address)
    logic [10:0] dw_len;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
    logic        mem;
    logic        locked;
    logic [31:0] wdata;
    logic [7:0]  bus;
    logic [4:0]  dev;
    logic [3:0]  rel_d;
  } t_cfg_ent;

  // A BAR0 read taken on cq: what the completion former (#291) keeps under
  // its handle.
  typedef struct packed {
    logic [2:0]  handle;
    t_req_id     id;
    logic [9:0]  addr;       // address bits 11:2
    logic [10:0] dw_len;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
  } t_hd_ctx;

  // One completion record to the completion former (#291).
  typedef struct packed {
    t_crkind     kind;
    t_req_id     id;
    logic        mem;        // UR: of a memory read (Byte Count and Lower Address from it)
    logic        locked;     // UR: of a locked read (locked completion)
    logic [11:0] addr;       // memory: byte address bits 11:0 of the request
    logic [10:0] dw_len;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
    logic [10:0] dw_idx;     // BAR2 read: index of this DW
    logic        dw_last;    // BAR2 read: the read's last DW
    logic [31:0] data;       // CFG_RD, BAR2_RD: the DW from cfg
  } t_cpl_rec;

  // A beat of the receive stream (to the tracker, #292).
  typedef struct packed {
    logic        valid;
    logic [31:0] data;
    logic        sop;
    logic        eop;
  } t_rx_beat;

  // Receive parser state.
  typedef struct packed {
    logic        busy;       // inside a TLP
    logic [5:0]  beat;       // beats of it taken, saturating at 63
    logic        hdr4;       // 4-DW header
    logic        cpl;        // a completion: its beats go to the tracker
    logic        push;       // the TLP ended at the last edge: push its entry now
  } t_rx_state;

  // Register-access engine state (MAS, register access).
  typedef enum logic [1:0] {
    R_IDLE  = 2'd0,
    R_OFFER = 2'd1,          // an access offered on reg_req
    R_WAIT  = 2'd2           // an access taken, waiting for reg_cpl_valid
  } t_reg_st;

  typedef struct packed {
    t_reg_st     st;
    logic        posted;     // a BAR2 write from the posted queue
    logic        up;         // reg_req_valid was 1 and the access is not yet taken
    logic [10:0] idx;        // access index within the request
  } t_reg_state;

  // cq stream state (MAS, cq).
  typedef struct packed {
    logic        busy;       // a packet offered and not finished
    logic        wr;         // it is a write
    logic [2:0]  handle;     // a read's handle
    logic [5:0]  beat;       // write beats taken
  } t_cq_state;

  // ------------------------------------------------------------ helpers

  // A DW of big-endian address bytes as an address word (TLP.md: byte 8
  // carries address bits 31:24).
  function automatic logic [31:0] be_word(input logic [31:0] dw);
    return {dw[7:0], dw[15:8], dw[23:16], dw[31:24]};
  endfunction

  // Decode a request header from its DWs as they sit on the stream.
  function automatic t_hdr hdr_decode(input logic [31:0] dw0, input logic [31:0] dw1,
                                      input logic [31:0] dw2, input logic [31:0] dw3);
    t_hdr        h;
    logic [9:0]  lfield;
    logic [31:0] w2;
    logic [31:0] w3;
    lfield    = {dw0[17:16], dw0[31:24]};
    w2        = be_word(dw2);
    w3        = be_word(dw3);
    h.fmt     = dw0[7:5];
    h.typ     = dw0[4:0];
    h.tc      = dw0[14:12];
    h.b1_other = {dw0[15], dw0[11:8]};
    h.td      = dw0[23];
    h.ep      = dw0[22];
    h.attr    = dw0[21:20];
    h.at      = dw0[19:18];
    h.dw_len  = (lfield == 10'd0) ? 11'd1024 : {1'b0, lfield};
    h.rid     = {dw1[7:0], dw1[15:8]};
    h.tag     = dw1[23:16];
    h.lbe     = dw1[31:28];
    h.fbe     = dw1[27:24];
    if (dw0[5]) begin
      h.addr = {w2, w3};
    end else begin
      h.addr = {32'd0, w2};
    end
    h.bus     = dw2[7:0];
    h.dev     = dw2[15:11];
    h.fn      = dw2[10:8];
    h.reg_num = {dw2[19:16], dw2[31:26]};
    return h;
  endfunction

  // Credit class from Fmt bit 6 and Type, as the dll counts it.
  function automatic t_cls credit_class(input logic with_data, input logic [4:0] typ);
    t_cls c;
    if ((typ == T_MEM && with_data) || typ[4:3] == 2'b10) begin
      c = CLS_P;
    end else if (typ[4:1] == 4'b0101) begin
      c = CLS_CPL;
    end else begin
      c = CLS_NP;
    end
    return c;
  endfunction

  // Data credits the dll counted: Length / 4 rounded up when Fmt says the
  // TLP has data. The dll delivers at most 32 data DWs (Length bits 5:0),
  // so at most 8.
  function automatic logic [3:0] data_credits(input logic with_data, input logic [5:0] dw_len);
    return with_data ? 4'(({1'b0, dw_len} + 7'd3) >> 2) : 4'd0;
  endfunction

  // Step 1: Fmt and Type name a kind (TLP.md, kinds); no prefix.
  function automatic logic kind_known(input logic [2:0] fmt, input logic [4:0] typ);
    logic ok;
    ok = 1'b0;
    if (!fmt[2]) begin
      case (typ)
        T_MEM:                       ok = 1'b1;
        T_MEMLK:                     ok = !fmt[1];
        T_IO, T_CFG0, T_CFG1:        ok = !fmt[0];
        T_CPL, T_CPLLK:              ok = !fmt[0];
        T_FETCHADD, T_SWAP, T_CAS:   ok = fmt[1];
        default:                     ok = (typ[4:3] == 2'b10) && fmt[0];
      endcase
    end
    return ok;
  endfunction

  // A completion kind with a defined Fmt: its beats go to the tracker.
  function automatic logic is_cpl(input logic [2:0] fmt, input logic [4:0] typ);
    return (typ == T_CPL || typ == T_CPLLK) && kind_known(fmt, typ);
  endfunction

  // Step 3: the byte-enable rules of a memory request (ROUTING.md).
  function automatic logic be_ok(input logic [10:0] dw_len, input logic [3:0] fbe,
                                 input logic [3:0] lbe, input logic addr_b2);
    logic ok;
    if (dw_len == 11'd1) begin
      ok = (lbe == 4'd0);
    end else if (fbe == 4'd0 || lbe == 4'd0) begin
      ok = 1'b0;
    end else if (dw_len == 11'd2 && !addr_b2) begin
      ok = 1'b1;
    end else begin
      ok = (fbe == 4'b1111 || fbe == 4'b1110 || fbe == 4'b1100 || fbe == 4'b1000)
        && (lbe == 4'b0001 || lbe == 4'b0011 || lbe == 4'b0111 || lbe == 4'b1111);
    end
    return ok;
  endfunction

  // Step 4: the request's bytes leave its aligned 4 KiB block.
  function automatic logic cross_4k(input logic [11:2] addr_dw, input logic [10:0] dw_len);
    logic [12:0] end_dw;
    end_dw = {3'd0, addr_dw} + {2'd0, dw_len};
    return end_dw > 13'd1024;
  endfunction

  // Step 6: message codes that require TC 0 (ROUTING.md).
  function automatic logic msg_tc0(input logic [7:0] code);
    logic hit;
    case (code)
      8'h14, 8'h18, 8'h19, 8'h1B, 8'h30, 8'h31, 8'h33, MSG_UNLOCK, MSG_SET_SLOT_PWR: hit = 1'b1;
      default: hit = (code[7:3] == 5'b00100);   // INTx, 0x20 to 0x27
    endcase
    return hit;
  endfunction

  // Messages that are dropped without an error (ROUTING.md, messages).
  function automatic logic msg_silent(input logic [7:0] code);
    logic hit;
    case (code)
      MSG_UNLOCK, MSG_PM_AS_NAK, MSG_SET_SLOT_PWR, MSG_VDM1,
      8'h40, 8'h41, 8'h43, 8'h44, 8'h45, 8'h47, 8'h48: hit = 1'b1;
      default: hit = 1'b0;
    endcase
    return hit;
  endfunction

  // Lowest clear bit of a handle-busy mask (all-busy gives 0; callers check).
  function automatic logic [2:0] lowest_free(input logic [HANDLES-1:0] busy);
    logic [2:0] h;
    h = 3'd0;
    for (int i = HANDLES - 1; i >= 0; i--) begin
      if (!busy[i]) begin
        h = 3'(i);
      end
    end
    return h;
  endfunction

  // An entry of epoch e waits no more: the oldest posted entry still held
  // is of a later epoch on its line, or none is held. The difference is
  // exact in EP_W bits (MAS, receive queues).
  function automatic logic ep_after(input t_ep oldest, input t_ep e);
    t_ep d;
    d = oldest - e;
    return !d[EP_W-1] && d != '0;
  endfunction

endpackage

`default_nettype wire
