# pcie_phy DV plan

> **Power states wait on [#182](https://github.com/amichai-bd/pcie-factory/issues/182):**
> `e2e_power` and `e2e_loopback` run once it lands ([Gaps](#gaps)).

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
  then raises `retrain_req_i`, as the data-link layer's replay timeout
  would, and both ports must pass through Recovery back to L0. An idle
  line takes the receiver to Recovery on its own. A lossless phase follows
  every recovery. The stub does not replay lost packets: packets still in
  flight after the recovery are skipped, and one delivered late fails as
  out of order.
- **Functional coverage:** `phy_e2e_layers` (mac, pcs and channel, each
  `model` or `rtl`, mac crossed with pcs, `tx_scrambler`: the mac
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
  passes); `phy_e2e_power` (l0s, l1, l2, loopback; hit once
  [#182](https://github.com/amichai-bd/pcie-factory/issues/182) enables
  the tests). The run log also names the layers.
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
2. **One layer RTL, the other a model.** With `mac.rtl.f` and `pcs.rtl.f`
   reached from `phy.rtl.f`, add per-port `pcie_mac` and `pcie_pcs`
   instances to the top behind the parameters, with their PIPE nets as top
   ports, and a `MacRtl` or `PcsRtl` port in the environment that moves
   `PipeTx`/`PipeRx` (the PIPE objects of the mac model) to and from those
   pins; then drop the mixed-choice check.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Link-up](../../arch/phy/HAS.md#link-up-and-requests), [packet stream](../../arch/phy/HAS.md#packet-stream) both ways, a symbol-error burst and a directed retrain, all models | `e2e_smoke` | 0 | 1; dsp first, b +300 ppm |
| The RTL stack, channel model. RTL path: `cfg_*_o` echo, `RtlPort` for both instances, `rst_i` and `port_role_i` through reset per role. [Reset values](../../arch/phy/HAS.md#clocks-and-resets) with the upper-layer inputs random. [Link-up](../../arch/phy/HAS.md#link-up-and-requests) to L0, then 16 packets each way, lossless. Both ports reset while packets flow, once together and once with b up to 64 cycles later: reset values held, back to L0, and 8 packets each way lossless after each | `e2e_rtl_link` | 0 | 2; `MAC_RTL` 1, `PCS_RTL` 1; usp first, -300 ppm |
| The same over the RTL channel (the top joins the lines; the environment steps only the ports) | `e2e_rtl_link_chan` | 0 | 1; `MAC_RTL` 1, `PCS_RTL` 1, `CHAN_RTL` 1 |
| [Lossless](../../arch/phy/HAS.md#targets) at ±600 ppm and ±300 ppm, each port-role order: random mix, TLPs up to 1046 bytes, DLLPs; link-up with up to 2000 cycles of reset skew within 100 µs and training 65.536 to 80 µs ([targets](../../arch/phy/HAS.md#targets), `TIMEOUT_DIV` 1024) | `e2e_dsp_first_pos600`, `e2e_dsp_first_neg300`, `e2e_usp_first_neg600`, `e2e_usp_first_pos300` (RTL); the same names with `_model` (models) | 1 | 1 each |
| Symbol errors each way: flagged or lost packets only, directed Recovery, lossless after | `e2e_symbol_errors` (RTL), `e2e_symbol_errors_model` | 1 | 2 each; usp first, +600 ppm |
| Bit slips each way: relock, directed Recovery, lossless after | `e2e_bit_slips` (RTL), `e2e_bit_slips_model` | 1 | 2 each; dsp first, -600 ppm |
| Electrical-idle dropout each way: Recovery on its own, lossless after | `e2e_elec_idle` (RTL), `e2e_elec_idle_model` | 1 | 2 each; usp first, +300 ppm |
| Transmit scrambling by the `pcie_mac_scrambler` block (its cycle model) on both ports: train, lossless, an idle dropout, lossless | `e2e_scrambler_block` | 1 | 1; models; dsp first, -300 ppm |
| 8b/10b transmit by the `pcs` encoder block (its cycle model) on both ports: train, lossless, a bit slip each way, lossless | `e2e_encoder_block` | 1 | 1; models; usp first, +600 ppm |
| A port reset alone while packets flow, a then b: reset values held, the partner leaves L0 through Recovery's divided timeout, both back in L0, lossless after | `e2e_reset_single` | 1 | 1; RTL, `ERR_TIMEOUT_DIV` 1024; dsp first, +300 ppm |
| [Link control](../../arch/phy/HAS.md#link-up-and-requests): `retrain_req_i` on each port (Recovery and back within 4 µs, `link_up_o` high); `hot_reset_req_i` and `disable_req_i` on the upstream port ignored for 4000 cycles with traffic; Hot Reset and Disabled from the downstream port while packets flow (both ports in the state with `link_up_o` low while held, Detect, back to L0); lossless after each | `e2e_control` | 1 | 1; RTL, `ERR_TIMEOUT_DIV` 1024; usp first, +300 ppm |
| [Polarity](../../arch/phy/PIPE.md#handshakes): the a-to-b lane inverted from reset; b sets RxPolarity in training, link-up targets, lossless, then a reset of both ports and lossless | `e2e_polarity` | 1 | 1; RTL; dsp first, -300 ppm |
| [Targets](../../arch/phy/HAS.md#targets) on the RTL over the RTL channel at +600 ppm, each way. Throughput: 160 back-to-back 274-byte TLPs at least 2n/(n+2)·1176/1180 (1.979, ≥ 1.97); 600 DLLPs at least 1.495 (≥ 1.49). TX latency of every packet exactly 2 cycles ([MAS](../../rtl/phy/MAS.md#timing)), ≤ 6. RX latency ≤ 16 | `e2e_targets` | 1 | 1; RTL, `CHAN_RTL` 1; dsp first |
| [Link-up](../../arch/phy/HAS.md#targets) at `TIMEOUT_DIV` 1, simultaneous reset: release to `link_up_o` within 12.0 to 12.2 ms and training within 65.536 to 80 µs, on simulator clocks with no Python per cycle (about 3 minutes with coverage, so L2) | `e2e_link_up_spec` | 2 | 1; RTL, `CHAN_RTL` 1, `TIMEOUT_DIV` 1 |
| Soak: offset from the seed in ±600 ppm, reset skew up to 2000 cycles with the link-up targets, six random impairments between lossless phases, TLPs up to 4118 bytes (a 4096-byte payload) | `e2e_soak_dsp_first`, `e2e_soak_usp_first` (RTL); the same names with `_model` | 2 | RTL 4 each, models 2 each |

**Written, waiting on [#182](https://github.com/amichai-bd/pcie-factory/issues/182)** (no regression entry until then; #182 adds them at L1 with `MAC_RTL` 1, `PCS_RTL` 1, `ERR_TIMEOUT_DIV` 1024):

| Feature | Testcase |
| --- | --- |
| [Power states](../../arch/phy/HAS.md#link-up-and-requests). L0s: both transmitters reach Tx_L0s.Idle within `L0S_IDLE_CYCLES` + 16 cycles of the request, a packet leaves L0s within 2·N_FTS + 16, and traffic is lossless. L1: entry within 64 cycles, `link_up_o` high, exit through Recovery within 4 µs. L2: entry within 64 cycles, exit through Detect within 100 µs. Lossless after each | `e2e_power` |
| Loopback from each port as master (no traffic into the entry): both reach Loopback.Active with `link_up_o` low, then Detect and back to L0 | `e2e_loopback` |

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
| L0s, L1, L2 and Loopback end to end (`e2e_power`, `e2e_loopback`). Today the pcs reports electrical idle before the EIOS ahead of it reaches PIPE, and cuts that EIOS short. The receiving MAC then goes to Recovery instead of L0s, L1 or L2, and a Loopback slave never sees the master's EIOS | [#182](https://github.com/amichai-bd/pcie-factory/issues/182) |
| One layer RTL and the other a model (step 2 above); open whether M1 needs it | [#86](https://github.com/amichai-bd/pcie-factory/issues/86) |

## Coverage floors

The plan still has gaps: the step-2 mixed layers in `phy_e2e_layers`,
and `phy_e2e_power`, which waits on
[#182](https://github.com/amichai-bd/pcie-factory/issues/182). Per the
[DV BKM](../../../bkm/dv.md#coverage), `level2.yaml` therefore sets
per-covergroup floors only, for the covergroups whose plan rows are
complete:

| Covergroup | Floor |
| --- | --- |
| `phy_e2e_run` | 100 |
| `phy_e2e_events` | 100 |
| `phy_e2e_packets` | 100 |
| `phy_e2e_control` | 100 |

Line, toggle and combined functional floors follow once #182 closes the
power gap. Step 2 also needs a decision first: it is either done or
removed with a reason.
