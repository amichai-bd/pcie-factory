# pcie_mac symbols, ordered sets, scrambling and framing

What [`pcie_mac`](HAS.md) puts on PIPE and how it reads it back: the
symbols it uses, the ordered sets and their fields, scrambling and packet
framing. The rules are written out here in full (decision record
[0002](../../decisions/0002-m1-grill.md), Q21); citations point to the PCIe
Base Spec rev 3.0, Gen1 rules only, for cross-checking. The
[LTSSM page](LTSSM.md) says which state sends what.

A symbol is a byte plus a K flag (`pipe_tx_datak_o` or `pipe_rx_datak_i`
bit). Two symbols move per `clk_i` cycle; `[7:0]` is the earlier one on the
line ([PIPE page](../phy/PIPE.md#signals)).

## Symbols

The K symbols use the 8b/10b code names; the byte is what crosses PIPE
[cite: PCIe Base Spec, rev 3.0, §4.2.1.1].

| Name | Code | Byte | Use here |
| --- | --- | --- | --- |
| COM | K28.5 | BCh | First symbol of every ordered set; resets the scrambler. |
| STP | K27.7 | FBh | Starts a TLP. |
| SDP | K28.2 | 5Ch | Starts a DLLP. |
| END | K29.7 | FDh | Ends a packet. |
| EDB | K30.7 | FEh | Ends a nullified TLP or an aborted packet. The PCS also puts it in place of a symbol it could not decode ([PIPE page](../phy/PIPE.md#signals)). |
| PAD | K23.7 | F7h | Link or lane number field with no number. |
| SKP | K28.0 | 1Ch | Body of a SKP ordered set. |
| FTS | K28.1 | 3Ch | Body of an FTS ordered set. |
| IDL | K28.3 | 7Ch | Body of an electrical idle ordered set (EIOS). |
| Idle data | D0.0 | 00h before scrambling | Logical idle: the data symbol sent when there is nothing else. |

## Ordered sets

Every ordered set starts with COM and has an even number of symbols, and
the MAC starts every ordered set and packet it sends in `[7:0]`.

| Ordered set | Symbols sent | Received as |
| --- | --- | --- |
| TS1, TS2 | 16: COM and 15 field symbols ([below](#ts1-and-ts2)). | COM followed by any symbol but SKP, IDL or FTS (a data symbol, PAD or another K symbol); the next 14 symbols complete it. |
| SKP | COM and three SKP. | COM followed by one or more SKP (the PCS may have added or removed one, so 1 to 5). |
| EIOS | COM and three IDL. | COM followed by IDL. |
| FTS | COM and three FTS. | COM followed by FTS. |
| Compliance pattern | COM, D21.5 (B5h), COM, D10.2 (4Ah), repeated; `pipe_tx_compliance_o` is 1 in each word whose `[7:0]` is the first COM of a pattern, which makes the PCS send it with negative running disparity [cite: PCIe Base Spec, rev 3.0, §4.2.8]. | Not decoded; Polling.Compliance only watches electrical idle. |

[cite: PCIe Base Spec, rev 3.0, §4.2.4.1]
[cite: PCIe Base Spec, rev 3.0, §4.2.7]

### TS1 and TS2

A training set is 16 symbols. In order, after COM:

| Index | Field | Sent by this MAC | Used on receive |
| --- | --- | --- | --- |
| 1 | Link number | A data symbol 0 to 255, or PAD ([LTSSM, what each state transmits](LTSSM.md#what-each-state-transmits)). | Compared with `N` or PAD per state. Any K symbol other than PAD matches no number. |
| 2 | Lane number | 0 or PAD, likewise. | Compared with 0 or PAD per state. |
| 3 | N_FTS | The `N_FTS` parameter: the FTS ordered sets this receiver needs to leave L0s. | Taken as the partner's N_FTS from the TS2 that complete Configuration.Complete and Recovery.RcvrCfg; it sets how many FTS this port sends on an L0s exit. Before the first, 255. |
| 4 | Data rate identifier | 02h: 2.5 GT/s only, no speed change. | Ignored (Gen1 only). |
| 5 | Training control | Bit 0 Hot Reset, bit 1 Disable Link, bit 2 Loopback; set only where the LTSSM page says. Bits 3 (Disable Scrambling) and 4 (Compliance Receive) are never set; bits 7:5 are 0. | Bits 0 to 2 per state ([arcs page](ARCS.md)); bit 3 in Configuration.Complete ([Scrambling](#scrambling)); all other bits ignored. |
| 6 to 15 | Identifier | Ten times D10.2 (4Ah) for TS1, D5.2 (45h) for TS2. | All ten must be the same data symbol: 4Ah is TS1, 45h is TS2, and their bit-inverses B5h and BAh are a TS1 or TS2 received with inverted polarity. Anything else is a TS that fails to decode. |

TS symbols are never scrambled, but they advance the scrambler.

## SKP schedule

The MAC counts the symbols it sends, other than in electrical idle, in
the compliance pattern and as a loopback slave (Loopback.Entry and
Loopback.Active, including the rest of a TS it finishes there; a slave
sends no SKP ordered set, and its count holds). When the count reaches
1180 it sends a SKP ordered set at the next ordered-set or packet boundary
and subtracts 1180, so every SKP that falls due inside a packet goes out
after it and all that are due go out back to back. In `Tx_L0s.FTS` the
FTS sequence ends with one SKP ordered set and the count restarts at 0
[cite: PCIe Base Spec, rev 3.0, §4.2.7].

## Scrambling

One LFSR per direction, G(X) = X^16 + X^5 + X^4 + X^3 + 1, 16 bits
[cite: PCIe Base Spec, rev 3.0, §4.2.1.3]:

- **Reset.** COM sets the LFSR to FFFFh, on transmit when sent and on
  receive when received. `rst_i` sets it too.
- **Advance.** Every symbol other than COM and SKP advances the LFSR by
  eight shifts. A shift moves the register left by one; the bit shifted
  out of bit 15 is the key bit, and when it is 1 the new value is XORed
  with 0039h (taps x^0, x^3, x^4, x^5). The eight key bits of one symbol
  form its key byte, the first one in bit 0. SKP symbols leave the LFSR
  unchanged.
- **Hold.** Only symbols that cross PIPE move the LFSR. On transmit, a
  cycle with `pipe_tx_elec_idle_o` 1 sends no word, and the transmit LFSR
  holds. On receive, a word with `pipe_rx_valid_i` 0 or
  `pipe_rx_elec_idle_i` 1 is not received, and the receive LFSR holds
  (the same words the [receive rules](#receive) do not parse).
- **What is scrambled.** Each data symbol of a packet and each idle data
  symbol is XORed with its key byte (the key of the LFSR value before the
  symbol's advance), unless scrambling is disabled (below). K symbols, TS1
  and TS2 symbols, the compliance pattern and the loopback master's
  pattern are sent as they are.
- **Disable Scrambling.** A port that, in Configuration.Complete, receives
  2 consecutive TS2 with link `N`, lane 0 and the Disable Scrambling bit
  set, or sends a TS2 with the bit set there, disables scrambling on entry
  to Configuration.Idle: from then on packet and idle data symbols are sent
  and taken as they are, in both directions, while the LFSRs still reset
  and advance as above. On receive it holds from the first word the step
  after that entry takes. Recovery keeps it disabled; entry to
  Detect.Quiet enables it again. This MAC never sets the bit (there is no
  configuration space to request it); the model's injection can, which
  disables the model's own scrambling the same way
  [cite: PCIe Base Spec, rev 3.0, §4.2.1.3].
- **Descrambling** is the same operation on received symbols with the
  receive LFSR.
- **Known answer.** After a reset, the first sixteen key bytes are FFh,
  17h, C0h, 14h, B2h, E7h, 02h, 82h, 72h, 6Eh, 28h, A6h, BEh, 6Dh, BFh, 8Dh
  (the published Gen1 sequence, also the
  [model's self-test](../../../../src/arch/mac/test_pcie_mac_arch.py)).

## Framing

[cite: PCIe Base Spec, rev 3.0, §4.2.1.2]

### Transmit

- A TLP is sent as STP, its bytes, then END, or EDB when
  `pkt_tx_nullify_i` is 1 on its `eop` beat. A DLLP is SDP, its six bytes,
  then END; `pkt_tx_nullify_i` is ignored for a DLLP.
- Packets are a whole number of beats, so with their two framing symbols
  they stay even and every packet starts in `[7:0]`.
- In L0, idle data symbols fill every symbol that carries neither a
  packet, a SKP ordered set nor FTS.
- **Abort.** When the main LTSSM leaves L0 while a packet is being sent,
  its remaining symbols are replaced by EDB, followed by one idle data
  symbol when that keeps the next ordered set in `[7:0]`. The upper layer's
  remaining beats are taken and discarded ([HAS, transmit](HAS.md#transmit)).

### Receive

Received symbols are parsed one at a time, `[7:0]` before `[15:8]`, while
`pipe_rx_valid_i` is 1 and `pipe_rx_elec_idle_i` is 0. Ordered sets are
recognised in every state; packets only in L0 with the receive sub-machine
in `Rx_L0`. In that state:

| Received | Outside a packet | Inside a packet |
| --- | --- | --- |
| STP or SDP | Starts a TLP or DLLP. | Framing error: the open packet ends bad; a new one starts. |
| Data symbol | Idle if it descrambles to 00h; otherwise a framing error. | Descrambled into the packet. |
| END | Framing error. | The packet ends good. |
| EDB | Framing error. | A TLP ends nullified; a DLLP ends bad. |
| COM | An ordered set (above). | Framing error: the packet ends bad, then the ordered set is parsed. |
| Any other K symbol | Framing error. | Framing error: the packet ends bad. |

- After a framing error, symbols up to the next STP, SDP, END or EDB are
  discarded without further errors; leaving `Rx_L0` also ends this.
- **Empty packet.** STP or SDP followed at once by END, EDB or a cut
  carries no byte and is dropped, never delivered. Ended by END or EDB, or
  cut by `pipe_rx_valid_i` or electrical idle, it is a framing error (with
  no discarding after it); cut by leaving L0 or `Rx_L0` it is dropped
  silently ([#153](https://github.com/amichai-bd/pcie-factory/issues/153)).
- A packet is also cut short, and ends bad, when `pipe_rx_valid_i` falls,
  `pipe_rx_elec_idle_i` rises, or the LTSSM leaves L0 or `Rx_L0`.
- A packet ends bad when any word inside it had `pipe_rx_status_i` 4 to 7
  (decode, disparity, elastic-buffer overflow or underflow).
- How a packet that ends reaches the upper stream (realigned, `err`,
  `nullify`) is in the [HAS](HAS.md#receive).
