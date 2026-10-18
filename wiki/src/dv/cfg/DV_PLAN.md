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
    offers are taken at once or held; random `tl_np_pending_i`;
  - an **application stand-in** on the [interrupt port](../../arch/core/APP.md#interrupts):
    one request at a time, held until taken, vectors below
    `MSIX_TABLE_SIZE`; and queued or random error-event cycles on
    `tl_err_i`, `dl_err_i` and `rcv_err_i`;
  - the **shadow**, the [architecture model](../../arch/cfg/HAS.md#architecture-model)
    `Cfg` with the DUT's parameters, given every edge in the
    [MAS edge order](../../rtl/cfg/MAS.md#top): a read sees the state
    before the edge; an offer the DUT raises at an edge is the model's
    `offer` taken before the edge's operations; then the take, the write,
    the events and the request.
- **Checks on every run, every cycle:** the offer (`msg_valid_o`, and
  while it is up its kind, and for an interrupt write its address and
  data) equals the model's, so an offer the model withdraws must fall and
  one it keeps must stay; `irq_done_o` pulses exactly when the model
  resolves a request, with its status; `irq_ready_o` is never 1 while a
  request is open; every register-state output equals the model's; each
  read's `reg_cpl_data_o` equals the model's read; `reg_cpl_valid_o` comes
  within 4 cycles of its take and never without an access, and a request
  is resolved within 4 cycles unless it is held (the latency
  [targets](../../arch/cfg/HAS.md#targets)); in and after reset every
  qualified output has its [reset value](../../arch/cfg/HAS.md#reset). The
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
  dropped), `cfg_power` (D0 to D3hot, D3hot to D0, D1 and D2 discarded).

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
| [Reset values](../../arch/cfg/HAS.md#reset), the handshake, IDs | `smoke` | 0 | 1 |
| Every [header](../../arch/cfg/REGISTERS.md#type-0-header) and [capability](../../arch/cfg/CAPS.md) field: reset value, RW by byte enable, RO and HwInit ignore writes, RW1C not set by a write; unlisted DWs and the extended space read 0; reads whole DWs whatever the byte enables; bus and device capture on every write; [BAR sizing](../../arch/cfg/REGISTERS.md#bar-sizing); Max_Payload_Size, Max_Read_Request_Size and Completion Timeout Disable on their outputs (shadow) | `regmap` | 1 | 1 |
| The same at the parameter boundaries (BAR0 256 MiB, 64 vectors, set IDs) | `regmap_params` | 1 | 1 |
| [Power states](../../arch/cfg/HAS.md#power-states): D3hot and D0 at the write's edge, D1 and D2 discarded, No_Soft_Reset; Transactions Pending | `power_states` | 1 | 1 |
| [BAR2](../../arch/cfg/CAPS.md#bar2): table fields by byte enable, address bits 1:0, mask reset 1, pending bits read-only, other offsets 0, table kept and masks reset by a reset | `bar2_map` | 1 | 1 |
| The same with 64 vectors (both pending-bit DWs) | `bar2_map_64` | 1 | 1 |
| [Targets](../../arch/cfg/HAS.md#targets) enumeration: IDs, capability walk, BAR sizing and assignment, enables, an MSI and an MSI-X interrupt (pending first) | `enumeration` | 1 | 1 |
| The same with other parameters (BAR0 1 MiB, one vector, set IDs) | `enumeration_params` | 1 | 2 |
| [Errors](../../arch/cfg/HAS.md#errors): every event under 11 enable sets, status bits, message decision, UR gate, Signaled System Error, RW1C by byte | `error_table` | 1 | 1 |
| [Messages](../../arch/cfg/HAS.md#messages): priority, merging, no message outside D0 (each event), an offered error withdrawn by D3hot | `error_messages` | 1 | 1 |
| [Interrupts](../../arch/cfg/HAS.md#interrupts) under MSI: dropped with no mode, 32- and 64-bit writes, pending while not allowed and merged, MSI Enable clears the flag, D3hot | `irq_msi` | 1 | 1 |
| Interrupts under MSI-X: each vector's entry, vector and function masks, pending bits and their clearing on the take, lowest vector first, merging, MSI-X over MSI, pending kept while disabled, the entry read at the offer | `irq_msix` | 1 | 1 |
| [Withdrawal](../../arch/core/TL_CFG.md#withdrawal): by Bus Master Enable, D3hot, MSI Enable (to the flag, to the vector's bit, dropped); a mask after the offer does not withdraw; a waiting request turned pending | `irq_withdraw` | 1 | 1 |
| Everything at once under the shadow: random accesses, masks, enables, power states, events, requests, link-down resets, a stalling taker; nothing pending after the drain | `random_ops` | 1 | 3; 1500 operations |
| [Parameter](../../arch/cfg/HAS.md#parameters) limits | `param_limits` | 1 | 1 |
| The random test, longer | `random_long` | 2 | 4; 12000 operations |
| The random test with 64 vectors and BAR0 256 MiB | `random_long_64` | 2 | 2 |
| The random test with one vector | `random_long_1` | 2 | 2 |

**Level placement.** Level 0 holds the smoke test only. The `random_long`
entries are level 2 as soaks (longer runs of `random_ops`, which level 1
holds with three seeds).

## Mutation evidence

Each mutant is a deliberate bug in the RTL of
[#289](https://github.com/amichai-bd/pcie-factory/issues/289), run against
level 0 and level 1 one entry at a time (`--jobs 1`); the table gives the
first entry that fails.

| Mutant | Block | First failing entry | How |
| --- | --- | --- | --- |

## Gaps

| Feature | Owning issue |
| --- | --- |
| Captured Slot Power Limit capture from a Set_Slot_Power_Limit message | [#309](https://github.com/amichai-bd/pcie-factory/issues/309) |
| Enumeration end to end against the independent host model | [#293](https://github.com/amichai-bd/pcie-factory/issues/293) |
| Coverage floors | [#282](https://github.com/amichai-bd/pcie-factory/issues/282) (wave 3) |

## Coverage holes

Merged over levels 0 to 2 at the PR head: line 98.1%, functional 100%,
toggle 64.7%.

- **Line**, a tool artefact: the `COMMON_UNUSED_BITS` lint sink in
  `pcie_cfg_regs.sv` has no simulated activity.
- **Toggle**, by construction: the high bits of 64-bit addresses and
  32-bit table data the tests write with few patterns, constant parameter
  outputs of the testbench (`cfg_params_o`), unused RAM words above
  `MSIX_TABLE_SIZE` and the RAM's storage array.
