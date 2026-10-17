# pcie_dll DV plan

## Strategy

- **Testbench:** [`tb/pcie_dll_tb.sv`](../../../../src/dv/dll/tb/pcie_dll_tb.sv)
  holds one `pcie_dll` with its credit parameters and passes every port
  through by name, plus `cfg_fc_o`, the parameters for the environment.
  The DUT is driven only through its ports.
- **Environment:** [`tb/dll_env.py`](../../../../src/dv/dll/tb/dll_env.py),
  one step per `clk_i` cycle at the falling edge:
  - a **phy stand-in** on the transmit stream: ready 1 from an accepted
    `sop` to its `eop`, 0 in the framing cycle after each `eop`, otherwise
    1 with a set probability (stalls);
  - the **far end**, either the [independent partner](PARTNER.md) behind a
    22-cycle line each way (the HAS's two `phy` stacks), whose transmit
    may pause inside and between packets (gaps), or DLLPs a test queues
    by hand. A gate drops packets that start while the receiving side's
    link is down, as a `phy` would;
  - the **shadow**, the [architecture model](../../arch/dll/HAS.md#architecture-model)
    `Dll` with the DUT's parameters, given the same link state and the
    same received packets. It sends into nothing and is never steered.
- **Checks on every run:** each DLLP the DUT sends is 6 bytes with the
  CRC-16 that both the architecture model and the partner compute, has a
  type allowed in the DUT's state at its `sop` (InitFC1 in FC_INIT1,
  InitFC2 in FC_INIT2, UpdateFC in DL_Active), carries the DUT's
  parameters, equals both models' encoding, and InitFC groups go P, NP,
  Cpl. Every Bad DLLP pulse matches a received DLLP the models judge bad
  (flagged, not 6 bytes, or a CRC mismatch) outside DL_Inactive, one for
  one; no other `err_o` bit pulses. `dl_up_o` matches the state; in
  DL_Inactive every output holds its [reset value](../../arch/dll/HAS.md#reset-values);
  `pkt_tx_valid_o` never drops inside a packet unless the link goes down.
  With the partner, its `violations` list stays empty.
- **Port roles:** the `dll` is role neutral
  ([HAS, parameters](../../arch/dll/HAS.md#parameters)); a role shows only
  as the credits it advertises. Link-up is run with the DUT advertising
  the default (completions infinite: an Endpoint or a Root Port), finite
  completions (a role that may) and all infinite, each against partners
  advertising the default, finite completions, all infinite and the
  smallest legal values; the DUT comes up first, the partner first, or
  both together.
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
  sides; which side came up first), `dll_updatefc_tx` (entry and refresh
  per class), `dll_reset` (reset and link-up, crossed).

## Cross-check: RTL, architecture model, partner

Run in the `link_up` entries for the features `dll` has today (DLLPs,
InitFC, control states):

| Pair | Compared |
| --- | --- |
| RTL and partner | The partner as the far end and judge: both reach DL_Active, each records the other's advertisement, no partner violation. |
| RTL and architecture model | DLLP bytes and CRC-16 (every DLLP sent); the sequence of control states visited by the DUT and the shadow, each change within 200 cycles of the other; credits available equal to the shadow's. |
| Architecture model and partner | A run of the model against a fresh partner with the same skew and credits (`ArchEnd` puts the model on the partner's beat stream): both reach DL_Active, each records the other's advertisement, no partner violation, and the time to DL_Active within 200 cycles of the RTL's. CRC-16 of every received DLLP and of the known answers computed by both. |

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
| [UpdateFC refresh](../../arch/dll/FLOW_CONTROL.md#updatefc-scheduling), round robin, with ready stalls | `updatefc_refresh` | 1 | 2; two 2048-cycle periods |
| Link-up against the partner, default DUT credits, [init target](../../arch/dll/HAS.md#targets) 200 cycles; cross-check | `link_up` | 1 | 0 to 3 (the four partner advertisements); 6 link-ups each |
| The same, DUT completions finite | `link_up_cpl_finite` | 1 | 0, 2; `FC_CPLH` 16, `FC_CPLD` 64 |
| The same, DUT all infinite | `link_up_infinite` | 1 | 1, 3; every `FC_*` 0 |
| Link-up with stalls, gaps and DLLP errors both ways (5% corrupted, 3% dropped, 2% flagged by the partner; 5% lost at it), target 4500 cycles | `link_up_stress` | 1 | 0, 1 |
| The same, wide sweep | `link_up_stress_sweep` | 2 | 8 derived |
| The same, DUT at the smallest legal credits | `link_up_stress_smallest` | 2 | 4 derived |

## Mutation evidence

Each mutant was applied to the RTL and run with levels 0 and 1; each is
caught, and the unmutated RTL passes. The entry named is one that fails,
at its first seed.

| Block | Mutant | First failing entry | Result |
| --- | --- | --- | --- |
| Package | CRC-16 polynomial `100B`h to `100A`h | `crc16_kat` | InitFC1-P sent as `40020020288c`, the DLLP page's `40020020f534`. |
| Package | CRC-16 bytes 4 and 5 swapped | `fc_init_directed` | FC_INIT2 never reached: every received DLLP is bad. |
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
| Top | `dl_up_o` only in DL_Active | `fc_init_directed` | `dl_up_o` 0 in FC_INIT2. |
| Transmit | Beats advance without ready | `fc_init_directed` | An entry UpdateFC lost (NP missing). |

An UpdateFC that writes an infinite field's stored limit is not a mutant
that can be caught: an infinite field shows all ones from its flag,
whatever the limit holds.

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Sequence numbers, LCRC, replay buffer, replay timer, REPLAY_NUM, received Ack and Nak](../../arch/dll/RETRY.md#replay) | [#212](https://github.com/amichai-bd/pcie-factory/issues/212) |
| [Receive checks, duplicates, Ack and Nak scheduling](../../arch/dll/RETRY.md#receiving-tlps); a good TLP ending FC_INIT2 | [#213](https://github.com/amichai-bd/pcie-factory/issues/213) |
| [Credit consumption, the credit gate, receive accounting, UpdateFC on returned credits](../../arch/dll/FLOW_CONTROL.md) | [#214](https://github.com/amichai-bd/pcie-factory/issues/214) |
| [Targets](../../arch/dll/HAS.md#targets) end to end | [#215](https://github.com/amichai-bd/pcie-factory/issues/215), [#216](https://github.com/amichai-bd/pcie-factory/issues/216) |

## Coverage holes

No floors yet (the plan has gaps). Merged over levels 0 to 2, every line
point but these is hit:

| Cause | Points |
| --- | --- |
| Tied off until a later issue | The Ack/Nak branch of the transmit arbiter (#213). |
| Unreachable until #214 | The round-robin picks of the second and third class: without credit returns, classes fall due only in turn, so the pointer always names the due class. |

Toggle holes are mostly the tied-off TLP ports, the upper data bits of the
credit limits (no partner advertises them) and the testbench's
pass-through ports.
