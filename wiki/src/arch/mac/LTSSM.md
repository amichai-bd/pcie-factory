# pcie_mac LTSSM

The link training and status state machine of [`pcie_mac`](HAS.md): every
state, what the port transmits in it, the `ltssm_state_o` code and every
arc with its condition and timeout. The rules are written out here in full
(decision record [0002](../../decisions/0002-m1-grill.md), Q21); citations
point to the PCIe Base Spec rev 3.0, Gen1 rules only, for cross-checking.
The ordered sets, the TS1 and TS2 fields, scrambling and framing are on the
[symbols page](SYMBOLS.md). The arc tables are the 100% arc-coverage target
of the LTSSM tests ([#77](https://github.com/amichai-bd/pcie-factory/issues/77),
[#79](https://github.com/amichai-bd/pcie-factory/issues/79),
[#80](https://github.com/amichai-bd/pcie-factory/issues/80),
[#90](https://github.com/amichai-bd/pcie-factory/issues/90)); the
[architecture model](HAS.md#architecture-model) holds the same list
(`ARCS`) and its self-test fails if the two differ.

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
- **Sent after one received.** "16 TS2 sent after receiving one" counts
  the TS2 this port queues from the first matching TS received in the
  state; "16 idle symbols sent after receiving one" counts idle symbols
  sent from the first idle symbol received.
- **Idle symbol.** A data symbol outside a packet that descrambles to 00h
  ([symbols page](SYMBOLS.md#scrambling)). A TS, a K symbol or any other
  data symbol restarts the run.
- **Electrical idle.** *EI exit* means `pipe_rx_elec_idle_i` was 1 at some
  cycle since the state (or sub-state) was entered and is 0 now.
  Detect.Quiet and Polling.Compliance use the level of
  `pipe_rx_elec_idle_i` instead.
- **Timeouts** count `clk_i` cycles (8 ns) from state entry and restart on
  every entry. **T** marks a timeout divided by `TIMEOUT_DIV`
  (integer division). **E** marks an error-path timeout: unchanged when
  `ERR_TIMEOUT_DIV` is 1, otherwise divided by it and floored at 16384
  cycles ([HAS, Parameters](HAS.md#parameters)). Unmarked timeouts are
  never divided.
- **Priority.** When several arcs out of a state hold in the same cycle,
  the one listed first in the tables below is taken.
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
set on entry to L0 from Configuration.Idle, cleared on entry to
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
| L0, `Tx_L0` | P0 | Packets, idle symbols between them. |
| L0, `Tx_L0s.Entry`, `.Idle` | P0s | One EIOS, then electrical idle. |
| L0, `Tx_L0s.FTS` | P0 | FTS ordered sets, then one SKP ordered set. |
| Recovery.RcvrLock | P0 | TS1, link `N`, lane 0. |
| Recovery.RcvrCfg | P0 | TS2, link `N`, lane 0. |
| L1.Entry, L1.Idle | P1 | One EIOS, then electrical idle. |
| L2.Idle | P2 | One EIOS, then electrical idle. |
| Disabled | P0, then P1 | 16 TS1 with Disable Link set, one EIOS, then electrical idle. |
| Hot Reset | P0 | TS1, link `N`, lane 0, Hot Reset set, continuously. |
| Loopback.Entry | P0 | Master: TS1 with Loopback set, link and lane as in the state it came from. Slave: nothing (leaves at once). |
| Loopback.Active | P0 | Master: a data pattern (a byte count, not scrambled). Slave: `pipe_tx_detect_rx_o` high, so the PCS retransmits what it receives. |
| Loopback.Exit | P0 | One EIOS, then electrical idle. |

Every TS carries N_FTS from the `N_FTS` parameter, the 2.5 GT/s data
rate and no control bit unless the table says so
([symbols page](SYMBOLS.md#ts1-and-ts2)).

## Arcs

Arc names are the coverage bins: `<from>__<to>` of the states' short
names. The sampled value is the `(from, to)` tuple of the two state names
in the table, as in the regress SPEC's
[state-machine example](../../../tools/regress/SPEC.md#functional-coverage);
sub-machine arcs are sampled on the sub-machines' changes. Reset is not an
arc.

### Detect and Polling

[cite: PCIe Base Spec, rev 3.0, §4.2.6.1]
[cite: PCIe Base Spec, rev 3.0, §4.2.6.2]

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `detect_quiet__detect_active` | Detect.Quiet | Detect.Active | The PCS is ready (`pipe_phy_status_i` low after reset), PowerDown is P1 with no change pending, and the timeout expired or `pipe_rx_elec_idle_i` is 0. | 12 ms, T |
| `detect_active__polling_active` | Detect.Active | Polling.Active | Receiver detection reports a receiver (`pipe_rx_status_i` 3 with the `pipe_phy_status_i` pulse). | - |
| `detect_active__detect_quiet` | Detect.Active | Detect.Quiet | Receiver detection reports none (status 0). | - |
| `polling_active__polling_configuration` | Polling.Active | Polling.Configuration | At least 1024 TS1 sent in this state, and 8 consecutive TS1 or TS2 with link PAD and lane PAD received. | - |
| `polling_active__polling_compliance` | Polling.Active | Polling.Compliance | Timeout, and `pipe_rx_elec_idle_i` has been 1 in every cycle since entry. | 24 ms, E |
| `polling_active__detect_quiet` | Polling.Active | Detect.Quiet | Timeout otherwise. | 24 ms, E |
| `polling_compliance__polling_active` | Polling.Compliance | Polling.Active | `pipe_rx_elec_idle_i` is 0. | - |
| `polling_configuration__cfg_linkwidth_start` | Polling.Configuration | Configuration.Linkwidth.Start | 8 consecutive TS2 with link PAD and lane PAD received, and 16 TS2 sent after receiving one. | - |
| `polling_configuration__detect_quiet` | Polling.Configuration | Detect.Quiet | Timeout. | 48 ms, E |

In Polling.Active and Polling.Configuration, a received TS whose identifier
symbols are the bit-inverse of the TS1 or TS2 identifier means the lane is
inverted: the MAC toggles `pipe_rx_polarity_o`, and that TS does not match
[cite: PCIe Base Spec, rev 3.0, §4.2.6.2]. `pipe_rx_polarity_o` returns to
0 on entry to Detect.Quiet.

### Configuration

[cite: PCIe Base Spec, rev 3.0, §4.2.6.3]

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `cfg_linkwidth_start__loopback_entry` | Configuration.Linkwidth.Start | Loopback.Entry | `loopback_req_i` high (this port is the master), or 2 consecutive TS1 with Loopback set received (slave). | - |
| `cfg_linkwidth_start__disabled` | Configuration.Linkwidth.Start | Disabled | Downstream role with `disable_req_i` high, or 2 consecutive TS1 with Disable Link set received. | - |
| `cfg_linkwidth_start__cfg_linkwidth_accept` | Configuration.Linkwidth.Start | Configuration.Linkwidth.Accept | Downstream: 2 consecutive TS1 with link 0 and lane PAD. Upstream: 2 consecutive TS1 with a link number and lane PAD; `N` is the link number of the last TS1 received with one. | - |
| `cfg_linkwidth_start__detect_quiet` | Configuration.Linkwidth.Start | Detect.Quiet | Timeout. | 24 ms, E |
| `cfg_linkwidth_accept__cfg_lanenum_wait` | Configuration.Linkwidth.Accept | Configuration.Lanenum.Wait | Downstream: 2 consecutive TS1 with link `N` and lane PAD. Upstream: 2 consecutive TS1 with link `N` and lane 0. | - |
| `cfg_linkwidth_accept__detect_quiet` | Configuration.Linkwidth.Accept | Detect.Quiet | 2 consecutive TS1 with link PAD and lane PAD, or timeout. | 2 ms, E |
| `cfg_lanenum_wait__cfg_lanenum_accept` | Configuration.Lanenum.Wait | Configuration.Lanenum.Accept | 2 consecutive TS1 or TS2 with link `N` and lane 0. | - |
| `cfg_lanenum_wait__detect_quiet` | Configuration.Lanenum.Wait | Detect.Quiet | 2 consecutive TS1 with link PAD, or timeout. | 2 ms, E |
| `cfg_lanenum_accept__cfg_complete` | Configuration.Lanenum.Accept | Configuration.Complete | 2 consecutive TS1 or TS2 with link `N` and lane 0. | - |
| `cfg_lanenum_accept__cfg_lanenum_wait` | Configuration.Lanenum.Accept | Configuration.Lanenum.Wait | 2 consecutive TS1 with link `N` and a lane number other than 0. | - |
| `cfg_lanenum_accept__detect_quiet` | Configuration.Lanenum.Accept | Detect.Quiet | 2 consecutive TS1 with link PAD, or timeout. | 2 ms, E |
| `cfg_complete__cfg_idle` | Configuration.Complete | Configuration.Idle | 8 consecutive TS2 with link `N` and lane 0 received, and 16 TS2 sent after receiving one. The partner's N_FTS is taken from these TS2. | - |
| `cfg_complete__detect_quiet` | Configuration.Complete | Detect.Quiet | Timeout. | 2 ms, E |
| `cfg_idle__l0` | Configuration.Idle | L0 | 8 consecutive idle symbols received, and 16 idle symbols sent after receiving one. | - |
| `cfg_idle__recovery_rcvrlock` | Configuration.Idle | Recovery.RcvrLock | Timeout with `idle_to_rlock` below 255; it is incremented. | 2 ms, E |
| `cfg_idle__detect_quiet` | Configuration.Idle | Detect.Quiet | Timeout with `idle_to_rlock` at 255. | 2 ms, E |

### L0

[cite: PCIe Base Spec, rev 3.0, §4.2.6.5]

A main exit from L0 is taken only when the transmit sub-machine is in
`Tx_L0` and the receive sub-machine is not in `Rx_L0s.Entry`, nor in
`Rx_L0s.FTS` before its timeout. A Recovery exit that arises earlier is
held until then; meanwhile `Tx_L0s.Idle` leaves for `Tx_L0s.FTS`. A packet
being sent when the main machine leaves L0 is ended with EDB
([symbols page](SYMBOLS.md#transmit)). L1 and L2 entry also wait for the
end of a packet being sent.

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `l0__recovery_rcvrlock` | L0 | Recovery.RcvrLock | Any of: the `Rx_L0s.FTS` timeout; a TS1 or TS2 received in `Rx_L0`; `pipe_rx_elec_idle_i` 1 in `Rx_L0` with no EIOS received since `Rx_L0` was entered; downstream role with `hot_reset_req_i` or `disable_req_i` high; `loopback_req_i` high; a `retrain_req_i` pulse. | - |
| `l0__l1_entry` | L0 | L1.Entry | `pm_state_req_i` is 2, and either the upstream role, or the downstream role with an EIOS received in `Rx_L0` while `pm_state_req_i` was 2 or 3. | - |
| `l0__l2_idle` | L0 | L2.Idle | As `l0__l1_entry`, with `pm_state_req_i` 3. | - |

The downstream port waits for the partner's EIOS because the data-link
handshake makes the upstream port go first
[cite: PCIe Base Spec, rev 3.0, §4.2.6.7]; an EIOS that arrives while the
downstream port is not directed to L1 or L2 starts receive L0s instead.

### L0s sub-machines

[cite: PCIe Base Spec, rev 3.0, §4.2.6.6]

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `tx_off__tx_l0` | Tx_Off | Tx_L0 | The main machine enters L0. | - |
| `tx_l0__tx_off` | Tx_L0 | Tx_Off | The main machine leaves L0. | - |
| `tx_l0__tx_l0s_entry` | Tx_L0 | Tx_L0s.Entry | `pm_state_req_i` is 1, no main exit is pending, and no TLP or DLLP symbol was sent or waiting for `L0S_IDLE_CYCLES` consecutive cycles (SKP and idle symbols do not count as traffic). | - |
| `tx_l0s_entry__tx_l0s_idle` | Tx_L0s.Entry | Tx_L0s.Idle | The EIOS is sent, the transmitter has been in electrical idle for 3 cycles (20 ns), and P0s is acknowledged. | - |
| `tx_l0s_idle__tx_l0s_fts` | Tx_L0s.Idle | Tx_L0s.FTS | `pkt_tx_valid_i` high, `pm_state_req_i` not 1, or a main exit pending. | - |
| `tx_l0s_fts__tx_l0` | Tx_L0s.FTS | Tx_L0 | P0 is acknowledged and the FTS ordered sets (as many as the partner's N_FTS) and one SKP ordered set are sent. | - |
| `rx_off__rx_l0` | Rx_Off | Rx_L0 | The main machine enters L0. | - |
| `rx_l0__rx_off` | Rx_L0 | Rx_Off | The main machine leaves L0. | - |
| `rx_l0__rx_l0s_entry` | Rx_L0 | Rx_L0s.Entry | An EIOS received, unless it starts L1 or L2 entry (above). | - |
| `rx_l0s_entry__rx_l0s_idle` | Rx_L0s.Entry | Rx_L0s.Idle | 3 cycles (20 ns) after entry. | - |
| `rx_l0s_idle__rx_l0s_fts` | Rx_L0s.Idle | Rx_L0s.FTS | EI exit. | - |
| `rx_l0s_fts__rx_l0` | Rx_L0s.FTS | Rx_L0 | A SKP ordered set received. | - |
| `rx_l0s_fts__rx_off` | Rx_L0s.FTS | Rx_Off | Timeout; the main machine goes to Recovery.RcvrLock. | 2 x `N_FTS` + 32 cycles |
| `rx_l0s_idle__rx_off` | Rx_L0s.Idle | Rx_Off | The main machine leaves L0 for a directed request. | - |

### Recovery

[cite: PCIe Base Spec, rev 3.0, §4.2.6.4]

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `recovery_rcvrlock__recovery_rcvrcfg` | Recovery.RcvrLock | Recovery.RcvrCfg | 8 consecutive TS1 or TS2 with link `N` and lane 0. | - |
| `recovery_rcvrlock__cfg_linkwidth_start` | Recovery.RcvrLock | Configuration.Linkwidth.Start | Timeout, and at least one TS received in this state. | 24 ms, E |
| `recovery_rcvrlock__detect_quiet` | Recovery.RcvrLock | Detect.Quiet | Timeout, and no TS received. | 24 ms, E |
| `recovery_rcvrcfg__recovery_idle` | Recovery.RcvrCfg | Recovery.Idle | 8 consecutive TS2 with link `N` and lane 0 received, and 16 TS2 sent after receiving one. The partner's N_FTS is taken from these TS2. | - |
| `recovery_rcvrcfg__cfg_linkwidth_start` | Recovery.RcvrCfg | Configuration.Linkwidth.Start | 8 consecutive TS1 whose link is not `N` or whose lane is not 0. | - |
| `recovery_rcvrcfg__detect_quiet` | Recovery.RcvrCfg | Detect.Quiet | Timeout. | 48 ms, E |
| `recovery_idle__hot_reset` | Recovery.Idle | Hot Reset | Downstream role with `hot_reset_req_i` high, or 2 consecutive TS1 with Hot Reset set. | - |
| `recovery_idle__disabled` | Recovery.Idle | Disabled | Downstream role with `disable_req_i` high, or 2 consecutive TS1 with Disable Link set. | - |
| `recovery_idle__loopback_entry` | Recovery.Idle | Loopback.Entry | `loopback_req_i` high (master), or 2 consecutive TS1 with Loopback set (slave). | - |
| `recovery_idle__cfg_linkwidth_start` | Recovery.Idle | Configuration.Linkwidth.Start | 2 consecutive TS1 with lane PAD. | - |
| `recovery_idle__l0` | Recovery.Idle | L0 | 8 consecutive idle symbols received, and 16 idle symbols sent after receiving one. | - |
| `recovery_idle__recovery_rcvrlock` | Recovery.Idle | Recovery.RcvrLock | Timeout with `idle_to_rlock` below 255; it is incremented. | 2 ms, E |
| `recovery_idle__detect_quiet` | Recovery.Idle | Detect.Quiet | Timeout with `idle_to_rlock` at 255. | 2 ms, E |

Directed requests in Recovery.Idle win over received control bits, in the
order of the rows: Hot Reset, Disabled, Loopback.

### L1 and L2

[cite: PCIe Base Spec, rev 3.0, §4.2.6.7]
[cite: PCIe Base Spec, rev 3.0, §4.2.6.8]

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `l1_entry__l1_idle` | L1.Entry | L1.Idle | The EIOS is sent, the transmitter has been in electrical idle for 3 cycles (20 ns), and P1 is acknowledged. | - |
| `l1_idle__recovery_rcvrlock` | L1.Idle | Recovery.RcvrLock | `pm_state_req_i` is 0 or 1, or EI exit. | - |
| `l2_idle__detect_quiet` | L2.Idle | Detect.Quiet | P2 is acknowledged, and `pm_state_req_i` is not 3 or EI exit. | - |

### Control states

[cite: PCIe Base Spec, rev 3.0, §4.2.6.9]
[cite: PCIe Base Spec, rev 3.0, §4.2.6.10]
[cite: PCIe Base Spec, rev 3.0, §4.2.6.11]

A port *directs* Disabled or Hot Reset when it enters the state in the
downstream role with the request input high; otherwise it *follows*. A port
is the Loopback *master* when it enters Loopback.Entry with
`loopback_req_i` high; otherwise it is the *slave*.

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `disabled__detect_quiet` | Disabled | Detect.Quiet | Directing: the 16 TS1 and the EIOS are sent, the transmitter is in electrical idle and `disable_req_i` is low. Following: an EIOS was received and then EI exit, or the timeout with no EIOS received. | Following: 2 ms, E |
| `hot_reset__detect_quiet` | Hot Reset | Detect.Quiet | Directing: the timeout from entry has expired and `hot_reset_req_i` is low. Following: the timeout has expired since entry or since the last 2 consecutive TS1 with Hot Reset set were received, whichever is later. | 2 ms, T |
| `loopback_entry__loopback_active` | Loopback.Entry | Loopback.Active | Master: 2 consecutive TS1 with Loopback set received (its own, looped back by the slave). Slave: at once. | - |
| `loopback_entry__loopback_exit` | Loopback.Entry | Loopback.Exit | Master: timeout. | 2 ms, E |
| `loopback_active__loopback_exit` | Loopback.Active | Loopback.Exit | Master: `loopback_req_i` low. Slave: an EIOS received. | - |
| `loopback_exit__detect_quiet` | Loopback.Exit | Detect.Quiet | The EIOS is sent and the transmitter has been in electrical idle for 3 cycles (20 ns). | - |

The master in Loopback.Active checks that each returned data symbol is one
more than the previous (mod 256); a mismatch counts as a loopback error in
the model and the RTL's DV, not in `rcv_err_o`.
