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
  on `clk_i` ([RTL vocabulary](../../../bkm/rtl.md#registers-and-memories)).
  Reads and writes in separate processes make a collision read-first by
  nonblocking order, with no bypass logic.
- **Inference shape:** this is the conventional single-port RAM template
  (one write process, one registered read on the same address). Expected,
  not yet measured by a flow (no consumer synthesizes it):
  - Quartus maps it to block RAM in single-port mode with old-data
    read-during-write. Lanes of 8 or 9 bits map to the block's byte
    enables; other lane widths may split the RAM into one RAM per lane.
  - Yosys (the ASIC flow) infers a memory with a non-transparent
    synchronous read port. The ASIC flow has no SRAM macros, so the
    memory becomes flops and read multiplexers; size it accordingly.
  - Verilator simulates it as written; it is two-state, so an unwritten
    word reads as some defined value that tests must not check.

## Decisions

These hold for the whole family.

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Depth as `ADDR_W` (`2**ADDR_W` words) | Power-of-two depth is the source's rule anyway; one parameter cannot disagree with another, and it matches [`common_cdc_fifo`](common_cdc_fifo.md). | The source's `DEPTH` plus a checked `ADDR_WIDTH`. |
| Lane enables on every write port, `LANES` default 1 | The vocabulary has the lane-write form; consumers of partial words need it, and a one-lane RAM is the plain template. | A separate lane variant of each block. |
| Portable inferred RAM only | No consumer needs a vendor macro yet; one source serves simulation, lint, FPGA and ASIC. | The source's Intel and IHP backends (out of scope in [#126](https://github.com/amichai-bd/pcie-factory/issues/126)). |
| No reset, no initialization, no ROM image | RAM blocks cannot reset their contents; an image needs a system task the design rules forbid. | An initialization macro (no consumer). |
| No dual-clock RAM yet | A registered read of a memory written on another clock is a raw crossing to the [CDC check](../../../tools/checks/SPEC.md#cdc-check); its only exception needs a synchronized pointer inside the cell, which a plain RAM has not. Cross-domain data uses [`common_cdc_fifo`](common_cdc_fifo.md). | Porting the source's dual-clock RAM with a CDC-check exception; deferred to [#180](https://github.com/amichai-bd/pcie-factory/issues/180) with the CDC gate unchanged. |

Provenance: adapted from nand2anything `src/common/rtl/n2a_sync_ram.sv` and
its HAS (commit `085ef4b`), adapted with the owner's permission.

## Timing

One clock, no reset. `addr_i` and the write inputs go to the RAM's
registered ports; `rd_data_o` is a register output (the block RAM's output
register when inferred).

## Open items

| Item | Owner |
| --- | --- |
| Measure the inference shape in Quartus and the ASIC flow with the first consumer that synthesizes a family block. | [#65](https://github.com/amichai-bd/pcie-factory/issues/65) |
| A dual-clock simple dual-port RAM (the source's `n2a_sdp_ram_dc`) waits for a consumer; the CDC gate stays as it is, so it needs a consumer-specific crossing cell or a separately approved check rule. | [#180](https://github.com/amichai-bd/pcie-factory/issues/180) |

## Files

| File | Holds |
| --- | --- |
| [`common_sync_ram.sv`](../../../../src/rtl/common/common_sync_ram.sv) | The module. |
| [`common_sync_ram.rtl.f`](../../../../src/rtl/common/common_sync_ram.rtl.f) | `-F common_macros.rtl.f` (the vocabulary), then the module. |
