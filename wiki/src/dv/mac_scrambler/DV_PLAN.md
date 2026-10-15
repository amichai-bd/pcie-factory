# pcie_mac_scrambler DV plan

## Strategy

- **Reference model:** [`pcie_mac_scrambler_model.py`](../../../../src/dv/mac_scrambler/models/pcie_mac_scrambler_model.py)
  runs every symbol through the `Scrambler` of the mac
  [architecture model](../../arch/mac/MODEL.md), imported from
  `src/arch/mac/`, and adds only the two symbols per word, `valid_i` and
  the output register of the [MAS](../../rtl/mac_scrambler/MAS.md#behavior).
  Its pytest unit tests (`python3 -m pytest src/dv/mac_scrambler/models`)
  check the arch scrambler against an independent bit-serial statement of
  the [symbols page](../../arch/mac/SYMBOLS.md#scrambling) over 65536 keys
  (a full period of 65535 and the wrap to FFh), and the known answer, COM, SKP, K, disable, `valid_i`,
  reset and round-trip rules, without a simulator.
- **Tests:** [`tests/test_mac_scrambler.py`](../../../../src/dv/mac_scrambler/tests/test_mac_scrambler.py)
  drives `pcie_mac_scrambler` directly (no SV testbench;
  `mac_scrambler.dv.f` is empty). Inputs change at the falling edge, and
  every cycle `valid_o`, and `data_o` and `datak_o` when valid, are compared
  with the model (the scoreboard). `known_answer` also checks each rule
  against bytes taken from the HAS key sequence, not from the model. Random
  tests draw from a `random.Random` seeded with the regression seed, log
  it, and fail if a reset, a `valid_i` gap, a COM or a SKP never occurred.
  Stimulus mixes, in both lanes, COM, SKP, the other K symbols of the
  symbols page, data bytes (BCh and 1Ch biased up), and `scr_i` 0 and 1.
- **Functional coverage:** `mac_scrambler_lanes` (lane x symbol kind: COM,
  SKP, other K, scrambled data, unscrambled data, and data bytes equal to
  COM or SKP), `mac_scrambler_pairs` (kind of `[7:0]` x kind of `[15:8]` in
  one word, 25 bins) and `mac_scrambler_controls` (`rst_i` x `valid_i`),
  sampled where stimulus is driven. Merged L0+L1+L2 coverage is 100% line
  (24/24), toggle (326/326) and functional (66/66); `level2.yaml` sets all
  three floors to 100.
- **Mutation evidence:** in a local copy of the RTL, each of these breaks
  failed the L0 test and all seven L1 runs (scoreboard mismatch or an RTL
  assertion): SKP advancing the LFSR; symbol 1 keyed from the LFSR before
  symbol 0 (no chaining); K symbols scrambled when `scr_i` is 1; COM not
  resetting the LFSR. The RTL was restored and re-run green.
- **Out of scope:** which symbols the MAC marks with `scr_i`, and the
  scrambler in the MAC's two directions, belong to the parent
  ([mac DV plan](../mac/DV_PLAN.md)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Known answer, COM in either lane, SKP hold, other K, disable, data bytes equal to COM/SKP, `valid_i` hold, reset](../../rtl/mac_scrambler/MAS.md#behavior) | `known_answer` | 0 | 1; directed, expected bytes from the HAS key sequence |
| [Every rule in random mixes in both lanes, with `valid_i` gaps and resets](../../rtl/mac_scrambler/MAS.md#behavior) | `random_vs_model` | 1 | 3; 5000 random cycles vs the model |
| [Disable: `scr_i` 0 passes symbols unchanged while the LFSR follows the stream](../../rtl/mac_scrambler/MAS.md#behavior) | `disable` | 1 | 2; 40 alternating disabled and enabled stretches vs the model |
| [Descrambling: scrambler then descrambler gives the stream back](../../rtl/mac_scrambler/MAS.md#behavior) | `round_trip` | 1 | 2; 3000 random words scrambled, reset, fed back |
| [Full LFSR period and long random runs](../../rtl/mac_scrambler/MAS.md#behavior) | `long_random` | 2 | 8; 65536 keys (FFh again at key 65535), then 100000 random cycles vs the model |

Every [interface](../../rtl/mac_scrambler/MAS.md#interfaces) port is
exercised by every entry. Every feature in the MAS has a row; there are no
gaps.
