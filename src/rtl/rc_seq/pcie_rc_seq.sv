// Command-ROM test sequencer of pcie_rc: runs one program of steps that
// drive pcie_rc_tl and check its results. Interfaces, behavior and the
// step format: wiki/src/rtl/rc_seq/MAS.md; programs: PROGRAMS.md.
//
// Shell: the MAS ports present today with every output tied to its reset
// value. The tl-seq ports join with their common_pkg types and the
// interpreter (#376).
`default_nettype none

module pcie_rc_seq #(
  parameter int unsigned PROGRAM = 1     // program id (MAS: Programs); 1 is MMIO
) (
  input  logic        clk_i,             // core clock, 125 MHz
  input  logic        rst_i,             // synchronous to clk_i, active high

  // Run control and result (rc HAS: Sequencer run)
  input  logic        start_i,           // a rising edge starts a run when none is in progress
  output logic        done_o,            // the last run has ended
  output logic        pass_o,            // with done_o: the run passed
  output logic [15:0] fail_step_o,       // with done_o and not pass_o: ROM index of the failing step

  // Link status and control
  input  logic        dl_up_i,           // link dl_up_o: DL_Active
  input  logic        link_up_i,         // link link_up_o
  output logic        hot_reset_req_o    // to link hot_reset_req_i
);

  // Inputs and parameters the shell does not use yet; the interpreter
  // (#376) consumes them.
  logic unused_inputs;
  assign unused_inputs = ^{clk_i, rst_i, start_i, dl_up_i, link_up_i, PROGRAM[0]};

  // Reset values (MAS: Reset): no run has ended, no Hot Reset request.
  assign done_o          = 1'b0;
  assign pass_o          = 1'b0;
  assign fail_step_o     = 16'd0;
  assign hot_reset_req_o = 1'b0;

endmodule

`default_nettype wire
