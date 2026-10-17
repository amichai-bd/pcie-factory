# pcie_dll DV plan

## Strategy

- **Testbench:** [`tb/pcie_dll_tb.sv`](../../../../src/dv/dll/tb/pcie_dll_tb.sv)
  holds one `pcie_dll` with its credit parameters and passes every port
  through by name, plus `cfg_fc_o`, the parameters for the environment.
  The DUT is driven only through its ports, except by the slot drivers.
- **Slot drivers** (interim, [#214](https://github.com/amichai-bd/pcie-factory/issues/214)):
  until the TLP paths attach, no port reaches the credit accounting. A
  test that sets `slot_tx_i` gets `tlp_tx_ready_o` forced to a stand-in
  for #212's arbiter (DL_Active, and on a `sop` beat the gate `fc_tx_ok`);
  one that sets `slot_rx_i` gets the receive decision `rx_tlp` and the
  upper receive stream forced from `slot_rx_*`, a stand-in for #213.
  They drive only the signals the [MAS slot table](../../rtl/dll/MAS.md#slots-for-the-tlp-paths)
  names; `probe_tx_ok_o` and `probe_rx_fits_o` show the
  [credit interface](../../rtl/dll/MAS.md#credit-interface)'s `fc_tx_ok`
  and `rx_fits`. Each goes when its owner lands
  ([#212](https://github.com/amichai-bd/pcie-factory/issues/212),
  [#213](https://github.com/amichai-bd/pcie-factory/issues/213)), and the
  tests then run on the real paths.
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
    with the slot drivers its credit counters follow the same consumed,
    stored, malformed and returned TLPs;
  - with the slot drivers, an upper-layer **TLP source** (valid may drop
    between beats) that, with `fwd`, frames each accepted TLP with a
    sequence number and LCRC and sends it to the partner between the
    DUT's packets, holding back while 3 wait (a full replay buffer); and a
    **receive stand-in** that turns queued TLPs into decisions, stores what
    fits and delivers it on the upper stream, whose ready the test may
    stall when every class is finite.
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
  CRC mismatch) outside DL_Inactive, one for one; every Receiver overflow
  pulse comes the cycle after a decision that does not fit; no other
  `err_o` bit pulses. `dl_up_o` matches the state; in
  DL_Inactive every output holds its [reset value](../../arch/dll/HAS.md#reset-values),
  except the tail of a DLLP started before the link went down;
  `pkt_tx_valid_o` never drops inside a packet and no `sop` comes inside
  one, link up or down.
  With the partner, its `violations` list stays empty.
- **Checks with the slot drivers, every cycle:** the gate on the offered
  first DW (a TLP's, or a random header when idle) equals the
  [rule](../../arch/dll/FLOW_CONTROL.md#transmit-accounting) and the
  architecture model's `credits_ok`; the credits available equal the
  model's; the overflow check on the decided (or a random) header equals
  the [rule](../../arch/dll/FLOW_CONTROL.md#receive-buffer) and the model's
  `_rx_space`; the free credits of every class and field equal the
  model's counters. In the receive tests a returned credit is in an
  UpdateFC offered within 16 cycles (the stream never stalls there: at
  most the packet on the stream and two other classes' UpdateFCs ahead).
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
- **Partner model:** its pytest unit tests run in `./build ci unit`, not
  as a regression entry ([partner page](PARTNER.md#unit-tests)).
- **Functional coverage** (sampled where driven or observed):
  `dll_dl_states` (the six arcs of the control states), `dll_dllp_tx` (each
  InitFC1, InitFC2 and UpdateFC class; first group and resend; a `sop`
  that waited for ready), `dll_dllp_rx` (good per kind, other type, other
  VC; bad CRC, bad length, flagged; discarded in DL_Inactive; gaps inside),
  `dll_fc_init` (what ended FC_INIT2; finite and infinite fields on both
  sides; which side came up first), `dll_updatefc_tx` (entry, refresh and
  returned credits per class; the class sent counted from the round-robin
  pointer: 0, 1 or 2 on), `dll_tx_tail` (a DLLP finished with one, two or
  three beats taken at or after the edge that sampled the link down),
  `dll_reset` (reset and link-up, crossed), `dll_fc_tx` (gate open, closed
  by headers, data or both, or infinite, per class; each consumed counter
  wrapped; a TLP refused and then taken), `dll_fc_rx` (stored, overflow,
  malformed, malformed under 4 bytes, per class; finite and infinite
  classes; returns per class).

## Cross-check: RTL, architecture model, partner

Run in the `link_up` entries for DLLPs, InitFC and the control states,
and in the flow-control entries for credit accounting:

| Pair | Compared |
| --- | --- |
| RTL and partner | The partner as the far end and judge: both reach DL_Active, each records the other's advertisement, no partner violation. |
| RTL and architecture model | DLLP bytes and CRC-16 (every DLLP sent); the sequence of control states visited by the DUT and the shadow, each change within 200 cycles of the other; credits available equal to the shadow's. |
| Architecture model and partner | A run of the model against a fresh partner with the same skew and credits (`ArchEnd` puts the model on the partner's beat stream): both reach DL_Active, each records the other's advertisement, no partner violation, and the time to DL_Active within 200 cycles of the RTL's. CRC-16 of every received DLLP and of the known answers computed by both. |
| RTL and architecture model, credits | Every cycle with the slot drivers: the gate, the credits available, the overflow check and the free credits per class and field ([checks](#strategy)). |
| RTL and partner, credits | `credit_starve`, `credits_infinite`: the partner receives the TLPs the DUT accepted (forwarded by the source) and judges each against its advertisement; no overflow violation, every TLP once and in order. |
| Architecture model and partner, credits | The same TLPs, credits and upper-layer schedule from the model to a fresh partner (`arch_partner_traffic`): every TLP once and in order, no violation. |

The two models step at packet level with their own transmit timing, so the
state and timing comparisons allow one InitFC resend (128 cycles) and its
group; the logs record the largest difference. No disagreement was found;
any later one goes to the [HAS open items](../../arch/dll/HAS.md#open-items),
never into a model or the RTL silently.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/dll/HAS.md#reset-values) in reset and DL_Inactive | `smoke` | 0 | 1; 256 cycles, random inputs and link-up |
| [Credit parameter ranges](../../arch/dll/HAS.md#parameters) | `param_limits` | 0 | 1; 8 illegal and 3 legal lints, no simulation |
| [CRC-16](../../arch/dll/DLLP.md#crc-16) known answers, transmit and receive; [receiving DLLPs](../../arch/dll/DLLP.md#receiving-dllps): bad CRC, flagged, wrong length, DL_Inactive | `crc16_kat` | 0 | 1; 5 known answers and every single-bit error of them, 150 random DLLPs and one-bit errors, gaps |
| [Control states](../../arch/dll/HAS.md#data-link-control-states) and [initialisation](../../arch/dll/FLOW_CONTROL.md#initialisation) by hand: groups and 128-cycle resend, recording, ignored DLLPs, FC_INIT2 ends, entry UpdateFC, [credits available](../../arch/dll/HAS.md#credits-available), UpdateFC limits, link down from each state | `fc_init_directed` | 0 | 1 |
| [A started DLLP finished across a link-down](../../arch/dll/HAS.md#reset-values), two and one beats left, and all three when the `sop` is taken at the edge that samples `link_up_i` 0; an offered one withdrawn; clean restart | `link_down_mid_dllp` | 0 | 1 |
| [UpdateFC refresh](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling), round robin, with ready stalls | `updatefc_refresh` | 1 | 2; two 2048-cycle periods |
| Link-up against the partner, default DUT credits, [init target](../../arch/dll/HAS.md#targets) 200 cycles; cross-check | `link_up` | 1 | 0 to 3 (the four partner advertisements); 6 link-ups each |
| The same, DUT completions finite | `link_up_cpl_finite` | 1 | 0 to 3; `FC_CPLH` 16, `FC_CPLD` 64 |
| The same, DUT all infinite | `link_up_infinite` | 1 | 0 to 3; every `FC_*` 0 |
| Link-up with stalls, gaps and DLLP errors both ways (5% corrupted, 3% dropped, 2% flagged by the partner; 5% lost at it), target 4500 cycles | `link_up_stress` | 1 | 0, 1 |
| [Transmit accounting](../../arch/dll/FLOW_CONTROL.md#transmit-accounting): the gate for every class and field, at exactly half the range and one past it, after limits beyond half the range, with infinite fields; CREDITS_CONSUMED wrapping; credits available; cleared by link-down mid-TLP | `fc_tx_gate` | 0 | 1; slot drivers, hand-driven far end |
| [Receive accounting](../../arch/dll/FLOW_CONTROL.md#receive-accounting) and the [overflow check](../../arch/dll/FLOW_CONTROL.md#receive-buffer) with Receiver overflow; malformed TLPs' credits returned, none under 4 bytes; returns at the delivered `eop`; [UpdateFC](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling) on returns, in FC_INIT2 (entry value) and DL_Active, its value, cause, round robin and latency; refreshes; link down; default credits | `fc_rx_accounting` | 0 | 1; slot drivers |
| The same, every class finite, the upper layer stalling | `fc_rx_accounting_finite` | 1 | 2 derived |
| The same, every class infinite (the reserve only; UpdateFC all 0) | `fc_rx_accounting_infinite` | 1 | 2 derived |
| [Credits target](../../arch/dll/HAS.md#targets) on the transmit side: starvation and recovery with the partner's upper layer taking 30%, stalling 4000 cycles, taking 30%, stalling 10000 cycles, then taking all; no overflow at the partner's receive-side check; arch model against a fresh partner | `credit_starve` | 1 | 0 to 3 (partner credits: three all finite, one default) |
| The same, partner and DUT all infinite: the gate never refuses | `credits_infinite` | 1 | 2 derived |
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
| Package | Gate: half the range left counted as closed (`<` for `<=`, headers) | `fc_tx_gate` | The 128-byte write leaving exactly 128 header credits of headroom is refused. |
| Package | Gate ignores the TLP's own data credits | `fc_tx_gate` | The write leaving exactly 2048 data credits is refused. |
| Package | Gate ignores an infinite data field's flag | `fc_tx_gate` | Gate 0 for a completion, expected 1 (infinite Cpl, arch agrees). |
| Flow control | A taken `sop` consumes nothing | `fc_tx_gate` | The boundary write refused: the DUT's headroom is not the consumed credits'. |
| Package | Data credits rounded down | `fc_rx_accounting` | UpdateFC-P carries data 33, CREDITS_ALLOCATED is 34. |
| Package | Messages counted as non-posted | `fc_rx_accounting` | Stops on `a_free_within_space`: a message's return lands in the wrong class. |
| Package | Length 0 not read as 1024 DWs | `fc_rx_accounting` | Fits 1 for a P TLP needing 256 data credits with 32 free (rule and arch 0). |
| Package | Overflow check: exactly filling the free credits overflows | `fc_rx_accounting` | Stops on `a_free_within_space`: the stand-in stores what the rule admits, so its return finds nothing counted. |
| Flow control | No Receiver overflow pulse | `fc_rx_accounting` | 47 overflows, 0 pulses. |
| Flow control | A malformed TLP's credits not returned | `fc_rx_accounting` | Stops on `a_free_within_space`. |
| Flow control | A malformed TLP under 4 bytes counts its header field bits | `fc_rx_accounting` | Fits 0 for a no-header decision; an UpdateFC with no class due. |
| Flow control | Returned credits read from the `eop` beat, not the `sop` | `fc_rx_accounting` | Stops on `a_free_within_space`. |
| Package | Infinite-field data reserve 32 credits, not 24 | `fc_rx_accounting` | Fits 1 for a completion needing 31 with 24 free in the reserve. |
| Flow control | No UpdateFC on returned credits | `fc_rx_accounting` | NP credits returned at cycle 66 not sent within 16 cycles. |
| Flow control | The P value sent not recorded (P stays due) | `fc_rx_accounting` | UpdateFC-P sent while NP was the due class; then one with no class due. |
| Flow control | An infinite field's internal count sent instead of 0 | `fc_init_directed` | UpdateFC-Cpl `a0020018ef48`, expected `a00000001fd2`. |
| Flow control | Round robin ignores the pointer (P first) | `fc_rx_accounting` | UpdateFC-P sent with Cpl first in round-robin order. |
| Flow control | CREDITS_ALLOCATED cleared to 0 in DL_Inactive | `fc_init_directed` | Entry UpdateFC-P `80000000c91d`, expected the advertisement 8, 32. |
| Flow control | CREDITS_CONSUMED not cleared in DL_Inactive | `fc_tx_gate` | P credits available (137, 3850) after the restart, arch (2, 8). |
| Package | Credits available ignore CREDITS_CONSUMED (headers) | `fc_tx_gate` | P credits available (6, 12), arch (5, 12). |
| Flow control | A stored TLP not added to CREDITS_RECEIVED | `fc_rx_accounting` | Stops on `a_free_within_space`. |

An UpdateFC that writes an infinite field's stored limit is not a mutant
that can be caught: an infinite field shows all ones from its flag,
whatever the limit holds.

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Sequence numbers, LCRC, replay buffer, replay timer, REPLAY_NUM, received Ack and Nak](../../arch/dll/RETRY.md#replay) | [#212](https://github.com/amichai-bd/pcie-factory/issues/212) |
| [Receive checks, duplicates, Ack and Nak scheduling](../../arch/dll/RETRY.md#receiving-tlps); a good TLP ending FC_INIT2; the assertion that the upper layer never stalls the receive stream when a class is infinite ([HAS, Receive](../../arch/dll/HAS.md#receive)) | [#213](https://github.com/amichai-bd/pcie-factory/issues/213) |
| The credit gate in front of the real transmit path: TLPs sent by #212 instead of the slot driver, the starvation entries rerun on it, the transmit slot driver removed | [#212](https://github.com/amichai-bd/pcie-factory/issues/212), [#214](https://github.com/amichai-bd/pcie-factory/issues/214) |
| The [credits target](../../arch/dll/HAS.md#targets) on the receive side: the partner sending, the DUT's upper layer stalling 4000 and 10000 cycles (every class finite) or never (any infinite), no Receiver overflow; an overflowed TLP neither acknowledged nor Nak'd; the receive slot driver removed | [#213](https://github.com/amichai-bd/pcie-factory/issues/213), [#214](https://github.com/amichai-bd/pcie-factory/issues/214) |
| [Targets](../../arch/dll/HAS.md#targets) end to end | [#215](https://github.com/amichai-bd/pcie-factory/issues/215), [#216](https://github.com/amichai-bd/pcie-factory/issues/216) |

## Coverage holes

No floors yet (the plan has gaps). Merged over levels 0 to 2, every line
point but these is hit:

| Cause | Points |
| --- | --- |
| Tied off until a later issue | The Ack/Nak branch of the transmit arbiter (#213). |

Toggle holes are mostly the tied-off TLP ports, the upper bits of the
credit counters and limits (wrapped only by the P class in `fc_tx_gate`)
and the testbench's pass-through ports.
