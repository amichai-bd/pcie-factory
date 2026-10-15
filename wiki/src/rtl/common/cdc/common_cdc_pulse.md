# common_cdc_pulse MAS

`common_cdc_pulse` is the pulse synchronizer: each `src_clk_i` cycle with
`src_pulse_i` high becomes one `dst_clk_i` cycle with `dst_pulse_o` high.
Use it for events (a request, a counter increment, a done strobe) that
cross between unrelated clocks and are at least two `dst_clk_i` periods
apart. Like every shared block it has no HAS; this page owns its interface,
behavior and structure.

It has no back-pressure or acknowledge: pulses closer than the spacing rule
can merge or be lost, and the cell does not report it. Bursts of events, or
events that carry data, go through the [async FIFO](common_cdc_fifo.md).

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `src_clk_i` | in | 1 | - | Source clock. |
| `src_rst_i` | in | 1 | `src_clk_i` | Active-high reset of the source side: synchronous, or asynchronous assertion with `SRC_ASYNC_RESET` 1. |
| `src_pulse_i` | in | 1 | `src_clk_i` | One event per cycle in which it is high at a rising edge. |
| `dst_clk_i` | in | 1 | - | Destination clock. |
| `dst_rst_i` | in | 1 | `dst_clk_i` | Active-high reset of the destination side: synchronous, or asynchronous assertion with `DST_ASYNC_RESET` 1. |
| `dst_pulse_o` | out | 1 | `dst_clk_i` | High for one `dst_clk_i` cycle per event; combinational from two destination flops. |

CDC declaration ([convention](../../../../bkm/rtl.md#clock-domain-crossings)):
`// cdc-cell: src=src_clk_i dst=dst_clk_i`.

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `SRC_ASYNC_RESET` | `bit` | 0 | 0 or 1 | 1: `toggle_q` enters reset asynchronously (`src_rst_i` must then come from a `COMMON_RESET_SYNC` on `src_clk_i`); 0: synchronously. |
| `DST_ASYNC_RESET` | `bit` | 0 | 0 or 1 | 1: the destination flops (`u_sync`, `seen_q`) enter reset asynchronously (`dst_rst_i` from a `COMMON_RESET_SYNC` on `dst_clk_i`); 0: synchronously. |

## Behavior

- **Reset:** reset both sides together: assert `src_rst_i` and `dst_rst_i`
  over an interval that contains at least one rising edge of each clock
  whose side resets synchronously; an asynchronous side needs no edge of
  its clock. Then `dst_pulse_o` is low and stays low until an event
  arrives. Resetting one side alone can create or drop one pulse.
- **Asynchronous side:** with `DST_ASYNC_RESET` 1, `dst_pulse_o` is low as
  soon as `dst_rst_i` rises, with no `dst_clk_i` edge, even in the middle
  of an output pulse; with `SRC_ASYNC_RESET` 1, `toggle_q` clears as soon
  as `src_rst_i` rises. An asynchronous reset must fall synchronously to
  its clock (a `COMMON_RESET_SYNC` output does); a pulse with no edge of
  that clock between its rise and fall also resets the side. Events
  sampled after the release cross as below.
- **Transfer:** an event sampled at a `src_clk_i` edge gives `dst_pulse_o`
  high for exactly one `dst_clk_i` cycle, starting after the second (or,
  with late resolution or a coincident edge, the third) `dst_clk_i` rising
  edge at or after that `src_clk_i` edge. Events come out in order, one
  output pulse per input event.
- **Spacing rule** (the user's obligation): consecutive events are at least
  two `dst_clk_i` periods apart in time. A source slower than that may
  pulse on consecutive `src_clk_i` cycles.

## Microarchitecture

Toggle synchronizer. In the source domain `toggle_q` flips on every event.
A [`common_cdc_sync2`](common_cdc_sync2.md) instance (`u_sync`,
instantiated directly with `ASYNC_RESET` set to `DST_ASYNC_RESET`) brings
the level into the destination domain; `seen_q` delays it by one cycle,
and `dst_pulse_o` is the XOR of the two, high for one cycle after every
change. `toggle_q` and `seen_q` are `COMMON_DFF_RST` forms, or
`COMMON_DFF_ARST_VAL` (reset 0) on a side whose parameter is 1 (generate
blocks `g_src_srst`/`g_src_arst`, `g_dst_srst`/`g_dst_arst`;
[RTL vocabulary](../../../../bkm/rtl.md#registers-and-memories)). Outside
`src/rtl/common/`, RTL instantiates this cell only through
`COMMON_PULSE_SYNC` or `COMMON_PULSE_SYNC_ARST` (the destination side
asynchronous) ([crossings](../../../../bkm/rtl.md#clock-domain-crossings)).

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Toggle and edge detect | Works for any clock ratio; one flop crosses. | Stretch the pulse in the source domain (depends on the ratio); four-phase handshake (lower rate, needs a busy flag). |
| Combinational `dst_pulse_o` from two flops | One cycle less latency; no glitches, since both inputs are flops of `dst_clk_i`. | A third register (one more cycle). |
| No busy or error flag | Every current use meets the spacing rule by construction. | A source-side busy (add when a consumer needs back-pressure). |
| One reset-style parameter per side, default synchronous | Each side follows its domain's reset style, so a domain on a `COMMON_RESET_SYNC` output keeps one style per net (#170); defaults keep every other user unchanged. | One parameter for both sides (a crossing into such a domain has a synchronous source). |

## Timing

- `toggle_q` to `u_sync` is the only crossing; it uses the synchronizer's
  timing exceptions ([sync2 timing](common_cdc_sync2.md#timing)).
- Latency: two or three `dst_clk_i` edges; throughput: one event per two
  `dst_clk_i` periods.
- Reset: an asynchronous side's reset input to its flops is a reset path,
  timed by the recovery and removal checks of the `COMMON_RESET_SYNC` that
  drives it.

## Files

| File | Holds |
| --- | --- |
| [`common_cdc_pulse.sv`](../../../../../src/rtl/common/cdc/common_cdc_pulse.sv) | The module. |
| [`common_cdc_pulse.rtl.f`](../../../../../src/rtl/common/cdc/common_cdc_pulse.rtl.f) | `-F ../common_macros.rtl.f` (the vocabulary), `-F common_cdc_sync2.rtl.f`, then the module. |
