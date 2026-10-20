`default_nettype none

`include "common_macros.svh"

// Intel backend of common_sync_ram: the same module, parameters, ports and
// cycle behavior as the portable common_sync_ram.sv, built from the vendor
// altsyncram primitive in SINGLE_PORT mode on M9K blocks: port A reads and
// writes on clk_i (CLOCK0), with an unregistered output (one-edge read
// latency), read every edge (rden_a tied high), same-port read-during-write
// OLD_DATA (a same-edge read of the written word returns the old word) and
// the lanes on byte enables. Only the FPGA flow's source list selects this
// file, in place of the portable one; simulation, ./build check lint and the
// other flows never read it. Verilator elaborates it only against the
// DV-only stub src/dv/common/tb/altsyncram.sv. Spec:
// wiki/src/rtl/common/ram/common_sync_ram.md#intel-backend.
//
// Provenance: adapted from this repository's common_sdp_ram_intel.sv, itself
// adapted from nand2anything src/common/rtl/n2a_sdp_ram_intel.sv (commit
// 8bb70d248d4755d7510fb2c0ec6487a5e05dec41) with the owner's permission.
// Moved to SINGLE_PORT mode and common_sync_ram's ports; the unused port B
// pins are tied as Intel-generated single-port wrappers tie them.
module common_sync_ram #(
  parameter int unsigned WIDTH  = 8,
  parameter int unsigned ADDR_W = 4,
  parameter int unsigned LANES  = 1,
  // Vendor facts, not part of the contract: a board on another Intel family
  // overrides the family from its board settings file. Untyped, as in the
  // source that Quartus has compiled (string parameters).
  parameter INTEL_DEVICE_FAMILY  = "Cyclone IV E",
  parameter INTEL_RAM_BLOCK_TYPE = "M9K"
) (
  input  logic              clk_i,
  input  logic              wr_en_i,
  input  logic [LANES-1:0]  wr_lanes_i,
  input  logic [ADDR_W-1:0] addr_i,
  input  logic [WIDTH-1:0]  wr_data_i,
  output logic [WIDTH-1:0]  rd_data_o
);

  localparam int unsigned DEPTH      = 2 ** ADDR_W;
  localparam int unsigned LANES_SAFE = (LANES == 0) ? 1 : LANES;
  localparam int unsigned LANE_W     = WIDTH / LANES_SAFE;

  `COMMON_ASSERT_PARAM(g_width_check, WIDTH >= 1, "common_sync_ram: WIDTH must be at least 1")
  `COMMON_ASSERT_PARAM(g_addr_w_check, ADDR_W >= 1, "common_sync_ram: ADDR_W must be at least 1")
  `COMMON_ASSERT_PARAM(g_lanes_check, LANES >= 1 && WIDTH % LANES_SAFE == 0,
                       "common_sync_ram: LANES must be at least 1 and divide WIDTH")
  `COMMON_ASSERT_PARAM(g_lane_w_check, LANES <= 1 || LANE_W == 8 || LANE_W == 9,
                       "common_sync_ram: the Intel backend needs one lane or 8- or 9-bit lanes")

  // A write with no lane enabled writes nothing, as in the portable file.
  logic wr_any;
  assign wr_any = wr_en_i && (|wr_lanes_i);

  // Port B is unused in SINGLE_PORT mode: its inputs are tied as
  // Intel-generated single-port wrappers tie them (address_b and data_b
  // high, wren_b low, rden_b high), and its outputs q_b and eccstatus are
  // explicit empty connections. One lane uses no byte enables:
  // width_byteena_a, byte_size and byteena_a stay at their defaults
  // (byteena_a unconnected) and wren_a alone gates the write, as in
  // common_sdp_ram_intel.sv (Quartus Error 272006). Several lanes use byte
  // enables of LANE_W (8 or 9) bits.
  generate
    if (LANES_SAFE == 1) begin : g_one_lane
      altsyncram #(
        .operation_mode                ("SINGLE_PORT"),
        .intended_device_family        (INTEL_DEVICE_FAMILY),
        .ram_block_type                (INTEL_RAM_BLOCK_TYPE),
        .lpm_type                      ("altsyncram"),
        .width_a                       (WIDTH),
        .widthad_a                     (ADDR_W),
        .numwords_a                    (DEPTH),
        .outdata_reg_a                 ("UNREGISTERED"),
        .read_during_write_mode_port_a ("OLD_DATA"),
        .clock_enable_input_a          ("BYPASS"),
        .clock_enable_output_a         ("BYPASS"),
        .init_file                     ("UNUSED"),
        .power_up_uninitialized        ("FALSE")
      ) u_ram (
        .clock0         (clk_i),
        .wren_a         (wr_any),
        .address_a      (addr_i),
        .data_a         (wr_data_i),
        .q_a            (rd_data_o),
        .rden_a         (1'b1),
        .wren_b         (1'b0),
        .rden_b         (1'b1),
        .address_b      (1'b1),
        .data_b         (1'b1),
        .clock1         (1'b1),
        .clocken0       (1'b1),
        .clocken1       (1'b1),
        .clocken2       (1'b1),
        .clocken3       (1'b1),
        .aclr0          (1'b0),
        .aclr1          (1'b0),
        .byteena_b      (1'b1),
        .addressstall_a (1'b0),
        .addressstall_b (1'b0),
        .byteena_a      (),
        .q_b            (),
        .eccstatus      ()
      );
    end else begin : g_lanes
      altsyncram #(
        .operation_mode                ("SINGLE_PORT"),
        .intended_device_family        (INTEL_DEVICE_FAMILY),
        .ram_block_type                (INTEL_RAM_BLOCK_TYPE),
        .lpm_type                      ("altsyncram"),
        .width_a                       (WIDTH),
        .widthad_a                     (ADDR_W),
        .numwords_a                    (DEPTH),
        .width_byteena_a               (LANES),
        .byte_size                     (LANE_W),
        .outdata_reg_a                 ("UNREGISTERED"),
        .read_during_write_mode_port_a ("OLD_DATA"),
        .clock_enable_input_a          ("BYPASS"),
        .clock_enable_output_a         ("BYPASS"),
        .init_file                     ("UNUSED"),
        .power_up_uninitialized        ("FALSE")
      ) u_ram (
        .clock0         (clk_i),
        .wren_a         (wr_any),
        .byteena_a      (wr_lanes_i),
        .address_a      (addr_i),
        .data_a         (wr_data_i),
        .q_a            (rd_data_o),
        .rden_a         (1'b1),
        .wren_b         (1'b0),
        .rden_b         (1'b1),
        .address_b      (1'b1),
        .data_b         (1'b1),
        .clock1         (1'b1),
        .clocken0       (1'b1),
        .clocken1       (1'b1),
        .clocken2       (1'b1),
        .clocken3       (1'b1),
        .aclr0          (1'b0),
        .aclr1          (1'b0),
        .byteena_b      (1'b1),
        .addressstall_a (1'b0),
        .addressstall_b (1'b0),
        .q_b            (),
        .eccstatus      ()
      );
    end
  endgenerate

endmodule

`default_nettype wire
