# pcie_pcs_align DV plan

## Strategy

- **Reference model:** [`pcie_pcs_align_model.py`](../../../../src/dv/pcs_align/models/pcie_pcs_align_model.py),
  a cycle model of the aligner written from the [MAS](../../rtl/pcs_align/MAS.md#behavior)
  and the [receive path](../../arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock),
  not from the RTL. It imports the pcs [architecture model](../../arch/pcs/HAS.md#architecture-model)
  from `src/arch/pcs` for the 8b/10b code, the encoder and the relock
  constants (`ERR_LIMIT` 3, `ERR_DECAY_WORDS` 8), and adds the traffic
  (TS1, FTS and SKP ordered sets, packets) and a channel with placed bit
  slips (a bit dropped or repeated), bit flips, electrical idle and noise.
  Its pytest unit tests run without a simulator:
  `python3 -m pytest src/dv/pcs_align/models`.
- **Tests:** [`tests/test_pcs_align.py`](../../../../src/dv/pcs_align/tests/test_pcs_align.py)
  drives `pcie_pcs_align` directly (no SV testbench; `pcs_align.dv.f` is
  empty). Three checkers run every cycle:
  - **Scoreboard:** `lock_o`, `gen_o` and `sym_o` against the cycle model.
  - **Alignment oracle:** every received bit carries its transmitted bit
    index, so each locked output symbol must be one whole transmitted
    symbol, except between a slip and the first lock after the relock that
    answers it.
  - **Closed relock loop:** the pcs top's [relock rule](../../arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock)
    (the error counter, its `ERR_LIMIT` and the re-arm on the lock
    generation) runs in the testbench on the output words 8 cycles late
    (buffer and decoder) and pulses `relock_i` 2 to 5 cycles later (the
    pulse crossing). Each slip that hits a locked aligner must be answered
    within the HAS [bit-slip target](../../arch/pcs/HAS.md#targets) of 32
    cycles, and each request must follow a misaligned symbol, so isolated
    bit errors never cost lock.
  Random tests take cocotb's seed, log it, and fail when the case they
  target never happened (a same-cycle relock, a lock, an answered slip).
- **Functional coverage:** `pcs_align_lock` (the comma position 0 to 19 in
  the window, crossed with its form, at each lock: 40 bins);
  `pcs_align_relock` (lock from hunting, same-cycle relock, relock to
  hunting, idle drop, relock while hunting); `pcs_align_impairments`
  (slips in TS1, FTS and packet traffic by direction, on the COM and on a
  SKP of a SKP ordered set, close slip pairs; isolated flips in TS1 and
  packets). Floors: `pcs_align_relock` 100 at L0,
  `pcs_align_impairments` 100 at L1, and at L2 line 100, toggle 99 and
  `pcs_align_lock` 100.
- **Coverage reached** (L0 to L2, all seeds): line 100% (27/27), toggle
  99.1% (424/428), functional 100% (78/78). The four toggle points left
  are bits 4 and 5 of the 6-bit window index `base`, which the boundary
  (0 to 9) never sets.
- **Mutation evidence** (scratch RTL edits, L0 and L1 rerun, then
  reverted): searching the highest offset instead of the lowest fails
  `no_false_lock` (both seeds); not flipping the generation fails every
  entry; ignoring `relock_i` fails `controls`, `no_false_lock` and
  `slips`; hunting with an idle word in the window fails `controls` (a
  lock one cycle early after a one-word idle pulse).
- **Out of scope:** the error counter and the relock pulse crossing are the
  pcs top's ([#85](https://github.com/amichai-bd/pcie-factory/issues/85));
  here they are a testbench model. Decode, the elastic buffer and clock
  offsets belong to the sibling sub-IPs and the top.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Lock from every bit offset, both comma forms](../../rtl/pcs_align/MAS.md#behavior) | `lock_offsets` | 0 | 1; 40 runs (20 offsets, start RD - and +), one lock each, every locked symbol whole, no relock |
| [Loss of lock, same-cycle relock and the generation bit, idle, reset](../../rtl/pcs_align/MAS.md#behavior) | `controls` | 0 | 1; open loop: relock while hunting, relock at all eight phases of the TS1 COM period, idle drop, one-word idle pulses at eight phases, reset while locked |
| [Relock after bit slips: TS1, FTS and packet traffic, inside SKP ordered sets, slip pairs; isolated errors keep lock](../../rtl/pcs_align/MAS.md#targets) | `slips` | 1 | 2; closed loop, slips detected within 32 cycles, at least one same-cycle relock re-armed through the generation bit |
| [No false lock](../../rtl/pcs_align/MAS.md#targets) | `no_false_lock` | 1 | 2; encoded random data at all 20 offsets never locks; idle noise never locks; unflagged noise locks falsely and the relock rule recovers the true boundary |
| [Long mixed traffic with slips, errors and idle gaps](../../rtl/pcs_align/MAS.md#targets) | `long_random` | 2 | 8; 40 segments, closed loop, every check on |

Every [interface](../../rtl/pcs_align/MAS.md#interfaces) port is exercised
by every entry. Every feature in the MAS has a row; there are no gaps.
