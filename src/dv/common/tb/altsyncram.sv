`default_nettype none

// DV-only port and parameter stub of the Intel altsyncram primitive
// (wiki/src/rtl/common/ram/common_sdp_ram.md#intel-backend). It is not a
// vendor simulation model: no source list of a product or a synthesis flow
// holds it. It lets Verilator lint the Intel backends of the shared RAMs and
// run their contract tests (cfg/backends.yaml dv_models). It carries the
// vendor module name, so the file is named after it.
//
// Elaboration rejects every parameter set outside the one shape the
// backends request: single-clock DUAL_PORT on a Cyclone IV E M9K, port B
// read-only and as wide and deep as port A, both on CLOCK0, unregistered
// outputs, mixed-port OLD_DATA, clock enables bypassed, no image, no power-up
// randomization, and either no byte enables (width_byteena_a and byte_size
// not given, as a one-lane backend leaves them) or 8- or 9-bit byte enables
// with width_a = width_byteena_a * byte_size (Quartus Error 272006). A check at
// every edge rejects a vendor input the backend must tie that is not tied
// inactive. Its behavior is the contract's (a read samples the pre-edge
// word; rden_b low holds q_b), never a claim about the vendor block: only
// Quartus fitter evidence proves the mapping.
//
// Provenance: adapted from nand2anything
// src/common/dv/memory/n2a_altsyncram_port_stub.sv (commit
// 8bb70d248d4755d7510fb2c0ec6487a5e05dec41), adapted with the owner's
// permission. Reduced to the DUAL_PORT shape, retargeted to Cyclone IV E,
// byte enables added, the lint pragmas dropped, the rejections made
// elaboration-time so that lint reports them.
module altsyncram #(
  parameter string operation_mode                     = "BIDIR_DUAL_PORT",
  parameter string intended_device_family             = "Stratix",
  parameter string ram_block_type                     = "AUTO",
  parameter string lpm_type                           = "altsyncram",
  parameter int    width_a                            = 1,
  parameter int    widthad_a                          = 1,
  parameter int    numwords_a                         = 0,
  parameter int    width_byteena_a                    = 1,
  // 0: not given (the vendor default applies only with byte enables).
  parameter int    byte_size                          = 0,
  parameter string outdata_reg_a                      = "UNREGISTERED",
  parameter int    width_b                            = 1,
  parameter int    widthad_b                          = 1,
  parameter int    numwords_b                         = 0,
  parameter int    width_byteena_b                    = 1,
  parameter string address_reg_b                      = "CLOCK1",
  parameter string rdcontrol_reg_b                    = "CLOCK1",
  parameter string outdata_reg_b                      = "UNREGISTERED",
  parameter string read_during_write_mode_mixed_ports = "DONT_CARE",
  parameter string clock_enable_input_a               = "NORMAL",
  parameter string clock_enable_output_a              = "NORMAL",
  parameter string clock_enable_input_b               = "NORMAL",
  parameter string clock_enable_output_b              = "NORMAL",
  parameter string init_file                          = "UNUSED",
  parameter string power_up_uninitialized             = "FALSE",
  parameter int    width_eccstatus                    = 3
) (
  input  logic                       wren_a,
  input  logic                       wren_b,
  input  logic                       rden_a,
  input  logic                       rden_b,
  input  logic [width_a-1:0]         data_a,
  input  logic [width_b-1:0]         data_b,
  input  logic [widthad_a-1:0]       address_a,
  input  logic [widthad_b-1:0]       address_b,
  input  logic                       clock0,
  input  logic                       clock1,
  input  logic                       clocken0,
  input  logic                       clocken1,
  input  logic                       clocken2,
  input  logic                       clocken3,
  input  logic                       aclr0,
  input  logic                       aclr1,
  input  logic [width_byteena_a-1:0] byteena_a,
  input  logic [width_byteena_b-1:0] byteena_b,
  input  logic                       addressstall_a,
  input  logic                       addressstall_b,
  output logic [width_a-1:0]         q_a,
  output logic [width_b-1:0]         q_b,
  output logic [width_eccstatus-1:0] eccstatus
);

  localparam bit BYTE_ENABLES = width_byteena_a > 1;
  localparam int BYTE_W       = (byte_size < 1) ? 1 : byte_size;

  // One generate block per rule; the message names the rule.
  if (operation_mode != "DUAL_PORT") begin : g_mode
    $fatal(1, "altsyncram stub: operation_mode must be DUAL_PORT");
  end
  if (intended_device_family != "Cyclone IV E") begin : g_family
    $fatal(1, "altsyncram stub: intended_device_family must be Cyclone IV E");
  end
  if (ram_block_type != "M9K") begin : g_block
    $fatal(1, "altsyncram stub: ram_block_type must be M9K");
  end
  if (lpm_type != "altsyncram") begin : g_lpm_type
    $fatal(1, "altsyncram stub: lpm_type must be altsyncram");
  end
  if (width_a < 1 || widthad_a < 1 || numwords_a != (1 << widthad_a)) begin : g_geometry
    $fatal(1, "altsyncram stub: numwords_a must be 2**widthad_a and width_a at least 1");
  end
  if (width_b != width_a || widthad_b != widthad_a || numwords_b != numwords_a) begin : g_port_b
    $fatal(1, "altsyncram stub: port B must match port A (no mixed widths)");
  end
  if ((BYTE_ENABLES || byte_size != 0) && !((byte_size == 8 || byte_size == 9)
                                           && width_a == width_byteena_a * byte_size)) begin : g_byteena_a
    $fatal(1, "altsyncram stub: byte_size, when given, must be 8 or 9 and width_a = width_byteena_a * byte_size");
  end
  if (width_byteena_a < 1 || width_byteena_b != 1) begin : g_byteena_b
    $fatal(1, "altsyncram stub: width_byteena_a must be at least 1 and width_byteena_b 1");
  end
  if (outdata_reg_a != "UNREGISTERED" || outdata_reg_b != "UNREGISTERED") begin : g_outdata
    $fatal(1, "altsyncram stub: outputs must be UNREGISTERED (one-edge read latency)");
  end
  if (address_reg_b != "CLOCK0" || rdcontrol_reg_b != "CLOCK0") begin : g_clock_b
    $fatal(1, "altsyncram stub: port B must register on CLOCK0 (single clock)");
  end
  if (read_during_write_mode_mixed_ports != "OLD_DATA") begin : g_mixed_rdw
    $fatal(1, "altsyncram stub: read_during_write_mode_mixed_ports must be OLD_DATA");
  end
  if (clock_enable_input_a != "BYPASS" || clock_enable_output_a != "BYPASS"
      || clock_enable_input_b != "BYPASS" || clock_enable_output_b != "BYPASS") begin : g_clock_enable
    $fatal(1, "altsyncram stub: clock enables must be BYPASS");
  end
  if (init_file != "UNUSED") begin : g_init_file
    $fatal(1, "altsyncram stub: init_file must be UNUSED");
  end
  if (power_up_uninitialized != "FALSE") begin : g_power_up
    $fatal(1, "altsyncram stub: power_up_uninitialized must be FALSE");
  end

  // The vendor inputs the backend ties must be inactive at every edge.
  logic ties_ok;
  assign ties_ok = wren_b == 1'b0 && rden_a == 1'b1 && clock1 == 1'b1
                   && {clocken0, clocken1, clocken2, clocken3} == 4'hf
                   && {aclr0, aclr1, addressstall_a, addressstall_b} == 4'h0
                   && byteena_b == {width_byteena_b{1'b1}};

  logic [width_a-1:0] mem_q [numwords_a];

  // Port A writes the enabled bytes (or the whole word) after port B has
  // sampled the pre-edge word: read-first, as OLD_DATA defines.
  always_ff @(posedge clock0) begin
    if (!ties_ok) begin
      $fatal(1, "altsyncram stub: an unused vendor input is not tied inactive");
    end
    if (wren_a) begin
      for (int i = 0; i < width_a; i++) begin
        if (!BYTE_ENABLES || byteena_a[i / BYTE_W]) begin
          mem_q[address_a][i] <= data_a[i];
        end
      end
    end
    if (rden_b) begin
      q_b <= mem_q[address_b];
    end
  end

  // Absent in DUAL_PORT mode.
  assign q_a       = '0;
  assign eccstatus = '0;
  // data_b is absent too; byteena_a is unused with full-word writes.
  logic unused_inputs;
  assign unused_inputs = ^{data_b, byteena_a};

endmodule

`default_nettype wire
