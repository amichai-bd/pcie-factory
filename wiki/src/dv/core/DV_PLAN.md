# pcie_core DV plan

How [`pcie_core`](../../arch/core/HAS.md) is verified: back to back with
an independently written host, end to end through the whole stack. The
`tl`, `cfg`, `link` and lower IPs keep their own unit DV; this plan covers
what only the integration shows: the [stack wiring](../../arch/core/HAS.md#stack-wiring),
the [tl-cfg interface](../../arch/core/TL_CFG.md) in place, the
[application interface](../../arch/core/APP.md), [reset and link
down](../../arch/core/HAS.md#reset), and goal items 4 to 7 of decision
record [0004](../../decisions/0004-m3-grill.md#goal).

## Strategy

- **Bench:** [`tb/pcie_core_e2e_tb.sv`](../../../../src/dv/core/tb/pcie_core_e2e_tb.sv)
  holds the endpoint `pcie_core` and the host's `pcie_link` (downstream,
  the `link` HAS default credits, `FC_RELEASE` 0) on one clock, their
  serial lines joined directly, host to endpoint through one register of
  delay (an error-free line: line impairments are the
  [`link` e2e's](../link/DV_PLAN.md)). For the link error test the
  environment sees each host-to-endpoint word on `mon_line_o` a cycle
  before the endpoint samples it, and can flip chosen bits of it
  (`line_flip_i`) or replace it (`line_sub_i`, `line_sub_data_i`): one
  data symbol inside a TLP replaced by another valid data symbol of the
  same running disparity, from the `pcs` architecture model's 8b/10b
  code, so only the TLP's LCRC fails. The host link's
  Hot Reset request is a bench input. `TIMEOUT_DIV` is 1024 and
  `CPL_TIMEOUT_DIV` 1024 (a 2048-cycle completion timeout) unless an entry
  sets them. The environment
  [`tb/core_e2e_env.py`](../../../../src/dv/core/tb/core_e2e_env.py)
  drives every input at the falling edge and reads every output once the
  design settles.
- **Oracle, the independent host model** ([HOST](HOST.md), F9, goal item
  7): written apart from the `tl` and `cfg` architecture models by another
  author with no shared code, on the host link's upper stream. It
  enumerates, sets up MSI and MSI-X, moves memory traffic, answers the
  endpoint's reads from its memory and judges every completion (completer
  ID, Byte Count, Lower Address, the 64-byte split rule, status, data),
  interrupt write (one whole DW to a programmed vector, from the function,
  with Bus Master Enable set) and endpoint request (bus mastering, 4 KiB,
  maximum payload and read request, tags 0 to 7). Its first violation
  fails the test. A bench fault hook withholds the completions of chosen
  endpoint reads (completion timeout); raw TLPs (I/O, function 1, Type 1,
  malformed, poisoned, late or poisoned completions) go out through its
  `send_request`.
- **Application model:** takes `cq` beats (with stalls in some tests),
  applies BAR0 writes in `cq` order and answers each read with one `cc`
  packet after a random latency, in any order; issues the `rq` requests a
  test queues and collects `rc`; raises interrupts. It honours the
  [write-completion](../../arch/core/APP.md#write-completion) obligation
  and so exercises it in every test: random gaps only before a packet's
  `sop`, and once a write's `sop` is taken its remaining beats are offered
  every cycle while it has `cc` packets due, interrupts open and `cq`
  stalled.
- **Shadow, the `cfg` architecture model** (`src/arch/cfg`, the DUT's
  parameters): every configuration request, BAR2 table write and
  Set_Slot_Power_Limit the host link takes is applied to it, and the
  tapped `tl_err` events and the endpoint link's `dl_err_o` and
  `rcv_err_o` as they pulse. Each configuration read and BAR2 table read
  the host receives must equal the shadow's value when it went out (Device
  Status Transactions Pending excepted: it follows `tl_np_pending`); the
  error messages of each kind never outnumber the shadow's decisions, and
  every kind it decided arrives. A disagreement fails the test; none was
  found.
- **Checks in every test**, from the HAS: reset values while `rst_i` is 1,
  and the application and status outputs at theirs in every cycle
  `dl_up_o` is 0; the `cq` and `rc` stream rules (valid held with the beat
  unchanged until taken, a read's data and a write's handle excepted;
  header held to `eop`; lengths, alignment, BAR0 bounds; a handle never
  reused while open); `rq_ready_o` 0 on a `sop` beat with bus mastering
  off or outside D0; `rc` data length, `rc_done_o` once per read, a failed
  status as one beat; `irq_ready_o` 0 from a take until `irq_done_o`; no
  `dl_err_o` or `rcv_err_o` at either end on the error-free line; liveness;
  at the end, the host model error-free and the application idle.
- **Functional coverage:** `core_e2e_enum` (each enumeration step),
  `core_e2e_traffic` (sizes per kind, completions per application read,
  3-DW and 4-DW headers), `core_e2e_irq` (mode and outcome, pending sent
  on unmask or enable), `core_e2e_err` (every `tl_err` event and each
  error message kind), `core_smoke`.
- **Smoke:** [`tests/test_core.py`](../../../../src/dv/core/tests/test_core.py)
  drives `pcie_core` alone with random inputs (the line too): reset
  values in reset, and the application outputs at theirs while the link
  cannot train.
- **Out of scope here:** the unit behaviour of each stacked IP (their own
  plans) and line impairments and ppm offsets (`link`); what the stacked
  IPs' code leaves uncovered here is in [Coverage holes](#coverage-holes).

### Shared L1 scenarios

L1 `e2e_functional` shares one training/enumeration episode across the
original enumeration, BAR, bus-master, MSI and MSI-X checks. Both interrupt
table/address/data programs remain; interrupt ledgers use phase boundaries,
and application stall/latency settings are restored. `e2e_error_paths`
shares another training/enumeration episode across unsupported, timeout and
error-status phases. It restores reporting controls and clears
W1C status before each phase; raw histories remain intact and exact error
counts use phase baselines. Link-error injection stays in standalone
`e2e_link_errors` with its original seed 830112853: its fresh link sequence,
replay-buffer and symbol-disparity state are part of the scenario. Sharing
that fixture changes the fault alignment even with the same random draws.
Destructive link-down and throughput also stay separate.
Each phase logs its original name and effective seed. At the pinned bundle
seed, stimulus uses the original regression seed plus cocotb 2.1's SHA1 of
the original testcase fullname; another bundle seed shifts every phase by
the same amount. Random generators are restored without starting another
clock or driver. Covergroups remain the same module-level instances, and
all phase final checks remain active.


## Plan

Entries run on `pcie_core_e2e_tb` with the HAS default parameters unless
stated; each brings the link up and enumerates first.

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/core/HAS.md#reset), elaboration | `smoke` | 0 | 1; `pcie_core` top, 128 cycles |
| Goal item 4, [enumeration](../../arch/core/HAS.md#targets): IDs, capability list (PM, MSI, MSI-X, PCI Express), BAR0 and BAR2 sized and assigned, no other BAR or ROM, enables and status outputs, Set_Slot_Power_Limit captured ([#309](https://github.com/amichai-bd/pcie-factory/issues/309)), an interrupt dropped with no mode, one MSI, one MSI-X write per vector | `e2e_functional` (phase `e2e_enumeration`) | 1 | 1 |
| The same with `BAR0_ADDR_W` 20, `MSIX_TABLE_SIZE` 64 and non-zero IDs | `e2e_enumeration_max` | 2 | 1 |
| Goal item 5, BAR0 traffic: the HAS [splitting examples](../../arch/tl/COMPLETIONS.md#splitting) with their completion counts, a zero-length read, random writes and reads up to 600 bytes with `cq` stalled 30% | `e2e_functional` (phase `e2e_bar_traffic`) | 1 | 1 |
| The same, more seeds; with the minimum credits (`FC_PH` 1, `FC_PD` 8, `FC_NPH` 1, `FC_NPD` 2), the [credit release](../../arch/core/HAS.md#credits) gating the host | `e2e_bar_traffic_sweep`, `e2e_bar_traffic_min_credits` | 2 | 3; 1 |
| Goal item 5, bus master: `rq` writes with 3-DW and 4-DW headers, `rq` reads of them, eight outstanding, completions fewest and per 64 bytes, `rc` stalled 30% | `e2e_functional` (phase `e2e_bus_master`) (level 1), `e2e_bus_master_sweep` | 1, 2 | 1; 3 |
| [MSI](../../arch/core/APP.md#interrupts): the write after the data written before it (six rounds of four 128-byte writes), pending with bus mastering off, merged requests, sent on enable | `e2e_functional` (phase `e2e_msi`) | 1 | 1 |
| MSI-X: masked vectors pend and show in the PBA, one write per unmask clearing its bit, Function Mask, unmasked writes at once | `e2e_functional` (phase `e2e_msix`) (level 1), `e2e_msix_sweep`, `e2e_msix_max` (64 vectors) | 1, 2 | 1; 2; 1 |
| Goal items 5, 6, [Unsupported Request](../../arch/core/HAS.md#request-routing): I/O read, read past the BARs, write hitting no BAR, Type 0 read to function 1, Type 1 read; UR completions, Unsupported Request Detected, ERR_COR and ERR_NONFATAL with reporting on, none off, Signaled System Error, BAR0 still served; a BAR0 read with Memory Space Enable 0 | `e2e_error_paths` (phase `e2e_unsupported`) | 1 | 1 |
| Goal items 5, 6, completion timeout: rc failed between 16P and 17P, Transactions Pending meanwhile, ERR_NONFATAL, Non-Fatal Error Detected; the late completion unexpected (ERR_COR); Correctable Error Detected after it; Completion Timeout Disable and the fresh timeout | `e2e_error_paths` (phase `e2e_cpl_timeout`) (level 1), `e2e_cpl_timeout_div64` | 1, 2 | 1; `CPL_TIMEOUT_DIV` 64 |
| Goal item 6, other errors: malformed (ERR_FATAL), poisoned request (dropped, Detected Parity Error), poisoned completion (rc failed, Master Data Parity Error), UR and CA completions (Received Master and Target Abort, no message) | `e2e_error_paths` (phase `e2e_errors`) (level 1), `e2e_errors_sweep` | 1, 2 | 1; 2 |
| [Link to cfg](../../arch/core/TL_CFG.md#link-to-cfg): line bit flips under traffic give `rcv_err_o` and `dl_err_o`, Correctable Error Detected and ERR_COR only, data lossless; then each wire alone: a flip on the idle line (`rcv_err_o` only) and a substituted data symbol in a TLP (`dl_err_o` Bad TLP only) | `e2e_link_errors` | 1 | 1 |
| [Throughput targets](../../arch/core/HAS.md#targets), screen: 300 host writes | `e2e_throughput_short` | 2 | 1 |
| Throughput targets: host writes, application writes (2000 of 128 bytes), host reads, application reads, both ways (1000) | `e2e_throughput_host_write`, `e2e_throughput_app_write`, `e2e_throughput_host_read`, `e2e_throughput_app_read`, `e2e_throughput_bidir` | 2 | 1 each |
| [Outside D0](../../arch/core/HAS.md#decisions): UR for BAR0, no `rq` request, interrupt pending, no message; back in D0 all resume, configuration kept | `e2e_d3hot` | 2 | 1 |
| [Link down](../../arch/core/HAS.md#link-down): Hot Reset with traffic in flight and Max_Payload_Size and Max_Read_Request_Size written to other values first (the status outputs follow them), outputs and configuration space at reset values, enumerated again and working; again from D3hot with interrupts resolving back to back (every gated output at its reset value from the first cycle `dl_up_o` is 0) | `e2e_link_down` | 1 | 1 |
| Random mix: BAR0 writes and reads, `rq` writes and reads, MSI-X interrupts, random stalls | `e2e_soak`, `e2e_soak_max`, `e2e_soak_min_credits` | 2 | 3; 2; 2 (`CPL_TIMEOUT_DIV` 16: with minimum credits the host's completions wait behind its blocked writes, up to 2752 and 2866 cycles for an rq read on its two seeds, more than the 2048-cycle default timeout) |

## Measured

At `CPL_TIMEOUT_DIV` 1024, one seed each (the regression's first), the
window from the first request taken to the last payload byte delivered,
payload over 2 bytes per cycle of line, against the `link` closed form
(one way 0.8569, both ways 0.7748); with `tl`'s registered transmit output stage
([#295](https://github.com/amichai-bd/pcie-factory/issues/295)):

| Scenario | Fraction of the line | Fraction of the link rate | HAS target |
| --- | --- | --- | --- |
| Host writes in, 2000 | 0.8563 | 0.9993 | ≥ 0.98, ≥ 0.833 |
| Application writes out, 2000 | 0.8563 | 0.9993 | ≥ 0.98, ≥ 0.833 |
| Host reads, 1000, application answering in 16 cycles | 0.7935 | 1.0241 | ≥ 0.98, ≥ 0.745 |
| Application reads, 1000 | 0.7738 | 0.9988 | ≥ 0.98, ≥ 0.745 |
| Both ways, 1000 each | 0.7740 | 0.9990 | ≥ 0.98, ≥ 0.745 |

Host reads beat the both-ways closed form because the closed form
charges one Ack and one UpdateFC DLLP per read on the completion
direction, while the host model queues all its reads and sends them in
bursts as non-posted credits return, so the endpoint coalesces Acks and
UpdateFCs: its transmit line carried 1565 DLLPs for 1000 completions
(1.57 per completion, against 2 in the closed form). Application reads,
where the host returns each completion as its read arrives, measure
0.9988 (the endpoint line carried 1122 DLLPs for 1000 read requests). The architecture
model's figures are in the [HAS targets](../../arch/core/HAS.md#targets):
the RTL agrees with them within 0.002 but for host reads, which measure
0.794 of the line against the model's 0.774 (both above the target; the
two hosts pace their reads differently). Link-up takes about 10300 cycles
(`TIMEOUT_DIV` 1024) and the host model's enumeration about 3000.

Coverage at levels 0 and 1 (#293): line 72.8%, toggle 38.7% over the whole
stack, functional 100% (62 of 62 bins); at levels 0 to 2 and the floors,
[Coverage holes](#coverage-holes).

## Mutation evidence

The following cycle/seed results are historical standalone-recipe evidence.
Those testcase wrappers remain for the named L2 sweeps; the shared L1
scenarios above retain their corresponding phases and assertions. These
are not new measurements of the consolidated scenarios.

Each mutant is one deliberate change to `pcie_core.sv`, the only RTL this
plan owns; the entries ran in level order and the first failing one is
named.

| Mutant | Entry | Seed | Fails at cycle, with |
| --- | --- | --- | --- |
| M1: `link` `FC_RELEASE` 0: credits return at delivery | `e2e_enumeration` | 914163979 | 10366: `u_dll` assertion `a_rel_tied`: a release pulse with `FC_RELEASE` 0 |
| M2: `cfg` reset without data link down (`cfg_rst = rst_i`) | `smoke` | 1152607520 | 8: `u_cfg` assertion `a_irq_vector`: `cfg` out of reset takes the random interrupt request while the link is down |
| M3: `bus_master_en_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 14010: `bus_master_en_o` 1 with `dl_up_o` 0 |
| M4: `link` `port_role_i` 0 (downstream) | `e2e_link_down` | 189935525 | 74157: no link-up again in 60000 cycles after the Hot Reset (a downstream port does not follow it) |
| M5: Slot power event not wired to `cfg` | `e2e_enumeration` | 914163979 | 13434: shadow: Device Capabilities 0x8000, the `cfg` model 0x8c88000 |
| M6: `tl_err` not wired to `cfg` | `e2e_unsupported` | 116918796 | 13580: shadow: Device Status without Unsupported Request Detected |
| M7: `dl_err` not wired to `cfg` | `e2e_link_errors` | 830112853 | 19956: shadow: Correctable Error Detected 0 after a Bad TLP alone |
| M8: `rcv_err` not wired to `cfg` | `e2e_link_errors` | 830112853 | 16662: shadow: Correctable Error Detected 0 after a receiver error alone |
| M9: `cpl_timeout_dis` not wired to `tl` | `e2e_cpl_timeout` | 2115626119 | 20867: a read timed out with Completion Timeout Disable set |
| M10: `tl_np_pending` not wired to `cfg` | `e2e_cpl_timeout` | 2115626119 | 13931: Transactions Pending 0 with a read outstanding |
| M11: `tl` sees bus number 0 | `e2e_enumeration` | 914163979 | 10402: host model: completer ID 0x0000, expected 0x0100 |
| M12: `max_read_req_o` 0 instead of 2 under `cfg_rst` | `smoke` | 1152607520 | 0: `max_read_req_o` expected 2 in reset, got 0 |
| M13: `irq_done_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 28250: `irq_done_o` 1 with `dl_up_o` 0 (link down from D3hot during the interrupt storm) |
| M14: Slot power scale not wired to `cfg` | `e2e_enumeration` | 914163979 | 13434: shadow: Device Capabilities 0xc88000, the `cfg` model 0x8c88000 |
| M15: `tl` sees Memory Space Enable always 1 | `e2e_unsupported` | 116918796 | 15715: a BAR0 read with Memory Space Enable 0 completed successfully |
| M16: `d_state_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 28250: `d_state_o` 3 with `dl_up_o` 0 |
| M17: `mem_space_en_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 14010: `mem_space_en_o` 1 with `dl_up_o` 0 |
| M18: `max_payload_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 14010: `max_payload_o` 1 (as written before the link down) with `dl_up_o` 0 |
| M19: `max_read_req_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 14010: `max_read_req_o` 4 (as written) with `dl_up_o` 0, expected 2 |

Removed after the first run: a gate on `irq_ready_o`, which survived because `cfg` already drives it 0 from its reset input (an equivalent mutant); the gate went, the MAS says why. M7, M8, M13, M15 and M16 survived that first run and led to the isolated link events of `e2e_link_errors`, the Memory Space Enable check of `e2e_unsupported` and the D3hot round of `e2e_link_down`. M17 to M19 came from review: `e2e_link_down` now writes Max_Payload_Size and Max_Read_Request_Size to other values before the first link down, so every gated output differs from its reset value there.

## Gaps

| Feature | Owning issue |
| --- | --- |
| FPGA and ASIC timing of `core` | FPGA [#295](https://github.com/amichai-bd/pcie-factory/issues/295); ASIC routed timing [#363](https://github.com/amichai-bd/pcie-factory/issues/363) |

## Coverage holes

Floors in [`level2.yaml`](../../../../src/dv/core/regress/level2.yaml),
checked by every `--coverage` run that selects level 2 (the nightly runs
levels 0 to 2): line 75.3, toggle 44.1, functional 100. Merged over levels
0 to 2 ([#294](https://github.com/amichai-bd/pcie-factory/issues/294)),
over the whole stack that `core.rtl.f` reaches (the endpoint `pcie_core`
and the bench's host `pcie_link`): line 75.88% (4168 of 5493 points),
toggle 44.63% (77783 of 174278), functional 100% (62 of 62 bins in the
five covergroups the level file names), with `tl`'s transmit output stage
([#295](https://github.com/amichai-bd/pcie-factory/issues/295)). The line and toggle floors are
the merged values less 0.5 points, rounded down to a tenth
([floor margin](../../../bkm/dv.md#coverage)).

`core` is an integration: its e2e drives the stack through its ports
with two compliant ends on an error-free line, so most of what it leaves
uncovered is behaviour the stacked IPs' own DV reaches (their
[granularity](../../../bkm/rtl.md#granularity)). Each point not hit was
looked up in the merged coverage of those IPs' own regressions, each at
levels 0 to 2 with `--coverage` on the same tree as the `core` run
(`fe5b43f`, [#416](https://github.com/amichai-bd/pcie-factory/issues/416)):
`link` (90.94% line, 76.85% toggle), `dll` (99.1%, 77.6%), `mac` (87.0%,
79.2%), `pcs` (99.2%, 98.0%), `phy` (81.7%, 76.0%), `tl` (97.8%, 78.4%),
`cfg` (97.9%, 64.9%) and the shared unit `common` (97.3%, 91.6%); every
unit's floors hold, and each unit's plan (Coverage holes) explains the
points it does not hit.

**Re-measured for [#416](https://github.com/amichai-bd/pcie-factory/issues/416)**
on `3ec1037` and again on `fe5b43f` (levels 0 to 2, `--coverage`, the same
figures): line 75.84% (4172 of 5501), toggle 44.59% (78118 of 175198),
functional 100% (62 of 62). Both floors hold and stay: the merged value
less 0.5 points gives 75.3 and 44.0. The `core` builds use neither
`REPLAY_TIMEOUT` nor `LOWER` 1, so they add no parameter-set copy. The
tables below are re-sorted on `fe5b43f` by the rule at their end. Against
the tables of [#294](https://github.com/amichai-bd/pcie-factory/issues/294)
(1325 line and 96495 toggle points not hit), the points not hit rose by 4
line and 585 toggle points (+7, -1, -3 and +1 line and +322, +116, +51 and
+96 toggle, row by row), by cause:

| Cause | Line | Toggle |
| --- | ---: | ---: |
| The GPIO ports at `LOWER` 0, held at 0 or tied off by the bench (constant by construction): `pcie_core.sv` 96 (own nets), `pcie_link.sv` 104 (not hit by `link` either) | | +200 |
| `pcie_gpio_pkg::replay_timeout`, called only for a `localparam` (elaboration only, [#378](https://github.com/amichai-bd/pcie-factory/issues/378)) | +1 | |
| `tl`'s queue, TLP helpers, output stage and receive beat tracker moved to the shared blocks ([#390](https://github.com/amichai-bd/pcie-factory/pull/390), [#402](https://github.com/amichai-bd/pcie-factory/pull/402)): the same logic, now counted in `common_sdp_ram_fifo` (the RAM rows), `common_skid_stage` and `common_tlp_rx_track`. By file, points not hit: toggle `tl` -3849, the common RAMs +3530, `common_skid_stage` +580, `common_tlp_rx_track` +124; line `tl` -58, the common RAMs +45, `common_skid_stage` +9, `common_tlp_rx_track` +7. Each point is in the row its cause gives below | +3 | +385 |
| Total | +4 | +585 |

How these were measured: `core` at levels 0 to 2 with `--coverage` on
`07bae13` (the tree of the #294 tables) gives the #294 figures again (line
4168 of 5493, toggle 77783 of 174278). Its points not hit, counted by file
and compared with `fe5b43f`, change only in the files above. `dll`,
`mac`, `pcs`, `phy`, `cfg`, the common CDC blocks and the rest of
`pcie_core.sv` and `pcie_link.sv` are unchanged, so the `dll`
`REPLAY_TIMEOUT` parameter ([#393](https://github.com/amichai-bd/pcie-factory/issues/393))
adds no point not hit in `core`. The #294 tables counted the common CDC
blocks inside the `pcs` (`common_cdc_fifo`, `common_cdc_pulse`,
`common_cdc_sync2`, `common_cdc_rst`) under "the common RAMs": that label
held 3881 points not hit (3750, 121 and 10), which is 3751 in the RAM
files and 130 in the CDC blocks. The tables below name the CDC blocks (57
hit in another copy, 69 reached by the `pcs` and `phy` DV, 4 not hit
either), which moves those 130 points out of the RAM counts with no net
change.

The `link` reference now includes its level 2 (it was levels 0 and 1).
That moves 104 `pcie_link.sv` points from "not hit either" to "reached by
the stacked IP's own DV", and leaves the totals as they are.

A PR in a stacked IP (`link`, `dll`, `mac`, `pcs`, `phy`, `tl`, `cfg`)
that adds state, or an entry here with a new parameter set, dilutes these
floors too: it re-measures `core` coverage (levels 0 to 2 with
`--coverage`) in the same PR, as the [level file](../../../../src/dv/core/regress/level2.yaml)
says. The 1329 line points not hit, by cause:

| Cause | Points | Where |
| --- | ---: | --- |
| Hit in another parameter set or at the host's end | 836 | The same point is hit in another copy of its block (the entries' five parameter sets, and the host's downstream `pcie_link`): `tl` 251, `mac` 158, `cfg` 129, `dll` 128, the common RAMs 116, `pcs` 37, `common_skid_stage` 9, `common_tlp_rx_track` 5, `pcie_core.sv` 3 (lines 191 to 194). |
| Reached by the stacked IP's own DV | 428 | `mac` 243, `pcs` 64, `tl` 42, `cfg` 39, `dll` 38, `common_tlp_rx_track` 2: behaviour two compliant stacks on this line do not produce, or that the host model does not send: the LTSSM's error, timeout and compliance exits, the power states the `link` ties off, elastic-buffer and decoder extremes ([`link` holes](../link/DV_PLAN.md#coverage-holes)); in `tl` and `cfg`, the malformed and error cases and the queue and parameter extremes of their plans. |
| Not hit by the stacked IP's own DV either | 64 | The holes of the unit plans, counted in each copy: `mac` 44 (elaboration-only functions, [`mac` holes](../mac/DV_PLAN.md#coverage-holes)), `tl` 14 ([`tl` holes](../tl/DV_PLAN.md#coverage-holes): the state `default`, the constant enable and the input-contract cases), `pcs` 4, `dll` 2 (the `noroom` branch of `u_tlp_rx` in both copies, unreachable by construction, [`dll` holes](../dll/DV_PLAN.md#coverage-holes)). |
| Elaboration only | 1 | `pcie_gpio_pkg::replay_timeout`, called only for a `localparam` of `pcie_link` (the GPIO build's replay timeout). |

The 97080 toggle points not hit, by cause:

| Cause | Points | Signals |
| --- | ---: | --- |
| Hit in another parameter set or at the host's end | 46325 | The same file, signal bit and direction toggles in another copy: `tl` 19709, `dll` 7913, the common RAMs 7199, `cfg` 5392, `mac` 2286, `pcie_core.sv` 1914, `pcs` 734, `pcie_link.sv` 456, `common_skid_stage` 400, `phy` 177, `common_tlp_rx_track` 88, the common CDC blocks 57. |
| Reached by the stacked IP's own DV | 33167 | `tl` 17723, `cfg` 8667, `dll` 3072, `mac` 2640, `pcs` 570, `common_skid_stage` 180, `pcie_link.sv` 136, the common RAMs 74, `common_cdc_fifo` 69, `phy` 36: data and address patterns, error and malformed paths, credit and queue extremes and parameter values that the host model and the e2e's parameter sets do not produce, and the CDC FIFO's data and fill bits, which the `pcs` and `phy` DV reach. |
| Not hit by the stacked IP's own DV either | 16188 | `tl` 5660, `dll` 4782, `cfg` 4722, `mac` 580, `pcie_link.sv` 260, `pcs` 130, `common_tlp_rx_track` 36, the common RAMs 8, `phy` 6, `common_cdc_rst` 4: the causes of each IP's plan ([`tl`](../tl/DV_PLAN.md#coverage-holes), [`cfg`](../cfg/DV_PLAN.md#coverage-holes), [`dll`](../dll/DV_PLAN.md#coverage-holes), [`link`](../link/DV_PLAN.md#coverage-holes)): bits outside the registers' writable masks, constant templates and fields, credit counters' top bits, power states tied off. The `dll` 4782 by the causes of its holes: constant per build 1284, one TLP's credits 1128, release events 952, the DLLP format 590, counter top bits 492, large data credits 260, the transmit block and hold release 40, receive buffer full 36. In `pcie_link.sv`, 104 of the 260 are the GPIO ports at `LOWER` 0 (constant by construction, [`link` holes](../link/DV_PLAN.md#coverage-holes)). `common_tlp_rx_track` 36: `hdr_end` and `pos_o.hdr_end` are 2 or 3, so their other bits are constant by construction. The common RAMs 8: the top bits of `in_ram` and `ram_used_o` in the RAM FIFO copies that `tl` builds, a fill level that no unit's DV reaches at that depth (the `tl` plan's queue fill and owed credits row: a host that keeps to its credits never fills a queue, [`tl` holes](../tl/DV_PLAN.md#coverage-holes)). `common_cdc_rst` 4: the reset synchronizer's first stage, constant ([`link` holes](../link/DV_PLAN.md#coverage-holes)). |
| The stack's own nets | 1400 | `pcie_core.sv` 1384: the GPIO ports at `LOWER` 0, held at 0 or tied off by the bench (96, constant by construction), the values the host model fixes (its BAR0, BAR2 and MSI addresses and data, one bus and device number, Max_Payload_Size and Max_Read_Request_Size), credits available above the advertisement (`fc_*_avail`, `fc_rel_npd`), and the error-event bits that do not pulse on this line (`dl_err_o`); `tl` 16: the completion timeout prescaler's top bits in a parameter set only `core` builds. |

How the points were sorted, so a re-measurement reproduces the counts: a
line point is keyed by file, line, column and kind, a toggle point by
file, signal bit and direction (any hierarchy and copy). A point not hit
is "another parameter set or the host's end" when its key is hit anywhere
in the `core` run; otherwise "reached by the stacked IP's own DV" when it
is hit in the merged coverage of the IP that owns the file (`link` for
`pcie_link.sv`, the union of the units for the common RAMs), "not hit
either" when it is a point there but not hit, and "the stack's own nets"
when the IPs' runs have no such point.

## Budget placement

[#435](https://github.com/amichai-bd/pcie-factory/issues/435): The throughput
screen is a performance check and runs at L2 with the full throughput entries.
Directed transaction, completion, interrupt and error acceptance remains at
L1.

The combined levels keep the original testcases, parameters, seeds and
per-seed coverage instrumentation; no coverage floor is raised or lowered.
The PR records plain and coverage-instrumented CPU measurements separately.

| Entry | Current placement |
| --- | --- |
| `e2e_throughput_short` | L2; unchanged seeds, parameters and instrumentation |
