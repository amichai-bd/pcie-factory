# pcie_phy DV plan

## Strategy

- **Reset contract:** an RTL port (`RtlPort`) checks every output against
  its [reset value](../../arch/phy/HAS.md#clocks-and-resets) at each
  falling edge that follows a rising edge with `rst_i` high. While in
  reset, the inputs toggle at random:
  - the upper-layer inputs, meaning the packet stream and the link
    requests, but not `port_role_i`;
  - `ser_tx_rxdet_i`;
  - with the channel model, `ser_rx_data_i` and `ser_rx_elec_idle_i`. This is checked in the first reset, in
  both port roles, and in resets asserted while packets flow.
- **Back-to-back e2e:**
  [`tests/test_phy_e2e.py`](../../../../src/dv/phy/tests/test_phy_e2e.py)
  joins a downstream and an upstream stack, port a and port b, over the
  channel and checks the [HAS](../../arch/phy/HAS.md) end to end: reset to
  L0 ([link-up](../../arch/phy/HAS.md#link-up-and-requests)), then the
  [packet stream](../../arch/phy/HAS.md#packet-stream) both ways, TLP- and
  DLLP-tagged, some TLPs nullified, under clock offset and channel
  impairments, in both port-role orders.
- **Testbench top:**
  [`tb/pcie_phy_e2e_tb.sv`](../../../../src/dv/phy/tb/pcie_phy_e2e_tb.sv)
  holds two `pcie_phy` instances, each on its own clock, with each one's
  `ser_rx_clk_i` the far port's clock (the recovered clock follows the far
  transmitter). Every other `pcie_phy` port is a top port prefixed `a_` or
  `b_`. With the layers as models, the instances are held in reset.
  `a_clk_i` runs at 125 MHz and `b_clk_i` `ppm` off it, with period
  8 ns × (1 + ppm × 10⁻⁶): **a positive ppm makes b the slower clock**. The
  edges are dithered to the picosecond so the average offset is exact.
- **Environment:**
  [`tb/phy_e2e_env.py`](../../../../src/dv/phy/tb/phy_e2e_env.py) runs one
  coroutine per clock. At each falling edge the port of that clock steps
  (its models, or its pins when it is RTL), and its 20-bit transmit word
  crosses the channel to the far port's receive front end. The upper layer
  is a data-link stub: each packet carries a 16-bit sequence number first
  and a check code last (CRC-32 on a TLP, CRC-16 on a DLLP).
- **Scoreboard:** per direction. A packet delivered without
  `pkt_rx_err_o` and with a good check code must be the next one sent or a
  later one (a gap is a loss), byte for byte, with its tag and nullify
  flag; anything else fails at once with the expected and observed bytes.
  A TLP/DLLP tag swapped on the way fails the check code instead (CRC-16
  versus CRC-32): it is counted, not asserted, and so fails a lossless
  phase.
  A **lossless phase** also requires no loss, no flagged or failed packet
  and no `rcv_err_o`, with both ports in L0 at the end. Loss and flagged
  packets are allowed only while an impairment is in flight; an undetected
  corruption never is.
- **Loopback echo scoreboard:** `LoopbackScoreboard` checks the master's
  echo at its line ports. `RtlPort` records the master's `ser_tx_data_o`
  words and the words it receives on `ser_rx_data_i`, each received word
  tagged with the master's `ltssm_state_o`. Both streams are decoded with
  running disparity, and SKP symbols are left out of both, because the
  slave may add or remove them
  ([pcs loopback](../../arch/pcs/PIPE_CONTROL.md#loopback)). The symbols
  received while the master is in Loopback.Active must decode with no code
  or disparity error. They must also form one contiguous run of the
  symbols sent, in order, with nothing dropped, added or changed. A
  mismatch gives the symbol index, the master's cycle, and the expected
  and observed symbol. The master's
  [data pattern](../../arch/mac/LTSSM.md#what-each-state-transmits) is the
  traffic, so the echo must also hold at least 1.9 pattern symbols per
  held cycle. A pattern symbol is a data symbol one more (mod 256) than
  the data symbol before it. The master sends 2 per cycle, and the margin
  covers the SKP ordered sets and the training sets that are still
  returning at entry.
- **Models** ([`models/`](../../../../src/dv/phy/models/), with pytest
  unit tests that need no simulator):
  - mac: the [`pcie_mac` architecture model](../../arch/mac/MODEL.md),
    `Mac`, stepped at PIPE.
  - pcs: `PcsModel`, a cycle stand-in for the [`pcs` HAS](../../arch/pcs/HAS.md)
    built from the `pcs` architecture model's 8b/10b code and PIPE control:
    encode with running disparity, the [receive path](../../arch/pcs/RECEIVE.md)
    (comma lock, relock after three errored words, the SKP elastic buffer
    at 8 of 16 symbols, decode and RxStatus). It is not cycle-exact to the
    RTL; a relock also flushes the buffer, and loopback is not modelled.
  - channel: `Channel`, one per direction, stepped on the transmitter's
    clock: a few words of delay, and the impairment hooks `flip` (one bit
    in each of the next n words), `slip` (one bit repeated or dropped),
    `idle` (n words of electrical idle without an EIOS) and `invert`.
  - `Bench` composes two ports and two channels; the pytest tests run it
    with a pure-Python two-clock scheduler.
- **Impairments and recovery:** an injection lands while packets flow on
  one direction. A flip or a slip leaves the link in L0 (the receiver
  flags or loses packets, and a slip costs a relock); the receiving port
  then raises `retrain_req_i`, as the data-link layer would on a
  REPLAY_NUM rollover, and both ports must pass through Recovery back to L0. An idle
  line takes the receiver to Recovery on its own. A lossless phase follows
  every recovery. The stub does not replay lost packets: packets still in
  flight after the recovery are skipped, and one delivered late fails as
  out of order.
- **Functional coverage:** `phy_e2e_layers` (mac, pcs and channel, each
  `model` or `rtl`; `stack`: `all_model` or `all_rtl`, mac and pcs as one
  choice, because the mixed choices of step 2 are not planned in M1, see
  below; `tx_scrambler`: the mac
  model's own, the block's cycle model, or `rtl` when the mac layer is RTL;
  `tx_encoder`: the same for the pcs stand-in; sampled once the pair
  has trained), the coverage hook that
  records which layers were RTL in each run; `phy_e2e_run` (port a's role,
  the offset ±300, ±600 or other, crossed); `phy_e2e_events`
  (impairment, recovery directed or autonomous, reset of port a or b while
  packets flow); `phy_e2e_packets`
  (direction, kind tlp, dllp or tlp_nullified, size, direction crossed
  with kind); `phy_e2e_control` (retrain, hot_reset, disable,
  usp_requests_ignored, polarity, reset_single, each sampled when its check
  passes); `phy_e2e_power` (l0s, l1, l2, loopback; hit by `e2e_power` and
  `e2e_loopback`, enabled with
  [#182](https://github.com/amichai-bd/pcie-factory/issues/182)). The run log also names the layers.
- **RTL by default:** every acceptance entry runs both layers as RTL
  (`MAC_RTL` 1, `PCS_RTL` 1). Each model-only copy, `<name>_model`, runs
  the same testcase with the models as a fast reference.
- **Link control and power:** `RtlPort.request` holds the level
  requests (`hot_reset`, `disable`, `loopback`, `pm_state`), and
  `RtlPort.retrain` pulses `retrain_req_i`. The tests that wait out the
  error-path timeouts set `ERR_TIMEOUT_DIV` 1024, the floor
  ([HAS parameters](../../arch/phy/HAS.md#parameters)).
- **Targets:** `Env.check_link_up` measures each port's last link-up from
  reset, in its own cycles: from `rst_i` release to `link_up_o`, and from
  Polling.Active entry to L0. It checks both against the HAS
  [targets](../../arch/phy/HAS.md#targets). `e2e_targets` traces beats and
  frame starts in `RtlPort`:
  - throughput as accepted bytes per cycle, compared with the HAS formula
    in integers;
  - TX latency, from an accepted `sop` to its STP or SDP on
    `ser_tx_data_o`;
  - RX latency, from that frame start entering the far port to its `sop`
    beat.

## Model-to-RTL switch

Each layer is chosen per run by a parameter of the testbench top, set in a
regression entry's `params` (the build SPEC passes them as `-G`); the top
echoes them on `cfg_*_o` and the environment builds the ports from them.

| Layer | Parameter | `0`: model | `1`: RTL |
| --- | --- | --- | --- |
| mac | `MAC_RTL` | `pcie_mac_arch.Mac` | `pcie_mac` inside `u_a`/`u_b` |
| pcs | `PCS_RTL` | `PcsModel` | `pcie_pcs` inside `u_a`/`u_b` |
| channel | `CHAN_RTL` | `Channel` in Python: offset, impairments | One register per direction in the top: ideal, no Python per cycle; needs `PCS_RTL` 1 |

The RTL side of mac and pcs is the whole `pcie_phy` instance, so the two
switch together (the environment rejects a mixed choice). With both at 1,
the environment drives the instance pins through `RtlPort`, which has the
same interface as the model port. A beat that `RtlPort` offers at a
falling edge is taken at the next rising edge if `pkt_tx_ready_o`, read at
that same falling edge, is 1. Ready depends on the MAC state and
`rst_i`, not on valid, so it holds from one rising edge to the next. Every mac and pcs block runs inside its RTL layer. In
the model layers, the block hooks below remain as seams for each block's
cycle model.

**Block hook, transmit scrambler.** `ModelPort(tx_scrambler=block)`
takes scrambling out of the mac model and puts `block` between the mac and
the pcs at the [`pcie_mac_scrambler` interface](../../rtl/mac/MAS.md#scrambler):
each cycle the port reads the block's registered `valid`, `data` and
`datak`, then clocks it with the mac's unscrambled word, `valid` (the
word is sent), `datak` and the per-symbol `scr` bits the mac model would
have scrambled with. The block's one cycle of latency delays the whole
PIPE transmit word. `e2e_scrambler_block` runs it on both ports with
`WordScrambler`, the block's cycle model in the `mac` DV
([`src/dv/mac/models/`](../../../../src/dv/mac/models/pcie_mac_scrambler_model.py)).
The receive side has no hook: which received symbols are descrambled
depends on the mac's parsing, so it switches with the mac layer.

**Block hook, transmit encoder.** `ModelPort(tx_encoder=block)` replaces
`PcsModel`'s encoder and transmit pipeline with `block` at the
[`pcie_pcs_enc8b10b` interface](../../rtl/pcs/MAS.md#encoder):
each cycle the stand-in clocks it with `rst` (PIPE reset), `valid` (the
transmitter is not in electrical idle), the PIPE word, `datak` and
`compliance`, then sends `data_o`, in electrical idle while `valid_o` is 0;
the block's latency is the transmit latency. `e2e_encoder_block` runs it on
both ports with `Encoder`, the encoder block's cycle model in the `pcs` DV
([`src/dv/pcs/models/`](../../../../src/dv/pcs/models/pcie_pcs_enc8b10b_model.py)).

The steps, all owned by
[#86](https://github.com/amichai-bd/pcie-factory/issues/86) (the
amended acceptance of [#131](https://github.com/amichai-bd/pcie-factory/issues/131)
delivers the hooks, the cycle-model runs and this recipe):

0. **Done ([ruling on #86](https://github.com/amichai-bd/pcie-factory/issues/86#issuecomment-6195155836)).** The `pcie_mac_scrambler` and
   `pcie_pcs_enc8b10b` RTL run inside the RTL layers in every
   `MAC_RTL`/`PCS_RTL` entry, and the `tx_scrambler` and `tx_encoder` `rtl`
   bins record them. Per-block adapters would only duplicate that, so none
   were added. The hooks stay as model references (`e2e_scrambler_block`,
   `e2e_encoder_block`). The original recipe, kept for a future per-block
   swap: **Scrambler and encoder RTL, transmit** (once a block is in the `phy`
   build: it is reached through `mac.rtl.f` or `pcs.rtl.f`, which `phy.rtl.f` includes only with
   [#86](https://github.com/amichai-bd/pcie-factory/issues/86); a `.dv.f`
   never lists RTL). Add a `pcie_mac_scrambler` instance per port to the top
   behind a parameter, its ports as top ports `a_scr_*`/`b_scr_*` on the
   port's clock; in the environment, an adapter whose `valid`, `data` and
   `datak` read `*_scr_valid_o`, `*_scr_data_o` and `*_scr_datak_o` and
   whose `clock(rst, valid, data, datak, scr)` drives the inputs. Driven at
   a falling edge and read at the next, it has the same one-cycle timing as
   the model. Pass it as `tx_scrambler`, add the `rtl` bin to the
   `tx_scrambler` coverpoint and a copy of `e2e_scrambler_block` with the
   parameter set. The encoder is the same with `*_enc_*` pins
   (`valid_i`, `data_i`, `datak_i`, `compliance_i`, `valid_o`, `data_o`),
   passed as `tx_encoder`, and a copy of `e2e_encoder_block`; read one cycle
   after its inputs, the RTL adds a cycle of latency over the model, which
   the hook absorbs because it takes electrical idle from `valid_o`.

1. **Whole stack.** `pcie_phy` integrates `pcie_mac` and `pcie_pcs`. The
   shell-contract checks are gone: `e2e_rtl_shell` and `e2e_rtl_shell_chan`
   became `e2e_rtl_link` and `e2e_rtl_link_chan` (see the
   [mapping](#removed-checks)). Their RTL-plumbing part stays
   (`rtl_plumbing`: the `cfg_*_o` echo, `RtlPort` for both ports, `rst_i`
   and `port_role_i` driven through reset per role). The acceptance entries
   carry `params: {MAC_RTL: 1, PCS_RTL: 1}`, and their `_model` copies stay
   as the reference. `CHAN_RTL: 1` runs over an ideal channel, minus the
   impairments: `e2e_rtl_link_chan`, `e2e_targets` and
   `e2e_link_up_spec`.
2. **One layer RTL, the other a model: not planned in M1** ([ruling on #86](https://github.com/amichai-bd/pcie-factory/issues/86#issuecomment-6197199773); post-M1:
   [#194](https://github.com/amichai-bd/pcie-factory/issues/194)). The full RTL stack and the full model stack are both run, so
   the mixed choices add debug value but no acceptance value. The
   `mac x pcs` cross of `phy_e2e_layers`, with its two mixed bins, became
   the `stack` coverpoint with `all_model` and `all_rtl` only. The recipe,
   kept for [#194](https://github.com/amichai-bd/pcie-factory/issues/194): with `mac.rtl.f` and `pcs.rtl.f`
   reached from `phy.rtl.f`, add per-port `pcie_mac` and `pcie_pcs`
   instances to the top behind the parameters, with their PIPE nets as top
   ports, and a `MacRtl` or `PcsRtl` port in the environment that moves
   `PipeTx`/`PipeRx` (the PIPE objects of the mac model) to and from those
   pins; then drop the mixed-choice check.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Link-up](../../arch/phy/HAS.md#link-up-and-requests), [packet stream](../../arch/phy/HAS.md#packet-stream) both ways, a symbol-error burst and a directed retrain, all models | `e2e_smoke` | 2 | 1; dsp first, b +300 ppm |
| The RTL stack, channel model. RTL path: `cfg_*_o` echo, `RtlPort` for both instances, `rst_i` and `port_role_i` through reset per role. [Reset values](../../arch/phy/HAS.md#clocks-and-resets) with the upper-layer inputs random. [Link-up](../../arch/phy/HAS.md#link-up-and-requests) to L0, then 16 packets each way, lossless. Both ports reset while packets flow, once together and once with b up to 64 cycles later: reset values held, back to L0, and 8 packets each way lossless after each | `e2e_rtl_link` / `e2e_rtl_link_sweep` | 2 | derived seed 0 / its derived seed 1 (737010230) pinned; `MAC_RTL` 1, `PCS_RTL` 1; usp first, -300 ppm |
| The same over the RTL channel (the top joins the lines; the environment steps only the ports) | `e2e_rtl_link_chan` | 0 | 1; `MAC_RTL` 1, `PCS_RTL` 1, `CHAN_RTL` 1 |
| [Lossless](../../arch/phy/HAS.md#targets) at ±600 ppm and ±300 ppm, each port-role order: random mix, TLPs up to 1046 bytes, DLLPs; link-up with up to 2000 cycles of reset skew within 100 µs and training 65.536 to 80 µs ([targets](../../arch/phy/HAS.md#targets), `TIMEOUT_DIV` 1024) | `e2e_dsp_first_pos600`, `e2e_usp_first_neg600` (RTL, level 1); `e2e_dsp_first_neg300`, `e2e_usp_first_pos300` (RTL, level 2); all four `_model` counterparts (level 2) | 1 / 2 | 1 each |
| Symbol errors each way: flagged or lost packets only, directed Recovery, lossless after | `e2e_symbol_errors` (RTL, level 1), `e2e_symbol_errors_sweep` (RTL, level 2), `e2e_symbol_errors_model` (level 2) | 1, 2 | RTL: derived seed 0 / its derived seed 1 (1952118910) pinned; model: 2; usp first, +600 ppm |
| Bit slips each way: relock, directed Recovery, lossless after | `e2e_bit_slips` (RTL, level 1), `e2e_bit_slips_sweep` (RTL, level 2), `e2e_bit_slips_model` (level 2) | 1, 2 | RTL: derived seed 0 / its derived seed 1 (1336911784) pinned; model: 2; dsp first, -600 ppm; coverage on both RTL seeds |
| Electrical-idle dropout each way: Recovery on its own, lossless after | `e2e_elec_idle` (RTL, level 1), `e2e_elec_idle_sweep` (RTL, level 2), `e2e_elec_idle_model` (level 2) | 1, 2 | RTL: derived seed 0 / its derived seed 1 (1262508128) pinned; model: 2; usp first, +300 ppm |
| Transmit scrambling by the `pcie_mac_scrambler` block (its cycle model) on both ports: train, lossless, an idle dropout, lossless | `e2e_scrambler_block` | 2 | 1; models; dsp first, -300 ppm |
| 8b/10b transmit by the `pcs` encoder block (its cycle model) on both ports: train, lossless, a bit slip each way, lossless | `e2e_encoder_block` | 2 | 1; models; usp first, +600 ppm |
| A port reset alone while packets flow, a then b: reset values held, the partner leaves L0 through Recovery's divided timeout, both back in L0, lossless after | `e2e_reset_single` | 2 | 1; RTL, `ERR_TIMEOUT_DIV` 1024; dsp first, +300 ppm |
| [Link control](../../arch/phy/HAS.md#link-up-and-requests): `retrain_req_i` on each port (Recovery and back within 4 µs, `link_up_o` high); `hot_reset_req_i` and `disable_req_i` on the upstream port ignored for 4000 cycles with traffic; Hot Reset and Disabled from the downstream port while packets flow (both ports in the state with `link_up_o` low while held, Detect, back to L0); lossless after each | `e2e_control` | 2 | 1; RTL, `ERR_TIMEOUT_DIV` 1024; usp first, +300 ppm |
| [Polarity](../../arch/phy/PIPE.md#handshakes): the a-to-b lane inverted from reset; b sets RxPolarity in training, link-up targets, lossless, then a reset of both ports and lossless | `e2e_polarity` | 1 | 1; RTL; dsp first, -300 ppm |
| [Targets](../../arch/phy/HAS.md#targets) on the RTL over the RTL channel at +600 ppm, each way. Throughput, with 160 back-to-back 274-byte TLPs and 600 DLLPs: the HAS floor 2n/(n+2)·1176/1180 (1.979 and 1.495, ≥ 1.97 and 1.49), checked free of the window's SKP phase. The cycles without a beat must be exactly one framing cycle per packet boundary plus two per SKP ordered set sent, and the SKP ordered sets no more than the 1180-symbol schedule allows in the window. TX latency of every packet exactly 2 cycles ([MAS](../../rtl/phy/MAS.md#timing)), ≤ 6. RX latency ≤ 16 | `e2e_targets` | 1 | 1; RTL, `CHAN_RTL` 1; dsp first |
| [Link-up](../../arch/phy/HAS.md#targets) at `TIMEOUT_DIV` 1, simultaneous reset: release to `link_up_o` within 12.0 to 12.2 ms and training within 65.536 to 80 µs, on simulator clocks with no Python per cycle (about 3 minutes with coverage, so L2) | `e2e_link_up_spec` | 2 | 1; RTL, `CHAN_RTL` 1, `TIMEOUT_DIV` 1 |
| Soak: offset from the seed in ±600 ppm, reset skew up to 2000 cycles with the link-up targets, six random impairments between lossless phases, TLPs up to 4118 bytes (a 4096-byte payload) | `e2e_soak_dsp_first`, `e2e_soak_usp_first` (RTL); the same names with `_model` | 2 | RTL 3 (dsp first) and 2 (usp first), coverage on every one; models 2 each ([seeds](#seeds-and-coverage-runs)) |

**Level placement** ([#272](https://github.com/amichai-bd/pcie-factory/issues/272),
[#435](https://github.com/amichai-bd/pcie-factory/issues/435)).
L0 keeps `e2e_rtl_link_chan`: full RTL link-up, traffic and resets over the
RTL channel. The all-model `e2e_smoke` runs at L2, alongside the model-only
scrambler and encoder recipes. L1 keeps RTL lossless traffic at +600 ppm
and -600 ppm in the two role orders, symbol errors, bit slips, electrical
idle, polarity, throughput targets, power and loopback. The loopback entry
keeps both recorded echo-mutant catches in the gate.

L2 holds the additional ±300 ppm role scenarios, the channel-model
`e2e_rtl_link` and its sweep, and `e2e_control`, which re-checks the
control-state behavior covered by MAC L1 tests. The single-port reset scenario
also stays at L2: the L0 RTL-channel recipe covers reset values and recovery
after both ports reset with traffic, while its single-port divided-timeout
case remains a nightly check. Entries, seeds and per-seed instrumentation
are retained across the combined levels; no floor is lowered.

For historical comparison, #272 measured `e2e_reset_single` at about
70 s of CPU and `e2e_rtl_link` at about 44 s for its then two seeds
(before #389).

**Seed placement** ([#389](https://github.com/amichai-bd/pcie-factory/issues/389)).
That change pinned derived seed 1 of `e2e_rtl_link`, `e2e_symbol_errors`,
`e2e_bit_slips` and `e2e_elec_idle` in their L2 `_sweep` entries while
keeping derived seed 0 at L1. Under #435 both `e2e_rtl_link` seeds run at
L2; the other three retain their gate seed at L1. Instrumentation is
unchanged, including the 54 points reached by `e2e_bit_slips_sweep`.

**Model-only copies run on a rule** ([#327](https://github.com/amichai-bd/pcie-factory/issues/327)).
The nine `_model` entries of level 2 (the two soaks and the seven scenario
copies) run every layer as its model, with the RTL instances held in reset:
they check the reference, and no RTL behaviour change can fail them (a
compile break or a reset-time assertion fails the non-reused entries of
the same default-parameter build, `e2e_scrambler_block` and
`e2e_encoder_block` at level 2). Each carries
`rerun_when: models_change`, so the nightly reruns it only when a file it
depends on changes (the phy DV and arch folders, the mac and pcs models and
arch models it imports, the shared DV helpers, the simulation driver, the
tools, the seed) and otherwise reuses its last pass on the host
([regress SPEC](../../../tools/regress/SPEC.md#reuse-of-unchanged-model-checks));
they were about 6.5 minutes of the nightly's CPU. Each also carries
`coverage_seeds: 0`: a run with the RTL in reset credits no RTL behaviour,
and every `phy_e2e_*` bin they hit is also hit by an RTL entry or the
all-model `e2e_smoke` (level 2), so the floors hold without them.

### Seeds and coverage runs

[#327](https://github.com/amichai-bd/pcie-factory/issues/327): the RTL
soaks went from four seeds each to three (`e2e_soak_dsp_first`: 729640957,
1165168337, 846478919) and two (`e2e_soak_usp_first`: its derived seeds 0
and 1). On the merged level 0 to 2 coverage of `005836f` the cut seeds hit
no point or bin the kept runs miss (the dsp-first seeds 1165168337 and
846478919 share five points no other run hits, so both stay), and no
mutant below names a soak. `--coverage` instruments each entry's first
seed only, except `e2e_bit_slips` (its second seed reaches 54 points the
first does not) and the two soaks (every kept seed), so the merged
coverage is unchanged point for point; a coverage build runs a phy
e2e test about twice as slow as a plain one (`e2e_rtl_link`: 8.2 s against
4.3 s of CPU).

**Power states and loopback** (L1, `MAC_RTL` 1, `PCS_RTL` 1, `ERR_TIMEOUT_DIV` 1024; enabled by [#182](https://github.com/amichai-bd/pcie-factory/issues/182), which carries electrical idle in order with the received words, so the whole EIOS reaches the MAC before RxElecIdle):

| Feature | Testcase |
| --- | --- |
| [Power states](../../arch/phy/HAS.md#link-up-and-requests). L0s: both transmitters reach Tx_L0s.Idle within `L0S_IDLE_CYCLES` + 16 cycles of the request, a packet leaves L0s within 2·N_FTS + 16, and traffic is lossless. L1: entry within 64 cycles, `link_up_o` high, exit through Recovery within 4 µs. L2: entry within 64 cycles, exit through Detect within 100 µs. Lossless after each | `e2e_power` |
| Loopback from each port as master, with packets flowing into the entry: both reach Loopback.Active with `link_up_o` low, then Detect and back to L0, lossless after. In Loopback.Active, the master's data pattern is echoed by the slave, and the [loopback echo scoreboard](#strategy) checks that every symbol is the master's own, in order and unchanged. At least 1.9 pattern symbols per held cycle (3800 in 2000 cycles) must come back. A packet of the master's own delivered back to it fails the packet scoreboard, either as not sent by the far port or as corrupted. Before #182, a packet looped back in an earlier run was delivered in L0 after the master trained against its own echo. This was not a mac defect ([#192](https://github.com/amichai-bd/pcie-factory/issues/192)); the mac HAS states the rule since [#193](https://github.com/amichai-bd/pcie-factory/pull/193). The echo check was added in [#198](https://github.com/amichai-bd/pcie-factory/issues/198) | `e2e_loopback` |

### Removed checks

| Removed check | Now covered by |
| --- | --- |
| `smoke` (`tests/test_phy.py`): every output at its reset value in reset, both roles, random inputs | `e2e_rtl_link` and `e2e_rtl_link_chan`. `RtlPort` checks the reset values on every edge in reset, in both roles. While in reset it drives random values on the upper-layer inputs and on `ser_tx_rxdet_i`. `e2e_rtl_link` also randomizes `ser_rx_data_i` and `ser_rx_elec_idle_i`; in `e2e_rtl_link_chan` the ideal channel drives those two. |
| `smoke`: reset values after reset (the shell's constant outputs) | Dropped: the stack trains after reset ([link-up](../../arch/phy/HAS.md#link-up-and-requests)). |
| `e2e_rtl_shell(_chan)` `shell_contract`: Detect.Quiet, electrical idle and no beat with traffic queued | Dropped (shell only); replaced by link-up, traffic and resets mid-traffic in `e2e_rtl_link(_chan)` |
| `e2e_rtl_shell(_chan)` `rtl_plumbing` | Kept unchanged in `e2e_rtl_link(_chan)` |

## Gaps

| Feature | Owning issue |
| --- | --- |
| One layer RTL and the other a model (step 2 above): not planned in M1 ([ruling on #86](https://github.com/amichai-bd/pcie-factory/issues/86#issuecomment-6197199773)) | [#194](https://github.com/amichai-bd/pcie-factory/issues/194) (post-M1) |

## Mutation evidence

Each mutant was applied in the issue worktree and run with
`./build sim --ip phy --test <entry>` at the entry's regression seed. Each
mutant fails, and the unmutated RTL passes; each mutant was reverted
afterwards.

| Block | Mutant | Entry, seed | Result |
| --- | --- | --- | --- |
| pcs loopback (the slave's echo path) | `enc_in` in loopback flips bit 0 of each echoed data byte of 80h or more: `sym0.data ^ {7'd0, !sym0.k && sym0.data[7]}`. The training sets' data bytes are all below 80h, so the master still reaches Loopback.Active | `e2e_loopback`, 74580909 | Fails on the echo scoreboard at port a's cycle 11431 (master a): echo symbol 182 of 3999, expected the sent data symbol 792, 80h, observed 81h. Before #198 (no echo check), the same entry passes with this mutant. |
| mac ordered sets (the master's pattern) | The pattern count never advances (`pat_d = pat_q`), so every pattern word is the same two bytes | `e2e_loopback`, 74580909 | Fails on the echo scoreboard: expected at least 3800 pattern symbols echoed, observed 1971 of 3999 received. The echo still matches what was sent; only the traffic check fails. |

## Coverage floors

`level2.yaml` sets, per the [DV BKM](../../../bkm/dv.md#coverage), line
78.7%, toggle 75.2% and functional 100%: the merged level 0 to 2 values
(79.27%, 75.77%, 100%) less the BKM margin. Functional 100% holds every
bin of every `phy_e2e_*` covergroup, `phy_e2e_layers` included; its `stack`
coverpoint has only the two planned choices, so the post-M1 gap
([#194](https://github.com/amichai-bd/pcie-factory/issues/194)) adds no
bin. It replaces the per-covergroup floors of `phy_e2e_run`, `_events`,
`_packets`, `_control` and `_power`, which it implies.

## Coverage holes

The phy's code coverage counts the whole stack: `phy.rtl.f` reaches the
mac and pcs RTL. Merged over levels 0 to 2 (the nightly run), 345 of the
1664 line points and 3483 of the 14374 toggle points are not hit. Verilator
counts each parameter set of a block apart
([regress SPEC](../../../tools/regress/SPEC.md#coverage)), and the mac is
built with three here. Every hole, by cause:

| Cause | Line | Toggle | Points |
| --- | ---: | ---: | --- |
| Hit in another parameter set or port | 221 | 1354 | The mac copies built for `e2e_link_up_spec` (dividers 1: training only) and for the entries without `ERR_TIMEOUT_DIV` (no error-path timeout), and port a's or b's copy of a point the other port hits. |
| Not hit by the IP's own testbench either | 27 | 640 | The holes of the [mac](../mac/DV_PLAN.md#coverage-holes) and [pcs](../pcs/DV_PLAN.md#coverage-holes) plans (unreachable, constant, assertion-only, tool artefacts, the timer's top bits), counted in each copy and port; the reasons are there. |
| Reached only by the IP's own testbench | 97 | 1480 | Behaviour two conforming stacks over a channel within the targets never produce; each is covered at the mac or pcs level: Polling.Compliance and TxCompliance; link numbers other than 0 (the downstream port sends 0), N_FTS other than 32 (both ports use the default) and the Disable Scrambling and Compliance Receive bits; malformed TS (K or unknown identifiers, cut sets); the Configuration and Recovery error exits (mismatching TS, PAD and renumbering runs, the error-path and Recovery.Idle timeouts and the `idle_to_rlock` count); the `Rx_L0s.FTS` timeout, the upstream wait withdrawn and a held retrain request; framing errors from a COM or K symbol inside a packet; receiver detection with no receiver; the elastic buffer's overflow and underflow (outside ±600 ppm) and the fill's top bits; an unsupported K sent as EDB; aligner offsets with bit 1 or 2 set (the e2e lines lock at few bit offsets; `rx_lock_offsets` sweeps all 20) and the EIOS idle tail. |
| The stack's own nets | 0 | 9 | `pipe_tx_compliance` (no compliance, above) and `port_role_i`, which changes once per run, so each port's copy toggles one way. |

## Budget placement

[#435](https://github.com/amichai-bd/pcie-factory/issues/435): L0 retains the
full RTL-channel smoke. The all-model smoke and reference sub-block recipes
are L2 checks; extra role/offset, channel-model and control repeats also run
at L2. The current plan above names retained RTL gate breadth and the loopback
mutant catches.

The combined levels keep the original testcases, parameters, seeds and
per-seed coverage instrumentation; no coverage floor is raised or lowered.
The PR records plain and coverage-instrumented CPU measurements separately.

| Entry | Current placement |
| --- | --- |
| `e2e_control` | L2; unchanged seeds, parameters and instrumentation |
| `e2e_dsp_first_neg300` | L2; unchanged seeds, parameters and instrumentation |
| `e2e_usp_first_pos300` | L2; unchanged seeds, parameters and instrumentation |
| `e2e_scrambler_block` | L2; unchanged seeds, parameters and instrumentation |
| `e2e_encoder_block` | L2; unchanged seeds, parameters and instrumentation |
| `e2e_rtl_link` | L2; unchanged seeds, parameters and instrumentation |
| `e2e_smoke` | L2; unchanged seeds, parameters and instrumentation |
