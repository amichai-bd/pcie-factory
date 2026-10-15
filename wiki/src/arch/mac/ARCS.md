# pcie_mac LTSSM arcs

Every arc of the [`pcie_mac` LTSSM](LTSSM.md), with its condition and
timeout, under the [common rules](LTSSM.md#common-rules); citations point
to the PCIe Base Spec rev 3.0 for cross-checking only (Q21). This list is
the 100% arc-coverage target of the LTSSM tests
([#146](https://github.com/amichai-bd/pcie-factory/issues/146),
[#79](https://github.com/amichai-bd/pcie-factory/issues/79),
[#80](https://github.com/amichai-bd/pcie-factory/issues/80),
[#90](https://github.com/amichai-bd/pcie-factory/issues/90)), **in each
port role**: all 64 arcs are reachable as downstream and as upstream port,
which the model's [arc sweep](MODEL.md#arc-sweep) shows. The
[architecture model](MODEL.md) holds the same list (`ARCS`), and its
self-test fails if the two differ.

Arc names are the coverage bins: `<from>__<to>` of the states' short
names. The sampled value is the `(from, to)` tuple of the two state names
in the table, as in the regress SPEC's
[state-machine example](../../../tools/regress/SPEC.md#functional-coverage);
sub-machine arcs are sampled on the sub-machines' changes. Reset is not an
arc.

## Detect and Polling

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
inverted: the MAC sets `pipe_rx_polarity_o` to 1, and that TS does not
match [cite: PCIe Base Spec, rev 3.0, §4.2.6.2]. It is set at most once per
training: a later inverted TS (one already in the receive pipeline when the
PCS started inverting) leaves it at 1. It returns to 0 on entry to
Detect.Quiet.

## Configuration

[cite: PCIe Base Spec, rev 3.0, §4.2.6.3]

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `cfg_linkwidth_start__loopback_entry` | Configuration.Linkwidth.Start | Loopback.Entry | `loopback_req_i` high (this port is the master), or 2 consecutive TS1 with Loopback set received (slave). | - |
| `cfg_linkwidth_start__disabled` | Configuration.Linkwidth.Start | Disabled | Downstream role with `disable_req_i` high, or 2 consecutive TS1 with Disable Link set received. | - |
| `cfg_linkwidth_start__cfg_linkwidth_accept` | Configuration.Linkwidth.Start | Configuration.Linkwidth.Accept | Downstream: 2 consecutive TS1 with link 0 and lane PAD. Upstream: 2 consecutive TS1 with a link number and lane PAD; `N` is the link number of the last TS1 received with one (a data symbol in the link field; PAD or another K symbol is none). | - |
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

## L0

[cite: PCIe Base Spec, rev 3.0, §4.2.6.5]

A main exit from L0 is taken only when the transmit sub-machine is in
`Tx_L0` and the receive sub-machine is not in `Rx_L0s.Entry`, nor in
`Rx_L0s.FTS` before its timeout. A Recovery exit that arises earlier is
held until then; meanwhile `Tx_L0s.Idle` leaves for `Tx_L0s.FTS`. A packet
being sent when the main machine leaves L0 is ended with EDB
([symbols page](SYMBOLS.md#transmit)).

**L1 and L2 entry** need, in both roles, the request (`pm_state_req_i` 2
or 3) **and** a received EIOS: one received in `Rx_L0` while the request
is held, or the receiver in `Rx_L0s.Entry` or `Rx_L0s.Idle` (an EIOS
started it) [cite: PCIe Base Spec, rev 3.0, §4.2.6.5]. The upstream port
sends first: once directed, with no packet being sent and the transmitter
in `Tx_L0`, it sends one EIOS and holds its transmitter in electrical idle
(PowerDown stays P0, the code stays `0x20`) while it waits for the
downstream port's EIOS. The downstream port sends its EIOS in L1.Entry or
L2.Idle. While the upstream port waits, a TS1 or TS2 from the partner (it
went to Recovery instead) takes it to Recovery as in any L0, and so does a
withdrawn request once its transmitter has been idle for 3 cycles (20 ns);
it never enters L1 or L2 without the partner's EIOS. The data-link
handshake that precedes the request is the upper layer's
[cite: PCIe Base Spec, rev 3.0, §4.2.6.7].

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `l0__recovery_rcvrlock` | L0 | Recovery.RcvrLock | Any of: the `Rx_L0s.FTS` timeout; a TS1 or TS2 received in `Rx_L0`; `pipe_rx_elec_idle_i` 1 in `Rx_L0` with no EIOS received since `Rx_L0` was entered; downstream role with `hot_reset_req_i` or `disable_req_i` high; `loopback_req_i` high; a `retrain_req_i` pulse; an upstream port waiting for L1 or L2 whose request was withdrawn (above). | - |
| `l0__l1_entry` | L0 | L1.Entry | `pm_state_req_i` is 2 and an EIOS received (above); the upstream port has sent its EIOS. | - |
| `l0__l2_idle` | L0 | L2.Idle | As `l0__l1_entry`, with `pm_state_req_i` 3. | - |

An EIOS that arrives while the port is not directed to L1 or L2 starts
receive L0s.

## L0s sub-machines

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
| `rx_l0s_fts__rx_off` | Rx_L0s.FTS | Rx_Off | Timeout; the main machine goes to Recovery.RcvrLock. | The smaller of 2 x `N_FTS` + 32 and 4 x `N_FTS` + 12 cycles |
| `rx_l0s_idle__rx_off` | Rx_L0s.Idle | Rx_Off | The main machine leaves L0 for a directed request. | - |

## Recovery

[cite: PCIe Base Spec, rev 3.0, §4.2.6.4]

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `recovery_rcvrlock__recovery_rcvrcfg` | Recovery.RcvrLock | Recovery.RcvrCfg | 8 consecutive TS1 or TS2 with link `N` and lane 0. | - |
| `recovery_rcvrlock__cfg_linkwidth_start` | Recovery.RcvrLock | Configuration.Linkwidth.Start | Timeout, and at least one TS received in this state. | 24 ms, E |
| `recovery_rcvrlock__detect_quiet` | Recovery.RcvrLock | Detect.Quiet | Timeout, and no TS received. | 24 ms, E |
| `recovery_rcvrcfg__recovery_idle` | Recovery.RcvrCfg | Recovery.Idle | 8 consecutive TS2 with link `N` and lane 0 received, and 16 TS2 sent after receiving one. The partner's N_FTS is taken from these TS2. | - |
| `recovery_rcvrcfg__cfg_linkwidth_start` | Recovery.RcvrCfg | Configuration.Linkwidth.Start | 8 consecutive TS1 whose link is not `N` or whose lane is not 0 received (latched), and 16 TS2 sent after receiving the first such TS1. | - |
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

## L1 and L2

[cite: PCIe Base Spec, rev 3.0, §4.2.6.7]
[cite: PCIe Base Spec, rev 3.0, §4.2.6.8]

| Arc | From | To | Condition | Timeout |
| --- | --- | --- | --- | --- |
| `l1_entry__l1_idle` | L1.Entry | L1.Idle | The EIOS is sent, the transmitter has been in electrical idle for 3 cycles (20 ns), and P1 is acknowledged. | - |
| `l1_idle__recovery_rcvrlock` | L1.Idle | Recovery.RcvrLock | `pm_state_req_i` is 0 or 1, or EI exit. | - |
| `l2_idle__detect_quiet` | L2.Idle | Detect.Quiet | P2 is acknowledged, and `pm_state_req_i` is not 3 or EI exit. | - |

## Control states

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
