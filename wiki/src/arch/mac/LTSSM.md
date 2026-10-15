# pcie_mac LTSSM

The link training and status state machine of [`pcie_mac`](HAS.md): its
structure, every state, its `ltssm_state_o` code, the rules common to all
arcs and what the port transmits in each state. Every arc, with its
condition and timeout, is on the [arcs page](ARCS.md). The rules are
written out in full (decision record [0002](../../decisions/0002-m1-grill.md),
Q21); citations point to the PCIe Base Spec rev 3.0, Gen1 rules only, for
cross-checking. Ordered sets, TS1 and TS2 fields, scrambling and framing
are on the [symbols page](SYMBOLS.md).

## Structure

The LTSSM is one **main machine** plus, inside L0, two independent
**L0s sub-machines**: transmit (`Tx_*`) and receive (`Rx_*`). Each port's
transmitter enters L0s on its own; its receiver follows the partner's
transmitter [cite: PCIe Base Spec, rev 3.0, §4.2.6.6]. While the main
machine is in L0 the main state stays `L0`; the sub-machines are `Tx_Off`
and `Rx_Off` whenever the main machine is not in L0. All three machines
step on `clk_i` and reset to Detect.Quiet, `Tx_Off` and `Rx_Off`
[cite: PCIe Base Spec, rev 3.0, §4.2.5].

Gen1 only: Recovery.Speed and Recovery.Equalization, Polling.Speed and
L2.TransmitWake (beacon, which the [`phy` HAS](../phy/HAS.md#link-up-and-requests)
excludes) are not implemented, and no arc leads to them. Configuration
forms an x1 link only.

## State encoding

`ltssm_state_o` (6 bits) shows the main state; in L0 it also shows both
sub-machines, so a transmit and a receive L0s state that differ are both
visible: in L0 the code is `0x20 | tx << 2 | rx`, with `tx` and `rx` each
0 (`Tx_L0`/`Rx_L0`), 1 (Entry), 2 (Idle) or 3 (FTS). Codes not listed are
reserved; 0x13 and 0x14 are kept for Recovery.Speed and
Recovery.Equalization, 0x35 for L2.TransmitWake.

| State | Code | Group |
| --- | --- | --- |
| Detect.Quiet | `0x00` | Detect |
| Detect.Active | `0x01` | Detect |
| Polling.Active | `0x04` | Polling |
| Polling.Compliance | `0x05` | Polling |
| Polling.Configuration | `0x06` | Polling |
| Configuration.Linkwidth.Start | `0x08` | Configuration |
| Configuration.Linkwidth.Accept | `0x09` | Configuration |
| Configuration.Lanenum.Wait | `0x0A` | Configuration |
| Configuration.Lanenum.Accept | `0x0B` | Configuration |
| Configuration.Complete | `0x0C` | Configuration |
| Configuration.Idle | `0x0D` | Configuration |
| Recovery.RcvrLock | `0x10` | Recovery |
| Recovery.RcvrCfg | `0x11` | Recovery |
| Recovery.Idle | `0x12` | Recovery |
| Disabled | `0x18` | Control |
| Hot Reset | `0x19` | Control |
| Loopback.Entry | `0x1C` | Loopback |
| Loopback.Active | `0x1D` | Loopback |
| Loopback.Exit | `0x1E` | Loopback |
| L0 | `0x20` | L0, both sub-machines in `Tx_L0`/`Rx_L0`; 0x21 to 0x2F as above |
| L1.Entry | `0x30` | L1 |
| L1.Idle | `0x31` | L1 |
| L2.Idle | `0x34` | L2 |

## Common rules

- **Counting received TS.** Each state defines which received TS1 or TS2
  *match* a named condition. A run of *consecutive* matching TS restarts at
  zero on any received TS that does not match, including a TS that fails
  to decode; SKP, EIOS, FTS and idle symbols neither count nor break a run.
  Runs start at zero on entry to each state.
- **Latched receive conditions.** "8 consecutive ... received" holds from
  the TS that completes the run until the state is left, even when the
  partner then moves on. Likewise "8 consecutive idle symbols received".
  One exception: Polling.Active's exit needs the run of 8 matching TS
  *and* 1024 TS1 sent in the same step, so a run broken before the 1024th
  TS1 starts again (this design's reading, model and RTL alike,
  [#158](https://github.com/amichai-bd/pcie-factory/issues/158)).
- **Sent after one received.** "16 TS2 sent after receiving one" counts
  the TS2 this port queues from the first matching TS received in the
  state; "16 idle symbols sent after receiving one" counts idle symbols
  sent from the first idle symbol received.
- **Idle symbol.** A data symbol outside a packet that descrambles to 00h
  ([symbols page](SYMBOLS.md#scrambling)). A TS, a K symbol or any other
  data symbol restarts the run.
- **Electrical idle.** *EI exit* means `pipe_rx_elec_idle_i` was 1 at some
  cycle since the state was entered (for `Rx_L0s.Idle`, since
  `Rx_L0s.Entry` was entered) and is 0 now.
  Detect.Quiet and Polling.Compliance use the level of
  `pipe_rx_elec_idle_i` instead.
- **Timeouts** count `clk_i` cycles (8 ns) from state entry and restart on
  every entry. **T** marks a timeout divided by `TIMEOUT_DIV`
  (integer division). **E** marks an error-path timeout: unchanged when
  `ERR_TIMEOUT_DIV` is 1, otherwise divided by it and floored at 16384
  cycles ([HAS, Parameters](HAS.md#parameters)). Unmarked timeouts are
  never divided.
- **Priority.** When several arcs out of a state hold in the same cycle,
  the one listed first on the [arcs page](ARCS.md) is taken.
- **Directed** requests are the upper-layer inputs `retrain_req_i`,
  `hot_reset_req_i`, `disable_req_i`, `loopback_req_i` and
  `pm_state_req_i` ([HAS, Link control](HAS.md#link-control)).
  `hot_reset_req_i` and `disable_req_i` count only in the downstream role.
- **Numbers in use.** `N` is the link number: 0 for the downstream port,
  and for the upstream port the link number it learned in
  Configuration.Linkwidth.Start. The lane number is always 0 (x1).
  `idle_to_rlock` is a counter of Configuration.Idle and Recovery.Idle
  timeouts, cleared on every entry to L0
  [cite: PCIe Base Spec, rev 3.0, §4.2.6.3]
  [cite: PCIe Base Spec, rev 3.0, §4.2.6.4].
- **PIPE power.** Each state has a PowerDown value (table below). The MAC
  raises `pipe_tx_elec_idle_o`, then changes `pipe_power_down_o`, then
  waits for the `pipe_phy_status_i` pulse before it changes PowerDown again
  or sends ([PIPE page](../phy/PIPE.md#handshakes)). States that end in
  electrical idle send one EIOS first.

## What each state transmits

`link_up_o` follows the [`phy` HAS](../phy/HAS.md#link-up-and-requests):
set on every entry to L0 (from Configuration.Idle or Recovery.Idle), cleared on entry to
Detect.Quiet, Polling.Active, Disabled, Hot Reset and Loopback.Entry, held
otherwise. SKP ordered sets are inserted in every state that sends TS,
idle or packets ([symbols page](SYMBOLS.md#skp-schedule)).

| State | PowerDown | Transmits |
| --- | --- | --- |
| Detect.Quiet | P1 | Electrical idle. |
| Detect.Active | P1 | Electrical idle; runs receiver detection ([PIPE page](../phy/PIPE.md#handshakes)). |
| Polling.Active | P0 | TS1, link PAD, lane PAD. |
| Polling.Compliance | P0 | The compliance pattern, no SKP. |
| Polling.Configuration | P0 | TS2, link PAD, lane PAD. |
| Configuration.Linkwidth.Start | P0 | Downstream: TS1, link 0, lane PAD. Upstream: TS1, link PAD, lane PAD. |
| Configuration.Linkwidth.Accept | P0 | TS1, link `N`, lane PAD. |
| Configuration.Lanenum.Wait, .Accept | P0 | TS1, link `N`, lane 0. |
| Configuration.Complete | P0 | TS2, link `N`, lane 0. |
| Configuration.Idle, Recovery.Idle | P0 | Idle symbols. |
| L0, `Tx_L0` | P0 | Packets, idle symbols between them. Upstream port directed to L1 or L2: one EIOS, then electrical idle while it waits for the partner's EIOS ([arcs, L0](ARCS.md#l0)). |
| L0, `Tx_L0s.Entry`, `.Idle` | P0s | One EIOS, then electrical idle. |
| L0, `Tx_L0s.FTS` | P0 | FTS ordered sets, then one SKP ordered set. |
| Recovery.RcvrLock | P0 | TS1, link `N`, lane 0. |
| Recovery.RcvrCfg | P0 | TS2, link `N`, lane 0. |
| L1.Entry, L1.Idle | P1 | One EIOS, then electrical idle. |
| L2.Idle | P2 | One EIOS, then electrical idle. |
| Disabled | P0, then P1 | 16 TS1 with Disable Link set, link and lane as in the state it came from (below), one EIOS, then electrical idle. |
| Hot Reset | P0 | TS1, link `N`, lane 0, Hot Reset set, continuously. |
| Loopback.Entry | P0 | Master: TS1 with Loopback set, link and lane as in the state it came from (below). Slave: nothing of its own: it sends as in Loopback.Active, which it enters in the next step. |
| Loopback.Active | P0 | Master: a data pattern, not scrambled: two data symbols per word, a byte count mod 256 that runs on from one loopback to the next (0 after reset). Slave: idle symbols, with `pipe_tx_detect_rx_o` high, so the PCS retransmits what it receives instead; the SKP count holds ([symbols page](SYMBOLS.md#skp-schedule)). |
| Loopback.Exit | P0 | One EIOS, then electrical idle. |

*As in the state it came from*: from Configuration.Linkwidth.Start, that
state's link and lane (downstream link 0, upstream PAD; lane PAD); from
Recovery.Idle, link `N` and lane 0.

Every TS carries N_FTS from the `N_FTS` parameter, the 2.5 GT/s data
rate and no control bit unless the table says so
([symbols page](SYMBOLS.md#ts1-and-ts2)). From Configuration.Idle to the
next Detect.Quiet, scrambling may be disabled
([symbols page](SYMBOLS.md#scrambling)).
