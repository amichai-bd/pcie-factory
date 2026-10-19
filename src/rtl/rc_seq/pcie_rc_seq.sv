// Command-ROM test sequencer of pcie_rc: runs one program of steps that
// drive pcie_rc_tl over the tl-seq interface and check its results.
// Interfaces, behavior, step format and timing: wiki/src/rtl/rc_seq/MAS.md;
// the programs: PROGRAMS.md; the interface: wiki/src/arch/rc/TL_SEQ.md.
`default_nettype none

`include "common_macros.svh"

module pcie_rc_seq #(
  parameter int unsigned PROGRAM = 1     // program id (MAS: Programs); 1 is MMIO
) (
  input  logic                clk_i,             // core clock, 125 MHz
  input  logic                rst_i,             // synchronous to clk_i, active high

  // Run control and result (rc HAS: Sequencer run)
  input  logic                start_i,           // a rising edge starts a run when none is in progress
  output logic                done_o,            // the last run has ended
  output logic                pass_o,            // with done_o: the run passed
  output logic [15:0]         fail_step_o,       // with done_o and not pass_o: ROM index of the failing step

  // Link status and control
  input  logic                dl_up_i,           // link dl_up_o: DL_Active
  input  logic                link_up_i,         // link link_up_o
  output logic                hot_reset_req_o,   // to link hot_reset_req_i

  // tl-seq interface with pcie_rc_tl (TL_SEQ.md)
  output logic                cmd_valid_o,
  input  logic                cmd_ready_i,
  output common_pkg::t_rc_cmd cmd_o,
  input  logic                rsp_valid_i,       // one beat per pulse, always taken
  input  common_pkg::t_rc_rsp rsp_i,
  input  logic                msi_valid_i,
  output logic                msi_ready_o,
  input  common_pkg::t_rc_msi msi_i
);

  localparam pcie_rc_seq_pkg::t_prog PRG = pcie_rc_seq_pkg::program_of(PROGRAM);

  `COMMON_ASSERT_PARAM(g_program_check, PRG.n != 16'd0, "pcie_rc_seq: PROGRAM names no program")

  // ---------------------------------------------------------------- state

  pcie_rc_seq_pkg::t_ctl    ctl_q, ctl_d;
  pcie_rc_seq_pkg::t_result res_q, res_d;
  logic                     start_prev_q;      // start_i of the previous cycle, 0 in reset
  common_pkg::t_rc_msi      msi_q;             // the capture taken, until R[0] and R[1] are written

  `COMMON_DFF_RST_VAL(ctl_q, ctl_d, clk_i, rst_i, pcie_rc_seq_pkg::CTL_RESET)
  `COMMON_DFF_RST(res_q, res_d, clk_i, rst_i)
  `COMMON_DFF_RST(start_prev_q, start_i, clk_i, rst_i)

  // --------------------------------------------------------------- the ROM

  logic [15:0]            rom_pc;
  pcie_rc_seq_pkg::t_step word;                // the step at pc, from FETCH's edge on

  assign rom_pc = ctl_q.pc;

  pcie_rc_seq_rom #(
    .PROGRAM (PROGRAM)
  ) u_rom (
    .clk_i  (clk_i),
    .pc_i   (rom_pc),
    .word_o (word)
  );

  // Bit 127 is reserved and ignored.
  `COMMON_UNUSED_BITS(unused_rsvd, {word.rsvd})

  // ------------------------------------------------------ result registers

  logic        ram_we;
  logic [4:0]  ram_waddr;
  logic [31:0] ram_wdata;
  logic        ram_re;
  logic [4:0]  ram_raddr;
  logic [31:0] ram_rdata;                      // R[idx], in READ
  logic        ram_wlane;

  assign ram_raddr = word.idx;
  assign ram_wlane = 1'b1;

  common_sdp_ram #(
    .WIDTH  (32),
    .ADDR_W (5),
    .LANES  (1)
  ) u_regs (
    .clk_i      (clk_i),
    .wr_en_i    (ram_we),
    .wr_lanes_i (ram_wlane),
    .wr_addr_i  (ram_waddr),
    .wr_data_i  (ram_wdata),
    .rd_en_i    (ram_re),
    .rd_addr_i  (ram_raddr),
    .rd_data_o  (ram_rdata)
  );

  // ---------------------------------------------------------------- decode

  logic        is_req;
  logic [15:0] jump_target;
  logic [15:0] jump_limit;
  logic        data_eq;          // (R[idx] ^ data) & mask is 0; valid in READ
  logic        status_eq;        // S equals the step's status
  logic        cond_data;        // DATA_EQ or DATA_NE
  logic        cond_bad;         // a condition code 5 to 7
  logic        jump_taken;       // the condition holds (data conditions in READ)
  logic        wait_cond;        // the condition the WAIT state waits for
  logic        wait_msi;
  logic [31:0] hold_cycles;
  logic        beat_now;         // a result beat of the current REQ this cycle
  logic        start_rise;

  assign is_req      = word.op[4];
  assign jump_target = word.addr[15:0];
  assign jump_limit  = word.addr[31:16];
  assign data_eq     = ((ram_rdata ^ word.data) & word.mask) == 32'd0;
  assign status_eq   = ctl_q.st == word.status;
  assign cond_data   = (word.cond == pcie_rc_seq_pkg::COND_DATA_EQ) ||
                       (word.cond == pcie_rc_seq_pkg::COND_DATA_NE);
  assign cond_bad    = word.cond > pcie_rc_seq_pkg::COND_STATUS_NE;
  assign wait_msi    = word.op == pcie_rc_seq_pkg::OP_WAIT_MSI;
  assign wait_cond   = wait_msi ? msi_valid_i : dl_up_i;
  assign hold_cycles = {16'd0, word.addr[15:0]};
  assign start_rise  = start_i && !start_prev_q;

  always_comb begin
    jump_taken = 1'b0;
    case (word.cond)
      pcie_rc_seq_pkg::COND_ALWAYS:    jump_taken = 1'b1;
      pcie_rc_seq_pkg::COND_DATA_EQ:   jump_taken = data_eq;
      pcie_rc_seq_pkg::COND_DATA_NE:   jump_taken = !data_eq;
      pcie_rc_seq_pkg::COND_STATUS_EQ: jump_taken = status_eq;
      pcie_rc_seq_pkg::COND_STATUS_NE: jump_taken = !status_eq;
      default:                         jump_taken = 1'b0;
    endcase
  end

  assign beat_now = rsp_valid_i &&
                    ((ctl_q.state == pcie_rc_seq_pkg::ST_REQ_WAIT) ||
                     ((ctl_q.state == pcie_rc_seq_pkg::ST_REQ_OFFER) && cmd_ready_i));

  // ---------------------------------------------------------- next state
  //
  // A step ends in one of three ways: step_next (pc + 1), step_jump (pc =
  // jump_target) or step_fail; DONE ends the run passing.

  logic step_next;
  logic step_jump;
  logic step_fail;
  logic run_pass;
  logic msi_take;

  always_comb begin
    ctl_d     = ctl_q;
    res_d     = res_q;
    ram_we    = 1'b0;
    ram_waddr = 5'd0;
    ram_wdata = 32'd0;
    ram_re    = 1'b0;
    step_next = 1'b0;
    step_jump = 1'b0;
    step_fail = 1'b0;
    run_pass  = 1'b0;
    msi_take  = 1'b0;

    case (ctl_q.state)
      pcie_rc_seq_pkg::ST_IDLE: begin
        if (start_rise) begin
          ctl_d = pcie_rc_seq_pkg::ctl_run_start();
          res_d = '0;
        end
      end

      pcie_rc_seq_pkg::ST_FETCH: begin
        ctl_d.state = pcie_rc_seq_pkg::ST_EXEC;
      end

      pcie_rc_seq_pkg::ST_EXEC: begin
        ram_re     = 1'b1;
        ctl_d.cnt  = 32'd0;
        ctl_d.beat = 6'd0;
        if (is_req) begin
          ctl_d.state = pcie_rc_seq_pkg::ST_REQ_OFFER;
        end else begin
          case (word.op)
            pcie_rc_seq_pkg::OP_EXPECT:    ctl_d.state = pcie_rc_seq_pkg::ST_READ;
            pcie_rc_seq_pkg::OP_WAIT_MSI,
            pcie_rc_seq_pkg::OP_WAIT_LINK: ctl_d.state = pcie_rc_seq_pkg::ST_WAIT;
            pcie_rc_seq_pkg::OP_HOT_RESET: ctl_d.state = pcie_rc_seq_pkg::ST_HR_FALL;
            pcie_rc_seq_pkg::OP_DONE:      run_pass    = 1'b1;
            pcie_rc_seq_pkg::OP_JUMP: begin
              if (cond_bad) begin
                step_fail = 1'b1;
              end else if (cond_data) begin
                ctl_d.state = pcie_rc_seq_pkg::ST_READ;
              end else begin
                step_jump = 1'b1;          // ALWAYS and the status conditions decide now
              end
            end
            default:                       step_fail = 1'b1;   // FAIL and the reserved ops
          endcase
        end
      end

      pcie_rc_seq_pkg::ST_READ: begin
        if (word.op == pcie_rc_seq_pkg::OP_EXPECT) begin
          step_fail = !data_eq;
          step_next = data_eq;
        end else begin
          step_jump = 1'b1;
        end
      end

      pcie_rc_seq_pkg::ST_WAIT: begin
        if (wait_cond) begin
          if (wait_msi) begin
            msi_take    = 1'b1;
            ctl_d.state = pcie_rc_seq_pkg::ST_MSI_R0;
          end else begin
            step_next = 1'b1;
          end
        end else if (ctl_q.cnt == word.data) begin
          step_fail = 1'b1;
        end else begin
          ctl_d.cnt = ctl_q.cnt + 32'd1;
        end
      end

      pcie_rc_seq_pkg::ST_MSI_R0: begin
        ram_we      = 1'b1;
        ram_waddr   = 5'd0;
        ram_wdata   = msi_q.data;
        ctl_d.state = pcie_rc_seq_pkg::ST_MSI_R1;
      end

      pcie_rc_seq_pkg::ST_MSI_R1: begin
        ram_we    = 1'b1;
        ram_waddr = 5'd1;
        ram_wdata = {22'd0, msi_q.offset};
        step_next = 1'b1;
      end

      pcie_rc_seq_pkg::ST_HR_FALL: begin
        if (!link_up_i) begin
          if (hold_cycles == 32'd0) begin
            step_next = 1'b1;
          end else begin
            ctl_d.state = pcie_rc_seq_pkg::ST_HR_HOLD;
            ctl_d.cnt   = 32'd1;
          end
        end else if (ctl_q.cnt == word.data) begin
          step_fail = 1'b1;
        end else begin
          ctl_d.cnt = ctl_q.cnt + 32'd1;
        end
      end

      pcie_rc_seq_pkg::ST_HR_HOLD: begin
        if (ctl_q.cnt == hold_cycles) begin
          step_next = 1'b1;
        end else begin
          ctl_d.cnt = ctl_q.cnt + 32'd1;
        end
      end

      pcie_rc_seq_pkg::ST_REQ_OFFER,
      pcie_rc_seq_pkg::ST_REQ_WAIT: begin
        if (cmd_ready_i) begin
          ctl_d.state = pcie_rc_seq_pkg::ST_REQ_WAIT;
        end
        if (beat_now) begin
          ram_we     = !ctl_q.beat[5];
          ram_waddr  = ctl_q.beat[4:0];
          ram_wdata  = rsp_i.data;
          ctl_d.beat = ctl_q.beat + 6'd1;
          if (rsp_i.last) begin
            ctl_d.st  = rsp_i.status;
            step_fail = word.chk && (rsp_i.status != word.status);
            step_next = !step_fail;
          end
        end
      end

      default: begin
        ctl_d.state = pcie_rc_seq_pkg::ST_IDLE;
      end
    endcase

    // A jump: ALWAYS leaves L; a conditional one taken backward counts a
    // take of the polling loop, failing at the limit; any other
    // conditional outcome sets L 0.
    if (step_jump) begin
      if (word.cond == pcie_rc_seq_pkg::COND_ALWAYS) begin
        ctl_d.pc    = jump_target;
        ctl_d.state = pcie_rc_seq_pkg::ST_FETCH;
      end else if (jump_taken && (jump_target <= ctl_q.pc)) begin
        if (ctl_q.loops == jump_limit) begin
          step_fail = 1'b1;
        end else begin
          ctl_d.loops = ctl_q.loops + 16'd1;
          ctl_d.pc    = jump_target;
          ctl_d.state = pcie_rc_seq_pkg::ST_FETCH;
        end
      end else begin
        ctl_d.loops = 16'd0;
        ctl_d.pc    = jump_taken ? jump_target : ctl_q.pc + 16'd1;
        ctl_d.state = pcie_rc_seq_pkg::ST_FETCH;
      end
    end

    if (step_next) begin
      ctl_d.pc    = ctl_q.pc + 16'd1;
      ctl_d.state = pcie_rc_seq_pkg::ST_FETCH;
    end

    // The run ends: done_o, pass_o and fail_step_o from the next cycle.
    if (step_fail || run_pass) begin
      ctl_d.state     = pcie_rc_seq_pkg::ST_IDLE;
      res_d.done      = 1'b1;
      res_d.pass      = run_pass;
      res_d.fail_step = run_pass ? 16'd0 : ctl_q.pc;
    end
  end

  `COMMON_DFF_EN(msi_q, msi_i, clk_i, msi_take)

  // --------------------------------------------------------------- outputs

  assign done_o          = res_q.done;
  assign pass_o          = res_q.pass;
  assign fail_step_o     = res_q.fail_step;
  assign hot_reset_req_o = (ctl_q.state == pcie_rc_seq_pkg::ST_HR_FALL) ||
                           (ctl_q.state == pcie_rc_seq_pkg::ST_HR_HOLD);
  assign cmd_valid_o     = ctl_q.state == pcie_rc_seq_pkg::ST_REQ_OFFER;
  assign msi_ready_o     = msi_take;

  // The command is the REQ step's fields, verbatim (MAS: Commands).
  always_comb begin
    cmd_o          = '0;
    cmd_o.op       = word.op[3:0];
    cmd_o.addr     = word.addr;
    cmd_o.len      = word.n_dw;
    cmd_o.first_be = word.first_be;
    cmd_o.last_be  = word.last_be;
    cmd_o.data     = word.data;
  end

  // ------------------------------------------------------------ assertions

  // tl-seq: a result beat belongs to a command taken and not finished.
  `COMMON_ASSERT_NEVER(a_beat_without_command, clk_i, rst_i,
                       rsp_valid_i && !beat_now)
  // At most 32 beats per command (TL_SEQ: Results).
  `COMMON_ASSERT_NEVER(a_beats_at_most_32, clk_i, rst_i, beat_now && ctl_q.beat[5])
  // A command is held while it is offered.
  `COMMON_ASSERT_STABLE_WHEN(a_cmd_held, clk_i, rst_i, cmd_valid_o && !cmd_ready_i,
                             {cmd_valid_o, cmd_o})
  // A capture is taken only when offered.
  `COMMON_ASSERT_NEVER(a_msi_take_offered, clk_i, rst_i, msi_ready_o && !msi_valid_i)

endmodule

`default_nettype wire
