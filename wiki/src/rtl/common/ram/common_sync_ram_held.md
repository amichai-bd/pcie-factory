# common_sync_ram_held MAS

`common_sync_ram_held` is the single-port synchronous RAM with a read
enable: an enabled edge reads like [`common_sync_ram`](common_sync_ram.md),
and with the enable low the output keeps the last enabled result while the
address and the contents change. It is built on `common_sync_ram` and adds
no RAM; like every shared block it has no HAS, and this page owns its
interface, behavior and structure. The [family
decisions](common_sync_ram.md#decisions) apply.

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock; every access is at its rising edge. |
| `wr_en_i` | in | 1 | `clk_i` | Write the selected lanes of word `addr_i` at this edge, whatever `rd_en_i` is. |
| `wr_lanes_i` | in | `LANES` | `clk_i` | Lane enables, as in [`common_sync_ram`](common_sync_ram.md#interfaces). |
| `addr_i` | in | `ADDR_W` | `clk_i` | Word read on an enabled edge, and written when `wr_en_i` is high. |
| `wr_data_i` | in | `WIDTH` | `clk_i` | Write data; only the enabled lanes are stored. |
| `rd_en_i` | in | 1 | `clk_i` | Read enable. |
| `rd_data_o` | out | `WIDTH` | `clk_i` | The word read at the last edge with `rd_en_i` high. |

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Word width in bits. |
| `ADDR_W` | `int unsigned` | 4 | 1 or more | Depth is `2**ADDR_W` words. |
| `LANES` | `int unsigned` | 1 | 1 or more, dividing `WIDTH` | Number of equal write lanes. |

Illegal values stop elaboration through `COMMON_ASSERT_PARAM` blocks
`g_width_check`, `g_addr_w_check` and `g_lanes_check` with the message
`common_sync_ram_held: <rule>`; the inner RAM repeats the checks under its
own name.

## Behavior

- **Enabled read:** at a rising edge with `rd_en_i` high, `rd_data_o`
  shows the word at `addr_i` as it was before that edge: one-edge latency.
- **Hold:** at a rising edge with `rd_en_i` low, `rd_data_o` keeps its
  value, while `addr_i` changes and while writes change any word,
  including the word it shows.
- **Write:** as in [`common_sync_ram`](common_sync_ram.md#behavior), and
  independent of `rd_en_i`.
- **Read during write:** an enabled read and a write of one word at one
  edge return the old word.
- **Contents:** words are undefined until written; `rd_data_o` is
  undefined until the first edge with `rd_en_i` high. No reset.

## Microarchitecture

- **Storage:** `u_ram`, a [`common_sync_ram`](common_sync_ram.md) that
  reads at every edge into `rd_raw`.
- **Qualifier:** `rd_en_q` (`COMMON_DFF`) is `rd_en_i` delayed one edge:
  high means `rd_raw` is a fresh enabled read. `rd_hold_q`
  (`COMMON_DFF_EN`, enable `rd_en_q`) captures `rd_raw` at the edge after
  each fresh read, so it always holds the last enabled result.
- **Output:** `rd_data_o` is `rd_raw` while `rd_en_q` is high, else
  `rd_hold_q`. No cycle is added; the cost is `WIDTH + 1` flops beside the
  RAM.
- **Inference shape:** the RAM is the plain single-port template, read at
  every edge (see the [base MAS](common_sync_ram.md#microarchitecture));
  the hold is fabric flops and a multiplexer, so it needs no read-enable
  support from the RAM block.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Hold in a register beside an always-reading RAM | The source's proven form: it needs no read enable on a single-port block RAM and keeps the inner RAM the base template. | `COMMON_MEM_READ_EN` on the RAM itself (relies on the block's read enable or clock enable in single-port mode). |

Provenance: adapted from nand2anything `src/common/rtl/n2a_sync_ram_held.sv`
(commit `085ef4b`), adapted with the owner's permission.

## Timing

One clock, no reset. `rd_data_o` comes through one 2:1 multiplexer after
the RAM output register and `rd_hold_q`.

## Files

| File | Holds |
| --- | --- |
| [`common_sync_ram_held.sv`](../../../../../src/rtl/common/ram/common_sync_ram_held.sv) | The module. |
| [`common_sync_ram_held.rtl.f`](../../../../../src/rtl/common/ram/common_sync_ram_held.rtl.f) | `-F ../common_macros.rtl.f`, `-F common_sync_ram.rtl.f`, then the module. |
