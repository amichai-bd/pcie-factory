# common_tlp_rx_track MAS

`common_tlp_rx_track` is the receive beat tracker of a TLP stream that
delivers whole TLPs, one DW per beat, and is never stalled (ready is 1).
For the beat taken in a cycle it says where that beat sits in its TLP: a
`sop`, a later beat, the last header DW, or header DW 0, 1 or 2. It counts
the TLP's beats, saturating at 63, and keeps the header size. Its
assertions state the whole-TLP contract of the stream. A user keeps its
own header registers (with the DW enables) and its own per-TLP state. Like
every shared block it has no HAS, and this page owns its interface,
behavior and structure. It moved here from `tl`'s receive parser
([#400](https://github.com/amichai-bd/pcie-factory/issues/400)) so that
`rc_tl` reuses it instead of copying it
([tl MAS, receive parser](../tl/MAS.md#receive-parser)).

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock; every change is at its rising edge. |
| `rst_i` | in | 1 | `clk_i` | Active-high synchronous reset: no TLP is open after an edge with `rst_i` high. The assertions are off while it is high. |
| `take_i` | in | 1 | `clk_i` | A beat is taken at this edge (the stream's valid, gated as the user needs). |
| `sop_i` | in | 1 | `clk_i` | With `take_i`: the beat starts a TLP. Ignored without it. |
| `eop_i` | in | 1 | `clk_i` | With `take_i`: the beat ends its TLP. Ignored without it. |
| `hdr4_i` | in | 1 | `clk_i` | With a `sop` beat taken: the TLP has a 4-DW header (Fmt bit 0, [TLP formats](../../arch/tl/TLP.md#common-fields); on a stream with header byte 0 in bits 7:0 it is data bit 5). Ignored on other beats. |
| `pos_o` | out | `common_pkg::t_tlp_rx_pos` | `clk_i` | Where the beat taken sits, and the state before it (below). |

`pos_o` fields ([`common_pkg`](common_pkg.md#contents)):

| Field | Width | Meaning |
| --- | --- | --- |
| `sop` | 1 | `take_i && sop_i`. |
| `mid` | 1 | `take_i && !sop_i` with a TLP open: a later beat of it. |
| `hdr_last` | 1 | `mid` and `beat == hdr_end`: the TLP's last header DW. |
| `en_dw` | 3 | Bit 0: `sop` (header DW 0); bit 1: `mid` at beat 1; bit 2: `mid` at beat 2. The enables of a user's header DW registers. |
| `busy` | 1 | A TLP is open: its `sop` was taken and its `eop` not yet. |
| `hdr4` | 1 | The open (or last) TLP has a 4-DW header. |
| `beat` | 6 | The open (or last) TLP's beats taken before this cycle, saturating at 63: the index of a `mid` beat within its TLP. |
| `hdr_end` | 6 | 3 when `hdr4`, else 2: the beat index of the last header DW. |

`busy`, `hdr4`, `beat` and `hdr_end` come from flops; `sop`, `mid`,
`hdr_last` and `en_dw` are combinational from the inputs and the flops.

## Parameters

None. The beat count is 6 bits: a TLP of more than 63 beats keeps
`beat` at 63.

## Behavior

- **A sop beat** opens a TLP: after its edge `busy` is 1, `beat` is 1 and
  `hdr4` is `hdr4_i`.
- **A later beat** (`mid`) adds 1 to `beat`, up to 63, where it stays.
- **An eop beat** closes the TLP: after its edge `busy` is 0. `beat` and
  `hdr4` keep their values until the next `sop`.
- **No take:** nothing changes, whatever `sop_i`, `eop_i` and `hdr4_i`
  are.
- **Reset:** clears `busy`, `beat` and `hdr4`.
- **Whole-TLP contract** (assertions, off while `rst_i` is high):
  `a_beat_outside_tlp`, a beat taken that is not a `sop` with no TLP open;
  `a_sop_inside_tlp`, a `sop` taken with a TLP open; `a_short_tlp`, an
  `eop` on the `sop` beat or before the last header DW (`beat < hdr_end`).

## Microarchitecture

- **State:** `st_q`, a `common_pkg::t_tlp_rx_trk` (`busy`, `beat`,
  `hdr4`), one `COMMON_DFF_RST`.
- **Next state:** a `sop` sets `busy`, `beat = 1` and `hdr4`; a `mid`
  beat takes `beat_inc` (`beat + 1`, or 63 at 63); then an `eop` taken
  clears `busy`, after either.
- **Outputs:** `pos_o` is assembled from these terms; `hdr_end` is
  `st_q.hdr4 ? 3 : 2`.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| A shared block with the position as one struct from `common_pkg` | `tl` and `rc_tl` both parse the same stream the same way; the layout rule puts shared logic in `common`, never a copy per IP ([#400](https://github.com/amichai-bd/pcie-factory/issues/400)). The struct keeps the related signals together across the boundary ([structs](../../../bkm/rtl.md#structs-at-interfaces)). | Copying the beat tracking and its assertions into `rc_tl`; one port per field. |
| The user keeps the header registers and its per-TLP state | `tl` captures DW 0 to 2 and `rc_tl` DW 0 and 1, each with its own decode and state (a completion flag, a push); the tracker owns only what both share. | Header registers in the tracker (an unused third DW in `rc_tl`). |
| `take_i` from the user, `hdr4_i` as one bit | A user may gate the take (`rc_tl` takes nothing in its link reset); the header size bit's place on the stream is the user's. | The stream's valid and data ports inside the tracker. |

## Timing

One clock, `clk_i`; reset synchronous. The position of a beat is
combinational in the cycle it is taken; the state changes at its edge.

## Files

| File | Holds |
| --- | --- |
| [`common_tlp_rx_track.sv`](../../../../src/rtl/common/common_tlp_rx_track.sv) | The module. |
| [`common_tlp_rx_track.rtl.f`](../../../../src/rtl/common/common_tlp_rx_track.rtl.f) | `-F common_macros.rtl.f` (the vocabulary), `-F common_pkg.rtl.f` (its types), then the module. |
