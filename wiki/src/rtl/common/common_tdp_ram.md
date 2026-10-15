# common_tdp_ram MAS

`common_tdp_ram` is the true dual-port synchronous RAM: two read/write
ports, `a` and `b`, on one clock, each like the port of
[`common_sync_ram`](common_sync_ram.md). Like every shared block it has no
HAS, and this page owns its interface, behavior and structure. The [family
decisions](common_sync_ram.md#decisions) apply.

## Interfaces

Each port `p` (`a` or `b`) has:

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock, shared by both ports. |
| `p_wr_en_i` | in | 1 | `clk_i` | Write the selected lanes of word `p_addr_i` at this edge. |
| `p_wr_lanes_i` | in | `LANES` | `clk_i` | Lane enables, as in [`common_sync_ram`](common_sync_ram.md#interfaces). |
| `p_addr_i` | in | `ADDR_W` | `clk_i` | Word read at every edge, and written when `p_wr_en_i` is high. |
| `p_wr_data_i` | in | `WIDTH` | `clk_i` | Write data. |
| `p_rd_data_o` | out | `WIDTH` | `clk_i` | The word `p_addr_i` held before the last edge. |

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Word width in bits. |
| `ADDR_W` | `int unsigned` | 4 | 1 or more | Depth is `2**ADDR_W` words. |
| `LANES` | `int unsigned` | 1 | 1 or more, dividing `WIDTH` | Number of equal write lanes, per port. |

Illegal values stop elaboration through `COMMON_ASSERT_PARAM` blocks
`g_width_check`, `g_addr_w_check` and `g_lanes_check` with the message
`common_tdp_ram: <rule>`.

## Behavior

- **Read:** at every rising edge each port's `p_rd_data_o` takes the word
  at its `p_addr_i` as it was before that edge: one-edge latency.
- **Write:** at a rising edge, each port with `p_wr_en_i` high writes the
  lanes `p_wr_lanes_i` enables of word `p_addr_i`.
- **Read during write:** every collision is read-first. A port that writes
  the word it reads, and a port that reads the word the other port writes,
  both return the old word; both see the new word from the next edge.
- **Both ports write one word:** allowed when their enabled lanes are
  disjoint; each lane takes its writer's data. Both ports writing the same
  lane of the same word at one edge is outside the contract: the stored
  value is undefined, and assertion `a_no_write_collision` stops a
  simulation that does it.
- **Contents:** words are undefined until written. No reset.

## Microarchitecture

- **Storage and ports:** `mem_q` with, per port, one
  `COMMON_MEM_WRITE_LANES` process at `p_addr_i` and one `COMMON_MEM_READ`
  process into `p_rd_data_o`, all on `clk_i`. Every read process samples
  `mem_q` before either write lands. The process order of the two writes
  is no promise of a winner.
- **Assertion:** `a_no_write_collision` (`COMMON_ASSERT_NO_RESET`, as the
  block has no reset): never both write enables with equal addresses and
  a common lane.
- **Inference shape:** the conventional true dual-port template.
  Expected, not yet measured: Quartus maps it to block RAM in true
  dual-port mode; whether its mixed-port read-during-write returns old
  data in that mode depends on the device family, and must be checked
  when a consumer is built. Yosys infers a memory with two write and two
  non-transparent read ports; the ASIC flow maps it to flops.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Double write of one lane is an assertion, not a priority | Block RAMs leave it undefined; a priority would not match a hardware RAM. | Port `a` wins. |
| Disjoint lanes of one word may be written by both ports | It follows from per-lane writes and costs nothing. | Forbid any same-word double write. |

Provenance: adapted from nand2anything `src/common/rtl/n2a_tdp_ram_portable.sv`
(commit `085ef4b`), adapted with the owner's permission.

## Timing

One clock, no reset. Each `p_rd_data_o` is a read register.

## Open items

| Item | Owner |
| --- | --- |
| Confirm old-data mixed-port read-during-write of the true dual-port shape in Quartus. | [#65](https://github.com/amichai-bd/pcie-factory/issues/65) |

## Files

| File | Holds |
| --- | --- |
| [`common_tdp_ram.sv`](../../../../src/rtl/common/common_tdp_ram.sv) | The module. |
| [`common_tdp_ram.rtl.f`](../../../../src/rtl/common/common_tdp_ram.rtl.f) | `-F common_macros.rtl.f`, then the module. |
