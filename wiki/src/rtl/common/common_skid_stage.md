# common_skid_stage MAS

`common_skid_stage` is a registered output stage with a skid slot on one
clock. It sits between an upstream that offers beats and a downstream that
takes them with a valid/ready handshake. Every downstream output comes from
a flop, and the downstream ready reaches no upstream logic. While
downstream takes a beat every cycle, beats still flow with no gap. Like
every shared block it has no HAS, and this page owns its interface,
behavior and structure. It moved here from `tl`'s transmit arbiter
([#400](https://github.com/amichai-bd/pcie-factory/issues/400)) so that
`rc_tl` reuses it instead of copying it
([tl MAS, transmit arbiter](../tl/MAS.md#transmit-arbiter)).

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `clk_i` | in | 1 | - | Clock; every change is at its rising edge. |
| `rst_i` | in | 1 | `clk_i` | Active-high synchronous reset: the stage is empty after an edge with `rst_i` high. |
| `in_valid_i` | in | 1 | `clk_i` | Upstream offers `in_data_i`. |
| `in_ready_o` | out | 1 | `clk_i` | The stage takes the upstream beat at this edge if `in_valid_i` is high; a flop (the skid slot empty). |
| `in_data_i` | in | `WIDTH` | `clk_i` | The upstream beat. |
| `out_valid_o` | out | 1 | `clk_i` | A beat is offered downstream; a flop. |
| `out_ready_i` | in | 1 | `clk_i` | Downstream takes the offered beat at this edge. |
| `out_data_o` | out | `WIDTH` | `clk_i` | The offered beat, meaningful while `out_valid_o` is high; a flop. |
| `out_take_o` | out | 1 | `clk_i` | `out_valid_o && out_ready_i`: the offered beat leaves at this edge. |
| `out_new_o` | out | 1 | `clk_i` | The offered beat is in its first cycle on `out_*`: it was not offered and left waiting at the last edge. A user that must sample something once per beat, as the beat is first offered, reads it (`tl` captures its ID there). |

Only `out_take_o` reads an input combinationally (`out_ready_i`).

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 32 | 1 or more | Beat width in bits. A user passes its beat struct as the vector (`$bits` of it). |

An illegal `WIDTH` stops elaboration with `common_skid_stage: WIDTH must
be at least 1` (`COMMON_ASSERT_PARAM`).

## Behavior

- **Two beats, in order:** the stage holds at most two beats: `out`, the
  oldest, offered downstream, and `skid` behind it. Beats leave in the
  order they were taken; none is lost or repeated.
- **Ready:** `in_ready_o` is high while `skid` is empty, so while the stage
  holds fewer than two beats.
- **At each edge:** when `out` is free (empty, or taken at this edge) it
  loads the `skid` beat if there is one, else the upstream beat if one is
  taken (else it is empty). When `out` is held (offered and not taken), a
  taken upstream beat goes to `skid`.
- **No gap:** while downstream takes every beat, `skid` stays empty and a
  beat taken at edge E is offered after edge E, so a stream of beats
  passes one per cycle with one cycle of latency.
- **Held beat:** an offered beat does not change until taken.
- **New beat:** `out_new_o` is high in the first cycle a beat is offered,
  and low in every later cycle while it is held.
- **Reset:** `rst_i` empties the stage (both beats dropped) and clears
  the held flag. The `out` and `skid` registers reset to 0 too, so
  `out_data_o` is 0 after reset.
- **Assertions:** `a_out_held` (an offered beat held at an edge is
  unchanged after it, valid included) and `a_skid_unheld` (`skid` holds a
  beat only while `out` holds one). Both are invariants of the stage's own
  state that no input can break.

## Microarchitecture

- **Registers:** `out_valid_q`, `out_q`, `skid_valid_q`, `skid_q` and
  `held_q`, each a `COMMON_DFF_RST`. `out_q` and `skid_q` are `WIDTH`
  bits wide, so they are flat vectors, not a struct
  ([structs](../../../bkm/rtl.md#structs-at-interfaces)).
- **Load:** with `out` free, `out_q` takes `skid_q` (and `skid_valid_q`
  clears; `skid_q` keeps its bits) or `in_data_i`, with `out_valid_q` set
  to the upstream take. When the upstream beat is not taken, `out_q`
  still loads `in_data_i` with `out_valid_q` low, so `out_data_o` between
  beats is the upstream's last offer; no user reads it there.
- **Held:** `held_q` takes `out_valid_q && !out_ready_i`;
  `out_new_o = out_valid_q && !held_q`.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| A shared block, generic in `WIDTH`, with no stream fields of its own | `tl` and `rc_tl` both need it with different side fields (an ID-fill flag, a read's tag, a class); the layout rule puts shared logic in `common`, never a copy per IP ([#400](https://github.com/amichai-bd/pcie-factory/issues/400)). | Keeping it in `tl` and copying it into `rc_tl`; a stream-specific block with fixed data, `sop` and `eop` fields. |
| `out_new_o` instead of the user's ID capture inside the stage | The ID fill is `tl`'s; the stage only says when a beat is first offered. | Moving `tl`'s ID register into the stage. |
| Ready from the skid flop | `out_ready_i` reaches only the stage, so the downstream's ready path is one flop deep ([#295](https://github.com/amichai-bd/pcie-factory/issues/295)). | A plain register with the ready passed through (a combinational ready path). |

## Timing

One clock, `clk_i`; reset synchronous. Upstream to `out_*`: one edge.
`out_valid_o`, `out_data_o`, `out_new_o` and `in_ready_o` come from flops;
`out_take_o` is one AND of a flop and `out_ready_i`.

## Files

| File | Holds |
| --- | --- |
| [`common_skid_stage.sv`](../../../../src/rtl/common/common_skid_stage.sv) | The module. |
| [`common_skid_stage.rtl.f`](../../../../src/rtl/common/common_skid_stage.rtl.f) | `-F common_macros.rtl.f` (the vocabulary), then the module. |
