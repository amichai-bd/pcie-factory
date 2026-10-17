# pcie_dll DV plan

## Strategy

- **Testbench:** [`tb/pcie_dll_tb.sv`](../../../../src/dv/dll/tb/pcie_dll_tb.sv)
  holds one `pcie_dll` with its credit parameters and passes every port
  through by name, plus `cfg_fc_o`, the parameters for the environment.
  The DUT is driven only through its ports.
- **Gate tap:** [`tb/pcie_dll_fc_tap.sv`](../../../../src/dv/dll/tb/pcie_dll_fc_tap.sv),
  a bind module on `pcie_dll` listed in `dll.dv.f`, observes the
  MAS-named `fc_tx_ok` ([credit interface](../../rtl/dll/MAS.md#credit-interface));
  the testbench top shows it as `probe_tx_ok_o` for the gate check. It
  drives nothing, and `pcie_dll`'s ports stay the HAS's.
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
    nothing at its own pace and is never steered; its upper layer takes
    beats when the DUT's does;
  - the **receive reference** (`RxRef`), the [receive rules](../../arch/dll/RETRY.md#receiving-tlps)
    written with the partner's CRC and header helpers (neither the RTL nor
    the architecture model), judging every TLP packet the DUT receives in
    the DUT's state of the verdict cycle: the TLPs the upper stream must
    deliver, the Bad TLP, Malformed TLP and Receiver overflow pulses due,
    NEXT_RCV_SEQ and every NAK_SCHEDULED setting. It keeps the receive
    credit counters by the [flow-control page](../../arch/dll/FLOW_CONTROL.md#receive-accounting):
    an expected, well-formed TLP its class's free credits (or reserve) do
    not cover overflows, with no Ack or Nak and NEXT_RCV_SEQ unchanged;
  - the **upper layer** on `tlp_rx_*`: takes beats, stalling at random
    (`rx_ready_p`) or for a set time only when every class is finite (the
    [obligation](../../arch/dll/HAS.md#receive)), checks each delivered
    TLP against the reference and returns its credits at its `eop`;
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
  parameters (InitFC) or its CREDITS_ALLOCATED as the registers showed it
  when the arbiter took it, infinite fields 0 (UpdateFC), equals both
  models' encoding, and InitFC groups go P, NP, Cpl. Each UpdateFC was due
  (entry, credits returned, or 2048 cycles since its class's last) and is
  the first due class from the round-robin pointer. Every Bad DLLP pulse
  matches a received DLLP the models judge bad (flagged, not 6 bytes, or a
  CRC mismatch) outside DL_Inactive, one for one; `err_o` bits 2 to 4
  (#212's) never pulse. `dl_up_o` matches the state; in
  DL_Inactive every output holds its [reset value](../../arch/dll/HAS.md#reset-values),
  except the tail of a DLLP started before the link went down;
  `pkt_tx_valid_o` never drops inside a packet and no `sop` comes inside
  one, link up or down.
  With the partner, its `violations` list stays empty. Every Ack and Nak
  is sent only in DL_Active and carries NEXT_RCV_SEQ − 1 as the reference
  had it when the arbiter took it; every TLP delivered upward is the next
  one the reference stored, `sop` to `eop`; the Bad TLP, Malformed TLP
  and Receiver overflow pulses, and one Nak per NAK_SCHEDULED setting,
  match the reference (`check_rx`). In the flow-control tests a returned
  credit is in an UpdateFC offered within 20 cycles (a free stream: at
  most the packet on it, an Ack or Nak and two other classes' UpdateFCs
  ahead).
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
  rollover.
- **Credit gate, every cycle:** the gate (`probe_tx_ok_o`, from the
  [gate tap](#strategy)) on the first DW driven (a TLP's `sop`, or a
  random header while the driver is idle) equals the
  [rule](../../arch/dll/FLOW_CONTROL.md#transmit-accounting) and the
  architecture model's `credits_ok`, with the limits as the DUT's
  registers hold them and CREDITS_CONSUMED counted from the `sop`s the
  DUT took; a taken `sop` needs the gate open; the credits available
  equal those limits less that count. A `sop` the gate refuses is left out
  of the replay-space cross-check with the shadow, which records an
  UpdateFC a cycle earlier than the DUT.
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
- **Partner credits:** the TLP transmit entries of #212 run with the
  partner advertising infinite credits; the flow-control entries use
  finite, default and infinite advertisements, and the partner judges
  every TLP against them.
- **Functional coverage** (sampled where driven or observed):
  `dll_dl_states` (the six arcs of the control states), `dll_dllp_tx` (each
  InitFC1, InitFC2 and UpdateFC class; first group and resend; a `sop`
  that waited for ready), `dll_dllp_rx` (good per kind, other type, other
  VC; bad CRC, bad length, flagged; discarded in DL_Inactive; gaps inside),
  `dll_fc_init` (what ended FC_INIT2; finite and infinite fields on both
  sides; which side came up first), `dll_updatefc_tx` (entry, refresh and
  returned credits per class; the class sent counted from the round-robin
  pointer: 0, 1 or 2 on), `dll_fc_tx` (gate open, closed by headers, data
  or both, or infinite, per class; each consumed counter wrapped; a TLP
  refused and then taken), `dll_fc_rx` (stored, overflow, malformed,
  malformed under 4 bytes, per class; finite and infinite classes;
  returns per class), `dll_tx_tail` (a DLLP finished with one, two or three beats
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
  overflow,
  outside FC_INIT2 and DL_Active; class of each stored TLP; gaps inside;
  a Nak scheduled or already scheduled), `dll_malformed` (not whole DWs,
  shorter than a header, Length disagreeing, payload above 128 bytes, no
  header), `dll_acknak_tx` (Ack and Nak; Ack by the timer or after a
  duplicate), `dll_rx_stream` (a beat taken or stalled; a `sop` back to
  back or after idle).

## Cross-check: RTL, architecture model, partner

Run in the `link_up` entries for DLLPs, InitFC and the control states, in
the TLP transmit entries for sequence numbers, LCRC and replay, in the
`rx_*` entries for the TLP receive path, and in the flow-control entries
for credit accounting:

| Pair | Compared |
| --- | --- |
| RTL and partner | The partner as the far end and judge: both reach DL_Active, each records the other's advertisement, no partner violation. Every TLP the DUT sends delivered by the partner exactly once and in order, under its fault hooks (Naks, lost TLPs, lost and damaged Acks, Ack delays). |
| RTL and architecture model | DLLP bytes and CRC-16 (every DLLP sent); the sequence of control states visited by the DUT and the shadow, each change within 200 cycles of the other; credits available equal to the shadow's. The LCRC of every TLP sent. Replay-buffer room: a TLP the DUT takes is taken by the shadow within 200 cycles, and a `sop` the DUT refuses is refused by the shadow (outside the cycles where the two act on an Ack at different edges, the LCRC-write cycle, or an acknowledged TLP still on the DUT's stream). The counts of replay timeouts, rollovers, DL protocol errors and replays equal the shadow's in `lcrc_kat`, `replay_directed`, `rollover_directed`, `rbuf_full`, `tlp_partner` and `tlp_rollover`. In `tlp_partner_errors` and `tlp_bidir` they are logged, not compared: the shadow transmits at its own pace (no `phy` stall or upper-stream gap, its own Acks), so with errors a Nak can land inside a replay of one model and after the replay of the other, and with both directions loaded its TLPs lag the DUT's, so an Ack can name a TLP it has not sent yet. |
| Architecture model and partner | A run of the model against a fresh partner with the same skew and credits (`ArchEnd` puts the model on the partner's beat stream): both reach DL_Active, each records the other's advertisement, no partner violation, and the time to DL_Active within 200 cycles of the RTL's. CRC-16 of every received DLLP and of the known answers computed by both. In `tlp_rollover`, the model sends the same TLPs to a fresh partner with the same loss window and Recovery (`arch_partner_send`): 4 timeouts, 1 rollover, 1 retrain, no Bad TLP, every TLP delivered once and in order, as the RTL. |
| RTL, reference and partner (receive) | The partner sends TLPs with its fault hooks; the DUT delivers exactly the partner's TLPs, once and in order; the partner sees no violation (every Ack and Nak in range) and its replay buffer drains (every TLP acknowledged); the RTL's pulses and Naks equal the reference's. |
| RTL and architecture model (receive) | The shadow, given the same packets, judges every received TLP as the reference does on the DUT's counters (stored, overflow, malformed, bad, duplicate, discarded), overflows included, and counts the same Bad TLP, Malformed TLP, Receiver overflow, Nak and duplicate events (`check_shadow_rx`). It delivers the same TLPs, except that at a link-down the TLP being delivered may be finished by one side and abandoned by the other (their delivery latencies differ): within an epoch ended by a link-down the two delivery lists are equal, or the longer is the shorter plus exactly one TLP; the last epoch must match exactly. Both sides' free credits for the TLP's class are equal before every TLP, so a drifting counter fails at the first TLP it affects. A verdict that differed on the same free credits would be a rule difference and go to the HAS open items. |
| RTL and architecture model, transmit credits | Every cycle: the gate and the credits available ([checks](#strategy)). |
| RTL and partner, credits | `credit_starve`, `credits_infinite`: the partner receives the TLPs the DUT accepted, on its real transmit stream, and judges each against its advertisement, no overflow violation, every TLP once and in order. `credit_starve_rx`, `credits_infinite_rx`: the partner sends as the DUT's UpdateFCs allow while the DUT's upper layer starves it; no Receiver overflow, every TLP delivered once and in order. |
| Architecture model and partner, credits | The same TLPs, credits and upper-layer schedule from the model to a fresh partner (`arch_to_partner_traffic`): every TLP once and in order, no violation. |
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
| [Transmit accounting](../../arch/dll/FLOW_CONTROL.md#transmit-accounting): the gate for every class and field, at exactly half the range and one past it, after limits beyond half the range, with infinite fields; CREDITS_CONSUMED wrapping; credits available; cleared by link-down mid-TLP | `fc_tx_gate` | 0 | 1; hand-driven far end that acknowledges every TLP |
| [Receive accounting](../../arch/dll/FLOW_CONTROL.md#receive-accounting) on real TLPs from a hand-driven far end that replays: stored, malformed (credits returned at once, none under 4 bytes), returns at the delivered `eop`; [UpdateFC](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling) on returns, in FC_INIT2 (entry value) and DL_Active, its value, cause, round robin and latency; refreshes; link down; default credits | `fc_rx_accounting` | 0 | 1 |
| The same, every class finite, the upper layer slow and stalling: the [overflow check](../../arch/dll/FLOW_CONTROL.md#receive-buffer), Receiver overflow, no Ack or Nak, the TLPs behind arriving ahead | `fc_rx_accounting_finite` | 1 | 2 derived |
| The same, every class infinite (the reserve; UpdateFC all 0) | `fc_rx_accounting_infinite` | 1 | 2 derived |
| [Credits target](../../arch/dll/HAS.md#targets), transmit side: starvation and recovery with the partner's upper layer taking 30%, stalling 4000 cycles, taking 30%, stalling 10000 cycles, then taking all; no overflow at the partner's receive-side check; arch model against a fresh partner | `credit_starve` | 1 | 0 to 3 (partner credits: three all finite, one default; with the default, seed 2, the partner stalls with completions infinite, outside the HAS obligation and the Credits target's every-class-finite wording, which binds the `dll`'s upper layer; the partner's store is unbounded and only the DUT's gate is under test) |
| The same, partner and DUT all infinite: the gate never refuses | `credits_infinite` | 1 | 2 derived; the partner's upper layer stalls although it advertises infinite credits, outside the HAS upper-layer obligation: the partner's store is unbounded ([partner page](PARTNER.md#interface)) and only the DUT's gate is under test |
| Credits target, receive side: every DUT class finite, its upper layer taking 30%, stalling 4000, taking 30%, stalling 10000 cycles, the partner sending 1500 TLPs as the UpdateFCs allow: no Receiver overflow, all delivered once and in order | `credit_starve_rx` | 1 | 2 derived; every `FC_*` finite |
| The same, every DUT class infinite, the upper layer never stalling | `credits_infinite_rx` | 1 | 2 derived |
| [UpdateFC refresh](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling), round robin, with ready stalls | `updatefc_refresh` | 1 | 2; two 2048-cycle periods |
| Link-up against the partner, default DUT credits, [init target](../../arch/dll/HAS.md#targets) 200 cycles; cross-check | `link_up` | 1 | 0 to 3 (the four partner advertisements); 6 link-ups each |
| The same, DUT completions finite | `link_up_cpl_finite` | 1 | 0 to 3; `FC_CPLH` 16, `FC_CPLD` 64 |
| The same, DUT all infinite | `link_up_infinite` | 1 | 0 to 3; every `FC_*` 0 |
| Link-up with stalls, gaps and DLLP errors both ways (5% corrupted, 3% dropped, 2% flagged by the partner; 5% lost at it), target 4500 cycles | `link_up_stress` | 1 | 0, 1 |
| 300 random TLPs from the partner within the DUT's credits, error-free: delivered once and in order; [Ack latency](../../arch/dll/HAS.md#targets) within 118 cycles, receive latency within 4; cross-checks | `rx_traffic` | 1 | 0, 1 |
| The same with the partner's TLPs dropped 2%, corrupted 3%, nullified 2%, flagged 2%, its DLLPs corrupted 3%, and 15% of the DUT's Acks and Naks lost (replays, duplicates): no bad TLP delivered | `rx_errors` | 1 | 0 to 2 |
| The same, DUT all infinite | `rx_errors_infinite` | 1 | 0; every `FC_*` 0 |
| The same, wide sweep | `rx_errors_sweep` | 2 | 8 derived |
| The same injection with 300 TLPs beyond the advertisement, the partner sending as the UpdateFCs allow, DUT at the smallest legal credits: bad and duplicate TLPs discarded in the smallest receive buffer (its rewind) | `rx_errors_smallest` | 2 | 2 derived; every `FC_*` at its smallest |
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
The flow-control mutants ([#214](https://github.com/amichai-bd/pcie-factory/issues/214))
were run serially on head `a92239b` (every block attached, the credit
gate in front of the real transmit path), each with `src/` clean.

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
| Package | Gate: half the range left counted as closed (`<` for `<=`, headers) | `fc_tx_gate` | The 128-byte write leaving exactly 128 header credits of headroom is refused. |
| Package | Gate ignores the TLP's own data credits | `fc_init_directed` | Gate 1 for an NP TLP needing 105 data credits with a limit of 40 (rule and arch 0). |
| Package | Gate ignores an infinite data field's flag | `fc_init_directed` | Gate 0 for a P TLP with P data infinite (rule and arch 1). |
| Flow control | A taken `sop` consumes nothing | `fc_tx_gate` | The boundary write refused: the DUT's headroom is not the consumed credits'. |
| Package | Data credits rounded down (`tlp_credit`) | `fc_rx_accounting` | UpdateFC-P carries data 32, CREDITS_ALLOCATED is 33. |
| Package | Messages counted as non-posted (`tlp_credit`) | `fc_init_directed` | Gate 0 for a message header with P credits free (rule and arch 1). |
| Package | Length 0 not read as 1024 DWs | `fc_rx_accounting` | Gate 1 for a P header needing 256 data credits with a limit of 32 (rule and arch 0). |
| Package | Overflow check: exactly filling the free credits overflows | `credit_starve_rx` (L1) | Receiver overflow with a compliant partner. |
| Flow control | No Receiver overflow pulse | `fc_rx_accounting_finite` (L1) | 0 pulses, the reference 16. |
| Flow control | A malformed TLP's credits not added to CREDITS_ALLOCATED | `fc_rx_accounting` | Credits returned for P not sent within 20 cycles; an UpdateFC out of round-robin order. |
| Flow control | A malformed TLP not added to CREDITS_RECEIVED | `fc_rx_accounting` | Stops on `a_free_within_space`. |
| Flow control | Credits of a TLP taken upward not returned | `fc_rx_accounting` | 94 TLPs never accepted: the far end runs out of credits. |
| Flow control | No UpdateFC on returned credits | `fc_rx_accounting` | P credits returned at cycle 35 not sent within 20 cycles. |
| Flow control | The P value sent not recorded (P stays due) | `fc_rx_accounting` | UpdateFC-P offered with no class due. |
| Flow control | An infinite field's internal count sent instead of 0 | `fc_init_directed` | UpdateFC-Cpl `a0020018ef48`, expected `a00000001fd2`. |
| Flow control | Round robin ignores the pointer (P first) | `fc_rx_accounting` | UpdateFC-P sent with Cpl first in round-robin order. |
| Flow control | CREDITS_ALLOCATED cleared to 0 in DL_Inactive | `fc_init_directed` | Entry UpdateFC-P `80000000c91d`, expected the advertisement 8, 32. |
| Flow control | CREDITS_CONSUMED not cleared in DL_Inactive | `fc_tx_gate` | P credits available (137, 3850) after the restart, expected (2, 8). |
| Package | Credits available ignore CREDITS_CONSUMED (headers) | `fc_tx_gate` | P credits available (6, 12), expected (5, 12). |
| Flow control | A stored TLP not added to CREDITS_RECEIVED | `fc_rx_accounting` | Stops on `a_free_within_space`. |
| Package | Infinite-field data reserve 8 credits, not 24 (too small) | `credits_infinite_rx` (L1, seed 55090061) | 27 Receiver overflows with a compliant partner. |

An UpdateFC that writes an infinite field's stored limit is not a mutant
that can be caught: an infinite field shows all ones from its flag,
whatever the limit holds. Neither is a larger infinite-field reserve
(`RX_INF_DATA` 24 to 32, run through levels 0 and 1, none fails): the
reserve fills only if the upper layer stalls with a class infinite, which
`a_rx_never_stall` forbids, so legal stimulus never reaches it. A reserve
that is too small is caught (the last row above).

## Gaps

None. The [targets](../../arch/dll/HAS.md#targets) end to end run in the
[`link` e2e](../link/DV_PLAN.md): Lossless with errors both ways, Rollover
and Credits ([#215](https://github.com/amichai-bd/pcie-factory/issues/215))
and line use, as the stack's throughput
([#216](https://github.com/amichai-bd/pcie-factory/issues/216)). An Ack or
Nak waiting behind a TLP (the 118-cycle bound with both directions loaded)
runs here in `tlp_bidir` and at each `dll`-to-`phy` boundary of the
`link` e2e. The floors below were set while #216 was open, by plan
([firstmate decision on #217](https://github.com/amichai-bd/pcie-factory/issues/217#issuecomment-6241327256));
its entries are on the `link` bench and add nothing to this one.

## Coverage holes

Floors in [`level2.yaml`](../../../../src/dv/dll/regress/level2.yaml),
checked by every `--coverage` run that selects level 2 (the nightly runs
levels 0 to 2): line 98.4, toggle 80.0, functional 100. Merged over levels
0 to 2 ([#217](https://github.com/amichai-bd/pcie-factory/issues/217)):
line 98.95% (472 of 477 line and branch points), toggle 80.53% (13566 of
16846), functional 100% (208 of 208 bins in the 17 covergroups the level file
names). The
line and toggle floors are the merged values less 0.5 points, rounded
down to a tenth ([floor margin](../../../bkm/dv.md#coverage)).

Verilator counts each parameter set of a block apart
([regress SPEC](../../../tools/regress/SPEC.md#coverage)): `pcie_dll` and
`pcie_dll_rx_buf` are built with the credit sets of the entries (default,
completions finite, all infinite, every class finite, smallest), so a
point is missed once per set that does not reach it. The 5 line points
not hit:

| Points | Where | Cause |
| --- | --- | --- |
| 4 | The lane-disable `else` branch of `COMMON_MEM_WRITE_LANES` in `common_sdp_ram`, once per RAM specialization: the replay buffer (`u_tlp_tx.u_rbuf`) and the receive buffer at its three sizes (`u_rx_buf.u_ram`, 7-, 9- and 10-bit addresses) | Unreachable by construction: `LANES` is 1 and the lane enable is tied to 1. |
| 1 | `u_tlp_rx`: the `buf_full_i` branch that sets `noroom` | Unreachable by construction: the receive buffer holds every TLP the advertised credits cover plus the one being collected ([MAS, receive buffer](../../rtl/dll/MAS.md#receive-buffer)), and the overflow check admits nothing more. |

The rewind arm of `u_rx_buf` in the 7-bit specialization (the smallest
credits), a hole until now, is closed by `rx_errors_smallest`, which
discards bad and duplicate TLPs at the smallest credits.

The 3280 toggle points not hit, by cause:

| Cause | Points | Signals |
| --- | ---: | --- |
| Hit in another parameter set | 1028 | The same bit and direction toggles in another credit set's copy of the block: for example, the infinite-field flags and the upper limit bits toggle only in the sets with finite classes, the receive buffer's upper address bits only in its larger sizes. |
| Constant per build | 599 | The credit parameters (`adv_i` in `u_ctrl` and `u_fc`, `rx_base`, `grp_base`) and the InitFC bodies built from them (`initfc_req` in the top, `u_ctrl` and `u_tx`, with their fixed type and reserved bits): a parameter does not change during a run, so its bits rise at most once after reset. |
| Constant by the DLLP format | 447 | Ack and Nak bodies (`acknak_req` in the top, `u_tlp_rx` and `u_tx`): the type bits other than the Ack-to-Nak bit, the reserved byte and the reserved nibble above the sequence number; the UpdateFC bodies (`updfc_req`): the type code's low bits and the reserved bits between the fields; the same bits of the transmit block's chosen body and packet register (`body`, `tx_q.pkt`). |
| Constant by construction: one TLP's credits | 771 | The credit fields of a single TLP (`rx_chk`, `rx_stored`, `rx_malformed`, `rx_taken`, their `u_fc` and `u_tlp_rx` ports, `u_rx_buf` `cur_q` and `taken_o`, and `u_fc` `tx_cost`): the header field is always 1 (bits 7:1 never set, bit 0 never falls once the field holds a TLP), the data field is at most 256 (1024 DWs, bits 11:9 never set), and the `valid` flag that `tlp_credit` sets to 1. |
| Not reached: large data credits | 242 | Bits 8:4 of a delivered TLP's data credits (`rx_taken`, `u_rx_buf` `cur_q` and `taken_o`): a TLP that is delivered carries at most 128 bytes, 8 credits. Bits 7:6 of the credit fields of `rx_chk`, `rx_stored` and `rx_malformed` and their ports, which carry every judged TLP's credits whatever its verdict (64 to 255 credits, a Length of 253 to 1020 DWs), and `hdr.length` bit 9 in `u_tlp_rx`. Only malformed TLPs exceed 8 credits; `rx_directed`'s malformed cases toggle bits 5:4 and 8 (Length 0 is 1024 DWs), and a Length in between would add no check. |
| Unreachable by construction: receive buffer full | 32 | `rx_full`, `u_rx_buf` `full_o` and its fill's top bit (`used`), `u_tlp_rx` `buf_full_i` and `noroom`: the line hole above. |
| Not reached: counter top bits | 137 | The top bits of the NP and Cpl credit counters and recorded limits in `u_fc` (`alloc`, `rcvd`, `sent`, `upd`, `used`, `lim_np`, `lim_cpl`) and bit 7 of the completion header in use: only the P class wraps its counters (`fc_tx_gate`), and the NP data counter grows by one credit per CfgWr, so a wrap would take over 2048 non-posted writes in one link-up. The wrap arithmetic is shared by every class (`fc_charge`, `fc_record`), and the P wrap checks it. |
| Constant by construction: transmit block | 18 | In `u_tlp_tx`: bit 0 of the space-in-use sums (`need_bytes`, `used_bytes`: whole words plus 2 bytes per TLP), bits 1:0 of a TLP's size (`sop_bytes`: whole DWs), bits 7:4 of the sequence-number beat (reserved, 0) and bit 13 of the space in use, which stays below 8192. |
| Constant by construction: RAM lanes | 4 | `wr_lanes_i` of each `common_sdp_ram` (tied to 1). |
| Not reached: hold release | 2 | `u_tlp_tx` `hold_left` never falls: only a second rollover or DL_Inactive clears it after a hold that a retrain ended, and no test rolls over twice in one link-up; its rise and the hold's end are checked in `rollover_directed`. Cost: a second rollover there, about 2500 more cycles (four replay timeouts and the hold) for a flag whose clearing line is already hit at DL_Inactive; not worth an L0 entry, so no owning issue. |

How the toggle points were sorted, so a re-measurement reproduces the
counts: a point is "hit in another parameter set" when the same file,
hierarchy, signal bit and direction is hit in any copy. The rest go by
signal name, in this order: `adv_i`, `rx_base`, `grp_base` and the
`initfc_req` bodies (constant per build); the `acknak_req` and `updfc_req`
bodies and `u_tx` `body` and `tx_q.pkt` (DLLP format); the `.cred` fields
of `rx_chk`, `rx_stored`, `rx_malformed`, `rx_taken`, `chk_o`, `stored_o`,
`malformed_o`, `cur_q`, `taken_o` and `tx_cost`, header bits 7:1, data bits
11:9, header bit 0 falling and the `valid` flags (one TLP's credits), data
bits 8:4 and `hdr.length` bit 9 (large credits); `rx_full`, `full_o`,
`buf_full_i`, `noroom` and the fill's top bits (buffer full); the `u_fc`
`alloc`, `rcvd`, `sent`, `upd`, `used` and `lim_*` fields (counter top
bits); then the named transmit-block bits, `wr_lanes_i` and `hold_left`.
