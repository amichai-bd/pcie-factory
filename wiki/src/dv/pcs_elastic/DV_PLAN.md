# pcie_pcs_elastic DV plan

## Strategy

- **Reference model and checker:** [`pcie_pcs_elastic_model.py`](../../../../src/dv/pcs_elastic/models/pcie_pcs_elastic_model.py),
  written from the [MAS behavior](../../rtl/pcs_elastic/MAS.md#behavior)
  and the pcs [elastic buffer rules](../../arch/pcs/RECEIVE.md#elastic-buffer),
  not from the RTL. It imports the pcs [arch model](../../../../src/arch/pcs/pcie_pcs_arch.py)
  for the 8b/10b encoder, the partner's traffic (`make_stream`) and the
  buffer constants. `check()` is a stream scoreboard. The symbols that leave
  must be the symbols that entered, in order, with the same lock generation.
  The only allowed differences are:
  - one SKP removed or added per locked SKP ordered set, with its status on
    the COM's word. A removal may instead be reported on the next symbol's
    word only when the COM's word went out at a visible fill below 4, the
    only case in which that symbol can be unseen
    ([RxStatus table](../../arch/pcs/RECEIVE.md#decode-and-rxstatus)). The
    in-target runs also assert that this never happens;
  - an unlocked aligned word dropped by recentring;
  - the one unlocked symbol before a lock start, dropped
    ([lock start](../../rtl/pcs_elastic/MAS.md#behavior)); a locked symbol
    leaving unlocked right after unlocked ones is counted as a late lock
    start, which the lock-start tests require to be zero;
  - in the loss tests, words lost to overflow, each loss marked by the
    overflow flag;
  - a symbol leaving unlocked only next to a lock or generation change.

  Every add, remove and overflow flag must be explained, and every change
  must be reported. Its pytest unit tests check the checker's accepts and
  rejects, the traffic kinds, and that the RTL package's raw COM and SKP codes
  and the module's levels equal the arch model's, with no simulator:
  `python3 -m pytest src/dv/pcs_elastic/models`.
- **Tests:** [`tests/test_pcs_elastic.py`](../../../../src/dv/pcs_elastic/tests/test_pcs_elastic.py)
  drives `pcie_pcs_elastic` directly (no SV testbench; `pcs_elastic.dv.f`
  is empty) with two free-running clocks. `clk_i` has a 1 us period, so the
  ppm offset of `ser_rx_clk_i` is exact to 2 ppm in integer picoseconds, and
  the RX clock starts at a random phase. The writer sends one aligned word per
  RX cycle at the falling RX edge. The monitor samples every `clk_i` cycle,
  records each word with `rx_valid_o`, and checks the visible fill
  (`fill_o`). It checks 4 to 12 symbols from the start to the last word
  written. It also checks the start rule exactly: the first word goes out
  in the first cycle with a visible fill of 8 or more, and not in any cycle
  before it. The reset sequence checks that `ser_rx_rst_o` rises with
  `rst_i` and falls at the second RX edge after it. When the check fails, the
  test writes `elastic_dump.json` (input and output) into the test
  directory; `check(*load(path))` reproduces it.
- **Threshold checks:** every run of partner traffic checks each SKP ordered
  set against the thresholds.
  - **Removal.** The set's first SKP must be removed exactly when the write
    side saw fill high as the word holding it was written. The test rebuilds
    that value from the logged `fill_o`: `fill_high_q` registers fill ≥ 9 at
    a `clk_i` edge, and its synchronizer's first flop samples it two RX edges
    before that write. A clock-edge tie is skipped.
  - **Addition.** A SKP must be added exactly when the COM's word went out at
    a visible fill of 7 or less, unless the set had a removal. This applies
    at fill 4 or more, where the next symbol is always visible.
  - **Boundaries.** At ±600 ppm the read side's fill is even, since a
    leftover symbol comes only from an addition. So `thresholds_swing`
    alternates fast and slow phases (+3000 ppm with about 0.6% one-cycle
    write pauses every other 2000 cycles), with SKP sets every 48 to 96
    symbols. Removal decisions then fall at fills 8 and 9, and addition
    decisions at 7 and 8. The test fails unless both sides of each threshold
    occur. `thresholds_m600` also requires additions decided at 7 and 8.
- **Traffic:** partner streams from the arch model's rules. A SKP ordered
  set falls due every 1180 to 1538 symbols, waits for the end of a packet,
  and sends back to back with others that are due. The kinds are:
  - maximum TLPs (4122 bytes);
  - random TLP and DLLP sizes with SKP sets of 1 to 5 SKPs;
  - `stress`: sizes that strand corrections (4122, 3070, 1534 and 2 bytes);
  - `short_burst`: a packet just under two intervals, so one set too few
    follows it, then a maximum TLP. This is the carried-forward reviewer
    case from the [#73 sign-off](https://github.com/amichai-bd/pcie-factory/pull/123#issuecomment-6185545402);
  - `dense`: SKP sets every 24 to 64 symbols, for the corner tests;
  - electrical-idle gaps of unlocked noise, with lock retaken (new
    generation) on the first COM after them;
  - same-cycle relocks: the generation flips while lock stays.
- **Seeds:** every random choice (offset, interval, sizes, data, phase)
  comes from the regression seed through cocotb's `random`, and the log
  prints it.
- **Functional coverage:** covergroup `pcs_elastic`:
  - `ppm`: -600, -300, 0, +300, +600, random within ±600, beyond;
  - `change`: an addition with the COM in slot 0 or slot 1, a removal
    reported on the COM's word or on the next word, a bare COM, back-to-back
    COMs;
  - `event`: overflow, underflow, write-side drop, read-side hold, lock
    downgrade, relock, start, reset restart, write pause, lock-start drop;
  - `fill`: 4–5, 6–7, 8, 9–10 and 11–12 symbols while writing;
  - `offset_correction`: each fixed offset class with the correction it
    needs (additions at -600 and -300, removals at +300 and +600), and a
    random offset with each.

  Sampling happens after each run's check, from the checker's attribution.
- **Out of scope:** decode, polarity, RxStatus precedence and the
  `pipe_reset_ni` gating are the decoder's and the top's
  ([#82](https://github.com/amichai-bd/pcie-factory/issues/82),
  [#85](https://github.com/amichai-bd/pcie-factory/issues/85)). So is
  the end-to-end RX latency (the top's, with the aligner and decoder).
  Metastability is out of scope: zero-delay simulation resolves each
  synchronizer in exactly two edges.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset, RX reset timing, start at fill 8, lossless pass-through, SKP sets of 1 to 5](../../rtl/pcs_elastic/MAS.md#behavior) | `smoke` | 0 | 1; 0 ppm, 40 random packets |
| [SKP addition](../../rtl/pcs_elastic/MAS.md#behavior) at -600 ppm, longest interval, maximum TLPs; fill 4..12 | `ppm_m600` | 1 | 1; 8 TLPs |
| SKP addition at -300 ppm | `ppm_m300` | 1 | 1; 200 random packets, random interval |
| [SKP removal](../../rtl/pcs_elastic/MAS.md#behavior) at +300 ppm | `ppm_p300` | 1 | 1; 200 random packets, random interval |
| SKP removal at +600 ppm, maximum TLPs | `ppm_p600` | 1 | 1; 8 TLPs |
| Random offset within ±600 ppm | `random_offset` | 1 | 3 |
| Burst one SKP set short at -600 ppm (reviewer's case) | `short_burst_m600` | 1 | 2 |
| Packet sizes that strand corrections, ±600 ppm | `stress_sizes` | 1 | 2 |
| [Recentring](../../rtl/pcs_elastic/MAS.md#behavior), read-side holds through electrical idle, relock with a new generation | `idle_m600` | 1 | 1; two 6000-symbol gaps |
| Recentring, write-side drops through electrical idle | `idle_p600` | 1 | 1; two 6000-symbol gaps |
| [Lock generation](../../rtl/pcs_elastic/MAS.md#interfaces) carried per word through same-cycle relocks | `relock_generation` | 1 | 2; 12 relocks |
| [Lock start](../../rtl/pcs_elastic/MAS.md#behavior): after electrical idle the first locked symbols never share a word with an unlocked one; the held (write side, fast RX clock) or leftover (read side, slow RX clock) unlocked symbol is dropped | `lock_start_m600`, `lock_start_p600` | 1 | 2 each; 12 gaps of 1500 symbols |
| [Thresholds](../../rtl/pcs_elastic/MAS.md#behavior): removal decided at fill 8 and 9, addition at 7 and 8, with odd fills | `thresholds_swing` | 1 | 2; dense sets, alternating fast and slow phases |
| Addition threshold at -600 ppm with dense sets | `thresholds_m600` | 1 | 2; additions decided at fill 7 and 8 |
| [Corners](../../rtl/pcs_elastic/MAS.md#behavior): bare COM, back-to-back sets, removal reported on the next word, `ser_rx_valid_i` pauses, underflow and overflow mixed with SKP changes | `pause_corners` | 1 | 3; +4% with about 4% write pauses, dense sets |
| [Overflow](../../rtl/pcs_elastic/MAS.md#behavior): words lost, flag on the first word after, the rest in order | `overflow` | 1 | 1; +2%, no ordered sets |
| [Underflow](../../rtl/pcs_elastic/MAS.md#behavior): underflow words, no symbol lost | `underflow` | 1 | 1; -2%, no ordered sets |
| Reset with traffic in flight, then a clean restart | `reset_restart` | 1 | 2 |
| Long runs, maximum TLPs, -600 ppm, both interval limits | `long_m600` | 2 | 3; 40 TLPs (about 83000 cycles) |
| Long runs, maximum TLPs, +600 ppm, both interval limits | `long_p600` | 2 | 3; 40 TLPs |
| Long random traffic, random offset and interval | `long_random` | 2 | 6; 1200 packets |
| Long correction-stranding sizes or short bursts, ±600 ppm | `long_stress` | 2 | 6; 60 packets |
| Long electrical idle, ±600 ppm | `long_idle` | 2 | 4; three 20000-symbol gaps |
| Corner seed sweep | `pause_corners_sweep` | 2 | 10 |
| Threshold seed sweep | `thresholds_swing_sweep` | 2 | 8 |

Every [interface](../../rtl/pcs_elastic/MAS.md#interfaces) port is
exercised by every entry. `ser_rx_valid_i` low is exercised in reset, after
the last word and in `pause_corners`.

### Results

All pass: L0 1/1, L1 22/22, L2 32/32. Over the L2 runs at ±600 ppm (0 lost
symbols, 0 overflows, no underflow while writing):

| Run | Visible fill |
| --- | --- |
| -600 ppm, maximum TLPs, short bursts, stranding sizes | 4..8 |
| +600 ppm | 5..12 |
| Electrical idle | 6..10 |

`pause_corners` reports removals on the next word (1 or 2 per run in 6 of
10 sweep seeds), 17 to 53 bare COMs, and overflows mixed with SKP changes.

### Mutation evidence

Each mutation was made in a scratch copy of `src/` and run against one
entry:

| Mutation | Entry, seed 1 | Result |
| --- | --- | --- |
| No removed flag on the next symbol (`w_pend_rem_d` 0) | `ppm_p600` | FAIL: a set's status mismatch |
| No removed flag when sym0 is the removed SKP | `pause_corners` | FAIL: removal not reported |
| No addition in the first slot | `ppm_m600` | FAIL: add flag expected |
| Add threshold at 5 instead of 7 | `short_burst_m600` | FAIL: visible fill 2 outside 4..12 |
| Overflow flag never set | `overflow` | FAIL: symbol mismatch with no flag |
| Generation bit not carried | `relock_generation` | FAIL: generation mismatch |
| No write-side lock-start drop (`w_flush` 0) | `lock_start_p600` | FAIL: late lock start |
| No read-side lock-start drop (`r_flush` 0) | `lock_start_m600` | FAIL: late lock start |
| Removal status of a slot-1 COM on the next word (`r_rem1` never set) | `ppm_p600` | FAIL: removal reported on the next word at fill 10 |
| Fill high at 10 (`EB_HIGH` 10) | `thresholds_swing` | FAIL: no removal at a seen fill of 9 |
| Fill high at 8 (`EB_HIGH` 8) | `thresholds_swing` | FAIL: underflow while writing |
| Fill high compare `>` instead of `>=` | `thresholds_swing` | FAIL: no removal at a seen fill of 9 |
| Add threshold at 6 (`EB_LOW` 6) | `thresholds_m600` | FAIL: no addition at fill 7 |
| Add threshold at 8 (`EB_LOW` 8) | `thresholds_m600` | FAIL: addition at fill 8 |
| Start at 10 (`EB_NOMINAL` 10) | `smoke` | FAIL: no word in the first cycle at fill 8 |

The sym0-removal mutation survives `ppm_p600` seed 1, whose TLP-only stream
puts every removed SKP in slot 1. The random and corner entries catch it.

### Coverage

Merged over L0 to L2 (`./build regress --level 0 --level 1 --level 2 --ip pcs_elastic --coverage`):
line 100.0% (118/118), toggle 97.4% (1254/1288), functional 100.0% (33/33).
`level2.yaml` sets the floors just below them: line 100, toggle 97,
functional 100. The floors hold only for a merged L0 to L2 run, which is
how the nightly runs them. Several bins are hit only by L1 entries
(`reset_restart`, `overflow`, `underflow`, the fixed offsets). An L2-only
`--coverage` run therefore reports about 79% functional and fails the
floor. The 34 untoggled toggle points are structural:
- the overflow bit of the symbols that never carry it (the write side's
  symbols, `head1`, the leftover register): the flag sits on a word's first
  symbol, and the leftover is always a second symbol;
- bit 3 of the write-side recentring counter (it counts from 7) and of the
  read side's decremented counter;
- `fill_o[5]`, `vis[4]` and `dst_fill_o[4]`, since the visible fill stays
  below 32 even in the overflow tests (the write side sees the FIFO full
  first);
- the constant input stage of `common_cdc_rst`.

## Gaps

| Feature | Owning issue |
| --- | --- |
| RxStatus precedence, EDB substitution for the underflow word, `pipe_reset_ni` gating, RX latency end to end | [#85](https://github.com/amichai-bd/pcie-factory/issues/85) |
