# pcie_phy DV plan

> **Scaffold, wave 2:** every layer runs as a model today. The stack RTL
> of [#86](https://github.com/amichai-bd/pcie-factory/issues/86) takes it over.

## Strategy

- **Shell test:** [`tests/test_phy.py`](../../../../src/dv/phy/tests/test_phy.py)
  drives `pcie_phy` directly. With `clk_i` and a slightly offset
  `ser_rx_clk_i` running and every input toggled at random, it checks each
  output against its [reset value](../../arch/phy/HAS.md#clocks-and-resets)
  at every falling `clk_i` edge, in both port roles. The `phy_shell`
  covergroup bins `port_role` and `rst`, crossed.
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
  `b_`. `a_clk_i` runs at 125 MHz and `b_clk_i` `ppm` off it, with period
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
  model's own or the block, and `tx_encoder`: the pcs stand-in's own or
  the block), the coverage hook that
  records which layers were RTL in each run; `phy_e2e_run` (port a's role,
  the offset ±300, ±600 or other, crossed); `phy_e2e_events`
  (impairment, recovery directed or autonomous); `phy_e2e_packets`
  (direction, kind tlp, dllp or tlp_nullified, size, direction crossed
  with kind). The run log also names the layers.
- **Out of scope here:** L0s, L1, L2, Hot Reset, Disabled and Loopback
  end to end, and the HAS [targets](../../arch/phy/HAS.md#targets)
  (link-up time, throughput, latency) measured on the RTL; see
  [Gaps](#gaps).

## Model-to-RTL switch

Each layer is chosen per run by a parameter of the testbench top, set in a
regression entry's `params` (the build SPEC passes them as `-G`); the top
echoes them on `cfg_*_o` and the environment builds the ports from them.

| Layer | Parameter | `0`: model | `1`: RTL |
| --- | --- | --- | --- |
| mac | `MAC_RTL` | `pcie_mac_arch.Mac` | `pcie_mac` inside `u_a`/`u_b` |
| pcs | `PCS_RTL` | `PcsModel` | `pcie_pcs` inside `u_a`/`u_b` |
| channel | `CHAN_RTL` | `Channel` in Python: offset, impairments | One register per direction in the top: ideal, no Python per cycle; needs `PCS_RTL` 1 |

Today the RTL side of mac and pcs is the whole `pcie_phy` instance, so the
two switch together (the environment rejects a mixed choice); with both at
1 the environment drives the instance pins through `RtlPort`, which has the
same interface as the model port. The blocks enter the e2e through their
layer: once `pcie_mac` or `pcie_pcs` instantiates a block, the layer's RTL
runs it.

| Block | Issue | Stand-in today | Runs in the e2e when |
| --- | --- | --- | --- |
| `pcs` encoder block | [#145](https://github.com/amichai-bd/pcie-factory/issues/145) (in `pcs`) | The transmit-encoder block hook below, today with the block's cycle model | The hook with an RTL adapter (step 0); the `pcs` RTL, an entry with `PCS_RTL: 1` ([#86](https://github.com/amichai-bd/pcie-factory/issues/86)) |
| `pcs` decoder, aligner, elastic buffer, PIPE control | [#145](https://github.com/amichai-bd/pcie-factory/issues/145) (in `pcs`) | `PcsModel` (its `decode`, `rx_edge` comma hunt and lock, buffer and PIPE control) | the `pcs` RTL, an entry with `PCS_RTL: 1` ([#86](https://github.com/amichai-bd/pcie-factory/issues/86)) |
| `mac` scrambler block | [#146](https://github.com/amichai-bd/pcie-factory/issues/146) (in `mac`) | Transmit: the block hook below, today with the block's cycle model. Receive: `pcie_mac_arch.Scrambler` | Transmit: the block hook with an RTL adapter (step 0). Both: the `mac` RTL, an entry with `MAC_RTL: 1` ([#86](https://github.com/amichai-bd/pcie-factory/issues/86)) |
| `mac` ordered sets, frame, LTSSM | [#146](https://github.com/amichai-bd/pcie-factory/issues/146) (in `mac`) | `pcie_mac_arch.Mac` | as above |

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

0. **Scrambler and encoder RTL, transmit** (once a block is in the `phy`
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

1. **Whole stack.** When `pcie_phy` integrates `pcie_mac` and `pcie_pcs`,
   add `params: {MAC_RTL: 1, PCS_RTL: 1}` to copies of the e2e entries (or
   flip existing ones). The shell-contract part of `e2e_rtl_shell` and
   `e2e_rtl_shell_chan` (`shell_contract` in the test module) then fails by
   design and is removed; their RTL-plumbing part (`rtl_plumbing`: the
   `cfg_*_o` echo, `RtlPort` for both ports, `rst_i` and `port_role_i`
   driven through reset per role) stays. `CHAN_RTL: 1` gives a fast ideal-channel run of the same tests
   minus the impairments.
2. **One layer RTL, the other a model.** With `mac.rtl.f` and `pcs.rtl.f`
   reached from `phy.rtl.f`, add per-port `pcie_mac` and `pcie_pcs`
   instances to the top behind the parameters, with their PIPE nets as top
   ports, and a `MacRtl` or `PcsRtl` port in the environment that moves
   `PipeTx`/`PipeRx` (the PIPE objects of the mac model) to and from those
   pins; then drop the mixed-choice check.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell, both roles](../../arch/phy/HAS.md#clocks-and-resets) | `smoke` | 0 | 1; 128 cycles with random inputs |
| [Link-up](../../arch/phy/HAS.md#link-up-and-requests), [packet stream](../../arch/phy/HAS.md#packet-stream) both ways, a symbol-error burst and a directed retrain, all models | `e2e_smoke` | 0 | 1; dsp first, b +300 ppm |
| RTL path of the switch, channel model: `cfg_*_o` echo, `RtlPort` for both instances, `rst_i` and `port_role_i` through reset per role; then the shell reset contract with traffic queued | `e2e_rtl_shell` | 0 | 1; `MAC_RTL` 1, `PCS_RTL` 1; usp first, -300 ppm |
| The same with the RTL channel (the top joins the lines; the environment steps only the ports) | `e2e_rtl_shell_chan` | 0 | 1; `MAC_RTL` 1, `PCS_RTL` 1, `CHAN_RTL` 1 |
| [Lossless](../../arch/phy/HAS.md#targets) at ±600 ppm and ±300 ppm, each port-role order: random mix, TLPs up to 1046 bytes, DLLPs | `e2e_dsp_first_pos600`, `e2e_dsp_first_neg300`, `e2e_usp_first_neg600`, `e2e_usp_first_pos300` | 1 | 1 each |
| Symbol errors each way: flagged or lost packets only, directed Recovery, lossless after | `e2e_symbol_errors` | 1 | 2; usp first, +600 ppm |
| Bit slips each way: relock, directed Recovery, lossless after | `e2e_bit_slips` | 1 | 2; dsp first, -600 ppm |
| Electrical-idle dropout each way: Recovery on its own, lossless after | `e2e_elec_idle` | 1 | 2; usp first, +300 ppm |
| Transmit scrambling by the `pcie_mac_scrambler` block (its cycle model) on both ports: train, lossless, an idle dropout, lossless | `e2e_scrambler_block` | 1 | 1; dsp first, -300 ppm |
| 8b/10b transmit by the `pcs` encoder block (its cycle model) on both ports: train, lossless, a bit slip each way, lossless | `e2e_encoder_block` | 1 | 1; usp first, +600 ppm |
| Soak: offset from the seed in ±600 ppm, reset skew up to 2000 cycles, six random impairments between lossless phases, TLPs up to 4118 bytes (a 4096-byte payload) | `e2e_soak_dsp_first`, `e2e_soak_usp_first` | 2 | 4 each |

## Gaps

| Feature | Owning issue |
| --- | --- |
| Any layer as RTL with traffic (`phy_e2e_layers` `rtl` bins are hit only by `e2e_rtl_shell` and `e2e_rtl_shell_chan`), the per-layer instances, the HAS [targets](../../arch/phy/HAS.md#targets) on the RTL | [#86](https://github.com/amichai-bd/pcie-factory/issues/86) |
| The `pcie_mac_scrambler` and `pcie_pcs_enc8b10b` RTL in the e2e (step 0 above), and replacing the shell-contract part of `e2e_rtl_shell` (step 1) | [#86](https://github.com/amichai-bd/pcie-factory/issues/86) |
| L0s, L1, L2, Hot Reset, Disabled, Loopback and polarity inversion end to end | [#86](https://github.com/amichai-bd/pcie-factory/issues/86) |
