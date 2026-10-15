# pcie_mac_frame DV plan

## Strategy

- **Bench.** cocotb drives [`pcie_mac_frame`](../../rtl/mac_frame/MAS.md)
  directly (`mac_frame.dv.f` is empty). Inputs change at the falling edge
  and outputs are sampled 1 ns later, so the combinational transmit outputs
  are compared for the same cycle's inputs.
- **References** in [`pcie_mac_frame_model.py`](../../../../src/dv/mac_frame/models/pcie_mac_frame_model.py),
  unit-tested without a simulator (`python3 -m pytest src/dv/mac_frame/models`):
  - `TxRef`, the [MAS transmit rules](../../rtl/mac_frame/MAS.md#transmit)
    written from the page: `pkt_tx_ready_o`, `skp_gnt_o`, `tx_act_o`,
    `tx_pkt_o` and the line word are compared every cycle.
  - `RxRef`, the receive parser of the mac
    [architecture model](../../arch/mac/MODEL.md), imported (not copied)
    and frozen in L0. It sees the same symbols, scrambled for its own
    descrambler; the adapter adds the MAS readings the model lacks
    (leaving `Rx_L0` cuts a packet and ends a resync; an empty packet is a
    framing error in L0, not a beat). Delivered packets (bytes, `dllp`, `err`, `nullify`, beat
    structure, `eop` at most 3 cycles after the reference ends the packet),
    `rx_frame_err_o` and `rx_idle_o` are compared.
  - `SkpSched`, a stand-in for `mac_os`'s
    [SKP schedule](../../arch/mac/SYMBOLS.md#skp-schedule): a credit of
    symbols sent, `skp_req_i` while 1180 or more are due, a two-word SKP
    ordered set per grant.
- **Transmit runs** loop the line (frame word, SKP word, or electrical idle
  when neither drives) back into the receive half one cycle later. Besides
  the cycle checks, the delivered packets must equal what the upper layer
  sent (the HAS lossless rule), with aborted packets arriving with `err`.
  Back-pressure comes from SKP requests, `tx_stop_i` and drops of
  `tx_l0_i` (random runs of 1 to 40 cycles), bubbles from the upper layer
  inside packets, and TLPs of 1400 to 2800 bytes that push a SKP past more
  than one interval.
- **Receive runs** play random symbol streams: good packets of any length
  starting in either lane (odd SKP runs shift the lane), idle, TS, SKP
  (1 to 5 SKP), EIOS and FTS ordered sets, and malformed items: STP, COM
  or another K inside a packet, empty, odd-length, DLLPs of the wrong
  length or ended by EDB, data, K, END or EDB outside a packet. Per word
  they add RxValid low, electrical idle, RxStatus 4 to 7 and `rx_l0_i`
  drops.
- **Functional coverage** (sampled where the outcome is checked):
  `mac_frame_tx` (packet outcome: TLP or DLLP ended with END, nullified,
  aborted by an L0 exit or by a bubble, DLLP nullify ignored; each reason
  ready is low between packets; packet size classes),
  `mac_frame_skp` (SKP immediate or deferred to a packet end; single or
  back to back), `mac_frame_rx` (delivered outcome, kind, start lane and
  their cross) and `mac_frame_rx_events` (every framing error and cut
  kind). `level2.yaml` sets a 100% functional floor and line and toggle
  floors just below the merged values.
- **Assertions** in the RTL: the upper layer's sop rules, ready inside a
  packet, no grant with a packet open, the receive queue bound and flag
  rules. Every simulation fails on them.
- **Out of scope:** scrambling (`mac_scrambler`), the SKP count and
  ordered-set fields (`mac_os`), LTSSM decisions (`mac_ltssm`), and the
  integration (`pcie_mac`, [#78](https://github.com/amichai-bd/pcie-factory/issues/78)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values and reset over the sop-to-eop ready rule](../../rtl/mac_frame/MAS.md#reset) | `smoke` | 0 | 1; reset with a packet offered and inside a packet, then a clean packet |
| [Resync ends when `rx_l0_i` falls](../../rtl/mac_frame/MAS.md#receive) | `smoke` | 0 | directed: framing error, `rx_l0_i` low, idle counted |
| [Framing of a TLP, a DLLP and a nullified TLP](../../rtl/mac_frame/MAS.md#transmit) | `smoke` | 0 | exact words checked |
| [Abort: L0 exit and bubble inside a packet](../../rtl/mac_frame/MAS.md#transmit) | `smoke` | 0 | directed, then random in `tx_random`, `tx_long` |
| [Random packet streams round trip with back-pressure; ready rules](../../rtl/mac_frame/MAS.md#transmit) | `tx_random` | 1 | 3; 20000 cycles |
| [SKP deferral to packet ends, back to back](../../rtl/mac_frame/MAS.md#transmit) | `tx_random` | 1 | 3; with TLPs past one SKP interval |
| [Receive: deframing, realignment, flags, framing errors, cuts](../../rtl/mac_frame/MAS.md#receive) | `rx_random` | 1 | 3; 3000 items, 15% malformed |
| [Status outputs](../../rtl/mac_frame/MAS.md#status) | `rx_random` | 1 | checked every cycle in every run |
| Long transmit runs, TLPs to 512 bytes and past the SKP interval | `tx_long` | 2 | 8; 50000 cycles |
| Long receive runs | `rx_long` | 2 | 8; 8000 items, TLPs to 256 bytes |

Every [interface](../../rtl/mac_frame/MAS.md#interfaces) port is driven or
checked in every entry.

### Mutation evidence

Each mutation was made in a scratch copy of the RTL and run on the L0 and
L1 entries; each was caught.

| Mutation | Caught by |
| --- | --- |
| Ready ignores `skp_req_i` between packets | `smoke`, `tx_random`: `pkt_tx_ready_o` mismatch against `TxRef` |
| Abort sends END instead of EDB | `smoke`: abort word mismatch |
| Nullify also applied to DLLPs | `tx_random`: end-word mismatch (EDB for END) |
| DLLP length check dropped on receive | `rx_random`: flags mismatch on a DLLP of the wrong length |
| Resync kept while `rx_l0_i` is 0 (the clear on leaving `Rx_L0` reverted) | `smoke`: `rx_idle_o` 0 after the retrain step (directed check and `RxRef` mismatch) |
| No resync after a framing error outside a packet | `rx_random`: `rx_idle_o` mismatch (discarded symbols counted as idle) |

Every feature in the MAS has a row; there are no gaps.
