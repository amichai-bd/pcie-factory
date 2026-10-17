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
    `Dll` with the DUT's parameters, given the same link state and the
    same received packets. It sends into nothing and is never steered;
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
    delivered TLP against the reference.
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
- **Functional coverage** (sampled where driven or observed):
  `dll_dl_states` (the six arcs of the control states), `dll_dllp_tx` (each
  InitFC1, InitFC2 and UpdateFC class; first group and resend; a `sop`
  that waited for ready), `dll_dllp_rx` (good per kind, other type, other
  VC; bad CRC, bad length, flagged; discarded in DL_Inactive; gaps inside),
  `dll_fc_init` (what ended FC_INIT2; finite and infinite fields on both
  sides; which side came up first), `dll_updatefc_tx` (entry and refresh
  per class), `dll_tx_tail` (a DLLP finished with one, two or three beats
  taken at or after the edge that sampled the link down), `dll_reset` (reset and link-up, crossed),
  `dll_tlp_rx` (each receive outcome: stored, duplicate, ahead, bad LCRC,
  flagged, too short, nullified, EDB without the inverse, malformed,
  outside FC_INIT2 and DL_Active; class of each stored TLP; gaps inside;
  a Nak scheduled or already scheduled), `dll_malformed` (not whole DWs,
  shorter than a header, Length disagreeing, payload above 128 bytes, no
  header), `dll_acknak_tx` (Ack and Nak; Ack by the timer or after a
  duplicate), `dll_rx_stream` (a beat taken or stalled; a `sop` back to
  back or after idle).

## Cross-check: RTL, architecture model, partner

Run in the `link_up` entries for DLLPs, InitFC and the control states, and
in the `rx_*` entries for the TLP receive path:

| Pair | Compared |
| --- | --- |
| RTL and partner | The partner as the far end and judge: both reach DL_Active, each records the other's advertisement, no partner violation. |
| RTL and architecture model | DLLP bytes and CRC-16 (every DLLP sent); the sequence of control states visited by the DUT and the shadow, each change within 200 cycles of the other; credits available equal to the shadow's. |
| Architecture model and partner | A run of the model against a fresh partner with the same skew and credits (`ArchEnd` puts the model on the partner's beat stream): both reach DL_Active, each records the other's advertisement, no partner violation, and the time to DL_Active within 200 cycles of the RTL's. CRC-16 of every received DLLP and of the known answers computed by both. |
| RTL, reference and partner (receive) | The partner sends TLPs with its fault hooks; the DUT delivers exactly the partner's TLPs, once and in order; the partner sees no violation (every Ack and Nak in range) and its replay buffer drains (every TLP acknowledged); the RTL's pulses and Naks equal the reference's. |
| RTL and architecture model (receive) | The shadow, given the same packets, delivers the same TLPs and counts the same Bad TLP, Malformed TLP, Nak and duplicate events, and no overflow (`check_shadow_rx`). |
| Architecture model and partner (receive) | The model receives the same TLPs from a fresh partner with the same injection (`arch_partner_traffic`): all delivered once and in order, all acknowledged, no partner violation. |

The two models step at packet level with their own transmit timing, so the
state and timing comparisons allow one InitFC resend (128 cycles) and its
group; the logs record the largest difference. The receive path found one
disagreement, between the partner and the architecture model on the
transmit side: which TLPs a replay resends when one is on the line as the
Nak arrives. It is in the [HAS open items](../../arch/dll/HAS.md#open-items)
for #212; the RTL's receive side is not involved. Any later one goes there
too, never into a model or the RTL silently.

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

## Mutation evidence

Each mutant was applied to the RTL and levels 0 and 1 run one test at a
time; each is caught, and the unmutated RTL passes. The entry named is the
first that fails in that run, at its first seed.

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

An UpdateFC that writes an infinite field's stored limit is not a mutant
that can be caught: an infinite field shows all ones from its flag,
whatever the limit holds.

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Sequence numbers, LCRC, replay buffer, replay timer, REPLAY_NUM, received Ack and Nak](../../arch/dll/RETRY.md#replay) | [#212](https://github.com/amichai-bd/pcie-factory/issues/212) |
| The receive overflow check and the overflowed-TLP verdict ([receive buffer](../../arch/dll/FLOW_CONTROL.md#receive-buffer)), driven through `rx_fit` | [#214](https://github.com/amichai-bd/pcie-factory/issues/214) |
| Ack and Nak waiting behind a TLP on the transmit stream (the 118-cycle bound with both directions loaded) | [#212](https://github.com/amichai-bd/pcie-factory/issues/212), [#215](https://github.com/amichai-bd/pcie-factory/issues/215) |
| [Credit consumption, the credit gate, receive accounting, UpdateFC on returned credits](../../arch/dll/FLOW_CONTROL.md) | [#214](https://github.com/amichai-bd/pcie-factory/issues/214) |
| [Targets](../../arch/dll/HAS.md#targets) end to end | [#215](https://github.com/amichai-bd/pcie-factory/issues/215), [#216](https://github.com/amichai-bd/pcie-factory/issues/216) |

## Coverage holes

No floors yet (the plan has gaps). Merged over levels 0 to 2, every line
point but these is hit:

| Cause | Points |
| --- | --- |
| Unreachable by construction | The receive buffer full (`rx_full`, `noroom`): the buffer holds every TLP the advertised credits cover plus the one being collected ([MAS, receive buffer](../../rtl/dll/MAS.md#receive-buffer)), and the partner respects the credits. |
| Unreachable until #214 | The round-robin picks of the second and third class: without credit returns, classes fall due only in turn, so the pointer always names the due class. |

Toggle holes are mostly the tied-off TLP ports, the upper data bits of the
credit limits (no partner advertises them) and the testbench's
pass-through ports.
