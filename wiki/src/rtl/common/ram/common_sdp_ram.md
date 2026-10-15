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
- **Inference shape:** the conventional simple dual-port template.
  Expected, not yet measured: Quartus maps it to block RAM in simple
  dual-port mode with old-data mixed-port read-during-write, and the read
  enable to the read port's read or clock enable; lanes map to byte
  enables as in the [base MAS](common_sync_ram.md#microarchitecture).
  Yosys infers a memory with one write port and one non-transparent
  synchronous read port with an enable; the ASIC flow maps it to flops.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| One module with a read enable | The source's two modules differ only in a tied-high enable; one block keeps one page and one test set. | The source's separate always-read wrapper. |

Provenance: adapted from nand2anything `src/common/rtl/n2a_sdp_ram_portable.sv`
(modules `n2a_sdp_ram_enabled` and `n2a_sdp_ram`, commit `085ef4b`),
adapted with the owner's permission.

## Timing

One clock, no reset. `rd_data_o` is the RAM's read register.

## Files

| File | Holds |
| --- | --- |
| [`common_sdp_ram.sv`](../../../../../src/rtl/common/ram/common_sdp_ram.sv) | The module. |
| [`common_sdp_ram.rtl.f`](../../../../../src/rtl/common/ram/common_sdp_ram.rtl.f) | `-F ../common_macros.rtl.f`, then the module. |
