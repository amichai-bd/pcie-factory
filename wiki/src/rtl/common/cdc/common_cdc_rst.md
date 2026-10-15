# common_cdc_rst MAS

`common_cdc_rst` is the reset synchronizer. It gives a clock domain a reset
that asserts asynchronously, as soon as the source reset asserts and with no
edge of the domain's clock, and releases synchronously to that clock after
`STAGES` edges. Use it when a domain's spec requires asynchronous assertion,
for example a domain whose clock may not run when reset arrives, or one
that must enter reset in a fixed time whatever its clock does. Like every
shared block it has no HAS; this page owns its interface, behavior and
structure.

It carries a reset only. A level or an event goes through
[`common_cdc_sync2`](common_cdc_sync2.md) or
[`common_cdc_pulse`](common_cdc_pulse.md).

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `dst_clk_i` | in | 1 | - | Destination clock; the release shifts on its rising edge. |
| `src_rst_i` | in | 1 | source (async) | Active-high source reset. Must be glitch-free: driven straight from a flop or from a reset input pin, with no logic between. |
| `dst_rst_o` | out | 1 | `dst_clk_i` (release) | Active-high destination reset; driven by the last flop. |

CDC declaration ([convention](../../../../bkm/rtl.md#clock-domain-crossings)):
`// cdc-cell: src=async dst=dst_clk_i`. The cell has no source clock port.
The structural CDC check treats `src_rst_i` as a `src=async` input, so it
must come straight from a flop or a top-level input port, and
`dst_rst_o` as a source in the `dst_clk_i` domain. RTL outside
`src/rtl/common/` instantiates the cell only through
`` `COMMON_RESET_SYNC(INST, Q, D, DST_CLK)`` (default `STAGES`; style rule
`cdc-cell-macro`, [crossings](../../../../bkm/rtl.md#clock-domain-crossings)):
`D` drives `src_rst_i`, `Q` is `dst_rst_o`.

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `STAGES` | `int unsigned` | 2 | 2 or more; less fails elaboration (`` `COMMON_ASSERT_PARAM``, `g_stages_check`) | Flops in the release chain: the release ends `STAGES` `dst_clk_i` edges after `src_rst_i` falls. |

## Behavior

- **Assertion.** When `src_rst_i` rises, `dst_rst_o` rises in the same
  time step through the flops' asynchronous set. No `dst_clk_i` edge is
  needed, and a pulse of any width asserts it, including one shorter than a
  `dst_clk_i` period that holds no edge. The assertion never passes through
  logic on `dst_rst_o`.
- **Hold.** While `src_rst_i` is high, `dst_rst_o` stays high whether
  `dst_clk_i` runs or not.
- **Release.** After `src_rst_i` falls, `dst_rst_o` falls at the
  `STAGES`-th rising `dst_clk_i` edge after the fall. It falls at
  `STAGES` + 1 when the first flop resolves late, because the fall came
  within the flops' recovery time before an edge. In zero-delay simulation
  a fall at the same time as an edge is a race. `dst_rst_o` changes only
  at that edge and comes from a flop, so the release has no glitch and is
  synchronous to `dst_clk_i`.
- **Re-assertion during release.** If `src_rst_i` rises again before the
  release ends, `dst_rst_o` stays high. The count restarts at the next
  fall.
- **Glitches on `src_rst_i`** are resets: any high pulse asserts
  `dst_rst_o`. That is why the input must come from a flop or a reset pin.
- **Power-up.** `dst_rst_o` is undefined until `src_rst_i` first asserts.
  The user asserts `src_rst_i` at power-up.

With the default `STAGES` = 2, the release ends within 3 `dst_clk_i`
cycles of the fall, even with late resolution. That meets a contract of
"asynchronous assertion, synchronous release within 3 cycles", such as the
`phy` RX-domain reset ([phy HAS](../../../arch/phy/HAS.md#clocks-and-resets)).

**Consumers.** Every consumer takes `dst_rst_o` asynchronously
([RTL BKM](../../../../bkm/rtl.md#reset-and-clocks), one net, one consumer
style): flops are `` `COMMON_DFF_ARST_VAL`` or `` `COMMON_DFF_ARST_EN``,
crossing cells use their asynchronous-reset forms on that side
(`` `COMMON_METAFLOP_ARST``, `` `COMMON_PULSE_SYNC_ARST``, the FIFO's
`*_ASYNC_RESET`), and assertions the `_ARST` forms, so that the whole
domain enters reset with no clock edge. A flop that samples `dst_rst_o` as
a synchronous reset only enters reset at its next edge, and then the
domain's reset is no longer asynchronous; Verilator lint reports the mix
as `SYNCASYNCNET` on `out_q`.

## Microarchitecture

The chain is `STAGES` flops on `dst_clk_i`, all set by `src_rst_i`: the
first `STAGES` - 1 are `sync_q`, one
`` `COMMON_DFF_ARST_VAL(sync_q, sync_d, dst_clk_i, src_rst_i, '1)``, and
the last is `out_q`, a
`` `COMMON_DFF_ARST_VAL(out_q, sync_q[STAGES-2], dst_clk_i, src_rst_i, 1'b1)``.
On each edge out of reset, `sync_d` (`sync_q` shifted up with a zero
entering bit 0) is loaded and `out_q` takes the top of `sync_q`.
`dst_rst_o` is `out_q`. Bit 0 of `sync_q` is the flop that may go
metastable on release; the others give it whole `dst_clk_i` periods to
resolve.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Asynchronous set of every stage | Assertion with no clock edge, from flops, so `dst_rst_o` has no logic and no glitch. All stages set, so a pulse shorter than a period still holds `dst_rst_o` high for the whole release. | `src_rst_i \| sync_q[last]` (logic on the output, and a short pulse never reaches the flops); synchronous assertion (needs clock edges). |
| The last stage, `out_q`, a register of its own | No flop of the cell reads the output net, so its consumers' asynchronous use is its only use and lint can tell a domain with one reset style from a mixed one. In one vector, the chain's own shift reads the output variable as flop data, which Verilator reports as `SYNCASYNCNET` whenever a consumer takes the output asynchronously (#170). | One `STAGES`-bit vector with the output as its top bit. |
| `STAGES` parameter, default 2 | The usual two flops meet a 3-cycle release contract; a fast clock that needs more MTBF raises it. | A fixed two flops. |
| Active-high reset | The repository default ([RTL BKM](../../../../bkm/rtl.md#reset-and-clocks)). | Active-low (`rst_ni`). |
| No synthesis attribute in the RTL | A vendor attribute ties one shared, flow-neutral source to one FPGA tool. The consuming IP's FPGA constraints mark the chain as a synchronizer, as they do for the other cells. | The source cell's `altera_attribute`. |

Provenance: adapted from nand2anything `src/common/rtl/n2a_reset_sync.sv`
(commit `085ef4b`, same owner, adapted with the owner's permission). The
cell and its ports are renamed to the `cdc-cell` convention, and the vendor
attribute is dropped.

## Timing

- `src_rst_i` to the flops' asynchronous set is a reset path with no
  timing relationship to `dst_clk_i`. Its assertion needs no constraint.
  The consuming IP's FPGA and ASIC constraints own the exception on it,
  and the recovery and removal checks of the release on `dst_clk_i`.
- `sync_q[i]` to `sync_q[i+1]`, and the top of `sync_q` to `out_q`, are
  direct flop-to-flop paths, each giving a whole `dst_clk_i` period to
  resolve.
- Assertion latency: none (same time step). Release latency: `STAGES`
  `dst_clk_i` edges, `STAGES` + 1 with late resolution.

## Files

| File | Holds |
| --- | --- |
| [`common_cdc_rst.sv`](../../../../../src/rtl/common/cdc/common_cdc_rst.sv) | The module. |
| [`common_cdc_rst.rtl.f`](../../../../../src/rtl/common/cdc/common_cdc_rst.rtl.f) | `-F ../common_macros.rtl.f` (the vocabulary), then the module; consumers include it with `-F`. |
