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
  - with `FC_RELEASE` 1, the **releaser** on `fc_rel_*_i`
    ([credit release](../../arch/dll/HAS.md#credit-release)): it holds each
    delivered posted and non-posted TLP at least 2 cycles, releases per
    class in delivery order at a set probability or not at all in a set
    window, drops what it holds at DL_Inactive, drives random data counts
    while not releasing, and fails if it ever holds more finite credits
    than the DUT advertises; the reference returns a release's finite
    fields, and the arch shadow takes the same releases (its `release`
    refuses credits it did not deliver);
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
  finite. Its negative check is the level-0 entry `rx_never_stall_fatal`:
  default credits and `tlp_rx_ready_i` 0 from DL_Active must stop the
  simulation on the assertion within 4 cycles. The entry's `expect_fatal`
  ([regress spec](../../../tools/regress/SPEC.md#expected-fatal-stops))
  makes it pass only on that stop, so a clean finish or any other fatal
  fails it.
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
  returns per class; with `FC_RELEASE` 1, releases per class, a release's
  data count 0, 1 to 7 or 8, a release in FC_INIT2 or DL_Active, and the
  upper layer holding all of a class's header credits), `dll_tx_tail` (a DLLP finished with one, two or three beats
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
  header), `dll_digest` (a TLP with a digest stored or malformed; 3- and
  4-DW headers), `dll_acknak_tx` (Ack and Nak; Ack by the timer or after a
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
| RTL and architecture model | DLLP bytes and CRC-16 (every DLLP sent); the sequence of control states visited by the DUT and the shadow, each change within 200 cycles of the other; credits available equal to the shadow's. The LCRC of every TLP sent. Replay-buffer room: a TLP the DUT takes is taken by the shadow within 200 cycles, and a `sop` the DUT refuses is refused by the shadow (outside the cycles where the two act on an Ack at different edges, the LCRC-write cycle, or an acknowledged TLP still on the DUT's stream). The counts of replay timeouts, rollovers, DL protocol errors and replays equal the shadow's in `lcrc_kat`, `replay_directed`, `rollover_directed`, `rbuf_full`, `tlp_partner`, `tlp_rollover` and `replay_timeout_slow`. In `tlp_partner_errors` and `tlp_bidir` they are logged, not compared: the shadow transmits at its own pace (no `phy` stall or upper-stream gap, its own Acks), so with errors a Nak can land inside a replay of one model and after the replay of the other, and with both directions loaded its TLPs lag the DUT's, so an Ack can name a TLP it has not sent yet. |
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
| [Parameter ranges](../../arch/dll/HAS.md#parameters): the credits, `FC_RELEASE` and `REPLAY_TIMEOUT` | `param_limits` | 0 | 1; 11 illegal and 7 legal lints, no simulation |
| [CRC-16](../../arch/dll/DLLP.md#crc-16) known answers, transmit and receive; [receiving DLLPs](../../arch/dll/DLLP.md#receiving-dllps): bad CRC, flagged, wrong length, DL_Inactive | `crc16_kat` | 0 | 1; 5 known answers and every single-bit error of them, 150 random DLLPs and one-bit errors, gaps |
| [Control states](../../arch/dll/HAS.md#data-link-control-states) and [initialisation](../../arch/dll/FLOW_CONTROL.md#initialisation) by hand: groups and 128-cycle resend, recording, ignored DLLPs, FC_INIT2 ends, entry UpdateFC, [credits available](../../arch/dll/HAS.md#credits-available), UpdateFC limits, link down from each state | `fc_init_directed` | 0 | 1 |
| [A started DLLP finished across a link-down](../../arch/dll/HAS.md#reset-values), two and one beats left, and all three when the `sop` is taken at the edge that samples `link_up_i` 0; an offered one withdrawn; clean restart | `link_down_mid_dllp` | 0 | 1 |
| [Receiving TLPs](../../arch/dll/RETRY.md#receiving-tlps) by hand: discarded in DL_Inactive and FC_INIT1; a bad TLP's Nak held in FC_INIT2; a good TLP ends FC_INIT2; every class and size, 3- and 4-DW headers, gaps and back to back; duplicates with an Ack at once (D = 2048 too); ahead (D = 1, 2047) with one Nak per NAK_SCHEDULED; bad LCRC, flagged, too short, nullified, EDB without the inverse; each malformed kind, packets beyond the beat counter; [digests](../../arch/dll/HAS.md#receive) (TD 1) delivered with the TLP, 3- and 4-DW headers, the largest TLP of 37 DWs, and malformed without the digest, with a 132-byte payload, or a DW too many with TD 0; link down while a TLP arrives and while one is delivered. [Ack timer](../../arch/dll/RETRY.md#ack-and-nak-scheduling): 35 cycles on an idle stream ([MAS, timing](../../rtl/dll/MAS.md#timing)); Nak and duplicate Ack within 7; [receive latency](../../arch/dll/HAS.md#targets) within 4 | `rx_directed` | 0 | 1 |
| [The upper layer never stalls](../../arch/dll/HAS.md#receive) when a class is infinite: `tlp_rx_ready_i` 0 in DL_Active with the default credits stops the run on `a_rx_never_stall` (negative, `expect_fatal`) | `rx_never_stall_fatal` | 0 | 1; passes only on that fatal stop |
| [Transmit accounting](../../arch/dll/FLOW_CONTROL.md#transmit-accounting): the gate for every class and field, at exactly half the range and one past it, after limits beyond half the range, with infinite fields; CREDITS_CONSUMED wrapping; credits available; cleared by link-down mid-TLP | `fc_tx_gate` | 1 | 1; hand-driven far end that acknowledges every TLP |
| [Receive accounting](../../arch/dll/FLOW_CONTROL.md#receive-accounting) on real TLPs from a hand-driven far end that replays: stored, malformed (credits returned at once, none under 4 bytes), returns at the delivered `eop`; [UpdateFC](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling) on returns, in FC_INIT2 (entry value) and DL_Active, its value, cause, round robin and latency; refreshes; link down; default credits | `fc_rx_accounting` | 0 | 1 |
| The same, every class finite, the upper layer slow and stalling: the [overflow check](../../arch/dll/FLOW_CONTROL.md#receive-buffer), Receiver overflow, no Ack or Nak, the TLPs behind arriving ahead | `fc_rx_accounting_finite` / `fc_rx_accounting_finite_sweep` | 1 / 2 | derived seed 0 / its derived seed 1 (1892473378) pinned; coverage on both |
| The same, every class infinite (the reserve; UpdateFC all 0) | `fc_rx_accounting_infinite` | 1 | seed 844183907; retained assertions unchanged |
| [Credits target](../../arch/dll/HAS.md#targets), transmit side: starvation and recovery with the partner's upper layer taking 30%, stalling 4000 cycles, taking 30%, stalling 10000 cycles, then taking all; no overflow at the partner's receive-side check; arch model against a fresh partner | `credit_starve` / `credit_starve_sweep` | 1 / 2 | 0 / 1 to 3 (partner credits: three all finite, one default; with the default, seed 2, the partner stalls with completions infinite, outside the HAS obligation and the Credits target's every-class-finite wording, which binds the `dll`'s upper layer; the partner's store is unbounded and only the DUT's gate is under test); coverage on seeds 0 to 2 |
| The same, partner and DUT all infinite: the gate never refuses | `credits_infinite` | 1 | seed 1044722899; partner stalls despite infinite advertisements, outside the HAS upper-layer obligation; partner store unbounded, only DUT gate under test ([partner](PARTNER.md#interface)) |
| Credits target, receive side: every DUT class finite, its upper layer taking 30%, stalling 4000, taking 30%, stalling 10000 cycles, the partner sending 1500 TLPs as the UpdateFCs allow: no Receiver overflow, all delivered once and in order | `credit_starve_rx` / `credit_starve_rx_sweep` | 1 / 2 | derived seed 0 / its derived seed 1 (1229081417) pinned; every `FC_*` finite; coverage on both |
| The same, every DUT class infinite, the upper layer never stalling | `credits_infinite_rx` / `credits_infinite_rx_sweep` | 1 / 2 | its derived seed 1 (55090061, the data-reserve mutant's catch) / its derived seed 0 (1951378203), both pinned; coverage on both |
| [Credit release](../../arch/dll/HAS.md#credit-release), [Credits target](../../arch/dll/HAS.md#targets) with `FC_RELEASE` 1: default credits, the upper layer taking every beat but releasing in 30% of cycles, none for 4000, 30%, none for 10000 cycles, then all; the partner sending 600 TLPs as the UpdateFCs allow; then, every release held, a directed burst of header-only TLPs (NP reads, posted messages, one more) that only the header limits stop, since the random phases fill a class only when a header limit stops the partner's in-order queue ([#429](https://github.com/amichai-bd/pcie-factory/issues/429)): no Receiver overflow, never more held than advertised, every finite P and NP header credit held with the burst's last TLP starved until the releases resume, every TLP once and in order, every release's UpdateFC within 20 cycles; reference and arch shadow | `credit_release` / `credit_release_sweep` | 1 / 2 | 0 / 1 and 119646981 (its random phases hold at most 7 of 8 P and 4 of 8 NP headers, [#429](https://github.com/amichai-bd/pcie-factory/issues/429)); `FC_RELEASE` 1; coverage on both: seed 1 alone reaches 22 toggle points that no other run reaches, the posted data counters' bits 10:9 in the `FC_RELEASE` 1 credit block (allocated, sent and UpdateFC values passing 1024 credits) ([#354](https://github.com/amichai-bd/pcie-factory/issues/354)) |
| The same at the smallest legal credits, every class finite: the stream stalls on the same schedule as the releases | `credit_release_smallest` | 2 | 2 derived |
| The transmit accounting of `fc_tx_gate` in the `FC_RELEASE` 1 build, whose credit block is its own specialization, with the releaser running ([#354](https://github.com/amichai-bd/pcie-factory/issues/354)) | `fc_tx_gate_release` | 2 | 0; `FC_RELEASE` 1 |
| [Receive accounting](../../arch/dll/FLOW_CONTROL.md#credit-release) with releases: P and NP returns on release, completions and malformed TLPs at once, an infinite NP data field freed at delivery (40 configuration writes past the reserve), releases in FC_INIT2 (the `phy` stand-in stalled so the InitFC2 group cannot complete; at least 2 per link-up) in the entry UpdateFC, link down dropping what is held | `fc_rx_accounting_release` | 1 | 1 derived; NP data infinite, `FC_RELEASE` 1 |
| [UpdateFC refresh](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling), round robin, with ready stalls | `updatefc_refresh` | 1 | seed 711896023; two 2048-cycle periods |
| Link-up against the partner, default DUT credits, [init target](../../arch/dll/HAS.md#targets) 200 cycles; cross-check | `link_up` / `link_up_sweep` | 1 / 2 | 0 / 1, 2 and 3 (the seed picks one of the four partner advertisements; level 1 runs advertisement 0 here, 1 in `link_up_cpl_finite` and 2 in `link_up_infinite`); 6 link-ups each |
| The same, DUT completions finite | `link_up_cpl_finite` / `link_up_cpl_finite_sweep` | 1 / 2 | 1 / 0, 2 and 3; `FC_CPLH` 16, `FC_CPLD` 64; coverage on the same seeds as before |
| The same, DUT all infinite | `link_up_infinite` / `link_up_infinite_sweep` | 1 / 2 | 2 / 0, 1 and 3; every `FC_*` 0; coverage on the same seeds as before |
| Link-up with stalls, gaps and DLLP errors both ways (5% corrupted, 3% dropped, 2% flagged by the partner; 5% lost at it), target 4500 cycles | `link_up_stress` / `link_up_stress_sweep` | 1 / 2 | 0 / 1 |
| 300 random TLPs from the partner within the DUT's credits, a quarter with a digest (in every partner entry of `test_dll_rx`), error-free: delivered once and in order, digests included; [Ack latency](../../arch/dll/HAS.md#targets) within 118 cycles, receive latency within 4; cross-checks | `rx_traffic` | 1 | seed 0; retained assertions unchanged |
| The same with the partner's TLPs dropped 2%, corrupted 3%, nullified 2%, flagged 2%, its DLLPs corrupted 3%, and 15% of the DUT's Acks and Naks lost (replays, duplicates): no bad TLP delivered | `rx_errors` | 1 | 0; seeds 1 and 2 in `rx_errors_sweep` |
| The same, DUT all infinite | `rx_errors_infinite` | 1 | 0; every `FC_*` 0 |
| The same, wide sweep | `rx_errors_sweep` | 2 | its 8 derived seeds pinned, then 1 and 2 from `rx_errors`; coverage on the first 6 (`coverage_seeds`) |
| The same with `FC_RELEASE` 1: bad, duplicate and nullified TLPs never delivered, so never released; the delivered ones released late, reference and arch shadow ([#354](https://github.com/amichai-bd/pcie-factory/issues/354)) | `rx_errors_release` | 2 | 0, 1; `FC_RELEASE` 1; coverage on both (`coverage_seeds`): seed 1 alone reaches 8 toggle points that no other run reaches, `u_tlp_rx` Length bit 9 (a corrupted TLP whose header reads a Length of 512 or more, judged by its LCRC) |
| The same injection with 300 TLPs beyond the advertisement, the partner sending as the UpdateFCs allow, DUT at the smallest legal credits: bad and duplicate TLPs discarded in the smallest receive buffer (its rewind) | `rx_errors_smallest` | 2 | 2 derived; every `FC_*` at its smallest |
| [Receive buffer](../../arch/dll/FLOW_CONTROL.md#receive-buffer) sizing: every class finite, the upper layer stalls while the partner sends TLPs that use every advertised credit (a quarter with a digest, which takes buffer space but no credit), then takes 30% of beats | `rx_stall` / `rx_stall_sweep` | 1 / 2 | 0 / 1; `FC_CPLH` 16, `FC_CPLD` 64; coverage on the same seeds as before |
| The same at the smallest legal credits | `rx_stall_smallest` | 2 | 4 derived |
| Receive buffer sizing for [digests](../../arch/dll/HAS.md#receive) ([MAS sizing](../../rtl/dll/MAS.md#receive-buffer), 5 DWs per header credit): the upper layer stalls while the partner sends 62 TLPs, every one with a digest, 4-DW headers (3-DW completions) and full data credits, 533 DWs; at these credits (118 in all) 4 DWs per header credit would round to 512 words, 5 give 1024 | `rx_stall_digest` | 2 | 1 derived; `FC_PH` 60, `FC_PD` 46, `FC_NPH` 1, `FC_NPD` 2, `FC_CPLH` 1, `FC_CPLD` 8 |
| NEXT_RCV_SEQ wrapping past 4095: 4200 short TLPs with light errors (Naks and duplicates around the wrap) | `rx_seq_wrap` | 2 | 1 derived |
| The same, wide sweep | `link_up_stress_sweep` | 2 | its 8 derived seeds pinned, then 1 from `link_up_stress` |
| The same, DUT at the smallest legal credits | `link_up_stress_smallest` | 2 | 4 derived; coverage on the first 2 (`coverage_seeds`) |
| [Sequence numbers](../../arch/dll/RETRY.md#transmitter-state) from 0 and the [LCRC](../../arch/dll/RETRY.md#lcrc): the retry page's example (`E1 F1 E7 E2`), 86 random TLPs of every shape with upper gaps and `phy` stalls; [TX latency target](../../arch/dll/HAS.md#targets) | `lcrc_kat` | 0 | 1; latency 3 cycles, target 4 |
| [Received Ack and Nak](../../arch/dll/RETRY.md#received-ack-and-nak) and the [replay](../../arch/dll/RETRY.md#replay) by hand: Ack, Nak with and without progress, out of range, everything acknowledged; [replay timer](../../arch/dll/RETRY.md#timers) at 356 cycles, held outside L0, restarted by progress; Ack during a replay, Nak during a replay | `replay_directed` | 0 | 1 |
| [REPLAY_NUM](../../arch/dll/RETRY.md#replay_num) rollover, `retrain_req_o`, the hold with and without a retrain, progress setting REPLAY_NUM to 0 | `rollover_directed` | 0 | 1; hold 1025 cycles without, 302 with a 300-cycle Recovery |
| [Replay timer](../../arch/dll/RETRY.md#timers) never early: the LTSSM leaves L0 for 5 cycles from the cycle the timer would reach 356; timeout 361 cycles after the `eop` | `timer_edge` | 0 | 1 |
| [Rollover edge](../../arch/dll/RETRY.md#replay_num): a TLP `sop` waiting for the `phy` in the rollover cycle, the `phy` ready in exactly that cycle: not offered, resent after the oldest by the replay | `rollover_edge` | 0 | 1 |
| [Replay-buffer](../../arch/dll/RETRY.md#replay-buffer) back-pressure on the upper stream: 6 maximum TLPs, the 1024-byte boundary (1022 taken, 1026 refused), room freed by an Ack | `rbuf_full` | 0 | 1 |
| [A started TLP finished across a link-down](../../arch/dll/HAS.md#reset-values), the TLP being taken abandoned, sequence numbers from 0 after the relink; an offered TLP withdrawn | `link_down_mid_tlp` | 0 | 1 |
| The same at `REPLAY_TIMEOUT` 1400: the abort and the flush of its transmit block, the wider replay timer included ([#401](https://github.com/amichai-bd/pcie-factory/issues/401)) | `link_down_mid_tlp_slow` | 2 | 1 derived; `REPLAY_TIMEOUT` 1400 |
| TLPs to the partner, error-free, stalls and gaps: delivered once and in order, no replay | `tlp_partner` | 1 | seed 0; retained assertions unchanged |
| [Slow lower layers](../../arch/dll/RETRY.md#slow-lower-layers) at `REPLAY_TIMEOUT` 1400: the stand-in's line delay at 300 cycles each way, so a maximum-payload TLP (4-DW header, 128 bytes) is acknowledged 634 cycles after its `eop`, with no timeout and no replay; then every partner DLLP dropped: the timeout exactly 1400 cycles after the second TLP's `eop`, that TLP replayed once, both delivered once; replay counts equal the shadow's | `replay_timeout_slow` | 1 | 1; `REPLAY_TIMEOUT` 1400 |
| TLPs to the partner with its fault hooks: Nak'd, lost, lost and damaged Acks, forced Naks, Ack delays past the timeout (each window one 200-cycle period at the default timeout, one more per 200 cycles a larger `REPLAY_TIMEOUT` adds); then an Ack-loss window, every partner DLLP dropped while one more TLP goes out, in which the timer must expire (a Nak in a delay window restarts the timer, so the windows alone left some seeds without a timeout: derived seed 930231584, and 1728083710 at `REPLAY_TIMEOUT` 1400, [#401](https://github.com/amichai-bd/pcie-factory/issues/401)); delivered once and in order | `tlp_partner_errors` | 1 | 0; seeds 1 and 2 in `tlp_partner_errors_sweep` |
| The same, wide sweep | `tlp_partner_errors_sweep` | 2 | its 8 derived seeds pinned, then 1 and 2 from `tlp_partner_errors` |
| The same at `REPLAY_TIMEOUT` 1400, whose `pcie_dll` and `pcie_dll_tlp_tx` are their own specializations: Naks, timeouts, an Ack ending or shortening a running replay, a replay held while one runs ([#401](https://github.com/amichai-bd/pcie-factory/issues/401)) | `tlp_partner_errors_slow` | 2 | 0; `REPLAY_TIMEOUT` 1400 |
| [Rollover target](../../arch/dll/HAS.md#targets): TLPs lost from a set cycle until the retrain; architecture model against the partner in the same scenario | `tlp_rollover` | 1 | seed 0; retained assertions unchanged |
| The same at `REPLAY_TIMEOUT` 1400: 4 timeouts of 1400 cycles, the rollover and its hold; the shadow and the architecture model at the same timeout ([#401](https://github.com/amichai-bd/pcie-factory/issues/401)) | `tlp_rollover_slow` | 2 | 0; `REPLAY_TIMEOUT` 1400 |
| [Line use and replay-buffer sizing targets](../../arch/dll/HAS.md#targets) (`mwr_max`, `ack_spec_max`): 500 128-byte writes, partner Acks at 32 and 118 cycles, at least 99% | `mwr_max`, `ack_spec_max` | 2 | 1 each; 99.30% both (architecture model 99.20%) |
| Sequence numbers across the 4095-to-0 wrap with Naks, lost TLPs and lost Acks around it | `seq_wrap` | 2 | 2 derived |
| Both directions loaded: 300 TLPs each way, the DUT's Acks waiting behind its own TLPs; [Ack latency target](../../arch/dll/HAS.md#targets) 118 cycles | `tlp_bidir` / `tlp_bidir_sweep` | 1 / 2 | 0 / 1; worst Ack 107 and 108 cycles; coverage on both |
| The same at `REPLAY_TIMEOUT` 1400: the receive side and credits of its `pcie_dll` specialization ([#401](https://github.com/amichai-bd/pcie-factory/issues/401)) | `tlp_bidir_slow` | 2 | 0; `REPLAY_TIMEOUT` 1400 |

**Level placement** ([#272](https://github.com/amichai-bd/pcie-factory/issues/272)):
`mwr_max` and `ack_spec_max` run in level 2. They measure the line-use and
replay-buffer sizing targets, performance numbers like the `link`
throughput entries, at about 10 s of CPU each; no mutant in the table
below is first caught by them, and the transmit and replay paths they load
stay under `ci-gate` through `lcrc_kat`, `rbuf_full`, `tlp_partner` and
`tlp_bidir`.

The other directed entries stay in level 0
([#325](https://github.com/amichai-bd/pcie-factory/issues/325)): each takes
about 1 to 5 s of CPU, and they are the quickest catch of a broken block
(most mutants in the table below are first caught there), so a change that
breaks the CRC, the control states, transmit, replay or receive fails in the
first minutes of `ci-gate`, and a factory-only change, which runs level 0 of
every IP, still exercises each block.

`fc_tx_gate` runs in level 1, not 0
([#325](https://github.com/amichai-bd/pcie-factory/issues/325)): level 0
checks that the build is not broken, and at about 13 s of CPU this
functional test was the heaviest level-0 entry of every IP (the next took
under 6 s). The mutants below that name it were measured while it ran in
level 0; it is the same test and still catches them, but an earlier level-1
entry may now be the first to fail.

`fc_tx_gate_release` and `rx_errors_release` run in level 2
([#354](https://github.com/amichai-bd/pcie-factory/issues/354)): they are
coverage-driven, running `fc_tx_gate` and `rx_errors` on the `FC_RELEASE`
1 build for the toggle points of its own `pcie_dll` and `pcie_dll_fc`
specializations (840 and 36 points no other entry reaches). No mutant in
the table below is first caught by them: the gate and release mutants
they catch (a payload above 128 bytes accepted, the non-posted release
charged to P, the half-range gate) are first caught by `rx_directed`,
`credit_release` and `fc_tx_gate`, which keep the release and gate paths
under `ci-gate`. They take about 12 s and 2 x 4 s of CPU.

`tlp_partner_errors_slow`, `tlp_rollover_slow`, `tlp_bidir_slow` and
`link_down_mid_tlp_slow` run in level 2 ([#401](https://github.com/amichai-bd/pcie-factory/issues/401)), for the same reason:
they run existing tests on the `REPLAY_TIMEOUT` 1400 build, whose
`pcie_dll` and `pcie_dll_tlp_tx` are their own specializations and which
`replay_timeout_slow` alone barely reaches (14 line and 1509 toggle points
missed). Of the 14 line and 912 toggle points they gain, 8 and 490 are
reached by one of them alone (`tlp_partner_errors_slow` 7 and 55,
`link_down_mid_tlp_slow` 1 and 134, `tlp_bidir_slow` 0 and 297,
`tlp_rollover_slow` 0 and 4) and the rest by two or more.
`tlp_rollover_slow` stays for its check: it alone compares the replay
counts with the shadow at 1400, and it alone of them catches a timeout
fixed at 356. `replay_timeout_slow`
(level 1) keeps the parameter under `ci-gate`. Each mutant they catch
([below](#replay-timeout-build-mutants)) is also caught under `ci-gate`:
by the same test on the default build, or, for a timeout fixed at 356, by
`replay_timeout_slow`. They take about 5, 3, 4 and 1 s of CPU.

**Seed-placement history (#389; plain repeats trimmed below)** ([#389](https://github.com/amichai-bd/pcie-factory/issues/389)):
the full level 1 of every IP, which `ci-gate` runs for a change to a
shared package, was over its 1200 s CPU budget (1269.6 s on the `ci-gate`
runner at `00990ed`), and `dll` was its largest share (310.6 s). The
seeded entries `fc_rx_accounting_finite`, `fc_rx_accounting_infinite`,
`credit_starve`, `credits_infinite`, `tlp_partner`, `tlp_partner_errors`,
`tlp_rollover`, `rx_traffic`, `rx_errors`, `credit_starve_rx`,
`credits_infinite_rx`, `credit_release` and `tlp_bidir` now run one seed
in level 1; their further seeds run in level 2, unchanged and pinned, in
the entry's `_sweep` (`tlp_partner_errors_sweep` and `rx_errors_sweep`
take seeds 1 and 2 after their own eight). Each seed keeps its coverage
instrumentation, so the merged level 0 to 2 runs are the same runs and
the floors see the same coverage. Level 1 keeps the first seed, which is
the seed every mutant below was measured at, except `credits_infinite_rx`,
which keeps its derived seed 1 (55090061), the catch of the data-reserve
mutant; so every mutant below is still first caught at level 0 or 1, and
every plan row keeps a level 1 run. The seeds moved are breadth (other
partner advertisements, impairment draws and stream orders), which the
nightly runs with its rotated extra seeds. Level 1 of `dll` went from 52 to 35 runs and from
142.5 s to 83.5 s of CPU on a development host. A second step (#389, the
rc_seq L1 added on `main`) also moves `updatefc_refresh`, `rx_stall` and
`link_up_stress` to one seed (seed 1 of `link_up_stress` appended to
`link_up_stress_sweep`), and the three `link_up` entries to one partner
advertisement each, 0, 1 and 2, so level 1 still runs three of the four;
each entry's other three seeds run in its `_sweep`. Level 1 of `dll` is
now 23 runs.

**Plain repeat removal (#461).** Six plain L2 repeats are removed; each
retains the same configuration and assertions in L1:

| Retained entry | Seed | Recorded mutant retained |
| --- | --- | --- |
| `fc_rx_accounting_infinite` | 844183907 | No first-catch row names it; finite overflow mutants retain `fc_rx_accounting_finite` and `credit_starve_rx` |
| `credits_infinite` | 1044722899 | No first-catch row names it; infinite-data reserve mutant retains `credits_infinite_rx` seed 55090061 |
| `tlp_partner` | 0 | No first-catch row names it; LCRC/replay directed catches remain |
| `tlp_rollover` | 0 | No first-catch row names it; rollover mutants retain `rollover_directed` / `rollover_edge` |
| `rx_traffic` | 0 | Every accepted TLP restarts Ack timer: Ack 154 cycles after eop, bound 118 |
| `updatefc_refresh` | 711896023 | Refresh never falls due: only one UpdateFC per class in two periods |

The #389 first-seed mutation evidence above remains valid at these seeds;
no checker changes. Removed seeds collect no coverage and add no recorded
sole fault catch. Extra stream-order/random-skew breadth and these rows'
nightly rotations are trimmed. Every covered sweep remains. In particular,
`link_up_sweep` stays: seed 3 selects the smallest partner advertisement
against default DUT credits, a distinct configuration cross beyond L1 seed 0.

**Coverage runs** ([#327](https://github.com/amichai-bd/pcie-factory/issues/327)):
`--coverage` instruments each entry's first seed only (`coverage_seeds`,
[regress SPEC](../../../tools/regress/SPEC.md#coverage)); every seed still
runs. The entries whose later seeds reach points or bins their first does
not, on the merged level 0 to 2 coverage of `005836f` (121 points and two
`dll_fc_tx` gate bins in all), keep coverage on the seeds the plan rows
name, so the merged coverage is unchanged point for point. No seed was cut.
`credit_release` and `rx_errors_release` instrument both seeds for the
points their second seed alone reaches (their plan rows,
[#354](https://github.com/amichai-bd/pcie-factory/issues/354)).

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
| Package | A payload above 128 bytes not malformed | `rx_directed` | Malformed TLP pulses 7, expected 8. Rerun on #354's head: 24 TLPs delivered, 23 expected (seed 1739615621). |
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
| Package | Gate: half the range left counted as closed (`<` for `<=`, headers) | `fc_tx_gate` (L1) | The 128-byte write leaving exactly 128 header credits of headroom is refused. Also caught by `fc_tx_gate_release` (L2, seed 0, #354): the boundary write refused, taken False, expected True. |
| Package | Gate ignores the TLP's own data credits | `fc_init_directed` | Gate 1 for an NP TLP needing 105 data credits with a limit of 40 (rule and arch 0). |
| Package | Gate ignores an infinite data field's flag | `fc_init_directed` | Gate 0 for a P TLP with P data infinite (rule and arch 1). |
| Flow control | A taken `sop` consumes nothing | `fc_tx_gate` (L1) | The boundary write refused: the DUT's headroom is not the consumed credits'. |
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
| Flow control | CREDITS_CONSUMED not cleared in DL_Inactive | `fc_tx_gate` (L1) | P credits available (137, 3850) after the restart, expected (2, 8). |
| Package | Credits available ignore CREDITS_CONSUMED (headers) | `fc_tx_gate` (L1) | P credits available (6, 12), expected (5, 12). |
| Flow control | A stored TLP not added to CREDITS_RECEIVED | `fc_rx_accounting` | Stops on `a_free_within_space`. |
| Package | Infinite-field data reserve 8 credits, not 24 (too small) | `credits_infinite_rx` (L1, seed 55090061) | 27 Receiver overflows with a compliant partner. |

An UpdateFC that writes an infinite field's stored limit is not a mutant
that can be caught: an infinite field shows all ones from its flag,
whatever the limit holds. Neither is a larger infinite-field reserve
(`RX_INF_DATA` 24 to 32, run through levels 0 and 1, none fails): the
reserve fills only if the upper layer stalls with a class infinite, which
`a_rx_never_stall` forbids, so legal stimulus never reaches it. A reserve
that is too small is caught (the last row above).

### Credit release mutants

The mutants of [#303](https://github.com/amichai-bd/pcie-factory/issues/303)'s
code (`u_fc` release split and the top's release events; the `link`
pass-through in the [`link` plan](../link/DV_PLAN.md#plan)), run serially
on the final RTL: levels 0 and 1, one entry at a time at its first seed,
the first failing entry named. Every level-0 entry passes each of them.

| Block | Mutant | First failing entry | Result |
| --- | --- | --- | --- |
| Flow control | P and NP finite credits also returned at delivery (`taken_rel` 0) | `credit_release` (L1) | Stops on `a_free_within_space`: credits returned twice. |
| Flow control | A release returns nothing (both release charges disabled) | `credit_release` (L1) | NP credits returned at cycle 112 not sent within 20 cycles; the drain never ends. |
| Flow control | The NP release charged to P | `credit_release` (L1) | Stops on `a_free_within_space`. Also caught by `rx_errors_release` (L2, seed 0, #354): `a_free_within_space` at 1600000. |
| Flow control | A release also returns an infinite field's credits (`fc_keep` bypassed) | `fc_rx_accounting_release` (L1) | Stops on `a_free_within_space`. |
| Flow control | A delivered P or NP TLP frees no reserve (infinite fields held back too) | `fc_rx_accounting_release` (L1) | 28 TLPs never accepted: the NP data reserve fills (the 40-write burst). |
| Flow control | Completions also wait for a release (`taken_rel` ignores the class) | `fc_rx_accounting_release` (L1) | 61 TLPs never accepted: completion credits never return. |
| Top | A release's data count dropped (data 0) | `credit_release` (L1) | UpdateFC-P carries data 32, expected 36 (cycle 190). |
| Top | A non-posted release's data count dropped (data 0) | `credit_release` (L1) | UpdateFC-NP carries data 8, expected 9 (cycle 113). |

`a_rel_data_max` is live: a one-off run of `credit_release` with the
releaser driving a data count of 9 stops on it at the first release (not yet an `expect_fatal` entry like
`rx_never_stall_fatal`).
`a_rel_tied` is shown live by the `link` mutant that drops `FC_RELEASE`.

### Digest mutants

The mutants of [#308](https://github.com/amichai-bd/pcie-factory/issues/308)'s
code (a received TLP digest), run serially, one entry at a time: the
first three through level 0 at its seeds, the sizing one on its targeted
level-2 entry.

| Block | Mutant | First failing entry | Result |
| --- | --- | --- | --- |
| TLP receive | TD not counted in the malformed rule (`tlp_malformed` given 0) | `rx_directed` | 20 TLPs delivered, 23 expected: the four with a digest dropped as malformed, the one without its digest stored. |
| Package | 36 DWs per packet (`RX_MAX_TLP_DW`), the digest of a 37-DW TLP not written | `rx_directed` | 22 TLPs delivered, 23 expected. |
| TLP receive | TD read from byte 2 bit 6 (EP) | `rx_directed` | 20 TLPs delivered, 23 expected. |
| Top | `RX_BUF_DW` at 4 DWs per header credit (512 words, not 1024) | `rx_stall_digest` (L2) | Bad TLP pulses 8, expected 0: TLPs that found the buffer full dropped, those behind them ahead. |

The sizing differs only where rounding up to a power of two separates the
two sums: every other parameter set in the regression lists gets the same
size either way (512 words by default, 128 at the smallest credits), so
only `rx_stall_digest` can catch it.

### Replay-timeout mutants

The mutants of [#384](https://github.com/amichai-bd/pcie-factory/issues/384)'s
code (the `REPLAY_TIMEOUT` parameter), run serially on the final RTL, one
entry at a time at its first seed: level 0, then `replay_timeout_slow`.
Each is caught; the `link` pass-through mutant is in the
[`link` DV plan](../link/DV_PLAN.md#seeds-and-coverage-runs), with the release mutants.

| Block | Mutant | First failing entry | Result |
| --- | --- | --- | --- |
| TLP transmit | Timeout compare one early (`RT_LAST` = `REPLAY_TIMEOUT` − 2) | `replay_directed` | Timeout 355 cycles after the `eop`, expected 356. Alone, `replay_timeout_slow` fails too: 1399, expected 1400. |
| TLP transmit | Timeout compare at the fixed 356 (`RT_LAST` = 355) | `replay_timeout_slow` (L1) | A spurious replay: 1 timeout and 1 replay with a 634-cycle round trip. Every level-0 entry passes. |
| TLP transmit | Timer counting by 2 | `replay_directed` | No timeout within 600 cycles. |
| Top | 356 passed to `u_tlp_tx`, not `REPLAY_TIMEOUT` | `replay_timeout_slow` (L1) | The same spurious replay. Every level-0 entry passes. |
| Top | Range check accepting 355 | `param_limits` | `REPLAY_TIMEOUT` 355 lints clean, expected the elaboration failure. |

### Replay-timeout build mutants

The `REPLAY_TIMEOUT` 1400 entries of [#401](https://github.com/amichai-bd/pcie-factory/issues/401), each run alone at its seed
against one RTL mutant, serially, the tree restored after each.

| Block | Mutant | Entry | Result | Also caught by |
| --- | --- | --- | --- | --- |
| TLP transmit | Timeout compare at the fixed 356 (`RT_LAST` = 355) | `tlp_rollover_slow` | Replay counts differ from the shadow's at 1400: timeouts 4 against 1, replays 4 against 1 (cycle 9102). `tlp_partner_errors_slow` passes: it logs those counts, not compares them. | `replay_timeout_slow` (L1) |
| TLP transmit | Flush keeps the retry state (`RETRY_RESET` not loaded) | `link_down_mid_tlp_slow` | First TLP after the relink carries sequence 1, expected 0 (cycle 306). | `link_down_mid_tlp` (L0) |
| TLP transmit | A running replay does not skip what an Ack removed | `tlp_partner_errors_slow` | Partner violation at cycle 7245: TLP 78 taken, already acknowledged (ACKD_SEQ 86). | `tlp_partner_errors` (L1), cycle 2555 |
| Transmit block | Ack and Nak below TLPs in the transmit priority | `tlp_bidir_slow` | An Ack started 14522 cycles after its TLP's `eop`, bound 118. | `tlp_bidir` (L1) |
| TLP transmit | The timer never expires at 1400 (the compare also needs a count whose bits 2:0 are not all 1, as 1399's are) | `tlp_partner_errors_slow` | The final Ack-loss window: no replay timeout within 1800 cycles (cycle 38705). | `replay_timeout_slow` (L1): the timeout not reached within 1410 cycles |

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

Line and toggle floors use the
[by-source union](../../../tools/regress/SPEC.md#coverage): each source point
counts once and is covered if any instance covers it. Functional coverage
is unchanged. The source remeasurement for
[#228](https://github.com/amichai-bd/pcie-factory/issues/228) uses
[nightly 39384846565](https://github.com/amichai-bd/pcie-factory/actions/runs/39384846565).
Separately measured low-level runs supply their own floors.

| Selected levels | Source line | Source toggle | Line / toggle floors |
| --- | --- | --- | --- |
| L0+L1+L2 | 396/398 (99.50%) | 8810/10252 (85.93%) | 98.9 / 85.4 |

### Per-instance diagnostic baseline

The counts and prior floor values below describe the original per-instance
view at the revisions cited. Current code floors use the source table above.
A point hit in another parameter set or instance is covered in the source
union; the other reviewed causes remain diagnostic explanations, without
adding a waiver.

Floors in [`level2.yaml`](../../../../src/dv/dll/regress/level2.yaml),
checked by every `--coverage` run that selects level 2 (the nightly runs
levels 0 to 2): line 98.5, toggle 77.0, functional 100. Merged over levels
0 to 2 ([#401](https://github.com/amichai-bd/pcie-factory/issues/401)): line 99.07% (637 of 643 line and branch points), toggle
77.57% (19987 of 25768), functional 100% (221 of 221 bins in the 18
covergroups the level file names). Both code floors are the merged value
less 0.5 points, rounded down to a tenth
([floor margin](../../../bkm/dv.md#coverage)), raised by [#401](https://github.com/amichai-bd/pcie-factory/issues/401) from
98.4 and 76.4.

**The `REPLAY_TIMEOUT` copies** ([#401](https://github.com/amichai-bd/pcie-factory/issues/401)). The replay-timeout parameter of
[#384](https://github.com/amichai-bd/pcie-factory/issues/384) gave the
entries at `REPLAY_TIMEOUT` 1400 their own `pcie_dll` and
`pcie_dll_tlp_tx` specializations (Verilator's `__R578`): 91 line and 3288
toggle points (1460 in the top, 1828 in the transmit block, whose replay
timer is 11 bits there), plus 3 line points of the new timer statements in
the default transmit block. With `replay_timeout_slow` the only entry on
that build, two TLPs and one replay, the merged value fell to 96.89% line
(623 of 643) and 74.03% toggle (19075 of 25768), below the floors of 98.4
and 76.4: 14 line points (Ack and replay interplay, the rollover and its
hold, the abort) and 1509 toggle points of the copies went unreached. The
level-2 entries `tlp_partner_errors_slow`, `tlp_rollover_slow`,
`tlp_bidir_slow` and `link_down_mid_tlp_slow` run those tests on that
build ([level placement](#plan)) and reach every line point of the copies
and all but 597 of their toggle points; the toggle table below counts
those 597 by the same causes as the other copies. The other copies' holes
are unchanged, point for point (5184).

**Toggle floor lowered from 80.0 to 76.4** (since raised to 77.0)
([captain decision on #354](https://github.com/amichai-bd/pcie-factory/issues/354#issuecomment-6253919165)).
The 80.0 floor of #217 was set on 80.53% (13566 of 16846); the credit
release of [#335](https://github.com/amichai-bd/pcie-factory/issues/335)
then added toggle points that no test can reach, and the merged value fell
to 72.53% (16304 of 22480). The causes, all structural:

- `FC_RELEASE` is a parameter, so its builds are two more parameter-set
  copies: `pcie_dll` with `FC_RELEASE` 1 and `pcie_dll_fc` with `RELEASE`
  1, 4612 toggle points between them. They repeat the whole transmit side
  and the credit counters, which the release entries share with every
  other copy but stress less.
- With `FC_RELEASE` 0 the release port is tied to 0 (`a_rel_tied` asserts
  it), so its ports and the release events built from it never toggle in
  the six copies without release: 709 points, plus 18 top bits of the new
  intermediate allocation sums (`alloc_dlv`, `alloc_rel`) that the table
  below counts with the counter top bits.

The [TD digest](https://github.com/amichai-bd/pcie-factory/issues/338)
cost 60 points: Length bits 8:6 and data credit bits 5:4 that a
mid-Length malformed TLP had toggled. `rx_directed` now sends one
explicitly (Length 508 with 4 DWs), which restores them and also toggles
data credit bit 6, a gain beyond #217. The +992 points to 76.94%, each
counted where only that addition reaches it: `fc_tx_gate_release` 840,
`rx_directed`'s Length-508 case 78, `rx_errors_release` 36, the second
`credit_release` coverage seed 22, and 16 that both of the last two
reach. Running
`link_up`, `link_up_stress`, `credit_starve`, `tlp_partner_errors` or
more `fc_rx_accounting_release` and `credit_release_smallest` seeds with
`FC_RELEASE` 1 adds under 50 points beyond those. Reaching 80 would take
runs that only toggle duplicate copies, which was rejected as low test
value. `coverage_seeds`
([#327](https://github.com/amichai-bd/pcie-factory/issues/327)) loses no
point: the coverage of `ade46ce`, before it, is #217's point for point.

Verilator counts each parameter set of a block apart
([regress SPEC](../../../tools/regress/SPEC.md#coverage)): `pcie_dll` and
`pcie_dll_rx_buf` are built with the credit sets of the entries (default,
completions finite, all infinite, every class finite, smallest, and
`FC_RELEASE` 1), `pcie_dll` and `pcie_dll_tlp_tx` also with
`REPLAY_TIMEOUT` 1400, and `pcie_dll_fc` with and without `RELEASE`, so a
point is missed once per set that does not reach it. The 6 line points not
hit:

| Points | Where | Cause |
| --- | --- | --- |
| 4 | The lane-disable `else` branch of `COMMON_MEM_WRITE_LANES` in `common_sdp_ram`, once per RAM specialization: the replay buffer (`u_tlp_tx.u_rbuf`) and the receive buffer at its three sizes (`u_rx_buf.u_ram`, 7-, 9- and 10-bit addresses) | Unreachable by construction: `LANES` is 1 and the lane enable is tied to 1. |
| 1 | `u_tlp_rx`: the `buf_full_i` branch that sets `noroom` | Unreachable by construction: the receive buffer holds every TLP the advertised credits cover plus the one being collected ([MAS, receive buffer](../../rtl/dll/MAS.md#receive-buffer)), and the overflow check admits nothing more. |
| 1 | `u_fc` without `RELEASE`: the release arm of `taken_ret` | Unreachable by construction: `RELEASE` is 0 in that specialization; the `RELEASE` 1 copy takes it. |

The 5781 toggle points not hit, by cause (of them, in the `REPLAY_TIMEOUT` 1400 copies: 597):

| Cause | Points | Signals |
| --- | ---: | --- |
| Constant by construction: release port tied off | 818 | With `FC_RELEASE` 0 (seven `pcie_dll` copies, 109 in the `REPLAY_TIMEOUT` 1400 one, and `pcie_dll_fc` without `RELEASE`): `fc_rel_*_i` and the release events `rel_p`, `rel_np` in the top; `rel_p_i`, `rel_np_i`, `rel_p_ret`, `rel_np_ret`, `taken_ret` and `taken_rel` in `u_fc`, tied to 0 or a copy of the delivery return (`a_rel_tied`). |
| Hit in another parameter set | 1660 | The same bit and direction toggles in another credit set's copy of the block (275 in the `REPLAY_TIMEOUT` 1400 copies: 122 in the top, 153 in the transmit block, such as the upper sequence-number bits that only `seq_wrap` reaches, on the default build): for example, the infinite-field flags and the upper limit bits toggle only in the sets with finite classes, the receive buffer's upper address bits only in its larger sizes, and the `FC_RELEASE` 1 copies' transmit side and counters only in the copies without release. |
| Constant per build | 817 (35 in the 1400 copy) | The credit parameters (`adv_i` in `u_ctrl` and `u_fc`, `rx_base`, `grp_base`) and the InitFC bodies built from them (`initfc_req` in the top, `u_ctrl` and `u_tx`, with their fixed type and reserved bits): a parameter does not change during a run, so its bits rise at most once after reset. |
| Constant by the DLLP format | 580 (57 in the 1400 copy) | Ack and Nak bodies (`acknak_req` in the top, `u_tlp_rx` and `u_tx`): the type bits other than the Ack-to-Nak bit, the reserved byte and the reserved nibble above the sequence number; the UpdateFC bodies (`updfc_req`): the type code's low bits and the reserved bits between the fields; the same bits of the transmit block's chosen body and packet register (`body`, `tx_q.pkt`, `tx_d.pkt`). |
| Constant by construction: one TLP's credits | 1042 (83 in the 1400 copy) | The credit fields of a single TLP (`rx_chk`, `rx_stored`, `rx_malformed`, `rx_taken`, their `u_fc` and `u_tlp_rx` ports, `u_rx_buf` `cur_q`, `cur_d` and `taken_o`, and `u_fc` `tx_cost`): the header field is always 1 (bits 7:1 never set, bit 0 never falls once the field holds a TLP), the data field is at most 256 (1024 DWs, bits 11:9 never set), and the `valid` flag that `tlp_credit` sets to 1. |
| Not reached: large data credits | 240 (16 in the 1400 copy) | Bits 8:4 of a delivered TLP's data credits (`rx_taken`, `u_rx_buf` `cur_q`, `cur_d` and `taken_o`): a TLP that is delivered carries at most 128 bytes, 8 credits. Bit 7 of the credit fields of `rx_chk`, `rx_stored` and `rx_malformed` and their ports, which carry every judged TLP's credits whatever its verdict (128 to 255 credits, a Length of 509 to 1020 DWs). Only malformed TLPs exceed 8 credits; `rx_directed`'s malformed cases toggle bits 6:4 (Length 508) and 8 (Length 0 is 1024 DWs), and another Length would add no check. (`u_tlp_rx` `hdr.length` bit 9, here until #354, is now hit by `rx_errors_release`.) |
| Constant by construction: release events | 238 | In the `FC_RELEASE` 1 copies, the release events (`rel_p`, `rel_np` in the top; `rel_p_i`, `rel_np_i`, `rel_p_ret`, `rel_np_ret` and `taken_ret` in `u_fc`): a fixed class, a header field that is always 1, data bits 11:4 (a release carries at most 8 credits, `a_rel_data_max`), and `fc_rel_npd_i` bit 3 with its event bits (no non-posted release of 8 credits: the non-posted requests the tests send carry fewer). |
| Unreachable by construction: receive buffer full | 32 (2 in the 1400 copy) | `rx_full`, `u_rx_buf` `full_o` and its fill's top bit (`used`), `u_tlp_rx` `buf_full_i` and `noroom`: the line hole above. |
| Not reached: counter top bits | 310 | The top bits of the NP and Cpl credit counters and recorded limits in `u_fc` (`alloc`, `alloc_mal`, `alloc_dlv`, `alloc_rel`, `rcvd`, `sent`, `upd`, `used`, `lim_np`, `lim_cpl`) and bit 7 of the completion header in use: only the P class wraps its counters (`fc_tx_gate`), and the NP data counter grows by one credit per CfgWr, so a wrap would take over 2048 non-posted writes in one link-up. The wrap arithmetic is shared by every class (`fc_charge`, `fc_record`), and the P wrap checks it. |
| Constant by construction: transmit block | 36 (18 in the 1400 copy) | In `u_tlp_tx`: bit 0 of the space-in-use sums (`need_bytes`, `used_bytes`: whole words plus 2 bytes per TLP), bits 1:0 of a TLP's size (`sop_bytes`: whole DWs), bits 7:4 of the sequence-number beat (reserved, 0) and bit 13 of the space in use, which stays below 8192. |
| Constant by construction: RAM lanes | 4 | `wr_lanes_i` of each `common_sdp_ram` (tied to 1). |
| Not reached: hold release | 4 (2 in the 1400 copy) | `u_tlp_tx` `hold_left` never falls: only a second rollover or DL_Inactive clears it after a hold that a retrain ended, and no test rolls over twice in one link-up; its rise and the hold's end are checked in `rollover_directed`. Cost: a second rollover there, about 2500 more cycles (four replay timeouts and the hold) for a flag whose clearing line is already hit at DL_Inactive; not worth an L0 entry, so no owning issue. |

How the toggle points were sorted, so a re-measurement reproduces the
counts: first, the release-port signals (`fc_rel_*`, `rel_p*`, `rel_np*`,
`taken_ret`, `taken_rel`) in a copy without release (release port tied
off). Then a point is "hit in another parameter set" when the same
hierarchy, signal bit and direction is hit in any copy. The rest go by
signal name, in this order: `adv_i`, `rx_base`, `grp_base` and the
`initfc_req` bodies (constant per build); the `acknak_req` and `updfc_req`
bodies and `u_tx` `body`, `tx_q.pkt` and `tx_d.pkt` (DLLP format); the
`.cred` fields of `rx_chk`, `rx_stored`, `rx_malformed`, `rx_taken`,
`chk_o`, `stored_o`, `malformed_o`, `cur_q`, `cur_d`, `taken_o` and
`tx_cost`, header bits 7:1, data bits 11:9, header bit 0 falling and the
`valid` flags (one TLP's credits), data bits 8:4 (large credits); the release-port signals of the `FC_RELEASE` 1 copies
(release events); `rx_full`, `full_o`, `buf_full_i`, `noroom` and the
fill's top bits (buffer full); the `u_fc` `alloc*`, `rcvd*`, `sent`,
`upd`, `used` and `lim_*` fields (counter top bits); then the named
transmit-block bits, `wr_lanes_i` and `hold_left`.
