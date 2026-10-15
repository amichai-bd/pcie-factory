# pcie_pcs DV plan

The `pcs` top is verified at its ports: PIPE above, the 2x10-bit symbol
interface below. Each sub-IP keeps its own unit DV
([`pcs_enc8b10b`](../pcs_enc8b10b/DV_PLAN.md),
[`pcs_dec8b10b`](../pcs_dec8b10b/DV_PLAN.md),
[`pcs_align`](../pcs_align/DV_PLAN.md),
[`pcs_elastic`](../pcs_elastic/DV_PLAN.md)); these tests check the
integration, PIPE control and the HAS [targets](../../arch/pcs/HAS.md#targets).

## Strategy

- **Environment** ([`tb/pcs_env.py`](../../../../src/dv/pcs/tb/pcs_env.py)):
  cocotb drives `pcie_pcs` directly (no SV testbench; `pcs.dv.f` is empty).
  `clk_i` runs at 125 MHz; `ser_rx_clk_i` runs at a seeded offset of up to
  ±600 ppm with a fractional period, its edges rounded to the picosecond, so
  the offset accumulates exactly. `PipeMac` is the PIPE-side driver and
  monitor: it drives a cycle's inputs at the falling `clk_i` edge and reads
  that cycle's outputs once they settle, following the MAC's rules of
  [PIPE control](../../arch/pcs/PIPE_CONTROL.md).
- **PIPE bench** ([`tb/pcs_bench.py`](../../../../src/dv/pcs/tb/pcs_bench.py)):
  every cycle, PhyStatus, the detection RxStatus and `ser_tx_elec_idle_o`
  against `PipeRef` ([`models/pcie_pcs_model.py`](../../../../src/dv/pcs/models/pcie_pcs_model.py),
  the [architecture model](../../arch/pcs/HAS.md#architecture-model)'s
  `PipeControl` plus the two-cycle line latency); the HAS reset values in
  every reset cycle; every word on `ser_tx_data_o` against the architecture
  model's `tx_encode`. It also holds the MAC procedures (reset, PowerDown,
  receiver detection, transmit).
- **Channel model** ([`tb/pcs_channel.py`](../../../../src/dv/pcs/tb/pcs_channel.py)):
  the architecture model's partner stream (training sets, packets, SKP
  ordered sets every 1180 to 1538 symbols), encoder and channel (bit slips,
  single-bit errors, electrical-idle gaps of noise, lane inversion), plus
  close error pairs and error bursts. Close pairs are chosen by decoding the
  corrupted line with the architecture model so that their errored symbols
  span three PIPE words at either word parity. `RxChannel` drives the line
  at the RX clock and records when each word was sampled; a queue source
  closes the round trip.
- **Receive scoreboard** ([`models/pcie_pcs_rx_check.py`](../../../../src/dv/pcs/models/pcie_pcs_rx_check.py),
  with pytest unit tests): every RxValid run must be a contiguous piece of
  the sent stream with SKP set aside (no loss, duplication or reordering).
  - A run is anchored on a 24-symbol window that has exactly one match
    consistent with the time its symbols arrived on the line (at most 24
    cycles before they leave on PIPE), so a run that starts in periodic
    training or FTS sets is anchored where the stream is unique. Its first
    symbols are then checked backwards from the anchor. This places, for
    example, a lock taken on a comma in the idle-exit noise, whose error
    words precede a same-cycle relock inside the FTS sets.
  - A wrong symbol is accepted only before a reported error (RxStatus 4 or
    7, within 16 symbols) or on a symbol the channel flipped (two flips can
    cancel each other's disparity).
  - Symbol 0 of a run may be the symbol before the lock's comma, unchecked,
    only when the run starts after electrical idle (the first run, and the
    idle-gap tests).
  - Every SKP ordered set leaves with 2, 3 or 4 SKP and its COM's word
    carries RxStatus 2, 0 or 1 to match.
  - The relock rule (`ERR_LIMIT` 3, `ERR_DECAY_WORDS` 8, one request per
    lock), applied to the words seen on PIPE, predicts every RxValid drop:
    each prediction drops RxValid within 20 cycles, and no drop happens
    without one (idle, slips and the end of the line aside). This follows
    `pcs_align`'s `ErrorCounter` testbench model, at the PIPE boundary.
  - The HAS lock target (the first locked word within 10 cycles of the
    comma's last bit) is measured after every lock: after electrical idle,
    slips, close-error relocks and error bursts. A lock taken on noise has
    no sent comma; it is counted, not timed.
- **Functional coverage:** `pcs_pipe_power` (the six PowerDown arcs);
  `pcs_pipe_control` (reset kinds, detection results, transmit states, a
  PowerDown change before ready); `pcs_rx_elec_idle` (RxElecIdle entry and
  exit, fast and slow RX clock); `pcs_rx` (every receive RxStatus code,
  clock offset signs, every scenario, both relock causes); `pcs_rx_status`
  (each code within the targets with the offset that produces it: SKP added
  at a slow RX clock, removed at a fast one, data and errors at both).
- **Coverage floors** (`level2.yaml`, met by L2 alone): line 98, toggle 96,
  `pcs_pipe_power` 100, `pcs_rx_status` 100. L0 to L2 together reach line
  99.5%, toggle 97.8% and functional 100%. The two lines left are in the
  sub-IPs: the encoder's assertion-only `run6` and an elastic-buffer
  removal with a held symbol, both covered in their own DV.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset and PhyStatus](../../arch/pcs/PIPE_CONTROL.md#reset-and-phystatus): reset values, 16 cycles after `rst_i` and after `pipe_reset_ni` (1 and 6 cycles), no pulse for a change before ready | `pipe_reset` | 0 | 1 |
| [Receiver detection](../../arch/pcs/PIPE_CONTROL.md#receiver-detection): 64 cycles, RxStatus 3 or 0 from the cycle-63 sample, no second detection without TxDetectRx low | `pipe_detect` | 0 | 1 |
| Receive path smoke: a short stream at 100 ppm arrives intact | `rx_smoke` | 0 | [1] |
| [PowerDown](../../arch/pcs/PIPE_CONTROL.md#powerdown): every arc acknowledged after 4 cycles, transmitter idle outside P0, no beacon in P2 | `pipe_power` | 1 | 2 |
| PIPE control and the [transmit path](../../arch/pcs/PIPE_CONTROL.md#transmit-path) under random legal MAC sequences | `pipe_random` | 1 | 3; 60 operations |
| The same, longer | `pipe_random_long` | 2 | 6; 400 operations |
| [RxElecIdle](../../arch/pcs/PIPE_CONTROL.md#receive-status-signals) through crossing 5: every change, in order, 2 or 3 `clk_i` edges after the RX edge that registered it | `rx_elec_idle` | 1 | [1, 2]: fast, slow RX clock |
| [Elastic buffer](../../arch/pcs/RECEIVE.md#elastic-buffer) at 300 to 600 ppm, random packets, SKP every 1180: lossless, one lock, SKP changes on the COM's word, RX latency at most 12 | `rx_ppm` / `rx_ppm_sweep` | 1 / 2 | [1, 2] / [3..8]; odd seeds fast |
| The same with back-to-back DLLPs | `rx_dllp` | 1 | [1, 2] |
| The same with maximum TLPs (4122 bytes), SKP every 1538, 550 to 600 ppm | `rx_max_tlp` | 2 | [1..4] |
| [Polarity](../../arch/pcs/RECEIVE.md#decode-and-rxstatus): an inverted lane with RxPolarity 1 is intact | `rx_polarity` | 1 | [1] |
| RxPolarity applies from the next word: wrong data before, every word after correct with no error | `rx_polarity_toggle` | 1 | [2] |
| Isolated bit errors: each reported (4 or 7), no relock, data intact around them | `rx_errors` / `rx_errors_sweep` | 1 / 2 | [1, 2] / [3..6] |
| Two close bit errors relock by design; RxValid stays low until the next comma (at most one SKP interval plus a packet); data resumes from that COM, lock within 10 cycles | `rx_close_errors` / `rx_close_errors_sweep` | 1 / 2 | [1, 2] / [3..6]; 8 pairs each |
| [Relock rule](../../arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock) (limit, decay, one request per lock, crossing 4) against error bursts; lock within 10 cycles | `rx_error_bursts` / `rx_error_bursts_sweep` | 1 / 2 | [1, 2] / [3..8]; 16 bursts |
| Bit slips: each in lock drops lock within 48 cycles (32 plus the RX latency), lock regained at the next COM within 10 cycles, every run's data intact (a run may lose the stream only after a slip inside it) | `rx_slips` / `rx_slips_sweep` | 1 / 2 | [1, 2] / [3..6]; 4 slips, up to 300 ppm |
| Electrical idle gaps: RxValid low, no buffer status, lock within 10 cycles of the FTS comma, data intact; errors only where a lock starts on noise | `rx_elec_idle_gaps` / `rx_elec_idle_gaps_sweep` | 1 / 2 | [1, 2] / [3, 4, 5, 6, 12, 14, 22, 24, 28, 30, 34, 36]; 3 gaps; even seeds slow (22 and 30 were 10.7 and 11.0 cycles before the lock-start fix; 12, 14, 24, 28 and 34 lock on noise) |
| [Loopback](../../arch/pcs/PIPE_CONTROL.md#loopback): received words re-encoded two cycles after they are on PIPE, with errors as EDB | `loopback` | 1 | [1] |
| Round trip: TxData through the encoder, line, aligner, buffer and decoder back to RxData, intact | `round_trip` | 1 | [1] |
| Buffer overflow (RxStatus 5) at +4000 ppm and underflow (RxStatus 6, EDB EDB as K, RxValid 0) at -4000 ppm, outside the targets on purpose | `eb_stress` | 1 | [1, 2] |

**Mutation evidence** (scratch edits, each reverted):

| Mutant | Caught by |
| --- | --- |
| Acknowledge pulse a cycle early | `pipe_reset`, `pipe_power`, `pipe_random` |
| PhyStatus falls after 15 cycles | all PIPE tests |
| `ser_tx_rxdet_i` sampled in detection cycle 62 | `pipe_detect`, `pipe_random` |
| Transmitting outside P0 | `pipe_power` |
| TxCompliance ignored | `pipe_random` |
| No RxStatus 2 for a removed SKP | `rx_ppm`, `rx_ppm_sweep` |
| Relock at 2 errored words | `rx_errors`, `rx_error_bursts` |
| No error decay | `rx_errors`, `rx_error_bursts` |
| Loopback ignored | `loopback` |
| RxPolarity ignored | `rx_polarity`, `rx_polarity_toggle` |
| Overflow status dropped | `eb_stress` |
| RxValid from symbol 0's lock alone | `rx_elec_idle_gaps` |
| No lock-start drop in `pcs_elastic` (write or read side) | `rx_elec_idle_gaps` seeds 22 and 30 (the lock target); `pcs_elastic`'s `lock_start_p600` and `lock_start_m600` |

RxValid from symbol 1's lock alone is equivalent now. Since the elastic
buffer drops an unlocked symbol at a lock start, a word whose second
symbol is locked and first is not no longer occurs; the remaining mixed
word, at a lock end, has its second symbol unlocked. The underflow word's
EDB, EDB comes from the decoder, so the top has no multiplexer to mutate
(`a_udf_edb` asserts it).

## Gaps

None.
