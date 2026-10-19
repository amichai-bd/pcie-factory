// pcie_rc_tl package: the Type 1 header state the blocks share, the
// header access, the front end's state and the command checks, the error
// record bits, the requester's, receive parser's and transmit arbiter's
// types, and the inbound side's: host memory, the non-posted queue, the
// completer and the MSI capture, with their helpers
// (wiki/src/rtl/rc_tl/MAS.md#leaf-blocks). Helper functions take plain
// vectors: the t_rc_cmd members len and last are read outside them (MAS,
// Interfaces).
`default_nettype none

package pcie_rc_tl_pkg;

  // ------------------------------------------------------------ header

  // The header state the other blocks route by, from the header's flops
  // (MAS, Type 1 header).
  typedef struct packed {
    logic        mse;        // Memory Space Enable
    logic        bme;        // Bus Master Enable
    logic [7:0]  pri_bus;    // Primary Bus Number
    logic [7:0]  sec_bus;    // Secondary Bus Number
    logic [11:0] mem_base;   // Memory Base [15:4]: address [31:20] of the window's first byte
    logic [11:0] mem_limit;  // Memory Limit [15:4]: address [31:20] of the window's last 1 MiB
  } t_hdr_st;

  // A header access from the front end: the DW the read data is for, and
  // a write at this edge with its byte enables.
  typedef struct packed {
    logic        wr;
    logic [9:0]  idx;
    logic [3:0]  be;
    logic [31:0] data;
  } t_hdr_acc;

  // ------------------------------------------------------------ error record

  // Error record bit numbers (TL_SEQ.md, Error record); 31:13 read 0.
  // Typed enums, so a user of the package that reads no code lints clean.
  localparam int unsigned ERR_W = 13;
  typedef enum logic [3:0] {
    ERR_UR    = 4'd0,   // completion with Unsupported Request
    ERR_CA    = 4'd1,   // completion with Completer Abort
    ERR_CRS   = 4'd2,   // configuration Request Retry Status
    ERR_TMO   = 4'd3,   // completion timeout
    ERR_UNEXP = 4'd4,   // completion matching no request
    ERR_POIS  = 4'd5,   // poisoned TLP
    ERR_MAL   = 4'd6,   // malformed TLP (or reserved status)
    ERR_COR   = 4'd7,   // ERR_COR message
    ERR_NFAT  = 4'd8,   // ERR_NONFATAL message
    ERR_FAT   = 4'd9,   // ERR_FATAL message
    ERR_MSG   = 4'd10,  // any other message
    ERR_IN_UR = 4'd11,  // inbound request handled as Unsupported Request
    ERR_MSI   = 4'd12   // MSI dropped, capture full
  } t_err_bit;

  // ------------------------------------------------------------ front end

  // The front end's state (MAS, Commands): a command in progress, from the
  // edge that takes it to its last result beat, the beat out this cycle
  // (rsp 0 between beats), and the error record.
  typedef struct packed {
    logic                busy;
    logic                beat;
    common_pkg::t_rc_rsp rsp;
    logic [ERR_W-1:0]    err;
  } t_fe_st;

  // Host memory is at most 64 KiB: a DW offset of 14 bits
  // (MAS, Parameters; INBOUND.md, Host memory).
  localparam int unsigned HADW_W = 14;

  // A HOST_* command that passed the checks, waiting for host memory from
  // the cycle after its take to its grant (INBOUND.md, Host memory).
  typedef struct packed {
    logic              valid;
    logic              wr;      // HOST_WR
    logic [HADW_W-1:0] adw;     // DW offset, addr[HOST_MEM_ADDR_W-1:2]
    logic [3:0]        be;      // first_be
    logic [31:0]       data;
  } t_host_acc;

  // A result beat at this edge, from the requester to the front end, which
  // registers it into the beat out in the next cycle.
  typedef struct packed {
    logic                valid;
    common_pkg::t_rc_rsp rsp;
  } t_res;

  // An endpoint command that passes checks 1 to 7, from the front end at
  // the take; the requester loads it (REQUESTER.md, Requests). Members are
  // not named len or last (MAS, Interfaces).
  typedef struct packed {
    logic        start;
    logic [1:0]  op;       // cmd.op: CFG_RD 0, CFG_WR 1, MEM_RD 2, MEM_WR 3
    logic [31:0] addr;
    logic [5:0]  dw_n;     // cmd.len
    logic [3:0]  fbe;
    logic [3:0]  lbe;
    logic [31:0] data;
  } t_req_cmd;

  // ------------------------------------------------------------ requester

  // The requester's state: the one endpoint request (REQUESTER.md).
  typedef struct packed {
    logic        act;      // outstanding: from the take to its last beat's edge
    logic        txq;      // waiting for the transmit arbiter's grant
    logic        sent;     // a non-posted request whose sop the link took
    logic [1:0]  op;
    logic [31:0] addr;
    logic [5:0]  dw_n;
    logic [3:0]  fbe;
    logic [3:0]  lbe;
    logic [31:0] data;
    logic [15:0] rid;      // Requester ID: {Primary Bus Number, 0, 0} at the take
    logic [4:0]  tag;      // tag_q at the take
    logic [7:0]  left;     // a read's bytes still due (1 to 128)
    logic [6:0]  nxt;      // a read's next byte due, address bits [6:0]
  } t_req_st;

  // The completion being received, as decided at its third header DW
  // (REQUESTER.md, Completion matching).
  typedef struct packed {
    logic                   on;      // from the third header DW to the eop
    logic                   match;   // it matched the outstanding request
    logic                   dlv;     // Successful and fit: its data DWs go out
    logic                   fin;     // ends the request at its eop with rsp
    logic                   lastc;   // it carries the read's last byte
    logic [5:0]             cnt;     // data DWs still to go out
    common_pkg::t_rc_status status;  // the result of a failing completion
    logic [ERR_W-1:0]       err;     // its error record bits, at its eop
  } t_cpl_st;

  // ------------------------------------------------------------ receive

  // A completion beat as the parser hands it to the requester: the header
  // fields with the third header DW, then every later beat (data and a
  // digest DW), and the eop.
  typedef struct packed {
    logic        hdr;      // the third header DW is taken at this edge
    logic        dat;      // a beat after the header is taken at this edge
    logic        eop;      // the completion's last beat is taken at this edge
    logic [31:0] data;     // the beat
    logic        locked;   // Type CplLk
    logic        wdata;    // Fmt: with data
    logic        ep;       // poisoned
    logic [2:0]  status;   // Completion Status
    logic [10:0] dw_n;     // data DWs: Length (0 read as 1024), 0 without data
    logic [11:0] bc;       // Byte Count (0 is 4096: never a read's bytes due)
    logic [15:0] rid;      // Requester ID
    logic [7:0]  tag;
    logic [6:0]  la;       // Lower Address
  } t_cpl_rx;

  // Completion Status codes [cite: PCIe Base Spec, rev 3.0, §2.2.9].
  typedef enum logic [2:0] {
    CS_SC  = 3'b000,
    CS_UR  = 3'b001,
    CS_CRS = 3'b010,
    CS_CA  = 3'b100
  } t_cpl_status;

  // ------------------------------------------------------------ transmit

  // A TLP for the transmit arbiter: 3-DW header and at most one data DW.
  typedef struct packed {
    logic        valid;
    logic        wdata;    // Fmt: with data (one DW)
    logic [4:0]  typ;      // Type, for its credit class
    logic [31:0] dw0;
    logic [31:0] dw1;
    logic [31:0] dw2;
    logic [31:0] data;
  } t_tx_tlp;

  // A completion for the transmit arbiter: its 3-DW header and its data
  // DWs, which are all in the completion data queue (INBOUND.md, Completer).
  typedef struct packed {
    logic        valid;
    logic [5:0]  dw_n;     // data DWs, 0 to 32
    logic [31:0] dw0;
    logic [31:0] dw1;
    logic [31:0] dw2;
  } t_cpl_tx;

  // The arbiter's state: the granted TLP, latched at the grant so a new
  // request never changes a TLP still being sent, and its next beat.
  typedef struct packed {
    logic        busy;
    logic [5:0]  beat;
    logic        np;       // a requester non-posted request
    logic        cpl;      // a completion: its data from the completion data queue
    logic [5:0]  dat_n;    // data beats after the header, 0 to 32
    logic [31:0] dw0;
    logic [31:0] dw1;
    logic [31:0] dw2;
    logic [31:0] data;     // a requester TLP's one data DW
  } t_tx_arb;

  // A DW for the output stage (common_skid_stage, which carries its valid).
  typedef struct packed {
    logic [31:0] data;
    logic        sop;
    logic        eop;
    logic        np;       // of a requester non-posted request
    logic        cpl;      // of a completion
  } t_tx_dw;

  // ------------------------------------------------------------ inbound

  // Message codes the error record names [cite: PCIe Base Spec, rev 3.0, §2.2.8.3].
  typedef enum logic [7:0] {
    MSG_ERR_COR  = 8'h30,
    MSG_ERR_NFAT = 8'h31,
    MSG_ERR_FAT  = 8'h33
  } t_msg_code;

  // A non-posted queue entry (INBOUND.md, Completer): a read of host
  // memory, an Unsupported Request (locked or not), or a drop.
  typedef enum logic [1:0] {
    NP_READ = 2'd0,
    NP_UR   = 2'd1,
    NP_URLK = 2'd2,
    NP_DROP = 2'd3
  } t_np_kind;

  typedef struct packed {
    t_np_kind          kind;
    logic              mem;     // a memory request: Byte Count T, Lower Address B
    logic [15:0]       rid;     // Requester ID
    logic [7:0]        tag;
    logic [2:0]        tc;
    logic [1:0]        attr;    // Attr bits 1:0
    logic [HADW_W-1:0] adw;     // address bits 15:2: the host DW offset, and the DW in 4 KiB
    logic [10:0]       dw_n;    // Length, 0 read as 1024
    logic [3:0]        fbe;
    logic [3:0]        lbe;
    logic [3:0]        dc;      // data credits released when it leaves the queue
  } t_np_ent;

  // A received request or message, from its last header DW to its eop
  // (INBOUND.md, Receive checks): what its eop does, and its data DWs.
  typedef struct packed {
    logic              on;
    logic              wr;      // a posted write to host memory: its data DWs written
    logic              msi;     // an MSI: captured at the eop
    logic              np;      // non-posted: ent pushed at the eop
    logic              rel;     // posted: released at the eop with dc
    logic [ERR_W-1:0]  err;     // its error record bits, at the eop
    logic [5:0]        cnt;     // data DWs still to come (a digest DW after them)
    logic              dbeg;    // the next data DW is the first
    logic [9:0]        moff;    // MSI offset, address bits 11:2
    logic [31:0]       mdata;   // MSI data, the first data DW
    t_np_ent           ent;     // the queue entry, or the write's lanes and offset
  } t_rx_req;

  // A host memory write, registered from the received DW (INBOUND.md,
  // Host memory): it always wins the port.
  typedef struct packed {
    logic              valid;
    logic [HADW_W-1:0] adw;
    logic [3:0]        be;
    logic [31:0]       data;
  } t_mem_wr;

  // The host memory port's state: who had the last contested turn and
  // whose read data comes back in this cycle.
  typedef struct packed {
    logic        host_last;  // the front end had the latest grant
    logic        rd_host;    // a HOST_RD was granted at the last edge
    logic        rd_cpl;     // a completer read was granted at the last edge
  } t_mem_st;

  // The completer's state (INBOUND.md, Completer): the entry in service
  // and its current completion.
  typedef struct packed {
    logic              act;     // an entry in service
    logic              gnt;     // its completion granted, waiting for the link to take its eop
    t_np_ent           ent;
    logic [9:0]        dwa;     // the DW (in 4 KiB) the current completion starts at
    logic              beg;     // the current completion is the first
    logic [12:0]       fin;     // B + T: the byte (in 4 KiB) after the read's last
    logic [5:0]        k;       // the current completion's data DWs
    logic [10:0]       rest;    // the read's DWs after the current completion
    logic [5:0]        iss;     // the current completion's reads still to issue
    logic [5:0]        got;     // its DWs still to arrive
    logic [HADW_W-1:0] radw;    // the next host DW to read
    logic [10:0]       ret;     // the read's DWs not yet returned
  } t_cpl_ctl;

  // The MSI capture (INBOUND.md, MSI capture): entries in arrival order,
  // the oldest at 0; an empty entry is 0.
  localparam int unsigned MSI_DEPTH = 4;
  typedef struct packed {
    logic [2:0]                                cnt;
    common_pkg::t_rc_msi [MSI_DEPTH-1:0]       ent;
  } t_msi_st;

  // ------------------------------------------------------------ helpers

  // The bits of `old` that `rw` marks, in the bytes `be` enables, take `data`.
  function automatic logic [31:0] wr_rw(input logic [31:0] old, input logic [31:0] data,
                                        input logic [3:0] be, input logic [31:0] rw);
    logic [31:0] m;
    m = {{8{be[3]}}, {8{be[2]}}, {8{be[1]}}, {8{be[0]}}} & rw;
    wr_rw = (old & ~m) | (data & m);
  endfunction

  // An endpoint command: CFG_* or MEM_*.
  function automatic logic is_ep(input logic [3:0] op);
    is_ep = (op == common_pkg::RC_OP_CFG_RD) || (op == common_pkg::RC_OP_CFG_WR)
         || (op == common_pkg::RC_OP_MEM_RD) || (op == common_pkg::RC_OP_MEM_WR);
  endfunction

  // Check 2: a defined operation.
  function automatic logic op_ok(input logic [3:0] op);
    op_ok = (op <= 4'(common_pkg::RC_OP_ERR_CLR));
  endfunction

  // Check 3: len 1 to 32 for MEM_RD, 1 otherwise.
  function automatic logic len_ok(input logic [3:0] op, input logic [5:0] n);
    if (op == common_pkg::RC_OP_MEM_RD) begin
      len_ok = (n >= 6'd1) && (n <= 6'd32);
    end else begin
      len_ok = (n == 6'd1);
    end
  endfunction

  // Check 4: the address bits outside the op's fields are 0. host_mask
  // marks the bits at and above HOST_MEM_ADDR_W.
  function automatic logic addr_ok(input logic [3:0] op, input logic [31:0] addr,
                                   input logic [31:0] host_mask);
    logic [31:0] m;
    case (op)
      common_pkg::RC_OP_CFG_RD, common_pkg::RC_OP_CFG_WR:   m = 32'hF000_0003;
      common_pkg::RC_OP_MEM_RD, common_pkg::RC_OP_MEM_WR:   m = 32'h0000_0003;
      common_pkg::RC_OP_HDR_RD, common_pkg::RC_OP_HDR_WR:   m = 32'hFFFF_F003;
      common_pkg::RC_OP_HOST_RD, common_pkg::RC_OP_HOST_WR: m = host_mask | 32'h0000_0003;
      default:                                              m = 32'h0000_0000;
    endcase
    addr_ok = ((addr & m) == 32'd0);
  endfunction

  // Check 5: a MEM_RD stays inside one aligned 4 KiB block.
  function automatic logic rd_4k_ok(input logic [3:0] op, input logic [9:0] dw,
                                    input logic [5:0] n);
    rd_4k_ok = (op != common_pkg::RC_OP_MEM_RD) || ((11'(dw) + 11'(n)) <= 11'd1024);
  endfunction

  // Check 6: legal byte enables (REQUESTER.md, Byte enables). a2 is addr[2].
  function automatic logic be_ok(input logic [3:0] op, input logic [5:0] n, input logic a2,
                                 input logic [3:0] fbe, input logic [3:0] lbe);
    logic contig;
    contig = ((fbe == 4'b1111) || (fbe == 4'b1110) || (fbe == 4'b1100) || (fbe == 4'b1000))
          && ((lbe == 4'b0001) || (lbe == 4'b0011) || (lbe == 4'b0111) || (lbe == 4'b1111));
    if (n == 6'd1) begin
      be_ok = (lbe == 4'd0);
      if ((op == common_pkg::RC_OP_CFG_RD) || (op == common_pkg::RC_OP_CFG_WR)
          || (op == common_pkg::RC_OP_MEM_WR)) begin
        be_ok = be_ok && (fbe != 4'd0);
      end
    end else begin
      // Only a MEM_RD passes check 3 with len above 1.
      be_ok = (fbe != 4'd0) && (lbe != 4'd0) && (contig || ((n == 6'd2) && !a2));
    end
  endfunction

  // Check 7: outbound routing by the header state. A configuration request
  // goes to bus Secondary Bus Number, device 0; a memory request needs
  // Memory Space Enable and addr[31:20] inside the memory window. a is
  // addr[31:15]; the routing reads only the enables, the Secondary Bus
  // Number and the window of the header state.
  function automatic logic route_ok(input logic [3:0] op, input logic [16:0] a,
                                    input logic mse, input logic [7:0] sec_bus,
                                    input logic [11:0] base, input logic [11:0] limit);
    case (op)
      common_pkg::RC_OP_CFG_RD, common_pkg::RC_OP_CFG_WR:
        route_ok = (a[12:5] == sec_bus) && (a[4:0] == 5'd0);
      common_pkg::RC_OP_MEM_RD, common_pkg::RC_OP_MEM_WR:
        route_ok = mse && (a[16:5] >= base) && (a[16:5] <= limit);
      default:
        route_ok = 1'b1;
    endcase
  endfunction

  // ------------------------------------------------------------ requests

  // DW 0 of a TLP with TC 0, Attr 0, TD 0, EP 0, AT 0 (REQUESTER.md,
  // Requests; byte 0 in [7:0]).
  function automatic logic [31:0] req_dw0(input logic [2:0] fmt, input logic [4:0] typ,
                                          input logic [9:0] lfield);
    req_dw0 = {lfield[7:0], 6'd0, lfield[9:8], 8'd0, fmt, typ};
  endfunction

  // DW 1 of a request: Requester ID in bytes 4 and 5, Tag, byte enables.
  function automatic logic [31:0] req_dw1(input logic [15:0] rid, input logic [4:0] tag,
                                          input logic [3:0] lbe, input logic [3:0] fbe);
    req_dw1 = {lbe, fbe, 3'd0, tag, rid[7:0], rid[15:8]};
  endfunction

  // DW 2 of a Type 0 configuration request from a CFG_* addr: Bus, Device
  // and Function, Extended Register and Register Numbers in bytes 8 to 11.
  function automatic logic [31:0] cfg_dw2(input logic [27:2] a);
    cfg_dw2 = {a[7:2], 2'b00, 4'd0, a[11:8], a[19:12], a[27:20]};
  endfunction

  // A read's next byte, from its address and first byte enables: the
  // lowest enabled byte (the first byte when none is).
  function automatic logic [6:0] first_byte(input logic [6:2] a, input logic [3:0] fbe);
    first_byte = {a, common_pkg::be_lo(fbe)};
  endfunction

  // ------------------------------------------------------------ completions

  // A completion's DWs [cite: PCIe Base Spec, rev 3.0, §2.2.9]: DW 0 with
  // TC, Attr bits 1:0 and Length, TD, EP and Attr bit 2 0; DW 1 with the
  // Completer ID, status, BCM 0 and Byte Count; DW 2 with the Requester
  // ID, Tag and Lower Address.
  function automatic logic [31:0] cpl_dw0(input logic [2:0] fmt, input logic [4:0] typ,
                                          input logic [2:0] tc, input logic [1:0] attr,
                                          input logic [9:0] lfield);
    cpl_dw0 = {lfield[7:0], 2'b00, attr, 2'b00, lfield[9:8], 1'b0, tc, 4'd0, fmt, typ};
  endfunction

  function automatic logic [31:0] cpl_dw1(input logic [15:0] cid, input logic [2:0] st,
                                          input logic [11:0] bc);
    cpl_dw1 = {bc[7:0], st, 1'b0, bc[11:8], cid[7:0], cid[15:8]};
  endfunction

  function automatic logic [31:0] cpl_dw2(input logic [15:0] rid, input logic [7:0] tag,
                                          input logic [6:0] la);
    cpl_dw2 = {1'b0, la, tag, rid[7:0], rid[15:8]};
  endfunction

  // A completion's data DWs (INBOUND.md, Completer): the rest of the read
  // when it is at most 32 DWs, else up to the highest 64-byte boundary
  // within 128 bytes of its start DW dwa (read completion boundary 64).
  function automatic logic [5:0] cpl_dws(input logic [10:0] rest, input logic [3:0] dwa);
    if (rest <= 11'd32) begin
      cpl_dws = rest[5:0];
    end else begin
      cpl_dws = 6'd32 - {2'd0, dwa};
    end
  endfunction

endpackage

`default_nettype wire
