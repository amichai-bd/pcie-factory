# pcie_cfg DV plan

## Strategy

- **Testbench:** [`tb/pcie_cfg_tb.sv`](../../../../src/dv/cfg/tb/pcie_cfg_tb.sv)
  holds one `pcie_cfg` with its parameters and passes every port through
  by name, plus `cfg_params_o`, the parameters for the environment (the
  IDs are `int` parameters there so a regression entry can set them; the
  DUT gets them at their HAS widths). The DUT is driven only through its
  ports.
- **Environment:** [`tb/cfg_env.py`](../../../../src/dv/cfg/tb/cfg_env.py),
  one step per `clk_i` cycle at the falling edge:
  - a **tl stand-in** on the [register access](../../arch/core/TL_CFG.md#register-access)
    handshake: one access at a time, held until taken, the next offered
    after `reg_cpl_valid_o`, random bus and device numbers on writes; a
    **message taker** raising `msg_ready_i` with a set probability, so
    offers are taken at once or held; random `tl_np_pending_i`; slot
    power events, queued or random, with a random payload while
    `slot_pwr_valid_i` is 0;
  - an **application stand-in** on the [interrupt port](../../arch/core/APP.md#interrupts):
    one request at a time, held until taken, vectors below
    `MSIX_TABLE_SIZE`; and queued or random error-event cycles on
    `tl_err_i`, `dl_err_i` and `rcv_err_i`;
  - the **shadow**, the [architecture model](../../arch/cfg/HAS.md#architecture-model)
    `Cfg` with the DUT's parameters, given every edge in the
    [MAS edge order](../../rtl/cfg/MAS.md#top): a read sees the state
    before the edge; an offer the DUT raises at an edge is the model's
    `offer` taken before the edge's operations; then the take, the write,
    the events, the slot power event and the request.
- **Checks on every run, every cycle:** the offer (`msg_valid_o`, and
  while it is up its kind, and for an interrupt write its address and
  data) equals the model's, so an offer the model withdraws must fall and
  one it keeps must stay; `irq_done_o` pulses exactly when the model
  resolves a request, with its status; `irq_ready_o` is 1 exactly when no
  request is open and `reg_req_ready_o` exactly when no access is (the
  first cycle out of reset included); every register-state output equals the model's; each
  read's `reg_cpl_data_o` equals the model's read; `reg_cpl_valid_o` comes
  within 4 cycles of its take and never without an access, and a request
  is resolved within 4 cycles unless it is held (the latency
  [targets](../../arch/cfg/HAS.md#targets), the `LATENCY` check); the
  [reset](../../arch/cfg/HAS.md#reset) timing: in every cycle where `rst_i`
  is 1 the two readys are 0 once the inputs settle (the cycle it rises
  included), from the cycle after each edge that samples it every
  qualified output has its reset value, and in the first cycle where
  `rst_i` is 0 again both readys are 1 once the inputs settle. The
  RTL assertions ([MAS](../../rtl/cfg/MAS.md)) hold in every run.
- **Oracles written from the HAS, not the model:**
  [`tb/cfg_regmap.py`](../../../../src/dv/cfg/tb/cfg_regmap.py) lists
  every field of [REGISTERS.md](../../arch/cfg/REGISTERS.md) and
  [CAPS.md](../../arch/cfg/CAPS.md) with its access type and reset value;
  [`tests/test_cfg_err.py`](../../../../src/dv/cfg/tests/test_cfg_err.py)
  writes out the [error table](../../arch/cfg/HAS.md#errors) and the
  message decision; the interrupt tests state each rule's expected writes
  and statuses, and the enumeration test is a host walk with the
  parameters as the reference.
- **Parameter limits:** [`tests/test_cfg_params.py`](../../../../src/dv/cfg/tests/test_cfg_params.py)
  lints `pcie_cfg` from `cfg.rtl.f` for each illegal value (both ends of
  `BAR0_ADDR_W` and `MSIX_TABLE_SIZE`, each ID at 16'hFFFF), which must
  stop elaboration with its `COMMON_ASSERT_PARAM` message, and for the
  legal boundaries, which must lint clean.
- **The MSI-X table** is a RAM that is not reset: tests that use it write
  every entry first (`Env.init_table`), as software must
  ([HAS, Reset](../../arch/cfg/HAS.md#reset)).
- **Functional coverage** (sampled where driven or observed):
  `cfg_access` (space, direction and byte enables crossed; the region:
  header, capability, gap, extended space, table, mask, pending bits,
  other BAR2), `cfg_err` (each event crossed with its outcome: message,
  gated by the enables, outside D0, no class; the impossible pairs
  declared unreachable: events 7 and 8 have no class, every other event
  has one), `cfg_msg` (each offer source crossed with taken or withdrawn;
  taken at once or held), `cfg_irq` (mode at the request crossed with
  status; when it resolved: at the request, waiting, withdrawn, taken;
  unreachable: no mode is only dropped, an MSI-X request is never
  dropped), `cfg_power` (D0 to D3hot, D3hot to D0, D1 and D2 discarded),
  `cfg_slot_power` (each scale; the event alone, at the edge of a
  configuration read or write, in D3hot).

## Cross-check: RTL, architecture model, HAS

The shadow runs in every entry. One disagreement was found and fixed in
the model ([#289](https://github.com/amichai-bd/pcie-factory/issues/289)):
the model built the application's interrupt write from the table entry
(or the MSI registers) at the request, while the HAS reads the entry when
the write is offered ([CAPS.md, BAR2](../../arch/cfg/CAPS.md#bar2)); a
write that waits behind an error message saw the difference. The model now
reads them at the offer, with a self-test. No other disagreement is open.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/cfg/HAS.md#reset) and when each output reaches them (the readys 0 from the cycle `rst_i` rises and 1 in the first cycle it is 0, the rest the cycle after its edge), the handshake, IDs | `smoke` | 0 | 1 |
| Every [header](../../arch/cfg/REGISTERS.md#type-0-header) and [capability](../../arch/cfg/CAPS.md) field: reset value, RW by byte enable, RO and HwInit ignore writes, RW1C not set by a write; unlisted DWs and the extended space read 0; reads whole DWs whatever the byte enables; bus and device capture on every write; [BAR sizing](../../arch/cfg/REGISTERS.md#bar-sizing); Max_Payload_Size, Max_Read_Request_Size and Completion Timeout Disable on their outputs (shadow) | `regmap` | 1 | 1 |
| The same at the parameter boundaries (BAR0 256 MiB, 64 vectors, set IDs) | `regmap_params` | 1 | 1 |
| [Power states](../../arch/cfg/HAS.md#power-states): D3hot and D0 at the write's edge, D1 and D2 discarded, No_Soft_Reset; Transactions Pending | `power_states` | 1 | 1 |
| [Slot power limit](../../arch/cfg/HAS.md#slot-power-limit): 0 from reset, each event loads value and scale, a payload without valid loads nothing, software writes ignored, back-to-back events, a read at the event's edge sees the old value, D3hot, cleared by reset | `slot_power` | 1 | 1 |
| [BAR2](../../arch/cfg/CAPS.md#bar2): table fields by byte enable, address bits 1:0, mask reset 1, pending bits read-only, other offsets 0, table kept and masks reset by a reset | `bar2_map` | 1 | 1 |
| The same with 64 vectors (both pending-bit DWs) | `bar2_map_64` | 1 | 1 |
| [Targets](../../arch/cfg/HAS.md#targets) enumeration: IDs, capability walk, BAR sizing and assignment, enables, an MSI and an MSI-X interrupt (pending first) | `enumeration` | 1 | 1 |
| The same with other parameters (BAR0 1 MiB, one vector, set IDs) | `enumeration_params` / `enumeration_params_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (1585370101), pinned |
| [Errors](../../arch/cfg/HAS.md#errors): every event under 11 enable sets, status bits, message decision, UR gate, Signaled System Error, RW1C by byte | `error_table` | 1 | 1 |
| [Messages](../../arch/cfg/HAS.md#messages): priority, merging (same cycle and into a held offer), no message outside D0 (each event), an offered error withdrawn by D3hot | `error_messages` | 1 | 1 |
| [Interrupts](../../arch/cfg/HAS.md#interrupts) under MSI: dropped with no mode, 32- and 64-bit writes, pending while not allowed and merged, MSI Enable clears the flag, D3hot | `irq_msi` | 1 | 1 |
| Interrupts under MSI-X: each vector's entry, vector and function masks, pending bits and their clearing on the take, lowest vector first, merging, MSI-X over MSI, pending kept while disabled, the entry read at the offer, a stale looked-up entry read again after a BAR2 write | `irq_msix` | 1 | 1 |
| [Withdrawal](../../arch/core/TL_CFG.md#withdrawal): by Bus Master Enable, D3hot, MSI Enable (to the flag, to the vector's bit, dropped); a mask after the offer does not withdraw; a waiting request turned pending | `irq_withdraw` | 1 | 1 |
| Everything at once under the shadow: random accesses, masks, enables, power states, events, slot power events, requests, link-down resets, a stalling taker; nothing pending after the drain | `random_ops` / `random_ops_sweep` | 1 / 2 | derived seeds 1 and 2 (329618035 and 179094473, pinned) / derived seed 0 (1688380133), pinned; 1500 operations |
| [Parameter](../../arch/cfg/HAS.md#parameters) limits | `param_limits` | 1 | 1 |
| The random test, longer | `random_long` | 2 | 4; 12000 operations |
| The random test with 64 vectors and BAR0 256 MiB | `random_long_64` | 2 | 2 |
| The random test with one vector | `random_long_1` | 2 | 2 |

**Level placement.** Level 0 holds the smoke test only. The `random_long`
entries are level 2 as soaks (longer runs of `random_ops`, which level 1
runs with two seeds and level 2 with a third, in `random_ops_sweep`).
Since [#389](https://github.com/amichai-bd/pcie-factory/issues/389) (the
full level 1 of every IP was over its 1200 s CPU budget), level 1 runs
`enumeration_params` at its derived seed 0, with seed 1 pinned in
`enumeration_params_sweep`, and `random_ops` at the two seeds the mutant
table names (179094473 and 329618035), with its derived seed 0 pinned in
`random_ops_sweep`. Each seed keeps its coverage instrumentation, so the
merged level 0 to 2 runs are the same runs, and every mutant below is
still caught in level 1 at the seed it names.

## Mutation evidence

Each mutant is a deliberate bug in the RTL of
[#289](https://github.com/amichai-bd/pcie-factory/issues/289), run against
level 0 and level 1 one entry at a time (`--jobs 1`); the table gives the
first entry that fails. M19 to M23 and the current M15 and M17 ran at
`2626b81`, the others at `cb6b529` and `151aab1` (their mutated lines are
unchanged since); M24 to M27 are the slot power capture of
[#309](https://github.com/amichai-bd/pcie-factory/issues/309). Entries run in level, then name order, so
`param_limits`, which lints the RTL, comes before `power_states`,
`random_ops` and `regmap`.

| Mutant | Block | First failing entry | How |
| --- | --- | --- | --- |
| M1: SERR# Enable read-only | `regs` | `error_messages` (L1, seed 1683172248) | cycle 36: cfg read of DW 0x1 expected 0x00100140 got 0x00100040 |
| M2: bus number captured only below 0x100 | `regs` | `regmap` (L1, seed 66002683) | cycle 2700: bus_num_o expected 0xa0 got 0x8b |
| M3: PowerState takes a D2 write | `regs` | `power_states` (L1, seed 272654647) | cycle 16: d_state_o expected 0x3 got 0x2 |
| M4: Device Status RW1C ignores byte enables | `regs` | `error_table` (L1, seed 673424925) | cycle 3048: cfg read of DW 0x1e expected 0x00032800 got 0x00002800 |
| M5: posted UR not gated by UR Reporting Enable | `err` | `error_table` (L1, seed 673424925) | cycle 835: DUT offers a message the model does not have |
| M6: advisory UR sets no Correctable Error Detected | `err` | `error_messages` (L1, seed 1683172248) | cycle 50: cfg read of DW 0x1e expected 0x0009280f got 0x0008280f |
| M7: due messages kept on leaving D0 | `err` | `error_messages` (L1, seed 1683172248) | cycle 259: DUT offers a message the model does not have |
| M8: ERR_COR ahead of ERR_FATAL | `msg` | `error_messages` (L1, seed 1683172248) | cycle 5: offer kind expected 3 (err_fatal) got 2 |
| M9: Bus Master Enable cleared does not withdraw | `msg` | `irq_withdraw` (L1, seed 1210582240) | cycle 74: DUT offers kind 0, the model withdrew or has no offer |
| M10: a mask after the offer withdraws it | `msg` | `irq_withdraw` (L1, seed 1210582240) | cycle 112: irq_done_o with status 1, the model resolved nothing |
| M11: highest pending vector first | `msg` | `irq_msix` (L1, seed 1249535295) | cycle 154: interrupt write (pend_msix) expected addr 0xfee00000 data 0x100 got addr 0xfee00070 data 0x107 |
| M12: request not merged into a set pending bit | `msg` | `random_ops` (L1, seed 179094473) | cycle 404: request resolved pending by the model, irq_done_o is 0 |
| M13: MSI Enable cleared keeps the MSI pending flag | `msg` | `irq_msi` (L1, seed 137415403) | assertion a_msi_pend_en failed |
| M14: lookup at an edge with a BAR2 access | `msg` | `random_ops` (L1, seed 179094473) | assertion a_one_reader failed |
| M15: address bits 1:0 not cleared on the read | `bar2` | `bar2_map` (L1, seed 519847080) | cycle 66: BAR2 read of DW 0x0 expected 0xa26c88d4 got 0xa26c88d5 |
| M16: mask bits reset to 0 | `bar2` | `bar2_map` (L1, seed 519847080) | cycle 2: BAR2 read of DW 0x3 expected 0x00000001 got 0x00000000 |
| M17: reg_req_ready_o not dropped while an access is open | `top` | `smoke` (L0, seed 25442184) | cycle 4: reg_req_ready_o expected 0 got 1 |
| M18: Signaled System Error not set for ERR_NONFATAL | `msg` | `error_table` (L1, seed 673424925) | cycle 290: cfg read of DW 0x1 expected 0x40100140 got 0x00100140 |
| M19: looked-up word kept across a BAR2 access | `msg` | `irq_msix` (L1, seed 1249535295) | cycle 250: interrupt write (pend_msix) expected data 0xd00d got 0x102 (the stale-lookup case) |
| M20: lookup hit ignores the vector | `msg` | `random_ops` (L1, seed 329618035) | cycle 467: interrupt write (app_msix) expected addr 0xfee00050 data 0x105 got addr 0xfeea3610 data 0x4287ec01 |
| M21: reg_req_ready_o 1 in reset | `top` | `smoke` (L0, seed 25442184) | cycle 0 in reset: reg_req_ready_o expected 0 got 1 |
| M22: irq_ready_o 1 while a request is held | `msg` | `enumeration` (L1, seed 702597822) | cycle 60: irq_ready_o expected 0 got 1 (request open) |
| M23: irq_ready_o 0 while any message is offered | `msg` | `enumeration` (L1, seed 702597822) | cycle 84: irq_ready_o expected 1 got 0 (no request open) |
| M24: the slot power capture ignores `slot_pwr_valid_i` | `regs` | `param_limits` (L1, seed 821874251) | the legal limits fail lint (an unused input); `regmap`: cycle 60: cfg read of DW 0x1d expected 0x00008000 got 0x0a1c8000 |
| M25: Captured Slot Power Limit Value and Scale swapped in the read | `regs` | `random_ops` (L1, seed 179094473) | cycle 292: cfg read of DW 0x1d expected 0x07d08000 got 0x0f448000 |
| M26: Captured Slot Power Limit not reset | `regs` | `random_ops` (L1, seed 179094473) | cycle 2350: cfg read of DW 0x1d expected 0x00008000 got 0x062c8000 |
| M27: Captured Slot Power Limit Scale not read | `regs` | `param_limits` (L1, seed 821874251) | the legal limits fail lint (unused bits); `slot_power`: cycle 58: cfg read of DW 0x1d expected 0x0e948000 got 0x02948000 |

## Gaps

None. Enumeration end to end against the independent host model is the
[`core` plan's](../core/DV_PLAN.md) `e2e_enumeration` and
`e2e_enumeration_max` ([#293](https://github.com/amichai-bd/pcie-factory/issues/293));
the coverage floors are set ([#294](https://github.com/amichai-bd/pcie-factory/issues/294)).

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
| L0+L1+L2 | 266/266 (100.00%) | 9954/11724 (84.90%) | 99.5 / 84.4 |

### Per-instance diagnostic baseline

The counts and prior floor values below describe the original per-instance
view at the revisions cited. Current code floors use the source table above.
A point hit in another parameter set or instance is covered in the source
union; the other reviewed causes remain diagnostic explanations, without
adding a waiver.

Floors in [`level2.yaml`](../../../../src/dv/cfg/regress/level2.yaml),
checked by every `--coverage` run that selects level 2 (the nightly runs
levels 0 to 2): line 97.4, toggle 64.4, functional 100. Merged over levels
0 to 2 ([#294](https://github.com/amichai-bd/pcie-factory/issues/294)):
line 97.92% (1038 of 1060 points), toggle 64.94% (25020 of 38528),
functional 100% (148 of 148 bins in the six covergroups the level file
names). The line and toggle floors are the merged values less 0.5 points,
rounded down to a tenth ([floor margin](../../../bkm/dv.md#coverage)).

Verilator keeps one copy of each block per parameter set, so the six
parameter sets of the entries count each block up to six times. The 22
line points not hit, by cause:

| Cause | Points | Where |
| --- | ---: | --- |
| Hit in another parameter set | 22 | `bar2_map_64` is the only entry of its parameter set (`MSIX_TABLE_SIZE` 64 alone) and makes BAR2 accesses only: no configuration write reaches the write arm of any register ([`pcie_cfg_regs.sv`](../../../../src/rtl/cfg/pcie_cfg_regs.sv) lines 83 to 109, the bus and device capture at 112 and 113, the RW1C clears at 122 and 124), no slot power event (line 155), and no configuration read (the read register's enable, [`pcie_cfg.sv`](../../../../src/rtl/cfg/pcie_cfg.sv) line 134): 20 points. `regmap_params` and `enumeration_params`, which share a copy, write no PowerState (line 93) and send no slot power event (line 155): 2 points. Each arm is hit in the default copy and does not depend on the parameters. |

The 13508 toggle points not hit, by cause:

| Cause | Points | Signals |
| --- | ---: | --- |
| Hit in another parameter set | 5522 | The same bit and direction toggles in another parameter set's copy (for example, the registers of `bar2_map_64`'s copy, which no configuration write reaches, and the table address bits above a smaller table). |
| Constant by construction: bits outside a register's writable mask | 7770 | Each header and capability register is held in a 32-bit flop whose bits outside its writable mask (`RW_*` in [`pcie_cfg_pkg.sv`](../../../../src/rtl/cfg/pcie_cfg_pkg.sv)) never change ([REGISTERS.md](../../arch/cfg/REGISTERS.md), [CAPS.md](../../arch/cfg/CAPS.md)): the `*_q` and `*_d` registers, the write masks and RW1C clears, BAR0 and BAR2 bits 11:0 (`bar*_base_o`), MSI and MSI-X address bits 1:0 and their copies in the offer path (`msi_addr`, `addr_lo`, `msg_addr_o`). Checked bit by bit against the masks. |
| The MSI-X table RAM | 192 | Word 1 of the one-vector build's two-word RAM (`MSIX_TABLE_SIZE` 1), above the table, never written ([HAS, Reset](../../arch/cfg/HAS.md#reset)). |
| Constant nets | 22 | `err_sts.sse` and `sts_set_o.sse` (Signaled System Error is set from `sse_set`, not from the error block) and the `COMMON_UNUSED_BITS` sink `unused_clr`. |
| Not reached | 2 | `app_bit[61]` in the 64-vector copy of `pcie_cfg_msg`: no 64-vector entry's coverage run requests vector 61 (random vectors). One bit of the request decode: the `cfg_irq` bins hold and the 64-vector entries request other vectors, so another coverage seed is not worth its cost. |

How the toggle points were sorted, so a re-measurement reproduces the
counts: a point is "hit in another parameter set" when the same file,
hierarchy, signal bit and direction is hit in any copy. The rest go by
signal name: the RAM row for `common_sdp_ram.sv`; the mask row for the
register flops (`cmd`, `cls`, `bar0`, `bar2`, `intline`, `pmcsr`, `msictl`,
`msilo`, `msihi`, `msidat`, `msix`, `devctl`, `lnkctl`, `devctl2`, each
`_q` and `_d`), `pm_rw`, `clr_*`, `sts_hdr`, `sts_dev`, `bar*_base`,
`msi_addr`, `addr_lo`, `msg_addr_o` and `.addr`; the constant row for
`.sse` and `unused`.
