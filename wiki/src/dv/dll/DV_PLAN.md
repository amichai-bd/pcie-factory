# pcie_dll DV plan

## Strategy

- **Testbench:** [`tb/pcie_dll_tb.sv`](../../../../src/dv/dll/tb/pcie_dll_tb.sv)
  holds one `pcie_dll` with its credit parameters and passes every port
  through by name, plus `cfg_fc_o`, the parameters for the environment.
  The DUT is driven only through its ports.
- **Environment:** [`tb/dll_env.py`](../../../../src/dv/dll/tb/dll_env.py),
  one step per `clk_i` cycle at the falling edge:
  - a **phy stand-in** on the transmit stream: ready 1 from an accepted
    `sop` to its `eop`, link up or down, 0 in the framing cycle after each
    `eop`, otherwise 1 with a set probability (stalls). Like the MAC
    (`pcie_mac_frame.sv`), it keeps a packet open from its `sop` to its
    `eop`, across a link-down too, and flags a bubble or a `sop` inside it;
  - the **far end**, either the [independent partner](PARTNER.md) behind a
    22-cycle line each way (the HAS's two `phy` stacks), whose transmit
    may pause inside and between packets (gaps), or DLLPs a test queues
    by hand. A gate drops packets that start while the receiving side's
    link is down, as a `phy` would;
  - the **shadow**, the [architecture model](../../arch/dll/HAS.md#architecture-model)
    `Dll` with the DUT's parameters, given the same link state, LTSSM
    state, received packets and the TLPs the DUT takes. It sends into
    nothing at its own pace and is never steered;
  - the **receive reference** (`RxRef`), the [receive rules](../../arch/dll/RETRY.md#receiving-tlps)
    written with the partner's CRC and header helpers (neither the RTL nor
    the architecture model), judging every TLP packet the DUT receives in
    the DUT's state of the verdict cycle: the TLPs the upper stream must
    deliver, the Bad TLP and Malformed TLP pulses due, NEXT_RCV_SEQ and
    every NAK_SCHEDULED setting. Until #214 every expected, well-formed
    TLP is stored;
  - the **upper layer** on `tlp_rx_*`: takes beats, stalling at random
    (`rx_ready_p`) or for a set time only when every class is finite (the
    [obligation](../../arch/dll/HAS.md#receive)), and checks each
    delivered TLP against the reference;
  - the **upper transmit driver**, which offers queued TLPs on `tlp_tx_*`
    (valid may drop inside a TLP) and records each TLP the DUT takes, in
    order, as its sequence number;
  - a **retrain stand-in**: on `retrain_req_o` it holds `ltssm_state_i` at
    Recovery for a set time, during which the stand-in takes no new packet
    and packets that start on either line are lost; a test may also drop
    every TLP the DUT starts from a set cycle until the retrain.
- **Checks on every run:** each DLLP the DUT sends is 6 bytes with the
  CRC-16 that both the architecture model and the partner compute, has a
  type allowed in the DUT's state at its `sop` (InitFC1 in FC_INIT1,
  InitFC2 in FC_INIT2, UpdateFC in DL_Active), carries the DUT's
  parameters, equals both models' encoding, and InitFC groups go P, NP,
  Cpl. Every Bad DLLP pulse matches a received DLLP the models judge bad
  (flagged, not 6 bytes, or a CRC mismatch) outside DL_Inactive, one for
  one; no other `err_o` bit pulses. `dl_up_o` matches the state; in
  DL_Inactive every output holds its [reset value](../../arch/dll/HAS.md#reset-values),
  except the tail of a DLLP started before the link went down;
  `pkt_tx_valid_o` never drops inside a packet and no `sop` comes inside
  one, link up or down.
  With the partner, its `violations` list stays empty. Every Ack and Nak
  is sent only in DL_Active and carries NEXT_RCV_SEQ − 1 as the reference
  had it when the arbiter took it; every TLP delivered upward is the next
  one the reference stored, `sop` to `eop`; the Bad TLP and Malformed TLP
  pulses, and one Nak per NAK_SCHEDULED setting, match the reference
  (`check_rx`).
- **TLP checks on every run:** `tlp_tx_ready_o` is 0 outside DL_Active and
  1 every cycle from a taken `sop` to its `eop`. Every TLP the DUT sends
  carries the sequence number it was taken with and that TLP, and its LCRC
  equals the architecture model's, the partner's and zlib's CRC-32. A
  **transmit-order model**, fed the Acks and Naks the DUT received and
  aligned to the cycle the DUT acts on them, judges each TLP the arbiter
  takes: new TLPs in sequence order with none skipped; a replay starts at
  the oldest unacknowledged TLP, goes on in order (skipping only what an
  Ack removed), and completes before any new TLP; no acknowledged TLP is
  sent; and the DL protocol errors it predicts from the range check equal
  the `err_o` bit 4 pulses. `retrain_req_o` pulses exactly with REPLAY_NUM
  rollover. `err_o` bit 5 never pulses (#214).
- **Port roles:** the `dll` is role neutral
  ([HAS, parameters](../../arch/dll/HAS.md#parameters)); a role shows only
  as the credits it advertises. Link-up is run with the DUT advertising
  the default (completions infinite: an Endpoint or a Root Port), finite
  completions (a role that may) and all infinite, each against partners
  advertising the default, finite completions, all infinite and the
  smallest legal values: the full 3 by 4 cross in level 1, all-infinite
  against all-infinite included, and the DUT at the smallest values in
  level 2. The DUT comes up first, the partner first, or both together.
- **Parameter limits:** [`tests/test_dll_params.py`](../../../../src/dv/dll/tests/test_dll_params.py)
  lints `pcie_dll` from `dll.rtl.f` for one illegal value of each `FC_*`
  (and the upper bound of `FC_PD` and `FC_NPD`), which must stop
  elaboration with its `COMMON_ASSERT_PARAM` message, and for the legal
  boundaries, which must lint clean.
- **Never-stall assertion:** `a_rx_never_stall` (the upper layer holds
  `tlp_rx_ready_i` at 1 while `dl_up_o` is 1 when a class is infinite)
  holds in every run, since the upper layer stalls only with every class
  finite. Its negative check, default credits and `tlp_rx_ready_i` 0 in
  DL_Active, stops the simulation on the assertion; a regression entry
  cannot expect a fatal stop, so it is a one-off run recorded in the
  [#213](https://github.com/amichai-bd/pcie-factory/issues/213) PR.
- **Partner model:** its pytest unit tests run in `./build ci unit`, not
  as a regression entry ([partner page](PARTNER.md#unit-tests)).
- **Partner credits:** until the credit gate exists (#214) the partner
  advertises infinite credits in every TLP test, so no TLP can overflow it;
  until the receive path exists (#213) the partner sends no TLPs.
- **Functional coverage** (sampled where driven or observed):
  `dll_dl_states` (the six arcs of the control states), `dll_dllp_tx` (each
  InitFC1, InitFC2 and UpdateFC class; first group and resend; a `sop`
  that waited for ready), `dll_dllp_rx` (good per kind, other type, other
  VC; bad CRC, bad length, flagged; discarded in DL_Inactive; gaps inside),
  `dll_fc_init` (what ended FC_INIT2; finite and infinite fields on both
  sides; which side came up first), `dll_updatefc_tx` (entry and refresh
  per class), `dll_tx_tail` (a DLLP finished with one, two or three beats
  taken at or after the edge that sampled the link down), `dll_reset` (reset and link-up, crossed),
  `dll_tlp_tx` (header 3 or 4 DW; payload none, one DW, some, maximum;
  new or replayed, crossed with payload; upper gaps inside a TLP; a new TLP
  with sequence number 0 after 4095), `dll_replay` (cause Nak or timeout;
  a replay completed, skipped by an Ack, followed by a pending one;
  rollover then retrain or fallback; the timer held outside L0, restarted
  or stopped by an Ack), `dll_acknak_rx` (Ack and Nak, each with progress
  over one or many TLPs, no progress, out of range), `dll_rbuf` (full with
  maximum TLPs, refused by bytes, freed by an Ack, held until a replayed TLP's eop),
  `dll_tlp_rx` (each receive outcome: stored, duplicate, ahead, bad LCRC,
  flagged, too short, nullified, EDB without the inverse, malformed,
  outside FC_INIT2 and DL_Active; class of each stored TLP; gaps inside;
  a Nak scheduled or already scheduled), `dll_malformed` (not whole DWs,
  shorter than a header, Length disagreeing, payload above 128 bytes, no
  header), `dll_acknak_tx` (Ack and Nak; Ack by the timer or after a
  duplicate), `dll_rx_stream` (a beat taken or stalled; a `sop` back to
  back or after idle).

## Cross-check: RTL, architecture model, partner

Run in the `link_up` entries for DLLPs, InitFC and the control states, in
the TLP transmit entries for sequence numbers, LCRC and replay, and in the
`rx_*` entries for the TLP receive path:

| Pair | Compared |
| --- | --- |
| RTL and partner | The partner as the far end and judge: both reach DL_Active, each records the other's advertisement, no partner violation. Every TLP the DUT sends delivered by the partner exactly once and in order, under its fault hooks (Naks, lost TLPs, lost and damaged Acks, Ack delays). |
| RTL and architecture model | DLLP bytes and CRC-16 (every DLLP sent); the sequence of control states visited by the DUT and the shadow, each change within 200 cycles of the other; credits available equal to the shadow's. The LCRC of every TLP sent. Replay-buffer room: a TLP the DUT takes is taken by the shadow within 200 cycles, and a `sop` the DUT refuses is refused by the shadow (outside the cycles where the two act on an Ack at different edges, the LCRC-write cycle, or an acknowledged TLP still on the DUT's stream). The counts of replay timeouts, rollovers, DL protocol errors and replays equal the shadow's in `lcrc_kat`, `replay_directed`, `rollover_directed`, `rbuf_full`, `tlp_partner` and `tlp_rollover`. In `tlp_partner_errors` and `tlp_bidir` they are logged, not compared: the shadow transmits at its own pace (no `phy` stall or upper-stream gap, its own Acks), so with errors a Nak can land inside a replay of one model and after the replay of the other, and with both directions loaded its TLPs lag the DUT's, so an Ack can name a TLP it has not sent yet. |
| Architecture model and partner | A run of the model against a fresh partner with the same skew and credits (`ArchEnd` puts the model on the partner's beat stream): both reach DL_Active, each records the other's advertisement, no partner violation, and the time to DL_Active within 200 cycles of the RTL's. CRC-16 of every received DLLP and of the known answers computed by both. In `tlp_rollover`, the model sends the same TLPs to a fresh partner with the same loss window and Recovery (`arch_partner_send`): 4 timeouts, 1 rollover, 1 retrain, no Bad TLP, every TLP delivered once and in order, as the RTL. |
| RTL, reference and partner (receive) | The partner sends TLPs with its fault hooks; the DUT delivers exactly the partner's TLPs, once and in order; the partner sees no violation (every Ack and Nak in range) and its replay buffer drains (every TLP acknowledged); the RTL's pulses and Naks equal the reference's. |
| RTL and architecture model (receive) | The shadow, given the same packets, delivers the same TLPs and counts the same Bad TLP, Malformed TLP, Nak and duplicate events, and no overflow (`check_shadow_rx`). |
| Architecture model and partner (receive) | The model receives the same TLPs from a fresh partner with the same injection (`arch_partner_traffic`): all delivered once and in order, all acknowledged, no partner violation. |

The two models step at packet level with their own transmit timing, so the
state and timing comparisons allow one InitFC resend (128 cycles) and its
group; the logs record the largest difference. Two RTL defects were found
by these runs and fixed in the RTL and the HAS together, not in a model: a
new TLP could overwrite an acknowledged one still being replayed (now the
[space-in-use rule](../../arch/dll/RETRY.md#replay-buffer), which the
architecture model follows too), and a TLP taken at a rollover went out
ahead of the rollover replay (now the [withdrawal](../../arch/dll/RETRY.md#replay_num)).
The receive path found that the partner left a TLP still going out at a
Nak out of the replay; the [retry page](../../arch/dll/RETRY.md#replay)
now says it is resent, and the partner is corrected
([HAS decision](../../arch/dll/HAS.md#decisions)). One reading difference
with the partner remains a [HAS open item](../../arch/dll/HAS.md#open-items)
(when a running replay ends). Any later disagreement goes there, never
into a model or the RTL silently.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/dll/HAS.md#reset-values) in reset and DL_Inactive | `smoke` | 0 | 1; 256 cycles, random inputs and link-up |
| [Credit parameter ranges](../../arch/dll/HAS.md#parameters) | `param_limits` | 0 | 1; 8 illegal and 3 legal lints, no simulation |
| [CRC-16](../../arch/dll/DLLP.md#crc-16) known answers, transmit and receive; [receiving DLLPs](../../arch/dll/DLLP.md#receiving-dllps): bad CRC, flagged, wrong length, DL_Inactive | `crc16_kat` | 0 | 1; 5 known answers and every single-bit error of them, 150 random DLLPs and one-bit errors, gaps |
| [Control states](../../arch/dll/HAS.md#data-link-control-states) and [initialisation](../../arch/dll/FLOW_CONTROL.md#initialisation) by hand: groups and 128-cycle resend, recording, ignored DLLPs, FC_INIT2 ends, entry UpdateFC, [credits available](../../arch/dll/HAS.md#credits-available), UpdateFC limits, link down from each state | `fc_init_directed` | 0 | 1 |
| [A started DLLP finished across a link-down](../../arch/dll/HAS.md#reset-values), two and one beats left, and all three when the `sop` is taken at the edge that samples `link_up_i` 0; an offered one withdrawn; clean restart | `link_down_mid_dllp` | 0 | 1 |
| [Receiving TLPs](../../arch/dll/RETRY.md#receiving-tlps) by hand: discarded in DL_Inactive and FC_INIT1; a bad TLP's Nak held in FC_INIT2; a good TLP ends FC_INIT2; every class and size, 3- and 4-DW headers, gaps and back to back; duplicates with an Ack at once (D = 2048 too); ahead (D = 1, 2047) with one Nak per NAK_SCHEDULED; bad LCRC, flagged, too short, nullified, EDB without the inverse; each malformed kind, packets beyond the beat counter; link down while a TLP arrives and while one is delivered. [Ack timer](../../arch/dll/RETRY.md#ack-and-nak-scheduling): 35 cycles on an idle stream ([MAS, timing](../../rtl/dll/MAS.md#timing)); Nak and duplicate Ack within 7; [receive latency](../../arch/dll/HAS.md#targets) within 4 | `rx_directed` | 0 | 1 |
| [UpdateFC refresh](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling), round robin, with ready stalls | `updatefc_refresh` | 1 | 2; two 2048-cycle periods |
| Link-up against the partner, default DUT credits, [init target](../../arch/dll/HAS.md#targets) 200 cycles; cross-check | `link_up` | 1 | 0 to 3 (the four partner advertisements); 6 link-ups each |
| The same, DUT completions finite | `link_up_cpl_finite` | 1 | 0 to 3; `FC_CPLH` 16, `FC_CPLD` 64 |
| The same, DUT all infinite | `link_up_infinite` | 1 | 0 to 3; every `FC_*` 0 |
| Link-up with stalls, gaps and DLLP errors both ways (5% corrupted, 3% dropped, 2% flagged by the partner; 5% lost at it), target 4500 cycles | `link_up_stress` | 1 | 0, 1 |
| 300 random TLPs from the partner within the DUT's credits, error-free: delivered once and in order; [Ack latency](../../arch/dll/HAS.md#targets) within 118 cycles, receive latency within 4; cross-checks | `rx_traffic` | 1 | 0, 1 |
| The same with the partner's TLPs dropped 2%, corrupted 3%, nullified 2%, flagged 2%, its DLLPs corrupted 3%, and 15% of the DUT's Acks and Naks lost (replays, duplicates): no bad TLP delivered | `rx_errors` | 1 | 0 to 2 |
| The same, DUT all infinite | `rx_errors_infinite` | 1 | 0; every `FC_*` 0 |
| The same, wide sweep | `rx_errors_sweep` | 2 | 8 derived |
| [Receive buffer](../../arch/dll/FLOW_CONTROL.md#receive-buffer) sizing: every class finite, the upper layer stalls while the partner sends TLPs that use every advertised credit, then takes 30% of beats | `rx_stall` | 1 | 0, 1; `FC_CPLH` 16, `FC_CPLD` 64 |
| The same at the smallest legal credits | `rx_stall_smallest` | 2 | 4 derived |
| NEXT_RCV_SEQ wrapping past 4095: 4200 short TLPs with light errors (Naks and duplicates around the wrap) | `rx_seq_wrap` | 2 | 1 derived |
| The same, wide sweep | `link_up_stress_sweep` | 2 | 8 derived |
| The same, DUT at the smallest legal credits | `link_up_stress_smallest` | 2 | 4 derived |
| [Sequence numbers](../../arch/dll/RETRY.md#transmitter-state) from 0 and the [LCRC](../../arch/dll/RETRY.md#lcrc): the retry page's example (`E1 F1 E7 E2`), 86 random TLPs of every shape with upper gaps and `phy` stalls; [TX latency target](../../arch/dll/HAS.md#targets) | `lcrc_kat` | 0 | 1; latency 3 cycles, target 4 |
| [Received Ack and Nak](../../arch/dll/RETRY.md#received-ack-and-nak) and the [replay](../../arch/dll/RETRY.md#replay) by hand: Ack, Nak with and without progress, out of range, everything acknowledged; [replay timer](../../arch/dll/RETRY.md#timers) at 356 cycles, held outside L0, restarted by progress; Ack during a replay, Nak during a replay | `replay_directed` | 0 | 1 |
| [REPLAY_NUM](../../arch/dll/RETRY.md#replay_num) rollover, `retrain_req_o`, the hold with and without a retrain, progress setting REPLAY_NUM to 0 | `rollover_directed` | 0 | 1; hold 1025 cycles without, 302 with a 300-cycle Recovery |
| [Replay timer](../../arch/dll/RETRY.md#timers) never early: the LTSSM leaves L0 for 5 cycles from the cycle the timer would reach 356; timeout 361 cycles after the `eop` | `timer_edge` | 0 | 1 |
| [Rollover edge](../../arch/dll/RETRY.md#replay_num): a TLP `sop` waiting for the `phy` in the rollover cycle, the `phy` ready in exactly that cycle: not offered, resent after the oldest by the replay | `rollover_edge` | 0 | 1 |
| [Replay-buffer](../../arch/dll/RETRY.md#replay-buffer) back-pressure on the upper stream: 6 maximum TLPs, the 1024-byte boundary (1022 taken, 1026 refused), room freed by an Ack | `rbuf_full` | 0 | 1 |
| [A started TLP finished across a link-down](../../arch/dll/HAS.md#reset-values), the TLP being taken abandoned, sequence numbers from 0 after the relink; an offered TLP withdrawn | `link_down_mid_tlp` | 0 | 1 |
| TLPs to the partner, error-free, stalls and gaps: delivered once and in order, no replay | `tlp_partner` | 1 | 0, 1 |
| TLPs to the partner with its fault hooks: Nak'd, lost, lost and damaged Acks, forced Naks, Ack delays past the timeout; delivered once and in order | `tlp_partner_errors` | 1 | 0, 1, 2 |
| The same, wide sweep | `tlp_partner_errors_sweep` | 2 | 8 derived |
| [Rollover target](../../arch/dll/HAS.md#targets): TLPs lost from a set cycle until the retrain; architecture model against the partner in the same scenario | `tlp_rollover` | 1 | 0, 1 |
| [Line use and replay-buffer sizing targets](../../arch/dll/HAS.md#targets) (`mwr_max`, `ack_spec_max`): 500 128-byte writes, partner Acks at 32 and 118 cycles, at least 99% | `mwr_max`, `ack_spec_max` | 1 | 1 each; 99.30% both (architecture model 99.20%) |
| Sequence numbers across the 4095-to-0 wrap with Naks, lost TLPs and lost Acks around it | `seq_wrap` | 2 | 2 derived |
| Both directions loaded: 300 TLPs each way, the DUT's Acks waiting behind its own TLPs; [Ack latency target](../../arch/dll/HAS.md#targets) 118 cycles | `tlp_bidir` | 1 | 0, 1; worst Ack 107 and 108 cycles |

## Mutation evidence

Each mutant was applied to the RTL and levels 0 and 1 run one test at a
time; each is caught, and the unmutated RTL passes. The entry named is the
first that fails in that run, at its first seed (level 0 unless marked).
The TLP transmit mutants ([#212](https://github.com/amichai-bd/pcie-factory/issues/212))
were all run serially; the timer and rollover ones (replay timeout at
355, timer not held outside L0, progress not restarting the timer,
rollover after 3 replays, no withdrawal, `sop` offered in the rollover
cycle, timer on L0 registered late) were rerun on the final RTL.

| Block | Mutant | First failing entry | Result |
| --- | --- | --- | --- |
| Package | CRC-16 polynomial `100B`h to `100A`h | `crc16_kat` | InitFC1-P sent as `40020020288c`, the DLLP page's `40020020f534`. |
| Package | CRC-16 bytes 4 and 5 swapped | `crc16_kat` | InitFC1-P sent as `4002002034f5`. |
| DLLP receive | Length check removed | `crc16_kat` | 394 Bad DLLP pulses for 396 bad DLLPs: the long DLLPs ending in a good CRC pass. |
| DLLP receive | `pkt_rx_err_i` ignored | `crc16_kat` | 395 pulses for 396. |
| Package | VC of an FC DLLP not checked | `fc_init_directed` | P credits (5, 5) from a VC 1 InitFC1, expected (12, infinite). |
| Package | A later InitFC overwrites the recorded credits | `fc_init_directed` | P credits (99, 99). |
| Control | FC_INIT1 and FC_INIT2 left before the group's last DLLP ends | `fc_init_directed` | An InitFC1-Cpl sent in FC_INIT2. |
| Control | An InitFC1 ends FC_INIT2 | `fc_init_directed` | DL_Active after an InitFC1. |
| Control | InitFC resend every 64 cycles | `fc_init_directed` | Groups 64 cycles apart. |
| Control | InitFC group sent Cpl first | `crc16_kat` | The first DLLP is InitFC1-Cpl (`60000000d892`), not the DLLP page's InitFC1-P. |
| Control | Link down does not reset the state | `fc_init_directed` | DL_Inactive not reached within 3 cycles of the link going down. |
| Flow control | No UpdateFC on entering DL_Active | `fc_init_directed` | No entry UpdateFC. |
| Flow control | An UpdateFC does not move a finite data limit | `fc_init_directed` | NP data credits 40, expected 41. |
| Flow control | The refresh never falls due | `updatefc_refresh` | One UpdateFC per class in two periods. |
| Flow control | An infinite field shown as 0 | `fc_init_directed` | NP header credits 0, expected all ones. |
| Top | `dl_up_o` only in DL_Active | `crc16_kat` | `dl_up_o` 0 in FC_INIT2. |
| Transmit | Beats advance without ready | `crc16_kat` | A beat outside a packet at the phy stand-in. |
| Transmit | A started DLLP cut when the link goes down | `link_down_mid_dllp` | Stops on `a_eop_follows_sop`: valid falls inside the open packet. |
| Transmit | A `sop` taken at the edge that samples `link_up_i` 0 not counted as started (`started` from `tx_q` only) | `link_down_mid_dllp` | Stops on `a_eop_follows_sop`: the packet opened at the phy is abandoned. |
| TLP transmit | Transmit LCRC register seeded with 0 | `lcrc_kat` | The example sent with LCRC `d977a3cc`, not the retry page's `e1f1e7e2`. |
| TLP transmit | LCRC word without the bit reversal | `lcrc_kat` | The example sent with LCRC `47e78f87`. |
| TLP transmit | Sequence-number bytes swapped | `lcrc_kat` | TLPs from sequence 1 on not recognised: 1 of 7 TX latencies measured. |
| TLP transmit | Sequence numbers assigned two apart | `lcrc_kat` | Stops on `a_held_limit`. |
| TLP transmit | Room check allows 1026 bytes | `rbuf_full` | A TLP taken beyond 1024 bytes. |
| TLP transmit | Room check counts 1 sequence-number byte per TLP, not 2 | `rbuf_full` | A TLP taken beyond 1024 bytes. |
| TLP transmit | Space of an acknowledged TLP on the stream freed at once | `rbuf_full` | A TLP taken at cycle 1830, the acknowledged replay of TLP 8 still on the stream. |
| TLP transmit | Out-of-range Ack/Nak not reported | `replay_directed` | DL protocol errors 0, expected 2. |
| TLP transmit | A Nak does not replay | `replay_directed` | Nothing sent after Nak 2, expected 3 and 4 replayed. |
| TLP transmit | A replay stops before the newest TLP sent | `replay_directed` | Only 3 replayed after Nak 2. |
| TLP transmit | A running replay does not skip what an Ack removed | `replay_directed` | Acknowledged TLP 12 sent in the replay. |
| Package | Replay timeout at 355 cycles | `replay_directed` | Timeout 355 cycles after the `eop`, expected 356. |
| TLP transmit | Replay timer not held outside L0 | `replay_directed` | Timeout 356 cycles after the `eop` with 100 cycles of Recovery, expected 456. |
| TLP transmit | Forward progress does not restart the replay timer | `replay_directed` | Timeout 135 cycles after the Ack, expected 357. |
| TLP transmit | REPLAY_NUM rolls over after 3 replays | `rollover_directed` | Rollover after 3 timeouts. |
| TLP transmit | No retrain request on a rollover | `rollover_directed` | `retrain_req_o` never pulses (rollover at cycle 1526). |
| TLP transmit | Rollover hold ignores the LTSSM's return to L0 | `rollover_directed` | No replay within 400 cycles of a 300-cycle Recovery. |
| Package | Rollover hold falls back after 512 cycles | `rollover_directed` | First TLP 513 cycles after the rollover, expected 1024. |
| TLP transmit | A replay requested during a replay is lost | `replay_directed` | One replay after two Naks. |
| Transmit | No withdrawal at a rollover | `rollover_edge` | A TLP started at the rollover edge, ahead of the replay. |
| TLP transmit | A `sop` taken while the LCRC word is written | `lcrc_kat` | Stops on `a_tlp_words`. |
| TLP transmit | A started TLP's stream cleared when the link goes down | `link_down_mid_tlp` | Stops on `a_beat_without_tlp`. |
| TLP transmit | New TLPs before a running replay | `rbuf_full` | At cycle 1972 TLP 15 taken before the replay sent TLP 10. |
| TLP transmit | A waiting `sop` still offered in the rollover cycle (hold from the register only) | `rollover_edge` | A TLP started at the rollover edge, ahead of the replay. |
| TLP transmit | Replay timer on L0 registered one edge late | `timer_edge` | Timeout 356 cycles after the `eop`, counting a cycle out of L0; expected 361. |
| Package | LCRC polynomial `04C11DB7`h to `04C11DB6`h | `rx_directed` | DL_Active not reached: the TLP that should end FC_INIT2 has a bad LCRC. |
| Package | LCRC remainder not complemented | `rx_directed` | The same. |
| TLP receive | The nullified check inverted (an inverted LCRC is bad, a plain one silent) | `rx_errors` | Bad TLP pulses 196, expected 188 (seed 0). |
| TLP receive | D = 2047 taken as a duplicate | `rx_directed` | Bad TLP pulses 9, expected 10. |
| TLP receive | A Nak for every bad TLP (NAK_SCHEDULED ignored) | `rx_directed` | 10 Naks sent, expected 6. |
| TLP receive | NAK_SCHEDULED not cleared by an accepted TLP | `rx_directed` | No Nak after the next bad TLP. |
| Package | Ack timer limit 32 to 16 cycles | `rx_directed` | Ack 19 cycles after the eop, expected 35. |
| TLP receive | No Ack at once after a duplicate | `rx_directed` | No Ack or Nak after the duplicate. |
| Package | A payload above 128 bytes not malformed | `rx_directed` | Malformed TLP pulses 7, expected 8. |
| Package | A length that is not whole DWs not malformed | `rx_directed` | Malformed TLP pulses 7, expected 8. |
| TLP receive | Ack and Nak carry NEXT_RCV_SEQ, not NEXT_RCV_SEQ − 1 | `rx_directed` | Nak 1, expected 0. |
| TLP receive | A discarded TLP not rewound out of the buffer | `rx_directed` | The bad TLP's DWs delivered before the TLP that ended FC_INIT2. |
| Receive buffer | Read up to the write pointer, not the commit pointer | `rx_directed` | A TLP delivered in FC_INIT1. |
| Receive buffer | A beat dropped when the upper layer stalls | `rx_stall` | Not every TLP delivered within 200000 cycles (seed 0). |
| TLP receive | Link down does not clear NEXT_RCV_SEQ | `rx_directed` | After the next link-up, sequence 0 is a duplicate: no delivery. |
| Top | A good TLP does not end FC_INIT2 | `rx_directed` | DL_Active not reached. |
| TLP receive | An ahead TLP pulses no Bad TLP | `rx_directed` | Bad TLP pulses 8, expected 10. |
| TLP receive | Ack and Nak sent outside DL_Active | `rx_directed` | A Nak sent in FC_INIT2. |
| TLP receive | Sending an Ack does not stop the Ack timer | `rx_directed` | Ack 9 cycles after the eop, expected 35. |
| TLP receive | A malformed TLP not acknowledged | `rx_directed` | 17 TLPs delivered, 19 expected. |
| TLP receive | A TLP one ahead taken as expected | `rx_directed` | 5 Naks sent, expected 6. |
| Receive buffer | Commit drops a stored TLP's last DW | `rx_directed` | The TLP that ended FC_INIT2 not delivered. |
| TLP receive | The two received LCRC beats swapped | `rx_directed` | DL_Active not reached. |
| TLP receive | Sequence number bytes swapped | `rx_directed` | Ack 3 cycles after the eop, expected 35. |
| TLP receive | Every accepted TLP restarts the Ack timer | `rx_traffic` | An Ack 154 cycles after its TLP's eop, HAS bound 118 (seed 0). |
| TLP receive | The last DW not marked eop | `rx_directed` | The TLP that ended FC_INIT2 not delivered. |
| TLP receive | A 4-byte packet not too short | `rx_directed` | Bad TLP pulses 9, expected 10: four zero bytes pass as the LCRC of nothing. |

An UpdateFC that writes an infinite field's stored limit is not a mutant
that can be caught: an infinite field shows all ones from its flag,
whatever the limit holds.

## Gaps

| Feature | Owning issue |
| --- | --- |
| The receive overflow check and the overflowed-TLP verdict ([receive buffer](../../arch/dll/FLOW_CONTROL.md#receive-buffer)), driven through `rx_fit` | [#214](https://github.com/amichai-bd/pcie-factory/issues/214) |
| [Credit consumption, the credit gate, receive accounting, UpdateFC on returned credits](../../arch/dll/FLOW_CONTROL.md) | [#214](https://github.com/amichai-bd/pcie-factory/issues/214) |
| [Targets](../../arch/dll/HAS.md#targets) end to end (Lossless with errors both ways) | [#215](https://github.com/amichai-bd/pcie-factory/issues/215), [#216](https://github.com/amichai-bd/pcie-factory/issues/216) |

## Coverage holes

No floors yet (the plan has gaps). Merged over levels 0 to 2: line 98.0%
(431 of 440 line and branch points), toggle 76.1%, functional 100% (152
bins). Every line and branch point of the TLP transmit block is hit;
every one but these is hit:

| Cause | Points |
| --- | --- |
| Unreachable by construction | The receive buffer full (`rx_full`, `noroom`): the buffer holds every TLP the advertised credits cover plus the one being collected ([MAS, receive buffer](../../rtl/dll/MAS.md#receive-buffer)), and the partner respects the credits. |
| Unreachable by construction | The lane-disable branch of both RAM instances' write port (`LANES` is 1, the lane enable tied to 1). |
| Unreachable until #214 | The round-robin picks of the second and third class: without credit returns, classes fall due only in turn, so the pointer always names the due class. |

Toggle holes are mostly the tied-off TLP receive ports, the upper data
bits of the credit limits (no partner advertises them) and the
testbench's pass-through ports. In the TLP transmit block 23 of 1820
toggle points are missed: `credit_ok_i` never falls (tied to 1 until
#214); `hold_left` never falls, as only the next rollover or DL_Inactive
clears it; bits that are constant by construction: bit 0 of the
space-in-use sums (whole words plus 2 bytes per TLP), bits 1:0 of a TLP's
size (whole DWs), bits 7:4 of the sequence-number beat (reserved, 0);
bit 13 of the space in use, which stays below 8192; and bit 10 of the
offered payload size (a 1024-DW Length, never offered: the upper layer
offers payloads of at most 128 bytes).
