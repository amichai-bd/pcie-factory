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
  plans), line impairments and ppm offsets (`link`), coverage floors
  ([#294](https://github.com/amichai-bd/pcie-factory/issues/294)).

## Plan

Entries run on `pcie_core_e2e_tb` with the HAS default parameters unless
stated; each brings the link up and enumerates first.

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/core/HAS.md#reset), elaboration | `smoke` | 0 | 1; `pcie_core` top, 128 cycles |
| Goal item 4, [enumeration](../../arch/core/HAS.md#targets): IDs, capability list (PM, MSI, MSI-X, PCI Express), BAR0 and BAR2 sized and assigned, no other BAR or ROM, enables and status outputs, Set_Slot_Power_Limit captured ([#309](https://github.com/amichai-bd/pcie-factory/issues/309)), an interrupt dropped with no mode, one MSI, one MSI-X write per vector | `e2e_enumeration` | 1 | 1 |
| The same with `BAR0_ADDR_W` 20, `MSIX_TABLE_SIZE` 64 and non-zero IDs | `e2e_enumeration_max` | 2 | 1 |
| Goal item 5, BAR0 traffic: the HAS [splitting examples](../../arch/tl/COMPLETIONS.md#splitting) with their completion counts, a zero-length read, random writes and reads up to 600 bytes with `cq` stalled 30% | `e2e_bar_traffic` | 1 | 1 |
| The same, more seeds; with the minimum credits (`FC_PH` 1, `FC_PD` 8, `FC_NPH` 1, `FC_NPD` 2), the [credit release](../../arch/core/HAS.md#credits) gating the host | `e2e_bar_traffic_sweep`, `e2e_bar_traffic_min_credits` | 2 | 3; 1 |
| Goal item 5, bus master: `rq` writes with 3-DW and 4-DW headers, `rq` reads of them, eight outstanding, completions fewest and per 64 bytes, `rc` stalled 30% | `e2e_bus_master` (level 1), `e2e_bus_master_sweep` | 1, 2 | 1; 3 |
| [MSI](../../arch/core/APP.md#interrupts): the write after the data written before it (six rounds of four 128-byte writes), pending with bus mastering off, merged requests, sent on enable | `e2e_msi` | 1 | 1 |
| MSI-X: masked vectors pend and show in the PBA, one write per unmask clearing its bit, Function Mask, unmasked writes at once | `e2e_msix` (level 1), `e2e_msix_sweep`, `e2e_msix_max` (64 vectors) | 1, 2 | 1; 2; 1 |
| Goal items 5, 6, [Unsupported Request](../../arch/core/HAS.md#request-routing): I/O read, read past the BARs, write hitting no BAR, Type 0 read to function 1, Type 1 read; UR completions, Unsupported Request Detected, ERR_COR and ERR_NONFATAL with reporting on, none off, Signaled System Error, BAR0 still served; a BAR0 read with Memory Space Enable 0 | `e2e_unsupported` | 1 | 1 |
| Goal items 5, 6, completion timeout: rc failed between 16P and 17P, Transactions Pending meanwhile, ERR_NONFATAL, Non-Fatal Error Detected; the late completion unexpected (ERR_COR); Completion Timeout Disable and the fresh timeout | `e2e_cpl_timeout` (level 1), `e2e_cpl_timeout_div64` | 1, 2 | 1; `CPL_TIMEOUT_DIV` 64 |
| Goal item 6, other errors: malformed (ERR_FATAL), poisoned request (dropped, Detected Parity Error), poisoned completion (rc failed, Master Data Parity Error), UR and CA completions (Received Master and Target Abort, no message) | `e2e_errors` (level 1), `e2e_errors_sweep` | 1, 2 | 1; 2 |
| [Link to cfg](../../arch/core/TL_CFG.md#link-to-cfg): line bit flips under traffic give `rcv_err_o` and `dl_err_o`, Correctable Error Detected and ERR_COR only, data lossless; then each wire alone: a flip on the idle line (`rcv_err_o` only) and a substituted data symbol in a TLP (`dl_err_o` Bad TLP only) | `e2e_link_errors` | 1 | 1 |
| [Throughput targets](../../arch/core/HAS.md#targets), screen: 300 host writes | `e2e_throughput_short` | 1 | 1 |
| Throughput targets: host writes, application writes (2000 of 128 bytes), host reads, application reads, both ways (1000) | `e2e_throughput_host_write`, `e2e_throughput_app_write`, `e2e_throughput_host_read`, `e2e_throughput_app_read`, `e2e_throughput_bidir` | 2 | 1 each |
| [Outside D0](../../arch/core/HAS.md#decisions): UR for BAR0, no `rq` request, interrupt pending, no message; back in D0 all resume, configuration kept | `e2e_d3hot` | 2 | 1 |
| [Link down](../../arch/core/HAS.md#link-down): Hot Reset with traffic in flight, outputs and configuration space at reset values, enumerated again and working; again from D3hot with interrupts resolving back to back (every gated output at its reset value from the first cycle `dl_up_o` is 0) | `e2e_link_down` | 1 | 1 |
| Random mix: BAR0 writes and reads, `rq` writes and reads, MSI-X interrupts, random stalls | `e2e_soak`, `e2e_soak_max`, `e2e_soak_min_credits` | 2 | 3; 2; 2 (`CPL_TIMEOUT_DIV` 16: with minimum credits the host's completions wait behind its blocked writes longer than 2048 cycles) |

## Measured

At `CPL_TIMEOUT_DIV` 1024, one seed each (the regression's first), the
window from the first request taken to the last payload byte delivered,
payload over 2 bytes per cycle of line, against the `link` closed form
(one way 0.8569, both ways 0.7748):

| Scenario | Fraction of the line | Fraction of the link rate | HAS target |
| --- | --- | --- | --- |
| Host writes in, 2000 | 0.8563 | 0.9993 | ≥ 0.98, ≥ 0.833 |
| Application writes out, 2000 | 0.8563 | 0.9994 | ≥ 0.98, ≥ 0.833 |
| Host reads, 1000, application answering in 16 cycles | 0.7929 | 1.0234 | ≥ 0.98, ≥ 0.745 |
| Application reads, 1000 | 0.7739 | 0.9988 | ≥ 0.98, ≥ 0.745 |
| Both ways, 1000 each | 0.7745 | 0.9996 | ≥ 0.98, ≥ 0.745 |

Host reads beat the both-ways closed form: their reverse direction
carries only small read requests, not 128-byte writes. The architecture
model's figures are in the [HAS targets](../../arch/core/HAS.md#targets):
the RTL agrees with them within 0.002 but for host reads, which measure
0.793 of the line against the model's 0.774 (both above the target; the
two hosts pace their reads differently). Link-up takes about 10300 cycles
(`TIMEOUT_DIV` 1024) and the host model's enumeration about 3000.

Coverage at levels 0 and 1 (#293): line 71.6%, toggle 37.7% over the whole
stack, functional 100% (62 of 62 bins). Floors:
[#294](https://github.com/amichai-bd/pcie-factory/issues/294).

## Mutation evidence

Each mutant is one deliberate change to `pcie_core.sv`, the only RTL this
plan owns; the entries ran in level order and the first failing one is
named.

| Mutant | Entry | Seed | Fails at cycle, with |
| --- | --- | --- | --- |
| M1: `link` `FC_RELEASE` 0: credits return at delivery | `e2e_enumeration` | 914163979 | 10366: `u_dll` assertion `a_rel_tied`: a release pulse with `FC_RELEASE` 0 |
| M2: `cfg` reset without data link down (`cfg_rst = rst_i`) | `smoke` | 1152607520 | 8: `u_cfg` assertion `a_irq_vector`: `cfg` out of reset takes the random interrupt request while the link is down |
| M3: `bus_master_en_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 13831: `bus_master_en_o` 1 with `dl_up_o` 0 |
| M4: `link` `port_role_i` 0 (downstream) | `e2e_link_down` | 189935525 | 73980: no link-up again in 60000 cycles after the Hot Reset (a downstream port does not follow it) |
| M5: Slot power event not wired to `cfg` | `e2e_enumeration` | 914163979 | 13434: shadow: Device Capabilities 0x8000, the `cfg` model 0x8c88000 |
| M6: `tl_err` not wired to `cfg` | `e2e_unsupported` | 116918796 | 13580: shadow: Device Status without Unsupported Request Detected |
| M7: `dl_err` not wired to `cfg` | `e2e_link_errors` | 830112853 | 19956: shadow: Correctable Error Detected 0 after a Bad TLP alone |
| M8: `rcv_err` not wired to `cfg` | `e2e_link_errors` | 830112853 | 16662: shadow: Correctable Error Detected 0 after a receiver error alone |
| M9: `cpl_timeout_dis` not wired to `tl` | `e2e_cpl_timeout` | 2115626119 | 20603: a read timed out with Completion Timeout Disable set |
| M10: `tl_np_pending` not wired to `cfg` | `e2e_cpl_timeout` | 2115626119 | 13931: Transactions Pending 0 with a read outstanding |
| M11: `tl` sees bus number 0 | `e2e_enumeration` | 914163979 | 10402: host model: completer ID 0x0000, expected 0x0100 |
| M12: `max_read_req_o` 0 instead of 2 under `cfg_rst` | `smoke` | 1152607520 | 0: `max_read_req_o` expected 2 in reset, got 0 |
| M13: `irq_done_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 28121: `irq_done_o` 1 with `dl_up_o` 0 (link down from D3hot during the interrupt storm) |
| M14: Slot power scale not wired to `cfg` | `e2e_enumeration` | 914163979 | 13434: shadow: Device Capabilities 0xc88000, the `cfg` model 0x8c88000 |
| M15: `tl` sees Memory Space Enable always 1 | `e2e_unsupported` | 116918796 | 15715: a BAR0 read with Memory Space Enable 0 completed successfully |
| M16: `d_state_o` not gated by `cfg_rst` | `e2e_link_down` | 189935525 | 28121: `d_state_o` 3 with `dl_up_o` 0 |

Removed after the first run: a gate on `irq_ready_o`, which survived because `cfg` already drives it 0 from its reset input (an equivalent mutant); the gate went, the MAS says why. M7, M8, M13, M15 and M16 survived that first run and led to the isolated link events of `e2e_link_errors`, the Memory Space Enable check of `e2e_unsupported` and the D3hot round of `e2e_link_down`.

## Gaps

| Feature | Owning issue |
| --- | --- |
| Coverage floors and their holes | [#294](https://github.com/amichai-bd/pcie-factory/issues/294) |
| FPGA and ASIC timing of `core` | [#295](https://github.com/amichai-bd/pcie-factory/issues/295), [#296](https://github.com/amichai-bd/pcie-factory/issues/296) |
