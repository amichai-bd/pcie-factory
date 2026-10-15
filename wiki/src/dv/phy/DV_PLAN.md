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
  `b_`. `a_clk_i` runs at 125 MHz and `b_clk_i` `ppm` off it, dithered to
  the picosecond so the average offset is exact.
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
  every recovery. The stub does not replay lost packets.
- **Functional coverage:** `phy_e2e_layers` (mac, pcs and channel, each
  `model` or `rtl`, and mac crossed with pcs), the coverage hook that
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
same interface as the model port. The sub-IPs enter the e2e through their
layer: once `pcie_mac` or `pcie_pcs` instantiates a block, the layer's RTL
runs it.

| Block | Issue | Stand-in today | Runs in the e2e when |
| --- | --- | --- | --- |
| `pcs_enc8b10b`, `pcs_dec8b10b` | [#81](https://github.com/amichai-bd/pcie-factory/issues/81), [#82](https://github.com/amichai-bd/pcie-factory/issues/82) | `PcsModel`'s `encode`/`decode` hooks (the `pcs` model's 8b/10b) | `pcie_pcs` instantiates it and an entry sets `PCS_RTL: 1` |
| `pcs_align` | [#83](https://github.com/amichai-bd/pcie-factory/issues/83) | `PcsModel.rx_edge` (comma hunt, lock) | as above |
| `mac_scrambler` | [#74](https://github.com/amichai-bd/pcie-factory/issues/74) | `pcie_mac_arch.Scrambler` | `pcie_mac` instantiates it and an entry sets `MAC_RTL: 1` |
| `mac_os`, `mac_frame`, `mac_ltssm` | [#75](https://github.com/amichai-bd/pcie-factory/issues/75), [#76](https://github.com/amichai-bd/pcie-factory/issues/76), [#77](https://github.com/amichai-bd/pcie-factory/issues/77) | `pcie_mac_arch.Mac` | as above |

The steps, in [#86](https://github.com/amichai-bd/pcie-factory/issues/86):

1. **Whole stack.** When `pcie_phy` integrates `pcie_mac` and `pcie_pcs`,
   add `params: {MAC_RTL: 1, PCS_RTL: 1}` to copies of the e2e entries (or
   flip existing ones); `e2e_rtl_shell` then fails by design and is
   replaced. `CHAN_RTL: 1` gives a fast ideal-channel run of the same tests
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
| RTL path of the switch: both `pcie_phy` instances driven through reset, shell reset contract held | `e2e_rtl_shell` | 0 | 1; `MAC_RTL` 1, `PCS_RTL` 1; usp first, -300 ppm |
| [Lossless](../../arch/phy/HAS.md#targets) at ±600 ppm and ±300 ppm, each port-role order: random mix, TLPs up to 1046 bytes, DLLPs | `e2e_dsp_first_pos600`, `e2e_dsp_first_neg300`, `e2e_usp_first_neg600`, `e2e_usp_first_pos300` | 1 | 1 each |
| Symbol errors each way: flagged or lost packets only, directed Recovery, lossless after | `e2e_symbol_errors` | 1 | 2; usp first, +600 ppm |
| Bit slips each way: relock, directed Recovery, lossless after | `e2e_bit_slips` | 1 | 2; dsp first, -600 ppm |
| Electrical-idle dropout each way: Recovery on its own, lossless after | `e2e_elec_idle` | 1 | 2; usp first, +300 ppm |
| Soak: offset from the seed in ±600 ppm, reset skew up to 2000 cycles, six random impairments between lossless phases, TLPs up to 4118 bytes (a 4096-byte payload) | `e2e_soak_dsp_first`, `e2e_soak_usp_first` | 2 | 4 each |

## Gaps

| Feature | Owning issue |
| --- | --- |
| Any layer as RTL with traffic (`phy_e2e_layers` `rtl` bins are hit only by `e2e_rtl_shell`, and `channel` `rtl` by none), the per-layer instances, the HAS [targets](../../arch/phy/HAS.md#targets) on the RTL | [#86](https://github.com/amichai-bd/pcie-factory/issues/86) |
| L0s, L1, L2, Hot Reset, Disabled, Loopback and polarity inversion end to end | [#86](https://github.com/amichai-bd/pcie-factory/issues/86) |
