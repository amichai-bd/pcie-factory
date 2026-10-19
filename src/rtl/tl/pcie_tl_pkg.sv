// Package of pcie_tl (wiki/src/rtl/tl/MAS.md#package): the header field
// codes but Type, the structs the blocks exchange, the block state types
// and the helpers that need them. The Type codes and the pure TLP helpers
// of the receive checks (wiki/src/arch/tl/ROUTING.md) and the credit rule
// (wiki/src/arch/tl/HAS.md#credit-release) are in common_pkg.
`default_nettype none

package pcie_tl_pkg;

  // ------------------------------------------------------------ constants

  // Ordering lines (MAS, receive queues): a posted entry carries one epoch
  // per line; an entry of a line waits for every posted entry of an epoch
  // not after its own.
  localparam int unsigned NLINE    = 3;
  localparam int unsigned LINE_CQ  = 0;   // non-posted, cq line (BAR0 reads)
  localparam int unsigned LINE_CFG = 1;   // non-posted, cfg line
  localparam int unsigned LINE_RC  = 2;   // received completions, to rc
  localparam int unsigned EP_W     = 9;   // epoch counter: 2**(EP_W-1) above FC_PH and FC_NPH + 1

  localparam int unsigned HANDLES  = 8;   // completer read handles (COMPLETIONS.md)
  localparam int unsigned TAGS     = 8;   // requester tags 0..7 (COMPLETIONS.md, requester tags)

  // Completion tracker (MAS, completion tracker): the timeout is the 17th
  // prescaler pulse after a read was sent; the rc buffer holds what 8
  // reads of 32 DWs can bring (1 KiB) and up to 3 completions per read.
  localparam int unsigned TMO_STEPS = 16;  // pulses that a read survives
  localparam int unsigned RC_DQ_AW  = 8;   // rc data queue: 2**8 DWs plus the head
  localparam int unsigned RC_HQ_AW  = 5;   // rc packet queue: 2**5 entries plus the head

  // Transmit ordering lines (MAS, transmit lines): an entry of a line may
  // go once every posted request taken into the transmit posted line
  // before it has gone; the posted entries carry one epoch per line.
  localparam int unsigned NTX      = 3;
  localparam int unsigned TX_NP    = 0;   // non-posted line (rq reads)
  localparam int unsigned TX_CC    = 1;   // completions from cc
  localparam int unsigned TX_CF    = 2;   // completions from cfg records
  localparam int unsigned TXP_D    = 4;   // transmit posted line entries (flop queue)
  localparam int unsigned CPL_MAX  = 32;  // DWs of the longest completion (128 bytes)

  // Message codes (ROUTING.md, messages)
  localparam logic [7:0] MSG_UNLOCK       = 8'h00;
  localparam logic [7:0] MSG_PM_AS_NAK    = 8'h14;
  localparam logic [7:0] MSG_PME_TURN_OFF = 8'h19;
  localparam logic [7:0] MSG_SET_SLOT_PWR = 8'h50;
  localparam logic [7:0] MSG_VDM1         = 8'h7F;
  localparam logic [7:0] MSG_PME_TO_ACK   = 8'h1B;
  localparam logic [7:0] MSG_ERR_COR      = 8'h30;
  localparam logic [7:0] MSG_ERR_NONFATAL = 8'h31;
  localparam logic [7:0] MSG_ERR_FATAL    = 8'h33;

  // Message routing subfield, Type bits 2:0 (TLP.md, messages)
  localparam logic [2:0] R_TO_RC  = 3'b000;
  localparam logic [2:0] R_GATHER = 3'b101;

  // Completion Status (TLP.md, completions)
  localparam logic [2:0] CS_SC  = 3'b000;
  localparam logic [2:0] CS_UR  = 3'b001;
  localparam logic [2:0] CS_CRS = 3'b010;
  localparam logic [2:0] CS_CA  = 3'b100;

  // rc_status_o (APP, requester completions)
  localparam logic [1:0] RC_OK     = 2'd0;
  localparam logic [1:0] RC_UR     = 2'd1;
  localparam logic [1:0] RC_CA     = 2'd2;
  localparam logic [1:0] RC_FAILED = 2'd3;

  localparam logic [1:0] D0 = 2'd0;

  // tl_err_o bits (TL_CFG error events)
  localparam int unsigned E_UR_P     = 0;
  localparam int unsigned E_UR_NP    = 1;
  localparam int unsigned E_MAL      = 2;
  localparam int unsigned E_POIS_REQ = 3;
  localparam int unsigned E_POIS_CPL = 4;
  localparam int unsigned E_UNEXP    = 5;
  localparam int unsigned E_TIMEOUT  = 6;
  localparam int unsigned E_RX_UR    = 7;
  localparam int unsigned E_RX_CA    = 8;

  // ---------------------------------------------------------------- types

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

  // Completion record kind, to pcie_tl_cpl.
  typedef enum logic [1:0] {
    CR_UR      = 2'd0,
    CR_CFG_WR  = 2'd1,
    CR_CFG_RD  = 2'd2,
    CR_BAR2_RD = 2'd3
  } t_crkind;

  typedef logic [EP_W-1:0] t_ep;
  typedef t_ep [NLINE-1:0] t_eps;
  typedef t_ep [NTX-1:0]   t_txeps;

  // Transmit posted line entry kind.
  typedef enum logic [1:0] {
    PX_RQ  = 2'd0,   // rq write: its data in the posted data queue
    PX_IRQ = 2'd1,   // interrupt write: its one DW in the entry
    PX_MSG = 2'd2    // message without data: an error message or PME_TO_Ack
  } t_pxkind;

  // The transmit arbiter's choice.
  typedef enum logic [1:0] {
    TS_P  = 2'd0,
    TS_NP = 2'd1,
    TS_CC = 2'd2,
    TS_CF = 2'd3
  } t_txsel;

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
    common_pkg::t_cls cls;
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
    logic [31:0] wdata;      // first data DW (a configuration write, a Set_Slot_Power_Limit)
    logic        spl;        // a Set_Slot_Power_Limit to capture: with data, no error
  } t_rx_cur;

  // The slot power event's payload (TL_CFG slot power limit).
  typedef struct packed {
    logic [7:0]  value;      // Slot Power Limit Value, data byte 0
    logic [1:0]  scale;      // Slot Power Limit Scale, data byte 1 bits 1:0
  } t_slot_pwr;

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

  // A BAR0 read taken on cq: what pcie_tl_cpl keeps under
  // its handle.
  typedef struct packed {
    logic [2:0]  handle;
    t_req_id     id;
    logic [9:0]  addr;       // address bits 11:2
    logic [10:0] dw_len;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
  } t_hd_ctx;

  // One completion record, to pcie_tl_cpl.
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

  // Transmit posted line entry (MAS, transmit lines).
  typedef struct packed {
    t_pxkind     kind;
    logic [61:0] addr;       // writes: address bits 63:2
    logic [5:0]  dw_len;     // writes: 1 to 32 DWs
    logic [3:0]  fbe;
    logic [3:0]  lbe;
    logic [7:0]  code;       // PX_MSG: Message Code
    logic [2:0]  rrr;        // PX_MSG: routing subfield
    logic [31:0] data;       // PX_IRQ: the DW written
    t_txeps      ep;         // its epoch on each transmit line
  } t_txp_ent;

  // Transmit non-posted line entry: an rq read.
  typedef struct packed {
    logic [61:0] addr;       // address bits 63:2
    logic [5:0]  dw_len;     // 1 to 32
    logic [3:0]  fbe;
    logic [3:0]  lbe;
    logic [2:0]  tag;
    t_ep         ep;
  } t_txnp_ent;

  // A completion formed and queued (COMPLETIONS.md): its header fields
  // but the Completer ID, which goes in when it is sent.
  typedef struct packed {
    logic [2:0]  status;
    logic        locked;     // the locked completion of a refused locked read
    logic [11:0] bc;         // Byte Count, 4096 as 0
    logic [6:0]  la;         // Lower Address
    t_req_id     id;
    logic [5:0]  dlen;       // data DWs, 0 to 32
    t_ep         ep;
  } t_cpl_desc;

  // What the completion former needs of a read whose DWs it splits.
  typedef struct packed {
    t_req_id     id;
    logic        fixed4;     // a configuration read: Byte Count 4, Lower Address 0
    logic [4:0]  alo;        // address bits 6:2
    logic [10:0] dw_len;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
  } t_cpl_ctx;

  // Completion former state (MAS, completion former).
  typedef struct packed {
    logic [10:0] k;          // DW index of the next beat within its read
    logic [5:0]  left;       // DWs still due in the open completion, 0: none open
    logic [12:0] rb;         // bytes from the next completion's first byte to the read's end
    logic [11:0] bc;         // the open completion's Byte Count
    logic [6:0]  la;         // its Lower Address
    logic [5:0]  n;          // its DWs
  } t_cplf_state;

  // cc stream state.
  typedef struct packed {
    logic        busy;       // inside a cc packet
    logic [2:0]  hd;         // its handle
    t_ep         ep;         // the cc line epoch at its sop beat
  } t_cc_state;

  // rq stream state.
  typedef struct packed {
    logic        busy;       // inside a write, after its sop beat
    logic [5:0]  beat;       // beats taken
    logic [5:0]  dw_len;     // its length
  } t_rq_state;

  // An rq read taken (to the tracker).
  typedef struct packed {
    logic [2:0]  tag;
    logic [9:0]  addr;       // address bits 11:2
    logic [5:0]  dw_len;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
  } t_rq_ctx;

  // Transmit engine state (MAS, transmit arbiter).
  typedef struct packed {
    logic        busy;       // a TLP offered and its eop not yet taken
    t_txsel      sel;        // its line
    logic [5:0]  beat;       // beats of it taken
    logic [1:0]  rr;         // round robin: 0 posted, 1 non-posted, 2 completions first
    logic        cf_turn;    // between the two completion queues: cfg first
  } t_tx_state;

  // A beat in the transmit output stage (MAS, transmit arbiter).
  typedef struct packed {
    logic        valid;
    logic [31:0] data;       // DW 1's ID bits 15:0 are 0 here; the stage fills them
    logic        sop;
    logic        eop;
    logic        idw;        // DW 1 of a TLP: the ID goes in bits 15:0
    logic        rq;         // the sop beat of an rq read
    logic [2:0]  tag;        // that read's tag
  } t_tx_beat;

  // Transmit output stage state: the offered beat, the skid slot, and the
  // ID captured as a sop beat is first offered.
  typedef struct packed {
    t_tx_beat    out;        // offered on tlp_tx_*
    t_tx_beat    skid;       // taken while out was held; out next
    logic        held;       // out was offered and not taken last cycle
    logic [15:0] id;         // this function's ID as the TLP's sop was first offered
  } t_tx_out;

  // A beat of a received completion (to the tracker).
  typedef struct packed {
    logic        valid;
    logic [31:0] data;
    logic        sop;
    logic        eop;
  } t_rx_beat;

  // One packet queued for rc (MAS, rc stream): a completion's data or one
  // status beat.
  typedef struct packed {
    logic [2:0]  tag;
    logic [1:0]  status;     // rc_status_o
    logic        done;       // rc_done_o with its eop: the read is finished
    logic [5:0]  dlen;       // data DWs, 1 to 32; 0: one status beat
    t_ep         ep;         // the rc line epoch when it was queued
  } t_rc_desc;

  // An outstanding requester read, per tag (MAS, completion tracker).
  typedef struct packed {
    logic        act;        // taken on rq and not ended by the tracker
    logic        snt;        // its sop beat taken by the link: completions may match
    logic        held;       // taken on rq and its rc_done beat not yet taken
    logic        failed;     // a poisoned completion arrived
    logic [4:0]  cnt;        // prescaler pulses since it was sent
    logic [7:0]  left;       // bytes still due, 1 to 128
    logic [6:0]  nxt;        // address bits 6:0 of the next byte due
  } t_trk_tag;

  // The completion being received (tracker): its first two header DWs.
  typedef struct packed {
    logic [5:0]  beat;       // beats of it taken, saturating at 63
    logic [31:0] dw0;
    logic [31:0] dw1;
  } t_trk_rx;

  // What the tracker decided at the completion's third header DW, applied
  // in the cycle after its eop.
  typedef struct packed {
    logic        pend;       // decided, not yet applied
    logic [8:0]  err;        // tl_err_o bits to pulse
    logic        push;       // a packet to queue for rc
    logic        deliver;    // its data DWs go to the rc data queue
    logic [5:0]  wr;         // data DWs written so far
    t_rc_desc    desc;
  } t_trk_cur;

  // rc stream state.
  typedef struct packed {
    logic [5:0]  beat;       // beats of the head packet taken
    logic        ok;         // the head packet has seen every earlier posted request leave
  } t_rc_state;

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

  // Decode a request header from its DWs as they sit on the stream.
  function automatic t_hdr hdr_decode(input logic [31:0] dw0, input logic [31:0] dw1,
                                      input logic [31:0] dw2, input logic [31:0] dw3);
    t_hdr        h;
    logic [9:0]  lfield;
    logic [31:0] w2;
    logic [31:0] w3;
    lfield    = {dw0[17:16], dw0[31:24]};
    w2        = common_pkg::be_word(dw2);
    w3        = common_pkg::be_word(dw3);
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

  // DW 0 of a TLP as it sits on the stream (TLP.md, common fields): TD,
  // EP, AT and Attr bit 2 are 0 on every TLP tl sends.
  function automatic logic [31:0] tx_dw0(input logic [2:0] fmt, input logic [4:0] typ,
                                         input logic [2:0] tc, input logic [1:0] attr,
                                         input logic [9:0] lfield);
    return {lfield[7:0], 2'b00, attr, 2'b00, lfield[9:8], 1'b0, tc, 4'b0000, fmt, typ};
  endfunction

  // DW 1 of a request: Requester ID, Tag, byte enables (TLP.md).
  function automatic logic [31:0] tx_dw1_req(input logic [15:0] rid, input logic [7:0] tag,
                                             input logic [3:0] lbe, input logic [3:0] fbe);
    return {lbe, fbe, tag, rid[7:0], rid[15:8]};
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
