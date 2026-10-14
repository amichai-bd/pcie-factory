# common_cdc_fifo MAS

`common_cdc_fifo` is the asynchronous FIFO: it carries `WIDTH`-bit words
from the `src_clk_i` domain to the `dst_clk_i` domain, with valid/ready
flow control on both sides and Gray-coded pointers crossing through
[`common_cdc_sync2`](common_cdc_sync2.md). It is the cell for any data, or
any burst of events, between unrelated clocks. Like every shared block it
has no HAS; this page owns its interface, behavior and structure.

It holds `2**ADDR_W` words; depths that are not a power of two, almost-full
or almost-empty thresholds and fill-level outputs are out of scope until a
consumer needs them.

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `src_clk_i` | in | 1 | - | Source (write) clock. |
| `src_rst_i` | in | 1 | `src_clk_i` | Synchronous, active-high reset of the source side. |
| `src_valid_i` | in | 1 | `src_clk_i` | A word is offered. |
| `src_ready_o` | out | 1 | `src_clk_i` | Not full: the offered word is taken at the edge when both are high. Does not depend on `src_valid_i`. |
| `src_data_i` | in | `WIDTH` | `src_clk_i` | The offered word. |
| `dst_clk_i` | in | 1 | - | Destination (read) clock. |
| `dst_rst_i` | in | 1 | `dst_clk_i` | Synchronous, active-high reset of the destination side. |
| `dst_valid_o` | out | 1 | `dst_clk_i` | Not empty: `dst_data_o` is the oldest word. Does not depend on `dst_ready_i`. |
| `dst_ready_i` | in | 1 | `dst_clk_i` | The word is taken at the edge when both are high. |
| `dst_data_o` | out | `WIDTH` | `dst_clk_i` | Oldest word (first-word fall-through); undefined while `dst_valid_o` is low. |

CDC declaration ([convention](../../../bkm/rtl.md#clock-domain-crossings)):
`// cdc-cell: src=src_clk_i dst=dst_clk_i`.

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Word width. |
| `ADDR_W` | `int unsigned` | 2 | 1 or more | Depth is `2**ADDR_W` words. |

## Behavior

- **Reset:** reset both sides together: assert `src_rst_i` and `dst_rst_i`
  over an interval that contains at least one rising edge of each clock.
  The FIFO is then empty: `dst_valid_o` low, `src_ready_o` high. Resetting
  one side alone while the FIFO holds words is undefined.
- **Order and capacity:** words come out in the order they went in, none
  lost or duplicated. With the reader stalled, exactly `2**ADDR_W` words are
  taken; then `src_ready_o` is low until a read is seen.
- **Local flags react at once:** `src_ready_o` drops in the cycle after the
  write that fills the FIFO, and `dst_valid_o` drops in the cycle after the
  read that empties it.
- **Remote updates arrive late:** the first word written into an empty
  FIFO raises `dst_valid_o` two or three `dst_clk_i` edges after the
  `src_clk_i` edge that wrote it; a read from a full FIFO raises
  `src_ready_o` two or three `src_clk_i` edges after the `dst_clk_i` edge
  that read it. Both flags are therefore conservative, never optimistic.
- Any clock ratio works. Sustained throughput needs a depth that covers the
  flag round trip (a pointer crossing each way); a shallow FIFO stalls
  between bursts.

## Microarchitecture

- **Storage:** `mem_q`, `2**ADDR_W` words, written in the source domain
  (no reset) and read combinationally at the read address.
- **Pointers:** each side keeps an `ADDR_W+1`-bit binary pointer
  (`wbin_q`, `rbin_q`) for addressing and a registered Gray copy
  (`wgray_q`, `rgray_q`). The extra bit tells full from empty.
- **Crossings:** `u_sync_wptr` brings `wgray_q` into the destination
  domain and `u_sync_rptr` brings `rgray_q` into the source domain, both
  [`common_cdc_sync2`](common_cdc_sync2.md). A Gray pointer changes one bit
  per update, as the synchronizer requires, and the registered Gray copy
  never glitches.
- **Flags:** empty when `rgray_q` equals the synchronized write pointer;
  full when `wgray_q` XOR the synchronized read pointer is `FULL_XOR` (the
  two top bits set, the rest clear).

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Gray pointers through two-flop synchronizers | The standard, proven structure; every crossing is a single-bit-change vector. | Handshake per word (slow); binary pointers with a capture handshake (more logic). |
| Flags compared in Gray code | No Gray-to-binary conversion on the flag path. | Binary compare after conversion. |
| Registered memory, combinational read | First-word fall-through with no extra latency; small depths fit in flops or distributed RAM. | Synchronous-read RAM (one more cycle and an output register stage). |
| Power-of-two depth only | Gray wrap is free. | Arbitrary depth (needs a modified Gray sequence). |

## Timing

- The crossings are `wgray_q` to `u_sync_wptr` and `rgray_q` to
  `u_sync_rptr`, with the synchronizer's
  [timing exception](common_cdc_sync2.md#timing): the pointer bits must
  arrive with less skew than one period of the capturing clock.
- `mem_q` to `dst_data_o` is a path between domains too: a word is read
  only after its pointer has crossed, at least two `dst_clk_i` edges after
  it was written, so it is stable when sampled. Constrain it like the
  pointers.
- Within each domain, the flag logic is a compare of two registered
  vectors.

## Files

| File | Holds |
| --- | --- |
| [`common_cdc_fifo.sv`](../../../../src/rtl/common/common_cdc_fifo.sv) | The module. |
| [`common_cdc_fifo.rtl.f`](../../../../src/rtl/common/common_cdc_fifo.rtl.f) | `-F common_cdc_sync2.rtl.f`, then the module. |
