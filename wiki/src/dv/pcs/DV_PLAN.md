# pcie_pcs DV plan

One testbench for the whole IP (decision Q24): every test drives
[`pcie_pcs`](../../arch/pcs/HAS.md) only through its ports, PIPE above and
the 2x10-bit symbol interface below, and checks it there against the
pages of the HAS and the architecture model. It replaces the four leaf
testbenches (encoder, decoder, aligner, elastic buffer); the
[mapping](#leaf-checks-old-to-new) shows where each of their checks went.
The blocks are described in the [MAS](../../rtl/pcs/MAS.md).

## Strategy

- **Environment** ([`tb/pcs_env.py`](../../../../src/dv/pcs/tb/pcs_env.py)):
  cocotb drives `pcie_pcs` directly (`pcs.dv.f` is empty). `clk_i` runs at
  125 MHz; `ser_rx_clk_i` at a seeded offset with a fractional period, its
  edges rounded to the picosecond, so the offset accumulates exactly.
  `PipeMac` drives a cycle's PIPE inputs at the falling `clk_i` edge and
  reads its outputs once they settle, following the MAC's rules of
  [PIPE control](../../arch/pcs/PIPE_CONTROL.md).
- **PIPE bench** ([`tb/pcs_bench.py`](../../../../src/dv/pcs/tb/pcs_bench.py)),
  every cycle of every test: PhyStatus, the detection RxStatus and
  `ser_tx_elec_idle_o` against `PipeRef`
  ([`models/pcie_pcs_model.py`](../../../../src/dv/pcs/models/pcie_pcs_model.py),
  the [architecture model](../../arch/pcs/HAS.md#architecture-model)'s
  `PipeControl` plus the two-cycle line latency); the HAS reset values in
  every reset cycle; every word on `ser_tx_data_o`, exactly two cycles
  after its PIPE word, against the architecture model's `tx_encode`
  (running disparity, TxCompliance, unsupported K as EDB, RD held across
  TxElecIdle, RD negative and the words in flight dropped at a reset). It
  holds the MAC procedures (reset, PowerDown, receiver detection, transmit)
  and samples the encoder coverage.
- **Lines.** Three ways to build the received line:
  - [`tb/pcs_channel.py`](../../../../src/dv/pcs/tb/pcs_channel.py): the
    architecture model's partner stream, encoder and channel (bit slips,
    single-bit errors, electrical-idle gaps of noise, lane inversion), plus
    close error pairs and error bursts;
  - [`tb/pcs_lines.py`](../../../../src/dv/pcs/tb/pcs_lines.py): a line
    built bit by bit with the aligner block's line model
    ([`models/pcie_pcs_align_model.py`](../../../../src/dv/pcs/models/pcie_pcs_align_model.py)):
    filler that puts the comma at any bit offset, idle, noise, slips and
    flips on chosen symbols, every received bit carrying its transmitted
    index; training traffic numbers its TS1 sets (the N_FTS byte) so that
    every window of it is unique;
  - [`models/pcie_pcs_traffic.py`](../../../../src/dv/pcs/models/pcie_pcs_traffic.py):
    the traffic kinds that load the elastic buffer's corners (maximum TLPs,
    random sizes with SKP ordered sets of one to five SKPs, sizes that
    strand corrections, a burst one set short, dense sets).
- **Receive scoreboard** ([`models/pcie_pcs_rx_check.py`](../../../../src/dv/pcs/models/pcie_pcs_rx_check.py),
  with pytest unit tests), behind [`tb/pcs_rxbench.py`](../../../../src/dv/pcs/tb/pcs_rxbench.py):
  - every RxValid run is a contiguous piece of the sent stream with SKP set
    aside: no loss, duplication or reordering. A run is anchored on a
    24-symbol window with exactly one match consistent with the time its
    symbols arrived; a wrong symbol is accepted only before a reported
    error (RxStatus 4 or 7) or on a symbol the channel flipped. A run that
    loses the stream after a slip is re-anchored further on (a relock in
    comma-dense traffic retakes lock in the same cycle and keeps RxValid
    high);
  - every SKP ordered set leaves with one SKP fewer, the same number or one
    more than sent (none for a COM sent with no SKP), and the word holding
    its COM carries RxStatus 2, 0 or 1 to match; no other word carries 1 or
    2;
  - **RX latency** (HAS target 12 cycles) for every delivered symbol of
    every clean run;
  - **visible fill 4 to 12** (HAS elastic-buffer target): `FillCheck` reads
    the MAS-named net `eb_fill` (the elastic buffer's `fill_o`) every
    `clk_i` cycle, as an observation net, from the buffer's start until the
    line ends, outside `rst_i`. The window holds only within the targets,
    so each test enables it: every receive test, the lock tests and the
    decode-free traffic tests run with it on; `eb_overflow`, `eb_overflow_leftover`, `eb_underflow`
    (2% offset, no SKP) and `eb_stress` (4000 ppm) leave it off, as they
    drive the buffer past its window on purpose;
  - **lock start and the lock target** after every loss of lock: the run
    starts at the comma that gave lock or the symbol before it (the
    elastic buffer's lock-start drop), and its first word is on PIPE within
    10 cycles of that comma's last bit. A run whose first word reports an
    error and a later symbol differs from the stream is a lock on noise,
    counted and not timed; an errored lead symbol alone does not make one
    (#162 re-review);
  - the relock rule (`ERR_LIMIT` 3, `ERR_DECAY_WORDS` 8, one request per
    lock) applied to the words on PIPE predicts every RxValid drop in packet
    traffic, within 20 cycles;
  - **RxValid rule** (`ValidRule`, every receive test): RxValid against
    the lock tags of the word that made it, read on the top's MAS-named
    observation nets `eb_valid`, `eb_status` and `eb_rx`: 1 exactly for a
    word with both symbols locked or an idle tail, 0 for an underflow word
    and for a lock end by a relock inside a word;
  - **electrical idle in order** (#182; RECEIVE.md, Electrical idle):
    every cycle, RxValid is never 1 with RxElecIdle 1; before each idle
    gap (`check_ei_order`, in every idle test), the last sent symbol that
    starts in a word received with `ser_rx_elec_idle_i` low leaves under
    RxValid before RxElecIdle rises, and a gap that follows an EIOS shows
    all four EIOS symbols under RxValid before it. A run whose last word
    ends in EDB and is followed by RxElecIdle is an idle tail; that EDB is
    not checked against the stream.
- **Exact-timing decode harness** ([`tb/pcs_direct.py`](../../../../src/dv/pcs/tb/pcs_direct.py)):
  at 0 ppm and a fixed phase, after lock, every word leaves on PIPE a fixed
  number of cycles after it is driven (measured on a marker word: 9). The
  line carries chosen codes at word boundaries, and every PIPE word
  (RxData, RxDataK, RxValid, RxStatus) is compared with the decoder
  block's cycle model ([`models/pcie_pcs_dec8b10b_model.py`](../../../../src/dv/pcs/models/pcie_pcs_dec8b10b_model.py),
  which imports the architecture model's `decode()`), with RxPolarity and
  `pipe_reset_ni` applied in the cycle each word is decoded. The RD is seen
  through a probe word (D0.0 RD-, a disparity error after a code that
  leaves RD+); the stimulus never reaches the relock limit.
- **Functional coverage:**

  | Covergroup | Contents |
  | --- | --- |
  | `pcs_pipe_power` | The six PowerDown arcs. |
  | `pcs_pipe_control` | Reset kinds and lengths, detection results, transmit states, a PowerDown change before ready. |
  | `pcs_rx_elec_idle` | RxElecIdle entry and exit at a fast and a slow RX clock. |
  | `pcs_tx_codes` | Every valid symbol x RD x lane on `ser_tx_data_o` (1072 crosses). |
  | `pcs_tx_controls` | Compliance x RD before the word, RD before x RD between the lanes, EDB per lane, RD held across TxElecIdle, a reset with a word in flight. |
  | `pcs_rx_codes` | Every valid symbol x RD x lane received on PIPE after the round trip. |
  | `pcs_dec_class` | Lane x (RD neg, pos, unknown x valid, disparity, code error). |
  | `pcs_dec_rd_unknown` | Why lane 0's RD was unknown (polarity change, after reset, after an unlocked word, unlocked) and lane 1 unlocked. |
  | `pcs_dec_recovery` | Error class x the RD it left x carried to the next lane or word. |
  | `pcs_align_lock` | The comma position 0 to 19 in the aligner's window x its form, at each lock. |
  | `pcs_align_relock` | Lock from hunting, same-cycle relock, relock to hunting, idle drop, reset drop. |
  | `pcs_align_eios` | The RX bit phase (0 to 19) of the word boundary where the line goes idle after an EIOS. |
  | `pcs_align_impairments` | Slips in TS1, FTS and packet traffic by direction, on a SKP set's COM and SKP, close slip pairs; flips in TS1 and packets. |
  | `pcs_elastic` | Offset classes, SKP changes (addition after a COM in either half of a word, removal, bare COM, back-to-back sets), events (overflow, overflow with an odd leftover, underflow, recentring both ways, lock start, rst_i and pipe_reset_ni with traffic), the correction each offset class needs. |
  | `pcs_rx`, `pcs_rx_status` | Every receive RxStatus code, offset signs, scenarios and relock causes; each code within the targets with the offset that produces it; electrical idle in order (`ei_order`: a gap checked, a whole EIOS, an idle tail). |

- **Coverage floors** (each level's file; floors apply to the merged
  levels selected): L0 line 88, toggle 87.2, `pcs_align_lock` 100; L1 (with
  L0, as in `ci-gate`) line 99, toggle 97.3 and every covergroup but `pcs_rx`
  at 100; L2 (L0 to L2, as nightly) line 99, toggle 97.5, functional
  100, per the [DV BKM](../../../bkm/dv.md#coverage) rule (the existing
  floor or the merged value less 0.5 points, the higher): the merged
  source values are 99.24% and 97.96%, so the line floor, kept at 99, leaves
  0.24 points (under one line point) of margin. Every point left is in
  [coverage holes](#coverage-holes).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset and PhyStatus](../../arch/pcs/PIPE_CONTROL.md#reset-and-phystatus): reset values, 16 cycles after `rst_i` and after `pipe_reset_ni` (1 and 6 cycles), no pulse for a change before ready | `pipe_reset` | 0 | 1 |
| [Receiver detection](../../arch/pcs/PIPE_CONTROL.md#receiver-detection): 64 cycles, RxStatus 3 or 0 from the cycle-63 sample, no second detection without TxDetectRx low | `pipe_detect` | 0 | 1 |
| Receive path smoke: a short stream at 100 ppm arrives intact | `rx_smoke` | 0 | [1] |
| [Encoder](../../rtl/pcs/MAS.md#encoder): TX latency 2, K28.5 at RD- (`0011111010`), a COM run, TxCompliance, unsupported K as EDB, RD held across TxElecIdle, `pipe_reset_ni` drops the words in flight | `tx_smoke` | 0 | 1 |
| [Decoder](../../rtl/pcs/MAS.md#decoder): commas at the RD and at the other RD (RxStatus 7), code errors as EDB EDB K (RxStatus 4), `pipe_reset_ni` leaves the RD unknown | `rx_decode_smoke` | 0 | 1; exact timing |
| [Aligner](../../rtl/pcs/MAS.md#aligner): lock from every bit offset 0 to 19, both comma forms, after electrical idle: one run each, intact from its comma, within the lock target | `rx_lock_offsets` | 0 | [1]; 40 locks |
| One-word electrical-idle pulses at the eight phases of the TS1 COM period (the window holding the idle word never locks), `rst_i` with traffic in flight (lock and data return at the next comma) | `rx_lock_controls` | 0 | [1] |
| [PowerDown](../../arch/pcs/PIPE_CONTROL.md#powerdown): every arc acknowledged after 4 cycles, transmitter idle outside P0, no beacon in P2 | `pipe_power` | 1 | 2 |
| PIPE control and the transmit path under random legal MAC sequences | `pipe_random` / `pipe_random_long` | 1 / 2 | 3; 60 operations / 6; 400 |
| [RxElecIdle](../../arch/pcs/PIPE_CONTROL.md#receive-status-signals) with the received words: every change, in order, 1 to 12 `clk_i` edges (the RX latency target) after the RX edge that registered it (measured: at most 10) | `rx_elec_idle` | 1 | [1, 2]: fast, slow; coverage on both |
| Every data byte and K-flagged byte at RD- and RD+ in both lanes through PIPE TX, back to back and with TxElecIdle between words; fails on a hole in `pcs_tx_codes` or the EDB and RD-hold bins | `tx_table` | 1 | 1; 5120 words twice |
| Random transmit traffic with TxCompliance, idle gaps, unsupported K and resets of both kinds with words in flight, every word against the model | `tx_random` / `tx_random_long` | 1 / 2 | 3; 5000 cycles / 4; 100000 |
| Every symbol at both RDs in both lanes through PIPE TX, the line and PIPE RX, intact, no error; fails on a hole in `pcs_rx_codes` | `table_round_trip` | 1 | [1] |
| All 1024 codes through the ports: each lane at RD- and RD+, lane 0 at an unknown RD (polarity change), the RD each leaves (probe); both lanes unlocked (line in idle, data and K); class counts 268, 196, 560 at a known RD and 464, 560 at an unknown one; no error on the first locked word after the unlocked run although its COM would be a disparity error at the RD the unlocked symbols left | `rx_decode_table` | 1 | 1; about 100000 words, exact timing |
| Random encoded traffic with bit errors, random codes, partner RD slips, RxPolarity changes (single-word pulses among them) and `pipe_reset_ni` pulses, every PIPE word against the decoder model | `rx_decode_random` / `rx_decode_random_long` | 1 / 2 | 3; 20000 words / 2; 200000 |
| Bit slips in numbered TS1 and in FTS traffic, on SKP sets, slip pairs 2 to 90 symbols apart; isolated flips keep lock; every slip answered (no errored word later than 48 cycles after it), the data intact once lock is retaken in the same cycle or at the next comma | `rx_slips_training` / `rx_slips_training_sweep` | 1 / 2 | [1] / [2..6]; up to 300 ppm |
| Bit slips in packet traffic and on its SKP sets; flips in packets keep lock | `rx_slips_packets` | 1 | [1, 2] |
| No false lock: encoded data at every bit offset and idle noise never give RxValid; unflagged noise may lock falsely and the true boundary follows | `rx_no_false_lock` | 1 | [1, 2] |
| Long mixed traffic (training, packets) with slips, flips and idle gaps at up to ±600 ppm, every check | `rx_align_long` | 2 | 4; 40 segments |
| [Elastic buffer](../../arch/pcs/RECEIVE.md#elastic-buffer) at 300 to 600 ppm, random packets, SKP every 1180: lossless, one lock, SKP sets within the rules, RX latency at most 12 | `rx_ppm` / `rx_ppm_sweep` | 1 / 2 | [1] / [2..8]; odd seeds fast |
| The same with back-to-back DLLPs | `rx_dllp` | 1 | [1, 2] |
| The same with maximum TLPs (4122 bytes), SKP every 1538, 550 to 600 ppm | `rx_max_tlp` | 2 | [1..4] |
| SKP addition at -600 ppm, maximum TLPs, SKP every 1538; removal at +600 ppm | `eb_tlp_m600`, `eb_tlp_p600` | 1 | [1] each |
| Random sizes, SKP sets of one to five SKPs, random interval, at -300 and +300 ppm | `eb_random_m300`, `eb_random_p300` | 1 | [1] each |
| The same at a seeded offset within ±600 ppm | `eb_random_offset` / `eb_random_offset_sweep` | 1 / 2 | [1, 2], coverage on both / [3..6] |
| A burst one SKP set short, then a maximum TLP, at -600 ppm (#73 sign-off case) | `eb_short_burst` / `eb_short_burst_sweep` | 1 / 2 | [1] / [2, 3] |
| Sizes that strand corrections at ±600 ppm | `eb_stress_sizes` | 1 | [1, 2]: fast, slow |
| Dense SKP sets (one to five SKPs every 48 to 96 symbols) at ±600 ppm: additions after a COM in either half of a word, removals leaving a bare COM, sets back to back | `eb_dense` / `eb_dense_sweep` | 1 / 2 | [1] / [2..8] |
| Recentring through two 6000-symbol electrical-idle gaps in 60 packets at -600 ppm (read side holds) and +600 ppm (write side drops); non-vacuous: `Recentre` counts the MAS observation nets `r_recentre` (read-side hold) and `w_drop` (write-side drop), and the test requires a hold at -600 ppm and a drop at +600 ppm | `eb_idle_m600`, `eb_idle_p600` | 1 | [1] each |
| A relock end inside a PIPE word: close bit-error pairs at ±600 ppm, the SKP corrections leaving an odd word parity at some relocks; that word (last locked symbol, then an unlocked one) leaves with RxValid 0, and at least one occurs (`ValidRule`) | `rx_relock_mid_word` | 1 | [1, 2]; odd seeds fast |
| [Electrical idle in order](../../arch/pcs/RECEIVE.md#electrical-idle): an EIOS before each of eight 1500-symbol idle gaps at ±600 ppm, the SKP corrections changing the word parity between gaps: the whole EIOS under RxValid before RxElecIdle, idle tails (EIOS ending in the first half of a word) reach PIPE; non-vacuous: every gap's EIOS checked whole and at least one idle tail per run | `rx_eios_idle` / `rx_eios_idle_sweep` | 1 / 2 | [1, 2] / [3..8]; odd seeds fast |
| [EIOS completion](../../arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock): an EIOS before idle at every RX bit phase 0 to 19 of the transmitter's word boundary (filler bits), at +300 or -300 ppm: all 20 EIOS whole under RxValid before RxElecIdle (at phases 10 to 19 the last IDL starts in the idle-flagged word) | `rx_eios_phases` | 1 | [1, 2]; odd seeds fast |
| Lock start after twelve 1500-symbol idle gaps in 120 packets: every run starts at its comma or the symbol before it, within the lock target; recentring required as in `eb_idle_*` | `eb_lock_start` / `eb_lock_start_sweep` | 1 / 2 | [1, 2] / [3..10]; odd seeds fast |
| Overflow after one slow-clock SKP addition creates a leftover, then +2% data-only clock offset: every lost word still reports RxStatus 5 | `eb_overflow_leftover` | 1 | [1] |
| Overflow at +2% with no SKP: RxStatus 5 on the first word after each loss, the rest in order | `eb_overflow` | 1 | [1] |
| Underflow at -2% with no SKP: RxStatus 6, EDB EDB as K, RxValid 0, nothing lost, no error after an underflow word (the decoder's RD held across it) | `eb_underflow` | 1 | [1] |
| Outside the targets at ±4000 ppm with long packets: overflow (RxStatus 5) or underflow (RxStatus 6, EDB EDB, RxValid 0) | `eb_stress` | 1 | [1, 2] |
| Reset with traffic in flight: `rst_i` (lock returns at a comma within the lock target, intact) and `pipe_reset_ni` (RxValid low, the stream resumes with the RD unknown, nothing out of order) | `rx_reset_traffic` / `rx_reset_traffic_sweep` | 1 / 2 | [1] / [2..6] |
| Long runs: forty maximum TLPs, 1200 random packets, 60 packets of stranding sizes or short bursts, three 20000-symbol idle gaps in 300 packets (recentring required as in `eb_idle_*`) | `eb_long_tlp`, `eb_long_random`, `eb_long_stress`, `eb_long_idle` | 2 | [1..4] (two per sign; odd seeds fast), 3, [1..4] (two per sign), [1..4] |
| [Polarity](../../arch/pcs/RECEIVE.md#decode-and-rxstatus): an inverted lane with RxPolarity 1 is intact | `rx_polarity` | 1 | [1] |
| RxPolarity applies from the next word: wrong data before, every word after correct with no error | `rx_polarity_toggle` | 1 | [2] |
| Isolated bit errors: each reported (4 or 7), no relock, data intact around them | `rx_errors` / `rx_errors_sweep` | 1 / 2 | [1] / [2..6] |
| Two close bit errors relock by design; RxValid low until the next comma; data resumes from that COM within the lock target | `rx_close_errors` / `rx_close_errors_sweep` | 1 / 2 | [1] / [2..6] |
| [Relock rule](../../arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock) (limit, decay, one request per lock, crossing 4) against error bursts | `rx_error_bursts` / `rx_error_bursts_sweep` | 1 / 2 | [1] / [2..8] |
| Bit slips in packet traffic at up to 300 ppm: lock dropped within 48 cycles of each slip in lock, regained at the next COM within the lock target | `rx_slips` / `rx_slips_sweep` | 1 / 2 | [1] / [2..6] |
| Electrical-idle gaps: RxValid low, no buffer status, lock start and the lock target after each gap, data intact | `rx_elec_idle_gaps` / `rx_elec_idle_gaps_sweep` | 1 / 2 | [1, 2] / [3, 4, 5, 6, 12, 14, 22, 24, 28, 30, 34, 36], coverage on the first five; 12, 14, 24, 28 and 34 lock once on noise |
| [Loopback](../../arch/pcs/PIPE_CONTROL.md#loopback): received words re-encoded two cycles after they are on PIPE, errors as EDB | `loopback` | 1 | [1] |
| Round trip: TxData through the encoder, line, aligner, buffer and decoder back to RxData, intact | `round_trip` | 1 | [1] |

**Level placement** ([#272](https://github.com/amichai-bd/pcie-factory/issues/272)):
`rx_ppm`, `rx_slips`, `rx_close_errors`, `rx_errors`, `rx_error_bursts`,
`rx_slips_training`, `eb_dense` and `rx_reset_traffic` run seed 1 (fast
RX clock) in level 1, and their seed 2 (slow RX clock) moved into the
entry's level 2 sweep, so the merged level 0 to 2 runs are the same runs.
The slow clock stays in level 1 through the entries that keep both seeds.
The entries whose seed 2 is the first catch of a mutant below keep it in
level 1: `eb_lock_start`, `rx_elec_idle_gaps` and `rx_eios_idle` (the
read-side lock-start drop and the recentring hold), and `eb_random_offset`
(the `pcs_elastic` floor of level 1, whose random addition bin only seed 2
reaches on levels 0 and 1).

**Seeds and coverage runs** ([#327](https://github.com/amichai-bd/pcie-factory/issues/327)).
The long random entries `rx_align_long` (8 to 4 seeds), `tx_random_long`
(8 to 4), `rx_decode_random_long` (4 to 2), `eb_long_random` (6 to 3),
`eb_long_tlp` and `eb_long_stress` ([1..6] to [1..4], two per sign) lost
seeds that, on the merged level 0 to 2 coverage of `005836f`, hit no point
or bin the rest of the levels miss, and none is the catch of a mutant
below (`eb_long_idle`, which is, keeps its four seeds). `--coverage`
instruments each entry's first seed only, except where a later seed
reaches a bin the first does not: `rx_elec_idle` and `eb_random_offset`
(both seeds; the slow-clock and random-addition bins) and
`rx_elec_idle_gaps_sweep` (the first five seeds), so the merged coverage
is unchanged bin for bin.

The model unit tests (`python3 -m pytest src/dv/pcs/models`) check the
encoder model against published codes (among them the A7 rule, D17.7 at
RD- and D11.7 at RD+ with their primary forms at the other RD, and `Kx.7`
as K23.7 and K28.7 at both RDs), the architecture model's `decode()`
against a classic-table oracle typed from the published 5b/6b and 3b/4b
tables (all 1024 codes at RD-, RD+ and an unknown RD: byte, K flag, RD
after, error class), which is independent of the architecture model that
the decoder model and `rx_decode_table` build on, the decoder model's
column sizes and rules, the aligner model's locks, the traffic kinds, the receive
scoreboard, and the RTL package's constants (`EB_*`, the raw COM and SKP
codes) and the shared `common_pkg` codes (`SYM_EDB`, RxStatus, PowerDown)
against the architecture model and the pages.

## Leaf checks, old to new

Every check of the removed leaf testbenches, and where it is now.

| Old entry (block) | What it checked | Now |
| --- | --- | --- |
| `smoke` (encoder) | Reset, latency 2, published anchors, compliance, EDB, RD hold | `tx_smoke` |
| `exhaustive` (encoder) | Every symbol at both RDs in both lanes, EDB for 244 unsupported K, back to back and with idle cycles; coverage holes fail | `tx_table` (same words through PIPE TX; idle cycles are TxElecIdle); received side: `table_round_trip` |
| `random_vs_model`, `long_random` (encoder) | Random words, idle, compliance, resets against the cycle model | `tx_random`, `tx_random_long` (both reset kinds with words in flight), `pipe_random` |
| Encoder model unit tests | Published anchors in both lanes, compliance, EDB, pipeline | Kept: [`test_pcie_pcs_enc8b10b_model.py`](../../../../src/dv/pcs/models/test_pcie_pcs_enc8b10b_model.py), with A7 (D17.7, D11.7) and `Kx.7` (K23.7, K28.7) anchors added (the model also serves the `phy` e2e's encoder block hook) |
| Encoder assertions `a_lane*_disparity`, `a_run_length` | Disparity per lane, run length | Kept in the RTL, live in every test |
| `smoke` (decoder) | Commas at both RDs, a disparity error, a code error to EDB, reset | `rx_decode_smoke` |
| `exhaustive` (decoder) | 1024 codes x both lanes at RD-, RD+ and unknown, with the RD after; class counts | `rx_decode_table`: both lanes at RD- and RD+ and lane 0 at unknown, with the RD after, and the counts; both lanes unknown through unlocked words (data and K; the code-error flag shows as EDB). The RD unknown after an unlocked word: RTL assertion `a_rd_unknown_after_unlocked`. Lane 1 at an unknown RD *with* the RD after it, and the lane-1 causes "lane 0 unlocked" and "generation split", are [gaps](#gaps) |
| `random_vs_model`, `long_random` (decoder) | Encoded traffic with bit errors, random codes, RD slips; valid gaps, lock drops, generation changes, polarity, reset | `rx_decode_random`, `rx_decode_random_long` (errors, codes, RD slips, polarity, `pipe_reset_ni`, every word exact); valid gaps: `eb_underflow` (no error after an underflow word); lock drops and new generations: `rx_decode_table` (after the unlocked run), `rx_elec_idle_gaps`, `eb_lock_start`; same-cycle relocks: `rx_slips_training` |
| Decoder model unit tests | Column sizes, K28.5, EDB, RD-unknown rules | Kept: [`test_pcie_pcs_dec8b10b_model.py`](../../../../src/dv/pcs/models/test_pcie_pcs_dec8b10b_model.py), with a classic-table cross-check of `decode()` at RD-, RD+ and an unknown RD added |
| `lock_offsets` (aligner) | Lock from every bit offset at both RDs, one lock, symbols whole, no relock | `rx_lock_offsets` (one intact run per lock, the boundary checked by the data; comma position from the aligner model as coverage) |
| `controls` (aligner) | Relock while hunting; relock at the eight phases of the TS1 COM period; idle drop; one-word idle pulses at eight phases; reset while locked | Idle pulses and reset: `rx_lock_controls`; relocks in TS1 traffic, same-cycle and to hunting: `rx_slips_training` (`pcs_align_relock`); idle drop: `rx_lock_offsets`, `rx_elec_idle_gaps`. A relock while hunting has no effect at the ports ([gap](#gaps)); the request phase is not a port, so the eight phases are covered as the two outcomes |
| `slips` (aligner) | Closed loop: slips in TS1, FTS, packets, SKP sets, pairs, detected within 32 cycles; same-cycle relock re-armed through the generation; flips keep lock | `rx_slips_training`, `rx_slips_packets`, `rx_slips` (the closed loop is the real error counter; detection is checked as the end of errored words within 48 cycles, and RxValid falling within 48 cycles in packet traffic) |
| `no_false_lock` (aligner) | Data at 20 offsets, idle noise, unflagged noise then recovery; the lowest comma offset wins | `rx_no_false_lock`, with a directed window holding commas at two symbol boundaries, checked by the RTL assertion `a_lock_at_lowest_comma` |
| `long_random` (aligner) | 40 mixed segments, every check | `rx_align_long` |
| Aligner model unit tests and assertions | The cycle model; offset range, no lock from idle, relock drops lock, generation flips only on lock, lock word holds a comma | Kept: [`test_pcie_pcs_align_model.py`](../../../../src/dv/pcs/models/test_pcie_pcs_align_model.py); the assertions in the RTL |
| `smoke` (elastic buffer) | Reset, RX reset timing, start at fill 8, lossless, SKP sets of 1 to 5 | Lossless and sets of 1 to 5: `eb_random_*`, `eb_dense`; start at fill 8: RTL assertion `a_start_at_8` in every test. RX reset timing is a [gap](#gaps) |
| `ppm_m600`, `ppm_m300`, `ppm_p300`, `ppm_p600`, `random_offset` (elastic buffer) | Additions or removals per offset, fill 4 to 12 | `eb_tlp_m600`, `eb_random_m300`, `eb_random_p300`, `eb_tlp_p600`, `eb_random_offset`; fill 4 to 12 by `FillCheck` in every receive test within the targets |
| `short_burst_m600`, `stress_sizes` | The reviewer's case; stranding sizes | `eb_short_burst`, `eb_stress_sizes` |
| `idle_m600`, `idle_p600` | Recentring, with the leaf's non-vacuity assertion (a read-side hold at -600 ppm, a write-side drop at +600 ppm), 60 packets | `eb_idle_m600`, `eb_idle_p600` (holds and drops counted on `r_recentre` and `w_drop`) |
| `lock_start_m600`, `lock_start_p600` | No locked word packed with an unlocked symbol | `eb_lock_start` (every run starts at its comma or the symbol before it), also in `rx_elec_idle_gaps` and every lock test |
| `relock_generation` | Generation carried per word through same-cycle relocks | `rx_slips_training` (same-cycle relocks; the error counter re-arms on the generation, so the next slip is answered) |
| `thresholds_swing`, `thresholds_m600`, their sweep | Removal exactly at a seen fill of 9, addition exactly at 7, both sides of each threshold | RTL assertions `a_fill_high_at_9` (fill high, the write side's removal condition, exactly at 9 or more), `a_add_at_7_or_less` and `a_add_when_due` (an addition only at 7 or less, and every possible one made there), live in every test; additions and removals in the right direction (`eb_*`); `FillCheck` |
| `pause_corners`, its sweep | Write pauses, bare COM, back-to-back sets, removal reported on the next word, overflow and underflow mixed with SKP changes | Bare COM, back-to-back sets: `eb_dense`; overflow and underflow: `eb_overflow`, `eb_overflow_leftover`, `eb_underflow`, `eb_stress`. Write pauses and the next-word removal report are [gaps](#gaps) |
| `overflow`, `underflow` | Losses flagged; underflow words, nothing lost | `eb_overflow`, `eb_underflow` |
| `reset_restart` | Reset with traffic in flight, clean restart | `rx_reset_traffic` (`rst_i` and `pipe_reset_ni`), `rx_lock_controls` |
| `long_m600`, `long_p600`, `long_random`, `long_stress`, `long_idle` | Long runs | `eb_long_tlp`, `eb_long_random`, `eb_long_stress`, `eb_long_idle` |
| Elastic model unit tests | The stream checker; traffic kinds; RTL constants against the arch model | Traffic kinds and RTL constants kept in [`test_pcie_pcs_traffic.py`](../../../../src/dv/pcs/models/test_pcie_pcs_traffic.py); the checker read the buffer's internal words and is replaced by the receive scoreboard |

## Mutation evidence

Each mutant was built in a scratch copy of `src/` (`./build sim
--src-root <copy>`) and run against the listed entries at their first
regression seed (or the seed named); the unmutated RTL passes all of them.
The three mutants that survived the first IP-level pass (lock at the
highest comma, the RD kept known after an unlocked word, RxValid from
symbol 0's lock) and the two threshold mutants the leaf caught on `fill_o`
are now caught: by RTL assertions, a directed two-comma window and the
slow-clock seeds.

| Block | Mutant | Caught by | Not caught by |
| --- | --- | --- | --- |
| Encoder | A7 rule dropped at RD+ | `tx_table`, `tx_random`, `table_round_trip` | - |
| Encoder | TxCompliance ignored | `tx_smoke`, `tx_table`, `tx_random`, `pipe_random` | - |
| Encoder | Lane 1 encoded at lane 0's RD, not the RD between the symbols (`u_sym1.rd_i` from `rd_lane0`) | `tx_smoke`, `tx_table`, `tx_random`, `table_round_trip`, `pipe_random`, `round_trip` (RTL assertion `a_lane1_disparity`; seeds 2061514873, 767703684, 1667229892, 1, 98053844, 1 fail at 272, 288, 288, 256, 416 and 256 ns) | - |
| Decoder | Disparity errors not flagged | `rx_decode_smoke`, `rx_decode_table`, `rx_decode_random` | - |
| Decoder | A polarity change keeps the RD known | `rx_decode_table`, `rx_decode_random`, `rx_polarity_toggle` | - |
| Decoder | The RD stays known after an unlocked word | `rx_smoke`, `rx_decode_table`, `rx_elec_idle_gaps` (RTL assertion `a_rd_unknown_after_unlocked`) | - |
| Decoder | Lane 1 starts from the stored RD, not lane 0's | `rx_decode_smoke`, `rx_decode_table`, `rx_decode_random` | - |
| Aligner | Lock at the highest comma offset | `rx_no_false_lock` (the two-comma window; RTL assertion `a_lock_at_lowest_comma`) | `rx_slips_training`, `rx_slips_packets`, `rx_lock_offsets` (valid 8b/10b has one comma boundary per window) |
| Aligner | Hunting with an idle word in the window | `rx_lock_controls`, `rx_lock_offsets` | - |
| Aligner | `relock_i` ignored | `rx_slips_training`, `rx_slips` (and `a_relock_drops_lock`) | `rx_errors` (no relock wanted) |
| Elastic buffer | No write-side lock-start drop (`w_flush` 0) | `eb_lock_start` seed 1 (fast RX clock) | `rx_elec_idle_gaps` seed 1 |
| Elastic buffer | No read-side lock-start drop (`r_flush` 0) | `eb_lock_start` seeds 2 and 4, `rx_elec_idle_gaps` seeds 2 and 22 (slow RX clock) | - |
| Elastic buffer | Generation not carried in the FIFO word | `rx_slips_training`, `rx_close_errors`, `rx_error_bursts` | `rx_elec_idle_gaps` seed 1 |
| Elastic buffer | No addition after a COM in the first slot | `eb_tlp_m600` | `eb_dense` seed 1, `rx_ppm` seed 1 (fast RX clocks) |
| Elastic buffer | No read-side recentring (`r_recentre` 0) | `eb_idle_m600` seed 1, `eb_lock_start` seed 2, `eb_long_idle` seed 2, `rx_elec_idle_gaps` seed 2: the visible fill falls below 4 (`FillCheck`) before the hold count is checked | - |
| Elastic buffer | No write-side recentring (`w_drop` 0) | `eb_idle_p600` seed 1, `eb_lock_start` seed 1, `eb_long_idle` seed 1 (the visible fill rises above 12, `FillCheck`); `rx_elec_idle_gaps` seed 1 (first locked word past the lock target) | - |
| Elastic buffer | Second output symbol overflow flag ignored (`ovf` from `o0.ovf` only) | `eb_overflow_leftover` seed 1: no overflow reports after the forced odd packing (0 reports, requires at least 2) | - |
| Elastic buffer | Overflow flag never set | `eb_overflow`, `eb_stress` | - |
| Elastic buffer | Addition threshold at 6 (`EB_LOW` 6) | `eb_tlp_m600`, `eb_short_burst`, `eb_dense_sweep` seed 2 (`a_add_when_due`) | - |
| Elastic buffer | Fill high at 10 (`EB_HIGH` 10) | `eb_tlp_p600` (`a_fill_high_at_9`) | `eb_dense` seed 1, `rx_ppm` seed 1: on a fast clock with no addition the visible fill stays even and skips 9 |
| Elastic buffer | Start at 10 (`EB_NOMINAL` 10) | `rx_smoke`, `eb_tlp_m600` (`a_start_at_8`) | - |
| Elastic buffer | Removal of a second-slot COM's SKP not reported (`r_rem1` dropped) | `eb_tlp_p600`, `eb_dense` | `rx_ppm` seed 1 |
| Top | Relock at 2 errored words | `rx_errors`, `rx_error_bursts`, `rx_decode_table` | - |
| Top | RxValid from symbol 0's lock alone | `rx_relock_mid_word` seeds 1 and 2, `rx_close_errors` seed 1 and `rx_close_errors_sweep` seed 2, `rx_slips` seed 1 (`ValidRule`: RxValid 1 on a word whose second symbol lost lock by a relock) | `eb_idle_m600` seed 1 (its lock ends are before idle, now valid idle tails) |
| Aligner | Lock dropped on the idle word itself (`idle_q` in `keep`, the rule before #182) | `rx_eios_idle` seeds 1 and 2, `eb_idle_m600` seed 1, `rx_elec_idle_gaps` seed 1 (the last symbol before the gap never leaves under RxValid) | - |
| Top | No idle tail (`word_tail` 0) | `rx_eios_idle` seeds 1 and 2, `eb_idle_m600` seed 1 (`ValidRule` fires first: RxValid 0 on a word its lock tags make an idle tail; without it, the EIOS's last symbol is lost) | `rx_elec_idle_gaps` seed 1 |
| Aligner | No EIOS completion (`tail` 0) | `rx_eios_phases` seeds 1 and 2 (the EIOS not whole before idle at a phase of 10 to 19) | `rx_eios_idle` seed 1, `eb_idle_m600` seed 1 (phase 0) |
| Elastic buffer | Recentring hold on the head word's lock (`head0` instead of `c0`, the rule before #182) | `rx_eios_idle` seed 2, `eb_idle_m600` seed 1, `eb_lock_start` seed 2, `rx_elec_idle_gaps` seed 2 (a locked leftover held behind an unlocked head splits a run before idle) | `rx_eios_idle` seed 1, `rx_eios_phases` seed 2 |
| Top | RxElecIdle straight from `ser_rx_elec_idle_i` (a fast path, the defect of #182) | `rx_eios_idle` seed 1, `rx_eios_phases` seed 1, `eb_idle_m600` seed 1 (RxValid 1 with RxElecIdle 1), `rx_elec_idle` seed 1 (out of order) | - |
| Top | RxElecIdle from symbol 1's idle flag (one symbol early) | `rx_eios_idle` seeds 1 and 2, `eb_idle_m600` seed 1 (RxValid 1 with RxElecIdle 1 on an idle tail) | `rx_elec_idle_gaps` seed 1 |
| Elastic buffer | One lock tag per FIFO word (the rule before #182) | `rx_eios_idle` seed 1 (an EIOS split across two FIFO words loses its last IDL) | `rx_eios_idle` seed 2, `eb_idle_m600` seed 1, `rx_elec_idle_gaps` seed 1 |
| Top | Decoder reset by `rst_i` only (not `pipe_reset_ni`) | `rx_decode_smoke` | `rx_decode_random` seed 1, `rx_reset_traffic` seed 1 |

Every mutant is caught; none survives the L1 list. #182 made the lock
end before electrical idle a valid word on purpose (an idle tail), which
was where **RxValid from symbol 0's lock alone** used to be caught; it is
now caught at a relock end inside a word by the directed
`rx_relock_mid_word` and by `ValidRule` in every receive test. Each mutant
is caught by at least one entry that runs in L0 or L1 at the seed shown (the
first regression seed otherwise); entries named `_sweep` run in L2.

The #162 re-review corrected the old top's row for the lock-start drop:
each single-side mutant was caught only by its own leaf `lock_start_*`
test, not by `rx_elec_idle_gaps` seeds 22 and 30. Through the ports the
lock-start check (every run starts at its comma or the symbol before it)
now catches each side, as the table shows.

## Gaps

These leaf checks are not reachable through the `pcie_pcs` ports. Each
stays covered as stated; none is dropped silently.

| Check | Why not through the ports | Covered by | Owner |
| --- | --- | --- | --- |
| Elastic buffer: write pauses (`ser_rx_valid_i` low) | The top ties it to 1: the aligner sends a word every RX cycle | Not needed by the IP | - |
| Elastic buffer: removal reported on the next word | Needs a visible fill below 4, outside the targets | The scoreboard rejects a SKP status on a word with no COM change | - |
| RX-domain reset timing (`ser_rx_rst_o` asserts with `rst_i`, releases on the second RX edge) | Internal net | [`common_cdc_rst`](../common/cdc/common_cdc_rst.md)'s library DV; `rx_reset_traffic` and `rx_lock_controls` restart after `rst_i` | `common` |
| Decoder: lane 1 at an unknown RD with the RD it leaves; lane 1 unknown because lane 0 alone is unlocked, or because the two symbols' generations differ | The lock-start drop removes every word with an unlocked first symbol and a locked second one (a half-locked word occurs only at a lock end, locked then unlocked); a generation split needs a same-cycle relock with an odd leftover symbol, which no stimulus places deterministically; an unlocked lane 1's RD never reaches a probe, as the next word is decoded with the RD unknown | Lane 1's data and K at an unknown RD (`rx_decode_table`); lane 0 at an unknown RD with the RD it leaves (the same `decode()` function); the model unit tests | - |
| Aligner: a relock request while hunting | It changes nothing on the ports | RTL assertion `a_relock_drops_lock` | - |

## Coverage holes

Line and toggle floors use the
[by-source union](../../../tools/regress/SPEC.md#coverage): each source point
counts once and is covered if any instance covers it. Functional coverage
is unchanged. The source remeasurement for
[#228](https://github.com/amichai-bd/pcie-factory/issues/228) uses
[nightly 39384846565](https://github.com/amichai-bd/pcie-factory/actions/runs/39384846565).
Separately measured low-level runs supply their own floors.

| Selected levels | Source line | Source toggle | Line / toggle floors |
| --- | --- | --- | --- |
| L0 | 348/393 (88.55%) | 2963/3378 (87.71%) | 88 / 87.2 |
| L0+L1 | 390/393 (99.24%) | 3307/3378 (97.90%) | 99 / 97.3 |
| L0+L1+L2 | 390/393 (99.24%) | 3309/3378 (97.96%) | 99 / 97.5 |

These measurements precede the #437 test addition and remain the
conservative floor basis; final integrated coverage remeasures the union.

`eb_overflow_leftover` closes the four
`c1.ovf`/`o1.ovf` toggle holes: 24 TS1 sets at -10000 ppm drain the
buffer until its single SKP set causes exactly one read-side addition.
The RX clock then switches to +20000 ppm for plain data with no further
SKP sets. The addition leaves an odd symbol pending; that parity remains
while the faster writer fills and overflows the FIFO. At seed 1, both
0-to-1 and 1-to-0 transitions of each field occur 107 times, with 107
overflow reports on PIPE. The test checks the addition, requires multiple
overflow reports, and checks all stream losses occur near those reports.
These offsets deliberately exceed the normal targets; no fill-window
waiver is added for normal traffic.

### Per-instance diagnostic baseline

The counts and prior floor values below describe the original per-instance
view at the revisions cited. Current code floors use the source table above.
A point hit in another parameter set or instance is covered in the source
union; the other reviewed causes remain diagnostic explanations, without
adding a waiver.

The remaining causes from that baseline are listed below; the four
overflow-leftover holes above are closed.

| Cause | Line | Toggle | Points |
| --- | ---: | ---: | --- |
| Forbidden by an assertion | 1 | 2 | The encoder's `run6` (`a_run_length`: no run of six equal bits across a word). |
| Tool artefact | 2 | 0 | The two early `return` branches of `eios_step` (package): Verilator never counts a branch whose body is a function `return`; the block count less the fall-through count gives their hits, and every EIOS check (`rx_eios_*`) depends on them. |
| Constant by design | 0 | 30 | `edb` in the encoder symbol (the EDB code); the elastic buffer's `ser_rx_valid_i` (tied 1, see [gaps](#gaps)); the per-symbol `ovf` fields that are tied 0 (`w_new0`, `w_new1`, `head1`, the held and leftover slots; a word's overflow travels in `pend_ovf` and the head word's flag); `sync_d[0]` of the RX reset synchronizer (its input, a constant). |
| Unreachable by construction | 0 | 28 | Counter top bits: the holdoff counters (loaded with 7, or 8 and decremented first), `clean_cnt[3]` (the decay restarts at 8), `ready_cnt_d[4]` (16 is only the reset value); the visible fill's top bit and those of the symbol fill (`vis[4]`, `dst_fill_o[4]`, `fill[5]`, `eb_fill[5]`, `fill_o[5]`: the reader sees the write pointer two to three RX cycles late and pops a word each cycle meanwhile, so it never sees all 16 words, even when the writer overflows in `eb_overflow`); `c0.rem` and `o0.rem` (a removal flag is acted on while its COM is in the second slot, and `rem_ack` clears it before the SKP reaches the first). |
