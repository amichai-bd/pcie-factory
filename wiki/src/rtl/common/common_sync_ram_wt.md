# common_sync_ram_wt MAS

`common_sync_ram_wt` is the single-port synchronous RAM with write-through
reads: like [`common_sync_ram`](common_sync_ram.md), except that an edge
that writes shows the new word. Like every shared block it has no HAS, and
this page owns its interface, behavior and structure. The [family
decisions](common_sync_ram.md#decisions) apply.

## Interfaces

The ports are those of [`common_sync_ram`](common_sync_ram.md#interfaces):

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock. |
| `wr_en_i` | in | 1 | `clk_i` | Write the selected lanes of word `addr_i` at this edge. |
| `wr_lanes_i` | in | `LANES` | `clk_i` | Lane enables. |
| `addr_i` | in | `ADDR_W` | `clk_i` | Word read at every edge, and written when `wr_en_i` is high. |
| `wr_data_i` | in | `WIDTH` | `clk_i` | Write data. |
| `rd_data_o` | out | `WIDTH` | `clk_i` | The word `addr_i` after the last edge, the written lanes included. |

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Word width in bits. |
| `ADDR_W` | `int unsigned` | 4 | 1 or more | Depth is `2**ADDR_W` words. |
| `LANES` | `int unsigned` | 1 | 1 or more, dividing `WIDTH` | Number of equal write lanes. |

Illegal values stop elaboration through `COMMON_ASSERT_PARAM` blocks
`g_width_check`, `g_addr_w_check` and `g_lanes_check` with the message
`common_sync_ram_wt: <rule>`.

## Behavior

- **Read:** at every rising edge `rd_data_o` takes the word at `addr_i`
  with this edge's write applied: lanes enabled by `wr_lanes_i` (with
  `wr_en_i` high) come from `wr_data_i`, the others from the stored word.
  One-edge latency.
- **Write:** as in [`common_sync_ram`](common_sync_ram.md#behavior).
- **Contents:** a lane is undefined until written. No reset.

## Microarchitecture

- **Storage and ports:** `mem_q` with one `COMMON_MEM_WRITE_LANES` and one
  `COMMON_MEM_READ` into `rd_old_q`, the read-first template of
  [`common_sync_ram`](common_sync_ram.md#microarchitecture).
- **Bypass:** `wr_hit` (the written lanes, `wr_lanes_i` and `wr_en_i`) and
  `wr_data_i` are registered beside the RAM (`wr_hit_q`, `wr_data_q`,
  `COMMON_DFF`). `wr_mask` widens `wr_hit_q` to bits, and `rd_data_o` takes
  `wr_data_q` under the mask and `rd_old_q` elsewhere. A single port
  always writes the word it reads, so no address compare is needed. Cost:
  `WIDTH + LANES` flops beside the RAM.
- **Inference shape:** the RAM keeps the old-data template; the bypass is
  fabric. A tool that offers a new-data read-during-write mode is not
  relied on.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Explicit bypass registers, RAM in old-data mode | Same behavior in every tool, and the RAM stays the base template. | A blocking-write, new-data template (tool-specific inference). |
| Per-lane merge | With lanes, only the written lanes are new. | Whole-word bypass (wrong for a partial write). |

Provenance: adapted from nand2anything `src/common/rtl/n2a_sync_ram_wt.sv`
(commit `085ef4b`), adapted with the owner's permission.

## Timing

One clock, no reset. `rd_data_o` is an AND-OR merge of three registers.

## Files

| File | Holds |
| --- | --- |
| [`common_sync_ram_wt.sv`](../../../../src/rtl/common/common_sync_ram_wt.sv) | The module. |
| [`common_sync_ram_wt.rtl.f`](../../../../src/rtl/common/common_sync_ram_wt.rtl.f) | `-F common_macros.rtl.f`, then the module. |
