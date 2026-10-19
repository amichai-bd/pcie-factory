# common_sdp_ram MAS

`common_sdp_ram` is the simple dual-port synchronous RAM: one write port
and one read port with its own address and a read enable, on one clock.
Like every shared block it has no HAS, and this page owns its interface,
behavior and structure. The [family decisions](common_sync_ram.md#decisions)
apply; a consumer that reads every edge ties `rd_en_i` high.

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock; every access is at its rising edge. |
| `wr_en_i` | in | 1 | `clk_i` | Write the selected lanes of word `wr_addr_i` at this edge. |
| `wr_lanes_i` | in | `LANES` | `clk_i` | Lane enables, as in [`common_sync_ram`](common_sync_ram.md#interfaces). |
| `wr_addr_i` | in | `ADDR_W` | `clk_i` | Write address. |
| `wr_data_i` | in | `WIDTH` | `clk_i` | Write data; only the enabled lanes are stored. |
| `rd_en_i` | in | 1 | `clk_i` | Read enable. |
| `rd_addr_i` | in | `ADDR_W` | `clk_i` | Read address. |
| `rd_data_o` | out | `WIDTH` | `clk_i` | The word read at the last edge with `rd_en_i` high. |

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Word width in bits. |
| `ADDR_W` | `int unsigned` | 4 | 1 or more | Depth is `2**ADDR_W` words. |
| `LANES` | `int unsigned` | 1 | 1 or more, dividing `WIDTH` | Number of equal write lanes. |

Illegal values stop elaboration through `COMMON_ASSERT_PARAM` blocks
`g_width_check`, `g_addr_w_check` and `g_lanes_check` with the message
`common_sdp_ram: <rule>`.

## Behavior

- **Read:** at a rising edge with `rd_en_i` high, `rd_data_o` takes the
  word at `rd_addr_i` as it was before that edge (one-edge latency); with
  `rd_en_i` low it keeps its value.
- **Write:** at a rising edge with `wr_en_i` high, the lanes enabled by
  `wr_lanes_i` of word `wr_addr_i` take `wr_data_i`; independent of the
  read port.
- **Read during write:** an enabled read of the word written at the same
  edge returns the old word; the new word is readable from the next edge.
- **Contents:** words are undefined until written; `rd_data_o` is
  undefined until the first enabled read. No reset.

## Microarchitecture

- **Storage and ports:** `mem_q` with one `COMMON_MEM_WRITE_LANES` process
  at `wr_addr_i` and one `COMMON_MEM_READ_EN` process into `rd_data_o` at
  `rd_addr_i`, both on `clk_i`. Separate processes make a collision
  read-first.
- **Inference shape:** the conventional simple dual-port template. The
  FPGA flow does not rely on inference: it selects the
  [Intel backend](#intel-backend). Yosys infers a memory with one write
  port and one non-transparent synchronous read port with an enable; the
  ASIC flow does not rely on it either: it selects the
  [sky130 backend](#sky130-backend).

## Intel backend

`common_sdp_ram_intel.sv` is the same module for Quartus: one `altsyncram`
named `u_ram` (in generate block `g_one_lane` for one lane, else
`g_lanes`), a [source backend](../../../../project/layout.md#source-backends)
that only the FPGA flow's source list selects (`cfg/backends.yaml`, suffix
`intel`), in place of the portable file. Simulation, `./build check` lint
and the ASIC flow read the portable file; Verilator reads the backend only
against the DV-only stub `src/dv/common/tb/altsyncram.sv`.

| `altsyncram` parameter | Value | Why |
| --- | --- | --- |
| `operation_mode` | `DUAL_PORT` | Port A writes, port B reads (simple dual port). |
| `intended_device_family`, `ram_block_type` | `INTEL_DEVICE_FAMILY` (default `Cyclone IV E`), `INTEL_RAM_BLOCK_TYPE` (default `M9K`) | Vendor facts, not contract: the backend's two extra parameters; another Intel board overrides them from its board settings file. |
| `width_a`, `widthad_a`, `numwords_a`; `width_b`, `widthad_b`, `numwords_b` | `WIDTH`, `ADDR_W`, `2**ADDR_W` on both ports | No mixed widths. |
| `width_byteena_a`, `byte_size` | One lane: not given (vendor defaults, no byte enables). Several lanes: `LANES` and the lane width | One lane writes whole words through `wren_a` alone, as Intel-generated wrappers do; Quartus rejects `width_byteena_a = 1` with a `byte_size` that is not the width (Error 272006, seen on the first `link` fit with 34-bit words). Several lanes are byte enables; an M9K byte is 8 or 9 bits, so they need 8- or 9-bit lanes (`g_lane_w_check`, Intel backend only). |
| `outdata_reg_a`, `outdata_reg_b` | `UNREGISTERED` | One-edge read latency: `rd_data_o` is the block's read register. |
| `address_reg_b`, `rdcontrol_reg_b` | `CLOCK0` | One clock. |
| `read_during_write_mode_mixed_ports` | `OLD_DATA` | Read-first collision, as the portable file. |
| `clock_enable_*` | `BYPASS` | No clock enables. |
| `init_file`, `power_up_uninitialized` | `UNUSED`, `FALSE` | No image; contents stay undefined to the contract. |

Pins: `clock0` is `clk_i`; `wren_a` is `wr_en_i` with at least one lane
enabled, `byteena_a` is `wr_lanes_i` with several lanes (unconnected with
one), `address_a` and `data_a` the write
port; `rden_b` is `rd_en_i` (a disabled read holds `q_b`), `address_b` and
`q_b` the read port. `wren_b`, `aclr*` and `addressstall_*` tie low;
`rden_a`, `clock1`, `clocken*` and `byteena_b` tie high. `data_b`, `q_a` and
`eccstatus`, absent in this mode, are explicit empty connections, as
Intel-generated wrappers leave them, and so is `byteena_a` with one lane;
the backend's lint test expects exactly `DECLFILENAME` and those
`PINCONNECTEMPTY` messages, counted over both generate branches (Verilator
reports each branch's empty connections whatever `LANES` is). The
parameter checks are the portable file's plus `g_lane_w_check`.

**Fitter evidence.** Simulation never proves the mapping; each consuming
target's fit does (`resources.rams` in its `metrics.json`). Expected per
instance: `Simple Dual Port`, `Single Clock`, mixed-port RDW `Old data`,
and the M9K count below (an M9K holds 256 words of up to 36 bits, or 512
of up to 18, in this mode). Measured on the first `link` fit
([#276](https://github.com/amichai-bd/pcie-factory/pull/276) `ci-fpga`, run
39074066731 at `080031d`, whose `link` FPGA inputs equal the PR's final
head, which reproduces it; Quartus Prime Lite 25.1std, EP4CE115F29C7, with
this backend's one-lane form; map, fit and timing passed;
[`link` FPGA, Results](../../../fpga/link/FPGA.md#results)). The fit's fourth M9K is not this block: Quartus
infers the 64 x 8 `end_q` array of the dll TX path as a RAM.

| Consumer (target) | Geometry | Expected M9K | Measured |
| --- | --- | --- | --- |
| dll receive buffer (`link`, DE2-115) | 512 x 34 | 2 (two x18 at 512 words) | 2 M9K: Simple Dual Port, Single Clock, 512 x 34, 17408 bits, mixed RDW Old data (`altsyncram:g_one_lane.u_ram`). |
| dll replay buffer (`link`, DE2-115) | 256 x 32 | 1 (x32 at 256 words) | 1 M9K: Simple Dual Port, Single Clock, 256 x 32, 8192 bits, mixed RDW Old data (`altsyncram:g_one_lane.u_ram`). |

## sky130 backend

[`common_sdp_ram_sky130.sv`](../../../../../src/rtl/common/ram/common_sdp_ram_sky130.sv)
is this module on sky130 SRAM macros, a
[source backend](../../../../project/layout.md#source-backends) that only
the ASIC flow's source list selects (`cfg/backends.yaml`, suffix `sky130`),
in place of the portable file. Simulation, `./build check` lint and the
FPGA flow read the portable file. Its
parameters, ports, parameter checks and the behavior above are identical.

**Selection.** The backend puts a RAM on macros (generate block
`g_macros`) only when it has one lane and holds at least 8192 bits
(`WIDTH × 2**ADDR_W`, half a macro: the `dll` replay buffer); any other RAM
(generate block `g_flops`) keeps the portable file's array and read
register, which Yosys maps to flops
([#296](https://github.com/amichai-bd/pcie-factory/issues/296)). Below half
a macro, flops take less area than a mostly empty 0.42 mm² macro and avoid
its late read data; the macro's write mask is not used for lanes, so a
lane-enabled RAM stays on flops. In `core` this puts the `dll` buffers and the
`tl` requester-completion data queue (256 × 32) on macros, and the other
`tl` queues (at most 128 × 32) and the `cfg` MSI-X table (8 × 96, 12
lanes) on flops.

**Macro.** `sky130_sram_1rw1r_64x256_8`, an OpenRAM macro the pinned ORFS
image carries (`flow/platforms/sky130ram/sky130_sram_1rw1r_64x256_8/`:
`.lef`, `_TT_1p8V_25C.lib`, `.gds`, `.v`; ORFS commit `d4b96380b10e`), 256
words of 64 bits with a byte write mask. Port 0 reads or writes (`csb0`,
`web0` active low), port 1 reads (`csb1` active low); both register every
input at the rising edge of their clock. Its Liberty view (typical corner,
1.8 V, 25 °C) gives a 0.165 ns setup and a −0.052 ns hold on every input,
and the read data on a falling-edge arc, about 0.38 to 0.53 ns after the
falling edge that follows the read edge; the area is about 0.42 mm²
(1041 × 403 µm). The image also holds 128x256, 80x64 and 44x64 variants;
64x256 tiles both `dll` buffers with the fewest macros of the same storage.
The macro's model drives undefined data in a cycle without a read, and a
port 1 read of the word port 0 writes at the same edge is undefined.

**Structure.**

The rest of this section describes `g_macros`.

- **Tiling.** `ceil(WIDTH / 64)` macros across and `2**(ADDR_W - 8)` deep
  (one row, low words only, when `ADDR_W <= 8`). Port 0 of every macro is
  the write port, port 1 the read port, both on `clk_i`; a row's chip
  selects decode the top address bits; unused data bits are written as
  zero. The `dll` replay buffer (256 × 32) takes one macro, the receive
  buffer (512 × 34) two.
- **Pending write.** A write is registered (`pend_q`, `pend_addr_q`,
  `pend_data_q`) and committed to the macros at the next edge. A read of
  the pending word (`collide`) disables the macro read and registers the
  pending data (`byp_q`, `byp_data_q`) instead. So no physical collision
  happens, and an enabled read always gets the word before this edge's
  write (read-first); a word written at edge N is readable at edge N+1,
  from the bypass, and from the macro after.
- **Read data and hold.** `fresh_q` (the last edge read the macros) and
  `row_q` select the macro row's read data; `hold_q` takes it at the next
  edge, and `rd_data_o` is the bypass word, the fresh macro word or
  `hold_q`. So `rd_data_o` keeps the last word read through cycles without
  a read, as the portable read register does. The extra state is
  `2 × WIDTH + ADDR_W + ROW_W + 3` flops.
- **Latency and timing.** Still one edge, but the macro word arrives about
  half a period plus 0.5 ns after the read edge, not at the clock-to-output
  of a register, so the consumer's logic after `rd_data_o` gets the
  remaining half period. STA covers it through the macro's Liberty arcs.

**Verification.** Verilator reads the backend only against the DV-only
cell model
[`sky130_sram_1rw1r_64x256_8.sv`](../../../../../src/dv/common/tb/sky130_sram_1rw1r_64x256_8.sv)
(`dv_models` of the `asic` entry), written for this repository from the
macro's documented behavior: inputs at the rising edge, read data for one
cycle only and junk otherwise, and a fatal error on a same-word write and
read. The shared contract tests run on the backend through the regression
entries with `backend: asic` ([DV plan](../../../dv/common/ram/common_sdp_ram.md)),
at a macro regression shape (two macros across), at both `dll` buffer
shapes (one and two macro rows) and at a two-lane flop shape;
`tests.test_ram_backends` lints it with `-Wall` and proves the selection by
linting without the macro model, where only the macro shapes fail. Silicon
behavior and timing come only from the ASIC flow.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| One module with a read enable | The source's two modules differ only in a tied-high enable; one block keeps one page and one test set. | The source's separate always-read wrapper. |
| An explicit Intel backend selected by source list | The fit must be known (M9K, old-data collision), not inferred; one module name in two files keeps every consumer and test unchanged. | Inference alone (shape unmeasured); `ifdef` or `ramstyle` in the block; vendor primitives in IP RTL. |
| Lanes on byte enables, 8- or 9-bit lanes only | Matches M9K byte sizes; the consumers use one lane. | Emulating narrow lanes with read-modify-write. |
| A sky130 SRAM-macro backend for the ASIC flow ([#279](https://github.com/amichai-bd/pcie-factory/issues/279)) | Inferred arrays map to flops on sky130hd, too large to route in `link` (about 25 kbit); the same source-backend mechanism as the Intel one. | Yosys memory mapping in an ORFS hook (no reviewed RTL, no lint); raising the container memory alone (timing stays far off). |
| Macros only for one-lane RAMs of at least 8192 bits in the sky130 backend ([#296](https://github.com/amichai-bd/pcie-factory/issues/296)) | Small RAMs (the `tl` queues, the `cfg` table) cost less as flops than as mostly empty macros, and the selection needs no consumer or flow change. | Every RAM on macros (nine more macros in `core`, most nearly empty, and no lane support); a per-instance flow knob (no reviewed RTL). |
| Pending write with a bypass in the sky130 backend | The macro defines no same-word write and read in one edge; delaying the write one edge and bypassing it keeps the read-first contract for every input. | Requiring consumers never to collide (a contract change). |

Provenance: adapted from nand2anything `src/common/rtl/n2a_sdp_ram_portable.sv`
(modules `n2a_sdp_ram_enabled` and `n2a_sdp_ram`, commit `085ef4b`),
adapted with the owner's permission. The Intel backend is adapted from
nand2anything `src/common/rtl/n2a_sdp_ram_intel.sv`, and its stub and
rejection set from `src/common/dv/memory/n2a_altsyncram_port_stub.sv` and
its reject fixtures (commit `8bb70d248d4755d7510fb2c0ec6487a5e05dec41`),
adapted with the owner's permission; that project fits the same backend on
the DE2-115. The sky130 backend's pattern (pending write with a collision
bypass, tiling, a strict DV-only macro model) is adapted from nand2anything
`src/common/rtl/n2a_sdp_ram_ihp_edge.sv` and
`src/common/dv/memory/n2a_ihp_edge_2p_model.sv` (commit `8bb70d2`), adapted
with the owner's permission, for another macro.

## Timing

One clock, no reset. `rd_data_o` is the RAM's read register.

## Files

| File | Holds |
| --- | --- |
| [`common_sdp_ram.sv`](../../../../../src/rtl/common/ram/common_sdp_ram.sv) | The module. |
| [`common_sdp_ram.rtl.f`](../../../../../src/rtl/common/ram/common_sdp_ram.rtl.f) | `-F ../common_macros.rtl.f`, then the module. |
| [`common_sdp_ram_intel.sv`](../../../../../src/rtl/common/ram/common_sdp_ram_intel.sv) | The [Intel backend](#intel-backend); in no filelist, selected by the FPGA flow. |
| [`common_sdp_ram_sky130.sv`](../../../../../src/rtl/common/ram/common_sdp_ram_sky130.sv) | The [sky130 backend](#sky130-backend); in no filelist, selected by the ASIC flow. |
