// pcie_rc_seq package: the 128-bit step format, the op, condition and
// state types, the step constructors and the programs, each a ROM image
// constant that the PROGRAM parameter selects.
// Step format and commands: wiki/src/rtl/rc_seq/MAS.md; the programs,
// section by section: wiki/src/rtl/rc_seq/PROGRAMS.md.
`default_nettype none

package pcie_rc_seq_pkg;

  // ROM: 2**ROM_AW steps; a pc at or above ROM_STEPS reads as FAIL.
  localparam int unsigned ROM_AW    = 8;
  localparam int unsigned ROM_STEPS = 2 ** ROM_AW;

  // Step ops (MAS: Commands). 5'h07 to 5'h0F are reserved and fail as FAIL;
  // 5'h10 + t is REQ with tl-seq op t.
  typedef enum logic [4:0] {
    OP_FAIL      = 5'h00,
    OP_EXPECT    = 5'h01,
    OP_WAIT_MSI  = 5'h02,
    OP_WAIT_LINK = 5'h03,
    OP_HOT_RESET = 5'h04,
    OP_JUMP      = 5'h05,
    OP_DONE      = 5'h06,
    OP_REQ       = 5'h10
  } t_op;

  // JUMP conditions; 5 to 7 fail the step.
  typedef enum logic [2:0] {
    COND_ALWAYS    = 3'd0,
    COND_DATA_EQ   = 3'd1,
    COND_DATA_NE   = 3'd2,
    COND_STATUS_EQ = 3'd3,
    COND_STATUS_NE = 3'd4
  } t_cond;

  // One ROM word (MAS: Step format). n_dw is the MAS field len, renamed:
  // a struct member named len cannot be selected inside a function under
  // Quartus Lite (RTL BKM, SystemVerilog rules).
  typedef struct packed {
    logic                   rsvd;      // 127: ignored, written 0
    logic [4:0]             op;        // 126:122, a t_op or 5'h10 + tl-seq op
    common_pkg::t_rc_status status;    // 121:119: REQ chk, JUMP on status
    logic                   chk;       // 118: REQ fails unless its status is status
    logic [2:0]             cond;      // 117:115: a t_cond
    logic [4:0]             idx;       // 114:110: EXPECT and data JUMP read R[idx]
    logic [5:0]             n_dw;      // 109:104: REQ cmd.len
    logic [3:0]             first_be;  // 103:100
    logic [3:0]             last_be;   // 99:96
    logic [31:0]            addr;      // 95:64: REQ addr; JUMP {limit, target}; HOT_RESET hold [15:0]
    logic [31:0]            data;      // 63:32: REQ data; EXPECT and JUMP value; wait timeout
    logic [31:0]            mask;      // 31:0: EXPECT and data JUMP compared bits
  } t_step;

  typedef t_step [ROM_STEPS-1:0] t_rom;

  // A program under construction: the image and the next free index.
  typedef struct packed {
    t_rom        rom;
    logic [15:0] n;
  } t_prog;

  // Interpreter states (MAS: Microarchitecture).
  typedef enum logic [3:0] {
    ST_IDLE      = 4'd0,   // no run
    ST_FETCH     = 4'd1,   // ROM read at pc
    ST_EXEC      = 4'd2,   // decode; finish FAIL, DONE, JUMP ALWAYS and status JUMPs
    ST_READ      = 4'd3,   // R[idx] read: EXPECT, data JUMP
    ST_REQ_OFFER = 4'd4,   // cmd_valid_o until cmd_ready_i
    ST_REQ_WAIT  = 4'd5,   // result beats until the last
    ST_WAIT      = 4'd6,   // WAIT_MSI, WAIT_LINK
    ST_MSI_R0    = 4'd7,   // write R[0] = the taken capture's data
    ST_MSI_R1    = 4'd8,   // write R[1] = its offset
    ST_HR_FALL   = 4'd9,   // HOT_RESET: wait for link_up_i 0
    ST_HR_HOLD   = 4'd10   // HOT_RESET: hold
  } t_state;

  // Control state of a run.
  typedef struct packed {
    t_state                 state;
    logic [15:0]            pc;      // step index
    common_pkg::t_rc_status st;      // S, the last REQ status
    logic [15:0]            loops;   // L, takes of the current polling loop
    logic [31:0]            cnt;     // wait cycle, hold cycle
    logic [5:0]             beat;    // result beats taken by the current REQ
  } t_ctl;

  // Reset state: no run.
  localparam t_ctl CTL_RESET = '{state: ST_IDLE, pc: 16'd0, st: common_pkg::RC_ST_OK,
                                 loops: 16'd0, cnt: 32'd0, beat: 6'd0};

  // The state a run starts in: step 0 fetched, S OK, L 0 (MAS: Run).
  function automatic t_ctl ctl_run_start();
    t_ctl c;
    c       = CTL_RESET;
    c.state = ST_FETCH;
    return c;
  endfunction

  // Run result: done_o, pass_o, fail_step_o.
  typedef struct packed {
    logic        done;
    logic        pass;
    logic [15:0] fail_step;
  } t_result;

  // ------------------------------------------------------------ constants

  localparam logic [31:0] FULL = 32'hFFFF_FFFF;

  // Wait timeouts, Hot Reset hold and polling-loop limit of the M4
  // programs (MAS: Open items, set by #376). WAIT_LINK covers link
  // training on a board at TIMEOUT_DIV 1 (200 ms); WAIT_MSI covers a DMA
  // copy (10 ms); HOT_RESET waits 1 ms for link_up to fall and then holds
  // 64 cycles, more than link_up falling to dl_up falling takes, so the
  // WAIT_LINK after it never sees the old dl_up. A polling loop takes at
  // most 4096 times.
  localparam logic [31:0] LINK_TIMEOUT = 32'd25_000_000;
  localparam logic [31:0] MSI_TIMEOUT  = 32'd1_250_000;
  localparam logic [31:0] HR_TIMEOUT   = 32'd125_000;
  localparam logic [15:0] HR_HOLD      = 16'd64;
  localparam logic [15:0] POLL_LIMIT   = 16'd4096;

  // Values the programs check: rc and core at their default parameters
  // (rc HAS: Parameters, Type 1 header; core HAS: Parameters).
  localparam logic [15:0] RC_VENDOR_ID   = 16'h0000;
  localparam logic [15:0] RC_DEVICE_ID   = 16'h0000;
  localparam logic [7:0]  RC_REVISION_ID = 8'h00;
  localparam logic [15:0] EP_VENDOR_ID   = 16'h0000;
  localparam logic [15:0] EP_DEVICE_ID   = 16'h0000;
  localparam logic [31:0] BAR_4K_SIZE    = 32'hFFFF_F000;  // BAR0 and BAR2 sizing reads, 4 KiB
  localparam int unsigned MSI_DEPTH      = 4;               // rc_tl MSI capture entries (rc_tl MAS, D10)

  // Address plan (PROGRAMS: Address plan).
  localparam logic [7:0]  BUS_SEC     = 8'd1;
  localparam logic [31:0] BUSES       = 32'h0001_0100;  // header DW 6: primary 0, secondary 1, subordinate 1
  localparam logic [31:0] WINDOW      = 32'h1000_1000;  // header DW 8: Memory Base and Limit 0x100
  localparam logic [31:0] BAR0        = 32'h1000_0000;
  localparam logic [31:0] BAR2        = 32'h1000_1000;
  localparam logic [31:0] NOT_DECODED = 32'h1000_8000;
  localparam logic [31:0] OUTSIDE     = 32'h2000_0000;
  localparam logic [31:0] MSI_ADDR    = 32'hFEE0_0000;
  localparam logic [31:0] MSI_DATA    = 32'h0000_0001;
  localparam logic [31:0] DMA_SRC     = 32'h0000_0100;
  localparam logic [31:0] DMA_DST     = 32'h0000_0200;
  localparam logic [31:0] RUN_FLAG    = 32'h0000_0FFC;
  localparam logic [31:0] DB_SRC      = 32'h0000_0F00;  // BAR0 offsets of the DMA doorbell (PROGRAMS: Boundary)
  localparam logic [31:0] DB_DST      = 32'h0000_0F04;
  localparam logic [31:0] DB_LEN      = 32'h0000_0F08;
  localparam logic [31:0] DB_RING     = 32'h0000_0F0C;
  localparam logic [31:0] NO_ANSWER   = 32'h0000_0F10;
  localparam logic [31:0] ERR_MASK    = 32'h0000_1FFF;  // error record bits 12:0

  // SELFTEST (PROGRAMS: Self-test): its timeouts, hold and loop limit,
  // the host-memory offsets whose writes and reads the rc_seq testbench's
  // stand-in for rc_tl watches, and the MSI it sends on SELF_KICK_MSI.
  localparam logic [31:0] SELF_WAIT      = 32'd8;
  localparam logic [15:0] SELF_HOLD      = 16'd3;
  localparam logic [15:0] SELF_LIMIT     = 16'd3;
  localparam logic [31:0] SELF_SCRATCH   = 32'h0000_0040;
  localparam logic [31:0] SELF_POLL_A    = 32'h0000_00D0;
  localparam logic [31:0] SELF_POLL_B    = 32'h0000_00D4;
  localparam logic [31:0] SELF_POLL_C    = 32'h0000_00D8;
  localparam logic [31:0] SELF_GUARD     = 32'h0000_00A0;  // seven guards, 4 bytes apart
  localparam logic [31:0] SELF_KICK_LINK = 32'h0000_00F0;
  localparam logic [31:0] SELF_KICK_MSI  = 32'h0000_00F4;
  localparam logic [31:0] SELF_MSI_KICK  = 32'h0155_BEEF;  // the MSI: data this DW, offset [25:16]
  localparam logic [15:0] SELF_FAR       = 16'h0123;       // a jump target at or above ROM_STEPS

  // Program ids (MAS: Programs).
  localparam int unsigned PROG_ENUM     = 0;
  localparam int unsigned PROG_MMIO     = 1;
  localparam int unsigned PROG_DMA_MSI  = 2;
  localparam int unsigned PROG_ERRORS   = 3;
  localparam int unsigned PROG_SELFTEST = 4;

  // ---------------------------------------------------- step constructors

  // REQ with tl-seq op tl_op and every field given.
  function automatic t_step step_req(input logic [3:0] tl_op, input logic [31:0] a,
                                     input logic [5:0] n, input logic [3:0] fbe,
                                     input logic [3:0] lbe, input logic [31:0] d,
                                     input logic check, input common_pkg::t_rc_status want);
    t_step s;
    s          = '0;
    s.op       = {1'b1, tl_op};
    s.addr     = a;
    s.n_dw     = n;
    s.first_be = fbe;
    s.last_be  = lbe;
    s.data     = d;
    s.chk      = check;
    s.status   = want;
    return s;
  endfunction

  // A 1-DW request checking OK (PROGRAMS: Defaults).
  function automatic t_step step_rd(input common_pkg::t_rc_op tl_op, input logic [31:0] a);
    return step_req(tl_op, a, 6'd1, 4'hF, 4'h0, 32'd0, 1'b1, common_pkg::RC_ST_OK);
  endfunction

  // A 1-DW request checking status want.
  function automatic t_step step_rd_st(input common_pkg::t_rc_op tl_op, input logic [31:0] a,
                                       input common_pkg::t_rc_status want);
    return step_req(tl_op, a, 6'd1, 4'hF, 4'h0, 32'd0, 1'b1, want);
  endfunction

  // A 1-DW request with no status check.
  function automatic t_step step_rd_any(input common_pkg::t_rc_op tl_op, input logic [31:0] a);
    return step_req(tl_op, a, 6'd1, 4'hF, 4'h0, 32'd0, 1'b0, common_pkg::RC_ST_OK);
  endfunction

  // A 1-DW write of the enabled bytes, checking OK.
  function automatic t_step step_wr_be(input common_pkg::t_rc_op tl_op, input logic [31:0] a,
                                       input logic [31:0] d, input logic [3:0] fbe);
    return step_req(tl_op, a, 6'd1, fbe, 4'h0, d, 1'b1, common_pkg::RC_ST_OK);
  endfunction

  function automatic t_step step_wr(input common_pkg::t_rc_op tl_op, input logic [31:0] a,
                                    input logic [31:0] d);
    return step_wr_be(tl_op, a, d, 4'hF);
  endfunction

  // MEM_RD of n DWs checking OK; last_be 0xF above 1 DW.
  function automatic t_step step_mem_rd(input logic [31:0] a, input logic [5:0] n);
    logic [3:0] lbe;
    lbe = (n > 6'd1) ? 4'hF : 4'h0;
    return step_req(common_pkg::RC_OP_MEM_RD, a, n, 4'hF, lbe, 32'd0, 1'b1,
                    common_pkg::RC_ST_OK);
  endfunction

  // EXPECT (R[i] ^ value) & m == 0.
  function automatic t_step step_expect(input logic [4:0] i, input logic [31:0] value,
                                        input logic [31:0] m);
    t_step s;
    s      = '0;
    s.op   = OP_EXPECT;
    s.idx  = i;
    s.data = value & m;
    s.mask = m;
    return s;
  endfunction

  function automatic t_step step_wait_msi(input logic [31:0] timeout);
    t_step s;
    s      = '0;
    s.op   = OP_WAIT_MSI;
    s.data = timeout;
    return s;
  endfunction

  function automatic t_step step_wait_link(input logic [31:0] timeout);
    t_step s;
    s      = '0;
    s.op   = OP_WAIT_LINK;
    s.data = timeout;
    return s;
  endfunction

  function automatic t_step step_hot_reset(input logic [31:0] timeout, input logic [15:0] hold);
    t_step s;
    s      = '0;
    s.op   = OP_HOT_RESET;
    s.data = timeout;
    s.addr = {16'd0, hold};
    return s;
  endfunction

  // JUMP with every field given; c is the raw condition code.
  function automatic t_step step_jump(input logic [2:0] c, input logic [15:0] target,
                                      input logic [15:0] limit, input logic [4:0] i,
                                      input logic [31:0] value, input logic [31:0] m,
                                      input common_pkg::t_rc_status want);
    t_step s;
    s        = '0;
    s.op     = OP_JUMP;
    s.cond   = c;
    s.addr   = {limit, target};
    s.idx    = i;
    s.data   = value & m;
    s.mask   = m;
    s.status = want;
    return s;
  endfunction

  function automatic t_step step_jump_always(input logic [15:0] target);
    return step_jump(COND_ALWAYS, target, 16'd0, 5'd0, 32'd0, 32'd0, common_pkg::RC_ST_OK);
  endfunction

  // DATA_EQ or DATA_NE on R[i].
  function automatic t_step step_jump_data(input t_cond c, input logic [15:0] target,
                                           input logic [15:0] limit, input logic [4:0] i,
                                           input logic [31:0] value, input logic [31:0] m);
    return step_jump(c, target, limit, i, value, m, common_pkg::RC_ST_OK);
  endfunction

  // STATUS_EQ or STATUS_NE on S.
  function automatic t_step step_jump_status(input t_cond c, input logic [15:0] target,
                                             input logic [15:0] limit,
                                             input common_pkg::t_rc_status want);
    return step_jump(c, target, limit, 5'd0, 32'd0, 32'd0, want);
  endfunction

  function automatic t_step step_done();
    t_step s;
    s    = '0;
    s.op = OP_DONE;
    return s;
  endfunction

  // FAIL is the all-zero word, as the padding.
  function automatic t_step step_fail();
    return '0;
  endfunction

  // A word with only its op set: the reserved ops.
  function automatic t_step step_op(input logic [4:0] o);
    t_step s;
    s    = '0;
    s.op = o;
    return s;
  endfunction

  // CFG_* address: bus [27:20], device [19:15], function [14:12], DW
  // index [11:2] from the byte offset reg.
  function automatic logic [31:0] cfg_addr(input logic [7:0] bus, input logic [4:0] dev,
                                           input logic [2:0] fn, input logic [11:0] reg_off);
    return {4'd0, bus, dev, fn, reg_off & 12'hFFC};
  endfunction

  // The endpoint's configuration register at byte offset reg_off.
  function automatic logic [31:0] ep(input logic [11:0] reg_off);
    return cfg_addr(BUS_SEC, 5'd0, 3'd0, reg_off);
  endfunction

  // Data patterns: one distinct DW per index.
  function automatic logic [31:0] mmio_pattern(input logic [5:0] k);
    return 32'hA500_0000 | ({26'd0, k} * 32'h0001_0203);
  endfunction

  function automatic logic [31:0] dma_pattern(input logic [5:0] k);
    return 32'hD000_0000 | ({26'd0, k} * 32'h0101_0101);
  endfunction

  function automatic logic [31:0] st_pattern(input logic [5:0] k);
    return 32'h5E00_0000 | ({26'd0, k} * 32'h0002_0401);
  endfunction

  // --------------------------------------------------------------- builder

  // No step yet: every index FAIL. Built word by word: one '0 of the
  // whole image is a replication above Verilator's limit.
  function automatic t_prog prog_empty();
    t_prog p;
    for (int i = 0; i < int'(ROM_STEPS); i++) begin
      p.rom[i] = '0;
    end
    p.n = 16'd0;
    return p;
  endfunction

  localparam t_prog PROG_EMPTY = prog_empty();

  // Append one step. An image past ROM_STEPS keeps counting n, which the
  // ROM's parameter check rejects.
  function automatic t_prog put(input t_prog p, input t_step s);
    t_prog q;
    q = p;
    if (q.n < 16'(ROM_STEPS)) begin
      q.rom[q.n[ROM_AW-1:0]] = s;
    end
    q.n = q.n + 16'd1;
    return q;
  endfunction

  // ENUM, E1 to E10 (PROGRAMS: Enumeration).
  function automatic t_prog sec_enum(input t_prog p0);
    t_prog       p;
    logic [31:0] bar_size;
    p = p0;
    // E1: buses, window, Memory Space and Bus Master Enable of the root port
    p = put(p, step_wr_be(common_pkg::RC_OP_HDR_WR, 32'h18, BUSES, 4'h7));
    p = put(p, step_wr(common_pkg::RC_OP_HDR_WR, 32'h20, WINDOW));
    p = put(p, step_wr_be(common_pkg::RC_OP_HDR_WR, 32'h04, 32'h0000_0006, 4'h1));
    // E2: the header read back
    p = put(p, step_rd(common_pkg::RC_OP_HDR_RD, 32'h00));
    p = put(p, step_expect(5'd0, {RC_DEVICE_ID, RC_VENDOR_ID}, FULL));
    p = put(p, step_rd(common_pkg::RC_OP_HDR_RD, 32'h04));
    p = put(p, step_expect(5'd0, 32'h0000_0006, FULL));
    p = put(p, step_rd(common_pkg::RC_OP_HDR_RD, 32'h08));
    p = put(p, step_expect(5'd0, {24'h060400, RC_REVISION_ID}, FULL));
    p = put(p, step_rd(common_pkg::RC_OP_HDR_RD, 32'h18));
    p = put(p, step_expect(5'd0, BUSES, FULL));
    p = put(p, step_rd(common_pkg::RC_OP_HDR_RD, 32'h20));
    p = put(p, step_expect(5'd0, WINDOW, FULL));
    // E3: DL_Active
    p = put(p, step_wait_link(LINK_TIMEOUT));
    // E4: IDs, Header Type, Capabilities List
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h000)));
    p = put(p, step_expect(5'd0, {EP_DEVICE_ID, EP_VENDOR_ID}, FULL));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h00C)));
    p = put(p, step_expect(5'd0, 32'h0000_0000, 32'h007F_0000));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h004)));
    p = put(p, step_expect(5'd0, 32'h0010_0000, 32'h0010_0000));
    // E5: capability pointer
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h034)));
    p = put(p, step_expect(5'd0, 32'h0000_0040, 32'h0000_00FF));
    // E6: the capability walk PM, MSI, MSI-X, PCI Express
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h040)));
    p = put(p, step_expect(5'd0, 32'h0000_5001, 32'h0000_FFFF));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h050)));
    p = put(p, step_expect(5'd0, 32'h0000_6005, 32'h0000_FFFF));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h060)));
    p = put(p, step_expect(5'd0, 32'h0000_7011, 32'h0000_FFFF));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h070)));
    p = put(p, step_expect(5'd0, 32'h0000_0010, 32'h0000_FFFF));
    // E7: BAR sizing, BAR0 to BAR5
    for (int i = 0; i < 6; i++) begin
      bar_size = (i == 0 || i == 2) ? BAR_4K_SIZE : 32'd0;
      p = put(p, step_wr(common_pkg::RC_OP_CFG_WR, ep(12'(32'h10 + 4 * i)), FULL));
      p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'(32'h10 + 4 * i))));
      p = put(p, step_expect(5'd0, bar_size, FULL));
    end
    // E8: BAR assignment
    p = put(p, step_wr(common_pkg::RC_OP_CFG_WR, ep(12'h010), BAR0));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h010)));
    p = put(p, step_expect(5'd0, BAR0, FULL));
    p = put(p, step_wr(common_pkg::RC_OP_CFG_WR, ep(12'h018), BAR2));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h018)));
    p = put(p, step_expect(5'd0, BAR2, FULL));
    // E9: MSI address, data and enable
    p = put(p, step_wr(common_pkg::RC_OP_CFG_WR, ep(12'h054), MSI_ADDR));
    p = put(p, step_wr(common_pkg::RC_OP_CFG_WR, ep(12'h058), 32'd0));
    p = put(p, step_wr(common_pkg::RC_OP_CFG_WR, ep(12'h05C), MSI_DATA));
    p = put(p, step_wr_be(common_pkg::RC_OP_CFG_WR, ep(12'h050), 32'h0001_0000, 4'h4));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h050)));
    p = put(p, step_expect(5'd0, 32'h0001_0000, 32'h0001_0000));
    // E10: Memory Space and Bus Master Enable of the endpoint
    p = put(p, step_wr_be(common_pkg::RC_OP_CFG_WR, ep(12'h004), 32'h0000_0006, 4'h1));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h004)));
    p = put(p, step_expect(5'd0, 32'h0000_0006, 32'h0000_0006));
    return p;
  endfunction

  // MMIO, M1 to M5 (PROGRAMS: MMIO).
  function automatic t_prog sec_mmio(input t_prog p0);
    t_prog p;
    p = p0;
    for (int k = 0; k < 32; k++) begin
      p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + 32'(4 * k), mmio_pattern(6'(k))));
    end
    p = put(p, step_mem_rd(BAR0, 6'd32));
    for (int k = 0; k < 32; k++) begin
      p = put(p, step_expect(5'(k), mmio_pattern(6'(k)), FULL));
    end
    p = put(p, step_mem_rd(BAR0 + 32'h38, 6'd4));
    for (int k = 0; k < 4; k++) begin
      p = put(p, step_expect(5'(k), mmio_pattern(6'(14 + k)), FULL));
    end
    p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + 32'h80, 32'h1234_5678));
    p = put(p, step_wr_be(common_pkg::RC_OP_MEM_WR, BAR0 + 32'h80, FULL, 4'h3));
    p = put(p, step_rd(common_pkg::RC_OP_MEM_RD, BAR0 + 32'h80));
    p = put(p, step_expect(5'd0, 32'h1234_FFFF, FULL));
    p = put(p, step_rd(common_pkg::RC_OP_ERR_RD, 32'd0));
    p = put(p, step_expect(5'd0, 32'd0, ERR_MASK));
    return p;
  endfunction

  // The DMA doorbell: source, destination, length in bytes, then rings
  // writes of 1 to the ring register.
  function automatic t_prog doorbell(input t_prog p0, input logic [31:0] src,
                                     input logic [31:0] dst, input logic [31:0] nbytes,
                                     input int unsigned rings);
    t_prog p;
    p = p0;
    p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + DB_SRC, src));
    p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + DB_DST, dst));
    p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + DB_LEN, nbytes));
    for (int r = 0; r < 8; r++) begin
      if (r < int'(rings)) begin
        p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + DB_RING, 32'd1));
      end
    end
    return p;
  endfunction

  // DMA and MSI, D1 to D5 (PROGRAMS: DMA and MSI), 64 bytes.
  function automatic t_prog sec_dma(input t_prog p0);
    t_prog p;
    p = p0;
    for (int k = 0; k < 16; k++) begin
      p = put(p, step_wr(common_pkg::RC_OP_HOST_WR, DMA_SRC + 32'(4 * k), dma_pattern(6'(k))));
    end
    for (int k = 0; k < 16; k++) begin
      p = put(p, step_wr(common_pkg::RC_OP_HOST_WR, DMA_DST + 32'(4 * k), 32'd0));
    end
    p = put(p, step_wr(common_pkg::RC_OP_ERR_CLR, 32'd0, ERR_MASK));
    p = doorbell(p, DMA_SRC, DMA_DST, 32'd64, 1);
    p = put(p, step_wait_msi(MSI_TIMEOUT));
    p = put(p, step_expect(5'd0, MSI_DATA, FULL));
    p = put(p, step_expect(5'd1, 32'd0, FULL));
    for (int k = 0; k < 16; k++) begin
      p = put(p, step_rd(common_pkg::RC_OP_HOST_RD, DMA_DST + 32'(4 * k)));
      p = put(p, step_expect(5'd0, dma_pattern(6'(k)), FULL));
    end
    p = put(p, step_rd(common_pkg::RC_OP_ERR_RD, 32'd0));
    p = put(p, step_expect(5'd0, 32'd0, ERR_MASK));
    return p;
  endfunction

  // An error-record poll: ERR_RD, then a backward DATA_NE jump to it until
  // the bits are all set.
  function automatic t_prog poll_err(input t_prog p0, input logic [31:0] bits);
    t_prog       p;
    logic [15:0] at;
    p  = p0;
    at = p.n;
    p  = put(p, step_rd(common_pkg::RC_OP_ERR_RD, 32'd0));
    p  = put(p, step_jump_data(COND_DATA_NE, at, POLL_LIMIT, 5'd0, bits, bits));
    return p;
  endfunction

  function automatic t_prog prog_enum();
    t_prog p;
    p = sec_enum(PROG_EMPTY);
    p = put(p, step_done());
    return p;
  endfunction

  function automatic t_prog prog_mmio();
    t_prog p;
    p = sec_enum(PROG_EMPTY);
    p = sec_mmio(p);
    p = put(p, step_done());
    return p;
  endfunction

  function automatic t_prog prog_dma_msi();
    t_prog p;
    p = sec_enum(PROG_EMPTY);
    p = sec_dma(p);
    p = put(p, step_done());
    return p;
  endfunction

  // ERRORS and the indices of its forward targets R11 and R14, which a
  // first build finds and a second one uses.
  typedef struct packed {
    t_prog       p;
    logic [15:0] r11;
    logic [15:0] r14;
  } t_err_build;

  // ERRORS, R1 to R14 (PROGRAMS: Errors and recovery).
  function automatic t_err_build prog_errors(input logic [15:0] r11_at, input logic [15:0] r14_at);
    t_err_build  b;
    t_prog       p;
    logic [15:0] enum_at;
    logic [15:0] at;
    // R1
    p       = put(PROG_EMPTY, step_wr(common_pkg::RC_OP_HOST_WR, RUN_FLAG, 32'd0));
    enum_at = p.n;
    p       = sec_enum(p);
    // R2: pass selection
    p = put(p, step_rd(common_pkg::RC_OP_HOST_RD, RUN_FLAG));
    p = put(p, step_jump_data(COND_DATA_EQ, r11_at, 16'd0, 5'd0, 32'd1, FULL));
    p = put(p, step_jump_data(COND_DATA_EQ, r14_at, 16'd0, 5'd0, 32'd2, FULL));
    // R3: error reporting enables, record clear
    p = put(p, step_wr_be(common_pkg::RC_OP_CFG_WR, ep(12'h078), 32'h0000_001F, 4'h1));
    p = put(p, step_wr(common_pkg::RC_OP_ERR_CLR, 32'd0, ERR_MASK));
    // R4: a posted and a non-posted Unsupported Request at the endpoint
    p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, NOT_DECODED, 32'h5A5A_5A5A));
    p = put(p, step_rd_st(common_pkg::RC_OP_MEM_RD, NOT_DECODED, common_pkg::RC_ST_UR));
    // R5: bits 0, 7 and 8 and no other
    p = poll_err(p, 32'h0000_0181);
    p = put(p, step_expect(5'd0, 32'h0000_0181, ERR_MASK));
    p = put(p, step_wr(common_pkg::RC_OP_ERR_CLR, 32'd0, ERR_MASK));
    // R6: rc_tl's outbound refusals
    p = put(p, step_rd_st(common_pkg::RC_OP_MEM_RD, OUTSIDE, common_pkg::RC_ST_LOCAL_UR));
    p = put(p, step_rd_st(common_pkg::RC_OP_CFG_RD, cfg_addr(BUS_SEC, 5'd1, 3'd0, 12'h000),
                          common_pkg::RC_ST_LOCAL_UR));
    p = put(p, step_rd_st(common_pkg::RC_OP_CFG_RD, cfg_addr(8'd2, 5'd0, 3'd0, 12'h000),
                          common_pkg::RC_ST_LOCAL_UR));
    // R7: bit 11, the endpoint's read outside host memory
    p = doorbell(p, OUTSIDE, DMA_DST, 32'd4, 1);
    p = poll_err(p, 32'h0000_0800);
    // R8: bit 12, the MSI past a full capture, then the D kept
    p = doorbell(p, DMA_SRC, DMA_DST, 32'd4, MSI_DEPTH + 1);
    p = poll_err(p, 32'h0000_1000);
    for (int i = 0; i < 8; i++) begin
      if (i < int'(MSI_DEPTH)) begin
        p = put(p, step_wait_msi(MSI_TIMEOUT));
      end
    end
    // R9: bit 3, completion timeout; the record clears
    p = put(p, step_rd_st(common_pkg::RC_OP_MEM_RD, BAR0 + NO_ANSWER, common_pkg::RC_ST_TIMEOUT));
    p = put(p, step_rd(common_pkg::RC_OP_ERR_RD, 32'd0));
    p = put(p, step_expect(5'd0, 32'h0000_1808, ERR_MASK));
    p = put(p, step_wr(common_pkg::RC_OP_ERR_CLR, 32'd0, ERR_MASK));
    p = put(p, step_rd(common_pkg::RC_OP_ERR_RD, 32'd0));
    p = put(p, step_expect(5'd0, 32'd0, ERR_MASK));
    // R10: Hot Reset, link-up, second enumeration
    p = put(p, step_wr(common_pkg::RC_OP_HOST_WR, RUN_FLAG, 32'd1));
    p = put(p, step_hot_reset(HR_TIMEOUT, HR_HOLD));
    p = put(p, step_wait_link(LINK_TIMEOUT));
    p = put(p, step_jump_always(enum_at));
    // R11: traffic after the Hot Reset
    b.r11 = p.n;
    p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + 32'h40, 32'h0BAD_F00D));
    p = put(p, step_rd(common_pkg::RC_OP_MEM_RD, BAR0 + 32'h40));
    p = put(p, step_expect(5'd0, 32'h0BAD_F00D, FULL));
    p = put(p, step_wr(common_pkg::RC_OP_HOST_WR, RUN_FLAG, 32'd2));
    // R12: poll BAR0 until the link goes down under traffic
    at = p.n;
    p  = put(p, step_rd_any(common_pkg::RC_OP_MEM_RD, BAR0));
    p  = put(p, step_jump_status(COND_STATUS_EQ, at, POLL_LIMIT, common_pkg::RC_ST_OK));
    p  = put(p, step_jump_status(COND_STATUS_EQ, p.n + 16'd2, 16'd0, common_pkg::RC_ST_LINK_DOWN));
    p  = put(p, step_fail());
    // R13: link-up, third enumeration
    p = put(p, step_wait_link(LINK_TIMEOUT));
    p = put(p, step_jump_always(enum_at));
    // R14: traffic after the link down
    b.r14 = p.n;
    p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + 32'h44, 32'h600D_CAFE));
    p = put(p, step_rd(common_pkg::RC_OP_MEM_RD, BAR0 + 32'h44));
    p = put(p, step_expect(5'd0, 32'h600D_CAFE, FULL));
    p = put(p, step_done());
    b.p = p;
    return b;
  endfunction

  // A trap guarded by a host-memory DW: HOST_RD it, skip the trap when it
  // reads 0. The testbench makes the guard nonzero to run the trap.
  function automatic t_prog guarded(input t_prog p0, input logic [31:0] guard, input t_step trap);
    t_prog p;
    p = p0;
    p = put(p, step_rd(common_pkg::RC_OP_HOST_RD, guard));
    p = put(p, step_jump_data(COND_DATA_EQ, p.n + 16'd2, 16'd0, 5'd0, 32'd0, FULL));
    p = put(p, trap);
    return p;
  endfunction

  // SELFTEST (PROGRAMS: Self-test). pad is the program's length, the first
  // FAIL padding index, which a first build finds and a second one uses.
  function automatic t_prog prog_selftest(input logic [15:0] pad);
    t_prog       p;
    logic [15:0] at;
    // S1: link-up
    p = put(PROG_EMPTY, step_wr(common_pkg::RC_OP_HOST_WR, SELF_KICK_LINK, 32'd0));
    p = put(p, step_wait_link(SELF_WAIT));
    // S2: header write and read
    p = put(p, step_wr_be(common_pkg::RC_OP_HDR_WR, 32'h18, BUSES, 4'h7));
    p = put(p, step_wr(common_pkg::RC_OP_HDR_WR, 32'h20, WINDOW));
    p = put(p, step_wr_be(common_pkg::RC_OP_HDR_WR, 32'h04, 32'h0000_0006, 4'h1));
    p = put(p, step_rd(common_pkg::RC_OP_HDR_RD, 32'h18));
    p = put(p, step_expect(5'd0, BUSES, FULL));
    // S3: configuration write and read, a masked expect
    p = put(p, step_wr(common_pkg::RC_OP_CFG_WR, ep(12'h010), FULL));
    p = put(p, step_rd(common_pkg::RC_OP_CFG_RD, ep(12'h010)));
    p = put(p, step_expect(5'd0, BAR_4K_SIZE, 32'hFFFF_FFF0));
    p = put(p, step_wr(common_pkg::RC_OP_CFG_WR, ep(12'h010), BAR0));
    p = put(p, step_wr_be(common_pkg::RC_OP_CFG_WR, ep(12'h004), 32'h0000_0006, 4'h1));
    // S4: 32 memory writes, a 32-DW read, 32 expects on R[0] to R[31]
    for (int k = 0; k < 32; k++) begin
      p = put(p, step_wr(common_pkg::RC_OP_MEM_WR, BAR0 + 32'(4 * k), st_pattern(6'(k))));
    end
    p = put(p, step_mem_rd(BAR0, 6'd32));
    for (int k = 0; k < 32; k++) begin
      p = put(p, step_expect(5'(k), st_pattern(6'(k)), FULL));
    end
    // S5: host memory and the error record
    p = put(p, step_wr(common_pkg::RC_OP_HOST_WR, SELF_SCRATCH, 32'hCAFE_F00D));
    p = put(p, step_rd(common_pkg::RC_OP_HOST_RD, SELF_SCRATCH));
    p = put(p, step_expect(5'd0, 32'hCAFE_F00D, FULL));
    p = put(p, step_wr(common_pkg::RC_OP_ERR_CLR, 32'd0, ERR_MASK));
    p = put(p, step_rd(common_pkg::RC_OP_ERR_RD, 32'd0));
    p = put(p, step_expect(5'd0, 32'd0, ERR_MASK));
    // S6: undefined tl-seq ops and an outbound refusal answer LOCAL_UR
    p = put(p, step_req(4'hA, 32'd0, 6'd1, 4'hF, 4'h0, 32'd0, 1'b1, common_pkg::RC_ST_LOCAL_UR));
    p = put(p, step_req(4'hF, 32'd0, 6'd1, 4'hF, 4'h0, 32'd0, 1'b1, common_pkg::RC_ST_LOCAL_UR));
    p = put(p, step_rd_st(common_pkg::RC_OP_MEM_RD, OUTSIDE, common_pkg::RC_ST_LOCAL_UR));
    // S7: status jumps, S is LOCAL_UR; a taken one skips a FAIL, a jump
    // not taken targets the padding
    p = put(p, step_jump_status(COND_STATUS_EQ, p.n + 16'd2, 16'd0, common_pkg::RC_ST_LOCAL_UR));
    p = put(p, step_fail());
    p = put(p, step_jump_status(COND_STATUS_EQ, pad, 16'd0, common_pkg::RC_ST_OK));
    p = put(p, step_jump_status(COND_STATUS_NE, p.n + 16'd2, 16'd0, common_pkg::RC_ST_OK));
    p = put(p, step_fail());
    p = put(p, step_jump_status(COND_STATUS_NE, pad, 16'd0, common_pkg::RC_ST_LOCAL_UR));
    // S8: data jumps on R[0] = 0xCAFE_F00D and on R[5]
    p = put(p, step_rd(common_pkg::RC_OP_HOST_RD, SELF_SCRATCH));
    p = put(p, step_jump_data(COND_DATA_EQ, p.n + 16'd2, 16'd0, 5'd0, 32'hCAFE_F00D, FULL));
    p = put(p, step_fail());
    p = put(p, step_jump_data(COND_DATA_EQ, pad, 16'd0, 5'd0, 32'hCAFE_F00E, FULL));
    p = put(p, step_jump_data(COND_DATA_NE, p.n + 16'd2, 16'd0, 5'd0, 32'd0, FULL));
    p = put(p, step_fail());
    p = put(p, step_jump_data(COND_DATA_NE, pad, 16'd0, 5'd0, 32'hCAFE_0000, 32'hFFFF_0000));
    p = put(p, step_jump_data(COND_DATA_EQ, p.n + 16'd2, 16'd0, 5'd5, st_pattern(6'd5), FULL));
    p = put(p, step_fail());
    p = put(p, step_jump_always(p.n + 16'd2));
    p = put(p, step_fail());
    // S9: guarded traps
    p = guarded(p, SELF_GUARD + 32'h00, step_fail());
    p = guarded(p, SELF_GUARD + 32'h04, step_op(5'h07));
    p = guarded(p, SELF_GUARD + 32'h08, step_op(5'h0F));
    p = guarded(p, SELF_GUARD + 32'h0C, step_jump(3'd5, 16'd0, 16'd0, 5'd0, 32'd0, 32'd0,
                                                 common_pkg::RC_ST_OK));
    p = guarded(p, SELF_GUARD + 32'h10, step_jump(3'd7, 16'd0, 16'd0, 5'd0, 32'd0, 32'd0,
                                                 common_pkg::RC_ST_OK));
    p = guarded(p, SELF_GUARD + 32'h14, step_jump_always(pad));
    p = guarded(p, SELF_GUARD + 32'h18, step_jump_always(SELF_FAR));
    // S10: polling loops of limit SELF_LIMIT. A: plain. B: a JUMP ALWAYS in
    // the body leaves L. C: a forward conditional jump in the body sets L
    // 0 on every pass, so C may take more than the limit.
    at = p.n;
    p  = put(p, step_rd(common_pkg::RC_OP_HOST_RD, SELF_POLL_A));
    p  = put(p, step_jump_data(COND_DATA_NE, at, SELF_LIMIT, 5'd0, 32'd1, 32'd1));
    at = p.n;
    p  = put(p, step_rd(common_pkg::RC_OP_HOST_RD, SELF_POLL_B));
    p  = put(p, step_jump_always(p.n + 16'd1));
    p  = put(p, step_jump_data(COND_DATA_NE, at, SELF_LIMIT, 5'd0, 32'd1, 32'd1));
    at = p.n;
    p  = put(p, step_rd(common_pkg::RC_OP_HOST_RD, SELF_POLL_C));
    p  = put(p, step_jump_data(COND_DATA_EQ, p.n + 16'd1, 16'd0, 5'd0, 32'd4, 32'd4));
    p  = put(p, step_jump_data(COND_DATA_NE, at, SELF_LIMIT, 5'd0, 32'd1, 32'd1));
    // S11: an MSI and its capture
    p = put(p, step_wr(common_pkg::RC_OP_HOST_WR, SELF_KICK_MSI, SELF_MSI_KICK));
    p = put(p, step_wait_msi(SELF_WAIT));
    p = put(p, step_expect(5'd0, SELF_MSI_KICK, FULL));
    p = put(p, step_expect(5'd1, {22'd0, SELF_MSI_KICK[25:16]}, FULL));
    // S12: Hot Reset, then one with no hold while link_up is already 0;
    // endpoint requests finish LINK_DOWN, local ones OK
    p = put(p, step_hot_reset(SELF_WAIT, SELF_HOLD));
    p = put(p, step_hot_reset(SELF_WAIT, 16'd0));
    p = put(p, step_rd_st(common_pkg::RC_OP_MEM_RD, BAR0, common_pkg::RC_ST_LINK_DOWN));
    p = put(p, step_rd_any(common_pkg::RC_OP_CFG_RD, ep(12'h000)));
    p = put(p, step_jump_status(COND_STATUS_EQ, p.n + 16'd2, 16'd0, common_pkg::RC_ST_LINK_DOWN));
    p = put(p, step_fail());
    p = put(p, step_rd(common_pkg::RC_OP_HDR_RD, 32'h20));
    p = put(p, step_expect(5'd0, WINDOW, FULL));
    p = put(p, step_done());
    return p;
  endfunction

  // ------------------------------------------------------------- programs

  localparam t_prog      PRG_ENUM     = prog_enum();
  localparam t_prog      PRG_MMIO     = prog_mmio();
  localparam t_prog      PRG_DMA_MSI  = prog_dma_msi();
  localparam t_err_build ERR_PASS1    = prog_errors(16'd0, 16'd0);
  localparam t_err_build ERR_PASS2    = prog_errors(ERR_PASS1.r11, ERR_PASS1.r14);
  localparam t_prog      PRG_ERRORS   = ERR_PASS2.p;
  localparam t_prog      SELF_PASS1     = prog_selftest(16'd0);
  localparam t_prog      PRG_SELFTEST = prog_selftest(SELF_PASS1.n);

  // The program with id prog: its image and its length in steps; an id
  // with no program has length 0.
  function automatic t_prog program_of(input int unsigned prog);
    case (prog)
      PROG_ENUM:     return PRG_ENUM;
      PROG_MMIO:     return PRG_MMIO;
      PROG_DMA_MSI:  return PRG_DMA_MSI;
      PROG_ERRORS:   return PRG_ERRORS;
      PROG_SELFTEST: return PRG_SELFTEST;
      default:       return PROG_EMPTY;
    endcase
  endfunction

endpackage

`default_nettype wire
