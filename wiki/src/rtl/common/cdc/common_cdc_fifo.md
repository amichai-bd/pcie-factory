# common_cdc_fifo MAS

`common_cdc_fifo` is the asynchronous FIFO: it carries `WIDTH`-bit words
from the `src_clk_i` domain to the `dst_clk_i` domain, with valid/ready
flow control on both sides and Gray-coded pointers crossing through
[`common_cdc_sync2`](common_cdc_sync2.md). It is the cell for any data, or
any burst of events, between unrelated clocks. Like every shared block it
has no HAS; this page owns its interface, behavior and structure.

It holds `2**ADDR_W` words. The read side also sees its fill and the word
after the oldest (first consumer: the pcs elastic buffer,
[#84](https://github.com/amichai-bd/pcie-factory/issues/84)). Depths that
are not a power of two, almost-full or almost-empty thresholds and a
write-side fill are out of scope until a consumer needs them.

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `src_clk_i` | in | 1 | - | Source (write) clock. |
| `src_rst_i` | in | 1 | `src_clk_i` | Active-high reset of the source side: synchronous, or asynchronous assertion with `SRC_ASYNC_RESET` 1. |
| `src_valid_i` | in | 1 | `src_clk_i` | A word is offered. |
| `src_ready_o` | out | 1 | `src_clk_i` | Not full: the offered word is taken at the edge when both are high. Does not depend on `src_valid_i`. |
| `src_data_i` | in | `WIDTH` | `src_clk_i` | The offered word. |
| `dst_clk_i` | in | 1 | - | Destination (read) clock. |
| `dst_rst_i` | in | 1 | `dst_clk_i` | Active-high reset of the destination side: synchronous, or asynchronous assertion with `DST_ASYNC_RESET` 1. |
| `dst_valid_o` | out | 1 | `dst_clk_i` | Not empty: `dst_data_o` is the oldest word. Does not depend on `dst_ready_i`. |
| `dst_ready_i` | in | 1 | `dst_clk_i` | The word is taken at the edge when both are high. |
| `dst_data_o` | out | `WIDTH` | `dst_clk_i` | Oldest word (first-word fall-through); undefined while `dst_valid_o` is low. |
| `dst_fill_o` | out | `ADDR_W+1` | `dst_clk_i` | Read-side fill: the words whose write has crossed and that are not yet read, 0 to `2**ADDR_W`. Non-zero exactly when `dst_valid_o` is high. |
| `dst_next_data_o` | out | `WIDTH` | `dst_clk_i` | The word after the oldest; undefined while `dst_fill_o` is below 2. |

CDC declaration ([convention](../../../../bkm/rtl.md#clock-domain-crossings)):
`// cdc-cell: src=src_clk_i dst=dst_clk_i`.

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 8 | 1 or more | Word width. |
| `ADDR_W` | `int unsigned` | 2 | 1 or more; less is an elaboration-time `$fatal` that lint and the simulation build report (`COMMON_ASSERT_PARAM` block `g_addr_w_check`) | Depth is `2**ADDR_W` words. |
| `SRC_ASYNC_RESET` | `bit` | 0 | 0 or 1 | 1: the source pointers (`wbin_q`, `wgray_q`) and the read-pointer synchronizer `u_sync_rptr` enter reset asynchronously (`src_rst_i` must then come from a `COMMON_RESET_SYNC` on `src_clk_i`); 0: synchronously. |
| `DST_ASYNC_RESET` | `bit` | 0 | 0 or 1 | The same for the destination side: `rbin_q`, `rgray_q` and `u_sync_wptr`, on `dst_rst_i`. |

## Behavior

- **Reset:** reset both sides together: assert `src_rst_i` and `dst_rst_i`
  over an interval that contains at least one rising edge of each clock
  whose side resets synchronously; an asynchronous side needs no edge of
  its clock. The FIFO is then empty: `dst_valid_o` low, `src_ready_o`
  high. Words queued before the reset are discarded. Resetting one side
  alone while the FIFO holds words is undefined.
- **Asynchronous sides:** with both parameters 1, the FIFO is empty
  (`dst_valid_o` low, `dst_fill_o` 0) and not full (`src_ready_o` high) as
  soon as both resets are high, with no clock edge (the clocks may be
  stopped). With one parameter 1, that side's pointer and synchronizer
  clear at once and the other side's at its next edge in reset. An
  asynchronous reset must fall synchronously to its clock (a
  `COMMON_RESET_SYNC` output does); a pulse with no edge of that clock
  between its rise and fall also resets the side.
- **Mixed reset styles:** with one side asynchronous and the other
  synchronous, the synchronous side's reset must already be high when the
  asynchronous side's reset rises, and stay high over at least one edge
  of its clock; otherwise the synchronous side can see the other side's
  pointer drop to zero while it still runs and read or write past it. A
  user meets this by deriving the asynchronous reset from the synchronous
  one through a `COMMON_RESET_SYNC` (the pcs does: its RX reset comes from
  `rst_i`, which also resets the read side).
- **Offers during reset are dropped:** `src_ready_o` is high while
  `src_rst_i` is asserted (both pointers are zero), so a word offered then
  completes a handshake and is lost. Hold `src_valid_i` low in reset, or
  share the reset with the source.
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
- **Read-side fill:** `dst_fill_o` is the synchronized write pointer less
  the read pointer. It is conservative like `dst_valid_o`: it counts a word
  two or three `dst_clk_i` edges after the edge that wrote it, never
  before, and drops in the cycle after a read. It never exceeds the words
  written and not read.
- **Look-ahead:** while `dst_fill_o` is 2 or more, `dst_next_data_o` is the
  second-oldest word; both are read without popping.
- Any clock ratio works. Sustained throughput needs a depth that covers the
  flag round trip (a pointer crossing each way); a shallow FIFO stalls
  between bursts.

## Microarchitecture

- **Storage:** `mem_q`, `2**ADDR_W` words, written in the source domain
  by one `COMMON_MEM_WRITE` port at `waddr` (no reset) and read
  combinationally at `raddr`. The pointer registers are `COMMON_DFF_RST`
  forms, or `COMMON_DFF_ARST_VAL` (reset 0) on a side whose parameter is 1
  (generate blocks `g_src_srst`/`g_src_arst`, `g_dst_srst`/`g_dst_arst`;
  [RTL vocabulary](../../../../bkm/rtl.md#registers-and-memories)).
- **Pointers:** each side keeps an `ADDR_W+1`-bit binary pointer
  (`wbin_q`, `rbin_q`) for addressing and a registered Gray copy
  (`wgray_q`, `rgray_q`). The extra bit tells full from empty.
- **Crossings:** `u_sync_wptr` brings `wgray_q` into the destination
  domain and `u_sync_rptr` brings `rgray_q` into the source domain, both
  [`common_cdc_sync2`](common_cdc_sync2.md), instantiated directly with
  `ASYNC_RESET` set to the parameter of the side that reads them. A Gray pointer changes one bit
  per update, as the synchronizer requires, and the registered Gray copy
  never glitches.
- **Fill:** the synchronized write pointer `dst_wgray`, converted to binary
  (`dst_wbin`, an XOR chain), less `rbin_q`. `dst_next_data_o` reads
  `mem_q` at `raddr + 1`.
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
| One reset-style parameter per side, default synchronous | Each side follows its domain's reset style, so a domain on a `COMMON_RESET_SYNC` output keeps one style per net and its pointers reset even with its clock stopped (#170); defaults keep every other user unchanged. | One parameter for both sides (the two domains' resets differ). |
| Read-side fill and look-ahead from the cell's own synchronized pointer | One crossing serves the flag, the fill and the look-ahead, so they always agree. A consumer that needs a fill (the pcs elastic buffer) does not duplicate the pointer crossing. | A second Gray counter through `common_cdc_sync2` in the consumer. |

## Timing

- The crossings are `wgray_q` to `u_sync_wptr` and `rgray_q` to
  `u_sync_rptr`, with the synchronizer's
  [timing exception](common_cdc_sync2.md#timing): the pointer bits must
  arrive with less skew than one period of the capturing clock.
- `mem_q` to `dst_data_o` and `dst_next_data_o` are paths between domains
  too: a word is read only after its pointer has crossed, at least two
  `dst_clk_i` edges after it was written, so it is stable when sampled.
  Constrain them like the pointers.
- `dst_fill_o` is a Gray-to-binary conversion and a subtraction of
  `dst_clk_i` registers.
- Within each domain, the flag logic is a compare of two registered
  vectors.
- Reset: an asynchronous side's reset input to its pointers and
  synchronizer is a reset path, timed by the recovery and removal checks
  of the `COMMON_RESET_SYNC` that drives it.

## Files

| File | Holds |
| --- | --- |
| [`common_cdc_fifo.sv`](../../../../../src/rtl/common/cdc/common_cdc_fifo.sv) | The module. |
| [`common_cdc_fifo.rtl.f`](../../../../../src/rtl/common/cdc/common_cdc_fifo.rtl.f) | `-F ../common_macros.rtl.f` (the vocabulary), `-F common_cdc_sync2.rtl.f`, then the module. |
