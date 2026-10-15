# pcie_pcs PIPE control

The PCS side of every PIPE handshake on the [PIPE page](../phy/PIPE.md),
with exact cycle timing, plus the transmit path. All signals are
synchronous to `clk_i`; "cycle n" counts `clk_i` cycles, and an input
"seen" in cycle n was sampled at the rising edge that ends it. These rules
are normative (decision Q21 in the
[M1 epic](https://github.com/amichai-bd/pcie-factory/issues/65)) and were
checked against the cached PIPE rev 3.0 text
([#117](https://github.com/amichai-bd/pcie-factory/issues/117)); where this
stack is stricter or differs, the [differences](#differences-from-pipe)
say so. The [architecture model](../../../../src/arch/pcs/pcie_pcs_arch.py)
runs these rules as `PipeControl`.

## Reset and PhyStatus

- **PCS reset.** The `clk_i` side of the PCS is in reset in every cycle in
  which `rst_i` is 1 or `pipe_reset_ni` is 0. Reset overrides every other
  input.
- **PhyStatus.** `pipe_phy_status_o` is 1 during the PCS reset and in the
  16 cycles after its last cycle; it is 0 from the 17th cycle on, which
  tells the MAC the PCS is ready and in the PowerDown state it sees
  [cite: PIPE Spec, rev 3.0, §6.2]. This stack's rule: a PowerDown change
  within those 16 cycles takes effect with no pulse. The MAC makes none,
  since it starts nothing before PhyStatus falls.
- **MAC during reset.** While it holds the PCS in reset, the MAC keeps
  `pipe_tx_detect_rx_i` 0, `pipe_tx_elec_idle_i` 1, `pipe_tx_compliance_i`
  0, `pipe_rx_polarity_i` 0 and `pipe_power_down_i` at P1
  [cite: PIPE Spec, rev 3.0, §6.2].
- **What `pipe_reset_ni` resets.** PIPE control (PhyStatus, PowerDown,
  receiver detection), the transmit path (RD, loopback), the decoder's RD,
  and the error counter with its relock wait. While it is 0, the receive
  outputs are gated: `pipe_rx_valid_o` 0 and `pipe_rx_status_o` 0. It does
  not reset the `ser_rx_clk_i` domain, the FIFO, or the elastic buffer's
  read side (started flag, leftover symbol, recentring); those reset only
  with `rst_i` (see [Clocks and reset](HAS.md#clocks-and-reset)). The
  read side keeps reading and discarding words during a PIPE reset, so the
  buffer stays near its nominal fill instead of filling behind it.

## PowerDown

`pipe_power_down_i` selects 0 P0, 1 P0s, 2 P1 or 3 P2
[cite: PIPE Spec, rev 3.0, §5.1]. Out of reset the PCS takes whatever value
it sees.

- **Transitions.** The MAC uses only P0 to P0s, P1 or P2, and P0s, P1 or P2
  back to P0, and changes PowerDown again only after the pulse that
  acknowledges the previous change [cite: PIPE Spec, rev 3.0, §6.3].
- **Acknowledge.** When a new value is first seen in cycle n,
  `pipe_phy_status_o` is 1 in cycle n + 4 only. Every change is
  acknowledged this way, P0s included; PIPE asks for a one-cycle pulse on
  every transition among P0, P0s and P1, with no exception for the 16-bit
  interface [cite: PIPE Spec, rev 3.0, §6.3].
- **Effect.** In P0 the transmitter follows `pipe_tx_elec_idle_i`. In P0s,
  P1 and P2 it is in electrical idle whatever `pipe_tx_elec_idle_i` says;
  the MAC holds it at 1 there, as PIPE requires in P0s and P1
  [cite: PIPE Spec, rev 3.0, §6.20]. The receive path runs in every
  state, so the MAC sees the partner's electrical-idle exit and data in P0s
  and P1, and the aligner regains lock on the first comma of the FTS
  sequence. `clk_i` runs in every state. There is no beacon.
- **Control combinations** in P0 [cite: PIPE Spec, rev 3.0, §6.20]:
  TxDetectRx 0 with TxElecIdle 0 transmits data; 0 with 1 is electrical
  idle; 1 with 0 is [loopback](#loopback); 1 with 1 is not used by the
  MAC. TxCompliance and RxPolarity are used only in P0.

## Receiver detection

- **Start.** In P1, with `pipe_tx_elec_idle_i` 1, the cycle in which
  `pipe_tx_detect_rx_i` is first seen 1 is detection cycle 1.
- **Result.** `ser_tx_rxdet_i` is sampled in detection cycle 63. In
  detection cycle 64, `pipe_phy_status_o` is 1 for one cycle and
  `pipe_rx_status_o` is 3 when the sample was 1 (a receiver is present) or
  0 when it was 0. That code overrides the receive status for that cycle.
- **End.** The MAC holds `pipe_tx_detect_rx_i` at 1 until the pulse and
  drops it before another detection, a PowerDown change or anything else;
  a new detection starts only after it has been seen 0. The transmitter
  stays in electrical idle throughout [cite: PIPE Spec, rev 3.0, §6.8].
  Receiver detection is part of the Detect state
  [cite: PCIe Base Spec, rev 3.0, §4.2.6.1].

## Transmit path

- **Encoding.** Each cycle the two bytes of `pipe_tx_data_i` (`[7:0]`
  first) are encoded with the [8b/10b code](8B10B.md) and appear on
  `ser_tx_data_o` (`[9:0]` first) two cycles after they are seen.
  `pipe_tx_datak_i` bit i marks byte i as a K symbol
  [cite: PIPE Spec, rev 3.0, §5.1]; a K flag on a byte that is no valid K
  code sends EDB.
- **Running disparity** is negative after reset and is kept across
  electrical idle; the receiver accepts either RD at the start of a locked
  run.
- **Electrical idle.** `ser_tx_elec_idle_o` follows `pipe_tx_elec_idle_i`
  (in P0) with the same two-cycle latency, for the whole word, so every
  word sent before TxElecIdle rose, including the whole electrical idle
  ordered set, reaches the line first. The MAC places that set's COM in
  `[7:0]` [cite: PIPE Spec, rev 3.0, §6.18]. `ser_tx_data_o` is don't care
  while `ser_tx_elec_idle_o` is 1.
- **TxCompliance.** When `pipe_tx_compliance_i` is 1 with a word, RD is
  set negative before the `[7:0]` symbol is encoded: in the 16-bit
  interface the low byte is the one sent at negative disparity
  [cite: PIPE Spec, rev 3.0, §6.17]. The MAC uses it to send the
  compliance pattern's COM with negative disparity
  [cite: PCIe Base Spec, rev 3.0, §4.2.8].

## Loopback

In P0 with `pipe_tx_elec_idle_i` 0, `pipe_tx_detect_rx_i` 1 selects
loopback: the transmitter ignores `pipe_tx_data_i` and sends the received
words, as they appear on `pipe_rx_data_o` and `pipe_rx_datak_o` (after SKP
addition or removal), re-encoded with the transmit RD, two cycles later.
The receive side keeps presenting the received words on PIPE. A symbol
with a code error goes out as EDB. While `pipe_rx_valid_o` is 0 the
symbols sent are valid codes of unspecified value.

Loopback ends in the cycle `pipe_tx_detect_rx_i` is seen 0; the MAC raises
`pipe_tx_elec_idle_i` in the same cycle when it has received an electrical
idle ordered set. Because both act two cycles later on the line, the
looped-back words already in the transmit pipeline, which hold the start
of that ordered set, still go out before electrical idle
[cite: PIPE Spec, rev 3.0, §6.15]. Adding or removing SKP symbols in
loopback is allowed to the loopback slave
[cite: PCIe Base Spec, rev 3.0, §4.2.6.10].

## Receive status signals

- `pipe_rx_valid_o`, `pipe_rx_data_o`, `pipe_rx_datak_o` and
  `pipe_rx_status_o`: see the [receive path](RECEIVE.md#decode-and-rxstatus).
- `pipe_rx_polarity_i` inverts received words from the cycle it is seen 1
  until the cycle it is seen 0.
- `pipe_rx_elec_idle_o` is `ser_rx_elec_idle_i` registered in the
  `ser_rx_clk_i` domain and brought to `clk_i` by a
  [`common_cdc_sync2`](../../rtl/common/common_cdc_sync2.md): one RX cycle
  plus two or three `clk_i` edges late. It is 1 during reset.

## Differences from PIPE

Each is this stack's choice, consistent within the stack because both
sides of PIPE are this project's; a third-party MAC or PHY would need them
revisited.

| PIPE rev 3.0 | This stack | Why |
| --- | --- | --- |
| Reset# is asynchronous [cite: PIPE Spec, rev 3.0, §5.1]. | `pipe_reset_ni` is synchronous to `clk_i` ([PIPE page](../phy/PIPE.md#signals)). | Both IPs share `clk_i`; stricter than PIPE. |
| RxElecIdle is asynchronous [cite: PIPE Spec, rev 3.0, §5.1]. | Synchronized to `clk_i`. | Stricter; the MAC samples it like any PIPE input. |
| In P2 the PHY stops PCLK; PhyStatus rises before PCLK stops and, on exit, stays high until PCLK is stable [cite: PIPE Spec, rev 3.0, §6.3]. | `clk_i` (PCLK) keeps running in P2; entry and exit are acknowledged with the same one-cycle pulse as every change. | PCLK is the stack's core clock, not a PHY output ([`phy` decisions](../phy/HAS.md#decisions)). |
| In P2 with TxElecIdle 0 the PHY sends a beacon [cite: PIPE Spec, rev 3.0, §6.9]. | No beacon; the transmitter stays idle in P2. | Beacon is out of scope ([`phy` HAS](../phy/HAS.md#link-up-and-requests)). |

**Values PIPE leaves to the PHY.** Transmit, receive and loopback-enable
latencies are the PHY's to state [cite: PIPE Spec, rev 3.0, §6.19]; this
PCS fixes them at two cycles to transmit and the RX latency of the
[targets](HAS.md#targets). PIPE bounds polarity inversion at 20 PCLK
cycles [cite: PIPE Spec, rev 3.0, §6.16]; this PCS inverts from the next
word, well within it.
