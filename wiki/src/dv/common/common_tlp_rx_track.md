# common_tlp_rx_track DV plan

## Strategy

The shared unit drives the tracker through
[`tb/common_tlp_rx_track_fixture.sv`](../../../../src/dv/common/tb/common_tlp_rx_track_fixture.sv),
which splits `pos_o` into one port per field
([`test_tlp_rx_track.py`](../../../../src/dv/common/tests/test_tlp_rx_track.py)).
Inputs change at falling edges. In every cycle, once they settle, every
field is compared with the cycle model
[`common_tlp_rx_track_model.py`](../../../../src/dv/common/models/common_tlp_rx_track_model.py),
written from the [MAS behavior](../../rtl/common/common_tlp_rx_track.md#behavior)
as beat numbers within a TLP, with its own pytest unit tests. In idle
cycles inside and between TLPs, `sop_i`, `eop_i` and `hdr4_i` are driven
at random with `take_i` low, and `hdr4_i` at random on every beat other
than a `sop`, so a tracker that reads them there fails.

- **Directed (`contract`):** a 3-DW and a 4-DW header alone; TLPs with
  data back to back; gaps inside a TLP; a TLP of 70 beats (the count
  saturates at 63); a reset inside a TLP, then a new one.
- **Random (`random_tlps`):** 300 whole TLPs of 3 to 80 beats (header
  alone, up to 40, or 41 to 80), both header sizes, with gaps inside
  TLPs, idle cycles between them, and a rare reset inside a TLP. A guard
  requires saturated beats.
- **Coverage:** covergroup `common_tlp_rx_track`: each beat's kind (`sop`,
  header DW 1 to 3, data, saturated, a gap inside a TLP), the header size
  crossed with the TLP length (header only, short, long, saturating), the
  next TLP back to back or after a gap, and resets inside or outside a
  TLP.
- **Framing assertions, by hand:** a regression cannot pass on a
  simulator stop, so the negative cases `framing_beat_outside`,
  `framing_sop_inside`, `framing_short_sop` and `framing_short_hdr4` in
  the same module are in no level list. Each breaks the whole-TLP contract
  once and passes only if its assertion stops the run; the PR runs them
  from a scratch copy whose level list names them, as the mutant runs
  do.
- **In its consumers:** `tl`'s receive parser uses this block, so the
  [`tl` DV plan](../tl/DV_PLAN.md#plan) entries exercise it under every
  received TLP and under the `core` entries.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Beat numbers, header DW enables, last header DW, saturation, reset](../../rtl/common/common_tlp_rx_track.md#behavior) | `tlp_rx_track_contract` | 0 | 1 |
| The same under random TLPs and gaps, against the model | `tlp_rx_track_random` / `tlp_rx_track_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (489432863 and 69564069), pinned; 300 TLPs |
| [Whole-TLP contract assertions](../../rtl/common/common_tlp_rx_track.md#behavior) | `framing_*` (no level) | - | by hand, from a scratch level list |
| Model rules | (model unit test) | - | `python3 -m pytest src/dv/common/models` |

**Level placement**
([#389](https://github.com/amichai-bd/pcie-factory/issues/389)): the full
level 1 of every IP, which `ci-gate` runs for a change to a shared
package, was over its 1200 s CPU budget. `tlp_rx_track_random` runs its
first seed in level 1; its further seeds run pinned and unchanged in its
level 2 `_sweep`, each seed with its coverage instrumentation, so the
merged level 0 to 2 runs are the same runs. The mutation evidence on this
page names no seed for it, and was not rerun at the first seed alone.

Mutation evidence ([#400](https://github.com/amichai-bd/pcie-factory/issues/400)):
the PR records the mutants of the moved tracker and which entries, here
and in `tl`, catch them.
