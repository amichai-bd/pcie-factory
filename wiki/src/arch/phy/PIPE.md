# pcie_phy PIPE interface

The MAC-to-PCS interface inside [`pcie_phy`](HAS.md): the Gen1 subset of
Intel's PHY Interface for the PCI Express and USB 3.0 Architectures, pinned
at **rev 3.0** and cited as `PIPE Spec`, in its 16-bit, 125 MHz mode
[cite: PIPE Spec, rev 3.0, §5]. It is part of the `phy` HAS: the same
sign-off covers it, and the `mac` and `pcs` HAS pages implement it.

## Signals

`pcie_mac` drives the MAC-to-PCS signals and `pcie_pcs` the reverse; none is
a `pcie_phy` port. Each stack net `pipe_<x>` is the port `pipe_<x>_o` of one
IP and `pipe_<x>_i` of the other. All are synchronous to `clk_i`.

| PIPE signal | Stack net | Width | Dir | Meaning here |
| --- | --- | --- | --- | --- |
| PCLK | `clk_i` | 1 | - | Not a separate clock: the stack's `clk_i` ([Decisions](HAS.md#decisions)). |
| Reset# | `pipe_reset_n` | 1 | MAC→PCS | Active-low PCS reset, synchronous to `clk_i`. |
| TxData | `pipe_tx_data` | 16 | MAC→PCS | Two bytes, `[7:0]` first. |
| TxDataK | `pipe_tx_datak` | 2 | MAC→PCS | Bit *i* marks byte *i* as a K code. |
| TxDetectRx/Loopback | `pipe_tx_detect_rx` | 1 | MAC→PCS | In P1: run receiver detection. In P0: loop received symbols back to the transmitter. |
| TxElecIdle | `pipe_tx_elec_idle` | 1 | MAC→PCS | Put the transmitter in electrical idle (whole word). |
| TxCompliance | `pipe_tx_compliance` | 1 | MAC→PCS | Force negative running disparity for the symbol in `[7:0]` of this word. |
| RxPolarity | `pipe_rx_polarity` | 1 | MAC→PCS | Invert the received bits. |
| PowerDown | `pipe_power_down` | 2 | MAC→PCS | 0 P0, 1 P0s, 2 P1, 3 P2. |
| RxData | `pipe_rx_data` | 16 | PCS→MAC | Two bytes, `[7:0]` first. |
| RxDataK | `pipe_rx_datak` | 2 | PCS→MAC | Bit *i* marks byte *i* as a K code. |
| RxValid | `pipe_rx_valid` | 1 | PCS→MAC | Symbol lock: `pipe_rx_data` is valid. |
| PhyStatus | `pipe_phy_status` | 1 | PCS→MAC | High during reset until the PCS is ready; then a one-cycle completion pulse. |
| RxElecIdle | `pipe_rx_elec_idle` | 1 | PCS→MAC | The line is in electrical idle (synchronized to `clk_i`). |
| RxStatus | `pipe_rx_status` | 3 | PCS→MAC | Per word, see below. |

`pipe_rx_status` codes: 0 data OK, 1 one SKP added, 2 one SKP removed,
3 receiver detected, 4 8b/10b decode error, 5 elastic-buffer overflow,
6 elastic-buffer underflow, 7 disparity error. A decode error also replaces
that byte with EDB as a K code [cite: PIPE Spec, rev 3.0, §5]. Which code
wins when several apply in one word is owned by the `pcs` HAS. Gen2 and
Gen3 signals (Rate, de-emphasis, margining, equalization) are absent and
are added with those rates.

## Handshakes

All from [cite: PIPE Spec, rev 3.0, §6], restricted to what this stack uses.

- **Reset.** While `pipe_reset_n` is low, `pipe_phy_status` is high; it
  falls once the PCS is ready, after which the MAC may change any input.
- **Receiver detection.** The MAC sets P1 and holds `pipe_tx_elec_idle`
  high, then raises `pipe_tx_detect_rx` and holds it until the
  `pipe_phy_status` pulse. With that pulse the PCS reports
  `pipe_rx_status` 3 (a receiver is present, from `ser_tx_rxdet_i`) or 0.
  The MAC then drops `pipe_tx_detect_rx`.
- **Loopback.** In P0 with `pipe_tx_elec_idle` low, `pipe_tx_detect_rx`
  high makes the PCS retransmit received symbols.
- **Power states.** The MAC raises `pipe_tx_elec_idle` before it moves
  `pipe_power_down` away from P0. The PCS acknowledges every
  `pipe_power_down` change, P0s included, with one `pipe_phy_status`
  pulse. `clk_i` keeps running in every state, P2 included.
- **Polarity.** `pipe_rx_polarity` applies from the next received word
  and stays until the MAC drops it.
