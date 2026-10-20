# common_sync_ram MAS

`common_sync_ram` is the single-port synchronous RAM: `2**ADDR_W` words of
`WIDTH` bits on one clock, written per lane and read every edge with
one-edge latency. It is the base of the shared RAM family; like every
shared block it has no HAS, and this page owns its interface, behavior and
structure. The family is:

| Block | Ports | Read | Same-word write and read at one edge |
| --- | --- | --- | --- |
| `common_sync_ram` (this page) | one read/write | every edge | old word |
| [`common_sync_ram_held`](common_sync_ram_held.md) | one read/write | read enable, held output | old word |
| [`common_sync_ram_wt`](common_sync_ram_wt.md) | one read/write | every edge | new word (write-through) |
| [`common_sdp_ram`](common_sdp_ram.md) | one write, one read | read enable, held output | old word |
| [`common_tdp_ram`](common_tdp_ram.md) | two read/write | every edge | old word on both ports |

Use a family block for any storage deeper than a few words; a small table
read combinationally stays a register array or a `COMMON_MEM_WRITE` array
in its own module. There is no reset, no initialization and no ROM image:
a consumer that needs defined contents writes them.

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock; every access is at its rising edge. |
| `wr_en_i` | in | 1 | `clk_i` | Write the selected lanes of word `addr_i` at this edge. |
| `wr_lanes_i` | in | `LANES` | `clk_i` | Lane enables: bit `l` writes bits `[l*W/LANES +: W/LANES]` (W is `WIDTH`). Ignored while `wr_en_i` is low. |
| `addr_i` | in | `ADDR_W` | `clk_i` | Word read at every edge, and written when `wr_en_i` is high. |
| `wr_data_i` | in | `WIDTH` | `clk_i` | Write data; only the enabled lanes are stored. |
| `rd_data_o` | out | `WIDTH` | `clk_i` | The word `addr_i` held before the last edge, registered. |

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Word width in bits. |
| `ADDR_W` | `int unsigned` | 4 | 1 or more | Depth is `2**ADDR_W` words. |
| `LANES` | `int unsigned` | 1 | 1 or more, dividing `WIDTH` | Number of equal write lanes; 1 writes whole words. |

An illegal value stops elaboration through `COMMON_ASSERT_PARAM`, so lint
and the simulation build report it: block `g_width_check` (`WIDTH`),
`g_addr_w_check` (`ADDR_W`) and `g_lanes_check` (`LANES` zero or not
dividing `WIDTH`), each with the message `common_sync_ram: <rule>`.

## Behavior

- **Read:** at every rising edge `rd_data_o` takes the word at `addr_i` as
  it was before that edge: one-edge latency, no read enable, no bypass.
- **Write:** at a rising edge with `wr_en_i` high, each lane `l` with
  `wr_lanes_i[l]` high takes its bits from `wr_data_i`; the other lanes
  keep their value. `wr_en_i` high with no lane enabled writes nothing.
- **Read during write:** the same edge reads and writes the same word, and
  the read returns the old word (read-first). The new word is visible from
  the next edge.
- **Contents:** a word is undefined until each of its lanes is written;
  `rd_data_o` is undefined until the first edge. Words keep their value
  forever; there is no reset or clear.

## Microarchitecture

- **Storage:** `mem_q`, `2**ADDR_W` words of `WIDTH` bits, never reset.
- **Ports:** one `COMMON_MEM_WRITE_LANES` process (lane width
  `WIDTH/LANES`) and one `COMMON_MEM_READ` process into `rd_data_o`, both
  on `clk_i` ([RTL vocabulary](../../../../bkm/rtl.md#registers-and-memories)).
  Reads and writes in separate processes make a collision read-first by
  nonblocking order, with no bypass logic.
- **Inference shape:** this is the conventional single-port RAM template
  (one write process, one registered read on the same address). Expected
  and, where a flow has measured it, measured:
  - Quartus was expected to map it to block RAM in single-port mode with
    old-data read-during-write, with lanes of 8 or 9 bits on the block's
    byte enables. **Measured otherwise:** in the first consumer that
    synthesizes it, the [`rc` fit](../../../fpga/rc/FPGA.md#results)
    (`fpga` workflow run 39328627425), the `rc_tl` host memory (`WIDTH`
    32, `ADDR_W` 10, `LANES` 4) was built from logic, not M9K: about
    32768 registers, and its read data ended the worst `clk_core` path.
    So the FPGA flow does not rely on inference: it selects the
    [Intel backend](#intel-backend)
    ([#417](https://github.com/amichai-bd/pcie-factory/issues/417)).
  - Yosys (the ASIC flow) infers a memory with a non-transparent
    synchronous read port. The ASIC flow has no SRAM macros, so the
    memory becomes flops and read multiplexers; size it accordingly.
  - Verilator simulates it as written; it is two-state, so an unwritten
    word reads as some defined value that tests must not check.

## Intel backend

`common_sync_ram_intel.sv` is the same module for Quartus: one
`altsyncram` named `u_ram` (in generate block `g_one_lane` for one lane,
else `g_lanes`), a [source backend](../../../../project/layout.md#source-backends)
that only the FPGA flow's source list selects (`cfg/backends.yaml`, suffix
`intel`), in place of the portable file. Simulation, `./build check` lint
and the ASIC flow read the portable file, so every other target keeps the
inferred RAM; Verilator reads the backend only against the DV-only stub
`src/dv/common/tb/altsyncram.sv`. It follows the
[`common_sdp_ram` Intel backend](common_sdp_ram.md#intel-backend) in
single-port mode.

| `altsyncram` parameter | Value | Why |
| --- | --- | --- |
| `operation_mode` | `SINGLE_PORT` | Port A reads and writes one address; port B is unused. |
| `intended_device_family`, `ram_block_type` | `INTEL_DEVICE_FAMILY` (default `Cyclone IV E`), `INTEL_RAM_BLOCK_TYPE` (default `M9K`) | Vendor facts, not contract: the backend's two extra parameters; another Intel board overrides them from its board settings file. |
| `width_a`, `widthad_a`, `numwords_a` | `WIDTH`, `ADDR_W`, `2**ADDR_W` | Port A geometry; every port B parameter is left unset. |
| `width_byteena_a`, `byte_size` | One lane: not given (no byte enables). Several lanes: `LANES` and the lane width | As in the `common_sdp_ram` backend: one lane writes whole words through `wren_a` alone (Quartus Error 272006 otherwise); several lanes are byte enables, so they must be 8 or 9 bits (`g_lane_w_check`, Intel backend only). |
| `outdata_reg_a` | `UNREGISTERED` | One-edge read latency: `rd_data_o` is the block's read register. |
| `read_during_write_mode_port_a` | `OLD_DATA` | Read-first collision on the one port, as the portable file. |
| `clock_enable_input_a`, `clock_enable_output_a` | `BYPASS` | No clock enables. |
| `init_file`, `power_up_uninitialized` | `UNUSED`, `FALSE` | No image; contents stay undefined to the contract. |

Pins: `clock0` is `clk_i`; `wren_a` is `wr_en_i` with at least one lane
enabled, `byteena_a` is `wr_lanes_i` with several lanes (unconnected with
one), `address_a` is `addr_i`, `data_a` is `wr_data_i` and `q_a` is
`rd_data_o`. `rden_a` ties high, so the port reads every edge. The unused
port B is tied as Intel-generated single-port wrappers tie it: `wren_b`,
`aclr*` and `addressstall_*` low; `rden_b`, `address_b`, `data_b`,
`clock1`, `clocken*` and `byteena_b` high. `q_b` and `eccstatus` are
explicit empty connections, and so is `byteena_a` with one lane; the
backend's lint test expects exactly `DECLFILENAME` and those
`PINCONNECTEMPTY` messages, counted over both generate branches. The
parameter checks are the portable file's plus `g_lane_w_check`.

**Fitter evidence.** Simulation never proves the mapping; each consuming
target's fit does (`resources.rams` in its `metrics.json`). Expected per
instance: `Single Port`, `Single Clock`, and the M9K count below (an M9K
holds 1024 words of up to 9 bits, or 512 of up to 18, or 256 of up to 36,
in single-port mode). The read-during-write mode is the instance's
parameter; the report's mixed-port column does not apply to one port.

| Consumer (target) | Geometry | Expected M9K | Measured |
| --- | --- | --- | --- |
| `rc_tl` host memory (`rc`, DE2-115) | 1024 x 32, four 8-bit lanes | 4 (four x8 at 1024 words) | 4 M9K: Single Port, Single Clock, 1024 x 32, 32768 bits (`altsyncram:g_lanes.u_ram`); the report's mixed-port column reads Don't care, as for any single-port RAM. |

Measured on the `rc` fit with this backend (`fpga` workflow run
39373371520 at `09b41c4`, the [#417](https://github.com/amichai-bd/pcie-factory/issues/417)
branch, whose `rc` FPGA inputs equal its final head; Quartus Prime Lite
25.1std.0, EP4CE115F29C7; map, fit and timing passed;
[`rc` FPGA, Results](../../../fpga/rc/FPGA.md#results)). The host memory
left logic: the build's registers fell from 35699 to 2899 and its logic
elements from 48243 to 6440, and its read data no longer ends the worst
`clk_core` path.

## Decisions

These hold for the whole family.

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Depth as `ADDR_W` (`2**ADDR_W` words) | Power-of-two depth is the source's rule anyway; one parameter cannot disagree with another, and it matches [`common_cdc_fifo`](../cdc/common_cdc_fifo.md). | The source's `DEPTH` plus a checked `ADDR_WIDTH`. |
| Lane enables on every write port, `LANES` default 1 | The vocabulary has the lane-write form; consumers of partial words need it, and a one-lane RAM is the plain template. | A separate lane variant of each block. |
| Portable inferred RAM for every target but the FPGA flow, which selects an [Intel backend](#intel-backend) ([#417](https://github.com/amichai-bd/pcie-factory/issues/417)) | Quartus built the byte-lane form from logic in the `rc` fit (above); one module name in two files keeps every consumer and test unchanged, and simulation, lint and the ASIC flow keep the one portable source. Only this block has a backend so far: `common_sync_ram_held` and `common_sync_ram_wt` have no FPGA consumer. | Inference alone (measured to miss M9K); `ifdef` or `ramstyle` in the block; vendor primitives in IP RTL; the source's IHP backend (out of scope in [#126](https://github.com/amichai-bd/pcie-factory/issues/126)). |
| No reset, no initialization, no ROM image | RAM blocks cannot reset their contents; an image needs a system task the design rules forbid. | An initialization macro (no consumer). |
| No dual-clock RAM yet | A registered read of a memory written on another clock is a raw crossing to the [CDC check](../../../../tools/checks/SPEC.md#cdc-check); its only exception needs a synchronized pointer inside the cell, which a plain RAM has not. Cross-domain data uses [`common_cdc_fifo`](../cdc/common_cdc_fifo.md). | Porting the source's dual-clock RAM with a CDC-check exception; deferred to [#180](https://github.com/amichai-bd/pcie-factory/issues/180) with the CDC gate unchanged. |

Provenance: adapted from nand2anything `src/common/rtl/n2a_sync_ram.sv` and
its HAS (commit `085ef4b`), adapted with the owner's permission. The Intel
backend is adapted from this repository's `common_sdp_ram_intel.sv`, itself
adapted from nand2anything `src/common/rtl/n2a_sdp_ram_intel.sv` (commit
`8bb70d248d4755d7510fb2c0ec6487a5e05dec41`) with the owner's permission.

## Timing

One clock, no reset. `addr_i` and the write inputs go to the RAM's
registered ports; `rd_data_o` is a register output (the block RAM's output
register when inferred or, in the FPGA flow, the M9K's read register).

## Open items

| Item | Owner |
| --- | --- |
| The Yosys (sky130 ASIC flow) inference shape is not measured: deferred until the first `common_sync_ram` consumer enters the ASIC flow. | [#420](https://github.com/amichai-bd/pcie-factory/issues/420) |
| A dual-clock simple dual-port RAM (the source's `n2a_sdp_ram_dc`) waits for a consumer; the CDC gate stays as it is, so it needs a consumer-specific crossing cell or a separately approved check rule. | [#180](https://github.com/amichai-bd/pcie-factory/issues/180) |

## Files

| File | Holds |
| --- | --- |
| [`common_sync_ram.sv`](../../../../../src/rtl/common/ram/common_sync_ram.sv) | The module. |
| [`common_sync_ram.rtl.f`](../../../../../src/rtl/common/ram/common_sync_ram.rtl.f) | `-F ../common_macros.rtl.f` (the vocabulary), then the module. |
| [`common_sync_ram_intel.sv`](../../../../../src/rtl/common/ram/common_sync_ram_intel.sv) | The [Intel backend](#intel-backend); in no filelist, selected by the FPGA flow. |
