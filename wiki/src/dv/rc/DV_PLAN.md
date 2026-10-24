# pcie_rc DV plan

How [`pcie_rc`](../../arch/rc/HAS.md) is verified: a smoke test of the
stack alone, and the back-to-back e2e against the endpoint `core` over
the full `phy` stack, where `rc_seq`'s programs alone drive the host side
(goal items 4 to 8 of [0005](../../decisions/0005-m4-grill.md#goal)),
and the same pair as the board build over the GPIO adapter (goal item 9).
`rc_tl` and `rc_seq` have their own testbenches
([`rc_tl`](../rc_tl/DV_PLAN.md), [`rc_seq`](../rc_seq/DV_PLAN.md)).

## Strategy

- **Smoke:** [`tests/test_rc.py`](../../../../src/dv/rc/tests/test_rc.py)
  drives `pcie_rc` alone, its receive line random noise: every output at
  its [reset value](../../arch/rc/HAS.md#clocks-and-reset) while `rst_i`
  is 1; out of reset, with `start_i` 0, no run result and no link.
- **E2e top:** [`pcie_rc_e2e_tb`](../../../../src/dv/rc/tb/pcie_rc_e2e_tb.sv)
  holds `pcie_rc` (`u_rc`) and `pcie_core` (`u_ep`) on one clock, each
  `ser_rx_clk_i` the clock, each line direction (`dn` rc to ep, `up` ep
  to rc) one register. `rc.dv.f` reaches `core.rtl.f` for it. Parameters:
  `PROGRAM`, the credits (both ends), `TIMEOUT_DIV` and `ERR_TIMEOUT_DIV`
  1024 (both links; a cut line goes down in 16384 cycles),
  `CPL_TIMEOUT_DIV` (rc_tl, 1024: 2048 cycles) and `EP_CPL_TIMEOUT_DIV`
  (1024); `core` at its defaults, which the programs assume. `LOWER`
  (both stacks) and `GPIO_CLK_DIV` select the board build: at `LOWER` 1
  each stack's `gpio_rx_*` are the other's `gpio_tx_*` (zero-delay
  modelled wires on the one clock, [`link` lower layer](../../arch/link/HAS.md#lower-layer)),
  the serial lines stay idle and the line models do not apply.
- **Host side:** `rc` alone. `start_i` is held at 1 from reset, so
  `rc_seq` runs its program once ([sequencer run](../../arch/rc/HAS.md#sequencer-run));
  every test ends when `done_o` rises and requires `pass_o`.
- **Host model shadow** (goal item 8): `HostShadow` of the core DV
  ([HOST shadow mode](../core/HOST.md#shadow-mode)), rebuilt on reset with
  `rc`'s parameters, sees the [nets the `rc` MAS names](../../rtl/rc/MAS.md#nets)
  in the HOST.md call order: every TLP `rc_tl` gives `link` and every TLP
  `link` delivers (both directions of the root-port link), every command,
  result beat and MSI capture. It is strict (any finding fails the test)
  but in `e2e_inject`, where the injected errors are the endpoint-side
  findings the test expects, each named, and any other finding fails. The
  bench sets the four options the `rc_tl` MAS decides and the HAS leaves
  open: the completion timeout window (N = ⌊2^21 / `CPL_TIMEOUT_DIV`⌋
  cycles from the request's `sop`; the shadow times from its last beat,
  so it gets N − 4 to N + 8), the MSI capture depth exactly 4 (D10: a
  drop is certain), and `LOCAL_UR` for a zero First DW BE on `CFG_RD`,
  `CFG_WR` and `MEM_WR` ([byte enables](../../rtl/rc_tl/REQUESTER.md#byte-enables)).
- **Endpoint side:** the core e2e's application model (`App` of
  [`core_e2e_env.py`](../../../../src/dv/core/tb/core_e2e_env.py)) with
  its stream checks, extended with the [program boundary](../../rtl/rc_seq/PROGRAMS.md#boundary)
  (`EpApp`, [`rc_e2e_env.py`](../../../../src/dv/rc/tb/rc_e2e_env.py)):
  BAR0 as memory; a write covering BAR0 0xF0C queues a doorbell (source
  0xF00, destination 0xF04, length 0xF08), served in order with no bound
  on the queue (so D + 1 = 5 wait in R8): a bus-master read, then, if it
  completed successfully, the write and, once the write is taken, MSI
  vector 0; a read of 0xF10 never answered. The `cfg` architecture-model
  shadow (`CfgShadow`) sees every configuration request `rc` sends and
  every TLP it receives, the endpoint's `tl_err` (tapped, core MAS) and
  link error pulses; the core HAS reset values are checked every cycle.
- **Line models** (the tb's `mon_*_tx_o` and `*_sub_*`): a model reads a
  transmitter's word and gives the receiver its output one cycle later,
  the register's timing. The rewriter
  ([`pcie_rc_line_model.py`](../../../../src/dv/rc/models/pcie_rc_line_model.py))
  replaces chosen TLPs on the line by others of any number and length
  (see [line models](#line-models)); the `link` DV's error injector
  ([`pcie_link_injector_model.py`](../../../../src/dv/link/models/pcie_link_injector_model.py))
  corrupts and drops packets; a cut drives electrical idle both ways.
- **Functional coverage:** `rc_e2e_run` (programs passed; tl-seq ops by
  result status, crossed, with the pairs the tl-seq page rules out marked
  unreachable), `rc_e2e_line` (line rewrites, split counts, link down,
  Hot Reset, replay), `rc_e2e_err` (error record bits read set), `rc_shell`
  (smoke); the endpoint side also samples the core e2e's covergroups
  (`core_e2e_*`) through the reused model; `rc`'s floors name its own
  covergroups only ([coverage holes](#coverage-holes)).

### Line models

The rewriter decodes and descrambles its direction, holds each packet
until it is whole, asks the test for its fate, then sends logical idle
while it holds a packet and takes the delay back from later logical-idle
words, re-encoding with its own LFSR and running disparity, so the
receiver sees a clean line. When one TLP becomes several, every later TLP
of that direction is renumbered (new LCRC) and the receiver's Acks and
Naks are carried back to the transmitter's numbers in the other
direction (new CRC); a replay of a replaced TLP is replaced alike. Its
unit tests (`test_pcie_rc_line_model.py`) run a synthetic transmitter and
receiver: pass-through is bit for bit, replaced packets arrive clean, the
delay returns to 0, renumbering and Ack translation round-trip, and a
nullified TLP (EDB) or one with an invalid code keeps its fault while it is
renumbered: still an error at the receiver, never fixed
(`test_nullified_and_broken_tlps_keep_their_fault_while_renumbered`).

| Rewrite | Entry | What the line carries instead |
| --- | --- | --- |
| Split completions (goal item 5) | `e2e_mmio` | The 32-DW read's completion and the 4-DW read's (crossing 0x40) as two completions each, split on the 64-byte boundary, Byte Count and Lower Address of each. |
| Completer Abort | `e2e_inject` | J3's completion as a Completer Abort, no data. |
| Poisoned | `e2e_inject` | J4's completion with EP 1. |
| Malformed completion | `e2e_inject` | J5's completion with Byte Count 8 (4 remain). |
| Unexpected | `e2e_inject` | J6's completion, then a copy with the previous request's tag. |
| Malformed request (ERR_FATAL) | `e2e_inject` | J7's write toward `core` with Last DW BE 0xF on Length 1. |

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../arch/rc/HAS.md#clocks-and-reset); no run, no link against noise | `smoke` | 0 | 1; 512 cycles, two resets |
| Goal item 4, [enumeration](../../rtl/rc_seq/PROGRAMS.md#enumeration) (`ENUM`): Type 1 header, IDs, capability walk, every BAR sized and assigned, enables, MSI; the shadow learns the function, its BARs and MSI; the `cfg` shadow compares all 18 configuration reads | `e2e_enum` | 1 | 1 |
| Goal item 5, [MMIO](../../rtl/rc_seq/PROGRAMS.md#mmio) (`MMIO`), with the two reads' completions split on the line; `rc_seq` checks the data, the shadow the reassembly | `e2e_mmio` | 1 | 1 |
| Goal item 6, [DMA and MSI](../../rtl/rc_seq/PROGRAMS.md#dma-and-msi) (`DMA_MSI`): one doorbell, 64 bytes each way, the MSI captured | `e2e_dma_msi` | 1 | 1 |
| Goal item 7, [errors and recovery](../../rtl/rc_seq/PROGRAMS.md#errors-and-recovery) (`ERRORS`, `CPL_TIMEOUT_DIV` 64): Unsupported Requests (bits 0, 7, 8), outbound refusals, an inbound Unsupported Request (bit 11), the MSI past a full capture (bit 12), completion timeout (bit 3), Hot Reset and a link down (the line cut during R12), each followed by re-enumeration and traffic | `e2e_errors` / `e2e_errors_sweep` | 1 / 2 | 1 / 3 |
| Goal item 7, [injected errors](../../rtl/rc_seq/PROGRAMS.md#injected-errors) (`INJECT`): Completer Abort (bit 1), poisoned (5), malformed (6) and unexpected (4) completions, ERR_FATAL from a malformed request toward `core` (9); the shadow reports exactly the three endpoint-side rule breaks | `e2e_inject` | 1 | 1 |
| Goal item 7, link errors with replay: `MMIO` with the injector on both directions (LCRC errors, corrupted and dropped TLPs and DLLPs, from link-up); at least three TLPs hit, `dl_err_o` pulses, no link down, the run passes and the shadow sees every TLP once and in order | `e2e_link_errors` / `e2e_link_errors_sweep` | 1 / 2 | 1 / 4 |
| Goal item 9, the board build ([#378](https://github.com/amichai-bd/pcie-factory/issues/378)): `rc` and `core` both at `LOWER` 1 over the GPIO adapter, `ENUM` (as `e2e_enum`) and `MMIO` (enumeration, BAR0 writes and reads, nothing rewritten on the line), the host model shadow and the `cfg` shadow attached; error-free, no link down, both stacks up at `0x20` with their serial lines idle (the `core` and `rc` `LOWER` wiring) | `board_enum`, `board_mmio` | 1 | 1 each; `LOWER` 1 |

**Level placement.** Every program runs in a few seconds of wall time at
`TIMEOUT_DIV` and `ERR_TIMEOUT_DIV` 1024 (`ENUM` about 13 000 cycles,
`ERRORS` about 90 000), so every acceptance case is level 1; level 2
holds the error and replay seed sweeps. The board-build entries take 1.01 s (`board_enum`, `ENUM` about 7 800 cycles) and 1.07 s (`board_mmio`, `MMIO` 12 400 over the adapter) of CPU ([#406](https://github.com/amichai-bd/pcie-factory/pull/406) regression summary, "L1 CPU for #404"). **ERRORS:** the cut line brings both ends to Detect one
floored Recovery timeout later (16384 cycles), which must fall inside
`rc_tl`'s completion timeout or R12's outstanding read ends `TIMEOUT`;
its entries set `CPL_TIMEOUT_DIV` 64 (32768 cycles), the
[programs page](../../rtl/rc_seq/PROGRAMS.md#boundary) records the rule.

**Seed breadth (#461).** MMIO, DMA/MSI and injected-error seed sweeps are
removed; their level-1 tests retain the same testcases, top and parameters
(`PROGRAM` 1, 2 and 5), all directed checks and their original seeds
1767327733, 1521184950 and 2076889066. The retained first seeds caught M2,
M3/M4 and M7/M9 below. This reduces random traffic/timing breadth by nine
pinned runs and three nightly rotations; it does not make those scenarios
deterministic. In the [paired run](https://github.com/amichai-bd/pcie-factory/actions/runs/39449165457)
at `a80fc50`, their covered runs add no positive RTL instance points,
normalized source points or functional bins beyond the retained L0/L1
union. The three entries account for 38.498 CPU seconds in that run,
including rotations; this is a checkpoint measurement, not a projected
whole-suite saving. Keep `e2e_errors_sweep` and `e2e_link_errors_sweep`:
removing those too loses 130 instance points and 111 normalized source
points, despite retaining all functional bins. Coverage floors stay unchanged.

**Mutants** (#377, one per [Q2](../../arch/rc/HAS.md#q2-observability)
area and the integration; each a deliberate RTL bug, run serially, the
entry's first seed; none committed):

| Mutant | Entry | Fails |
| --- | --- | --- |
| M1 Enumeration: `rc_tl` configuration requests lose register bit 2 | `e2e_enum` | Shadow: a `CfgRd0` no pending command asks for. |
| M2 MMIO: `rc_tl` does not advance a split read's next byte | `e2e_mmio` | Shadow: `MEM_RD` result `BAD`, predicted `OK` (only the split completions reach it). |
| M3 DMA: the completer skips a host-memory DW | `e2e_dma_msi` | Shadow: completion data differs from host memory. |
| M4 MSI: the capture offset off by one | `e2e_dma_msi` | Shadow: MSI capture offset 1, predicted 0. |
| M5 Errors: error record bits 0 and 1 swapped | `e2e_errors` | Shadow: `ERR_RD` record 0x0182. |
| M6 MSI drop: the capture drops at 3 held | `e2e_errors` | Shadow: bit 12 set before the fifth MSI. |
| M7 Poisoned completion accepted | `e2e_inject` | `rc_seq`: J4 fails (`OK`, expected `BAD`). |
| M8 Integration: `link` Hot Reset request tied to 0 in `pcie_rc` | `e2e_errors` | `rc_seq`: R10's `HOT_RESET` times out. |
| M9 `INJECT`: the expected record `INJ_RECORD` 0x0270, not 0x0272 (J8 expects no bit 1, Completer Abort) | `e2e_inject` | `rc_seq`: step 77 fails (J8's error-record check), cycle 14527, seed 2076889066. |

**Board-build mutants** ([#378](https://github.com/amichai-bd/pcie-factory/issues/378); the adapter's and `link`'s are in the [gpio](../gpio/DV_PLAN.md#mutants) and [link](../link/DV_PLAN.md#gpio-lower-layer) DV plans):

| Mutant | Entry | Fails |
| --- | --- | --- |
| M27 `pcie_core`: `LOWER` not passed to `link` | `board_enum` | Neither data link comes up: `ENUM` not done in 150 000 cycles. |
| M28 `pcie_core`: `gpio_rx_data_i` not connected to `link` | `board_enum` | `rcv_err_o` at the endpoint at cycle 101 on the error-free line. |
| M29 `pcie_rc`: `LOWER` not passed to `link` | `board_enum` | The same. |
| M30 `pcie_rc`: `gpio_tx_ctrl_o` not driven from `link` | `board_enum` | The same. |

## Gaps

| Feature | Owning issue |
| --- | --- |
| FPGA synthesis of `rc` (`ci-fpga` has no `rc` target until `src/fpga/rc/`) | [#379](https://github.com/amichai-bd/pcie-factory/issues/379) |
| `regress --affected` does not select `rc` for a change to the core e2e's `tb/core_e2e_env.py` alone, which the rc e2e imports (the import scan follows `models/` and `arch/` folders only); a change to `core` RTL or its host models does select it | [#408](https://github.com/amichai-bd/pcie-factory/issues/408) |

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
| L0+L1+L2 | 1800/2153 (83.60%) | 26465/39464 (67.06%) | 83.1 / 66.5 |

### Per-instance diagnostic baseline

The counts and prior floor values below describe the original per-instance
view at the revisions cited. Current code floors use the source table above.
A point hit in another parameter set or instance is covered in the source
union; the other reviewed causes remain diagnostic explanations, without
adding a waiver.

Floors in [`level2.yaml`](../../../../src/dv/rc/regress/level2.yaml),
checked by every `--coverage` run that selects level 2 (the nightly runs
levels 0 to 2): line 72.8, toggle 45.3 and, per covergroup, `rc_e2e_line`
100, `rc_shell` 100, `rc_e2e_err` 84.1 and `rc_e2e_run` 63.4. Merged over
levels 0 to 2 ([#380](https://github.com/amichai-bd/pcie-factory/issues/380),
re-measured in [#426](https://github.com/amichai-bd/pcie-factory/issues/426)), over what `rc.rtl.f` reaches (`rc_tl`, `rc_seq`, `pcie_rc` and the whole
`link` stack, the `gpio` adapter included; `core` is a DV-only source
here): line 73.29% (3400 of 4639 points), toggle 45.88% (48923 of
106620). The code floors are the merged values less 0.5 points, rounded
down to a tenth ([floor margin](../../../bkm/dv.md#coverage)).

**Functional floors per covergroup.** The e2e reuses the core e2e's
endpoint model, which samples `core_e2e_*`, so the run's combined value
(61.1%, 85 of 139 bins) depends on `core`'s covergroups as the e2e happens
to reach them; those hold `core`'s floors in its own
[level file](../../../../src/dv/core/regress/level2.yaml), not `rc`'s.
`rc`'s floors name its four covergroups and leave the others without a
floor ([regress SPEC](../../../tools/regress/SPEC.md#coverage-floors)).
`rc_e2e_line` (10 of 10) and `rc_shell` (7 of 7) hold every bin;
`rc_e2e_err` (11 of 13, 84.6%) and `rc_e2e_run` (39 of 61, 63.9%) are
floored at the merged value less 0.5 points, below which any lost bin
falls. Their bins not hit:

| Covergroup | Bins | Why |
| --- | --- | --- |
| `rc_e2e_err` | `b2`, `b10` | Error record bits that `core` never causes: bit 2, a configuration completion with CRS, and bit 10, a message other than the ones `rc_tl` handles (dropped); the [`rc_tl` DV](../rc_tl/DV_PLAN.md) sends both. |
| `rc_e2e_run` | `status` `CRS` and the pairs `CFG_RD` with `BAD`, `CA`, `CRS`, `LINK_DOWN`, `TIMEOUT`, `UR`; `CFG_WR` with `BAD`, `CA`, `CRS`, `LINK_DOWN`, `LOCAL_UR`, `TIMEOUT`, `UR`; `LOCAL_UR` of `ERR_CLR`, `ERR_RD`, `HDR_RD`, `HDR_WR`, `HOST_RD`, `HOST_WR`; `MEM_WR` with `LINK_DOWN`, `LOCAL_UR` (22 bins) | Reachable, but no M4 program draws them: a configuration request failing, a local command refused, a `MEM_WR` refused or cut by a link down. The programs are the goal items of [0005](../../decisions/0005-m4-grill.md#goal), fixed in `rc_seq`'s ROM; `rc_tl`'s `rc_tl_cmd` and `rc_tl_req` cover every pair at its own ports. A new program that draws one raises the floor. |

**Code points.** Each point not hit was looked up in the merged coverage
of the owning IP's own regression, levels 0 to 2 with `--coverage`, on the
same tree (`rc_tl`, `rc_seq`, `gpio`, `link`, `dll`, `mac`, `pcs`, `phy`
and the shared unit `common`). The 1239 line points not hit, by cause:

| Cause | Points | Where |
| --- | ---: | --- |
| Hit in another parameter set | 614 | The same point is hit in another copy of its block (the entries' `PROGRAM` and `LOWER` builds, the `CPL_TIMEOUT_DIV` 64 build, and the endpoint's `link` stack in `core`, which shares these files): `rc_seq` 148, `dll` 137, `mac` 130, `rc_tl` 130, the common blocks 48, `pcs` 21. |
| Reached by the stacked IP's own DV | 500 | `mac` 231, `pcs` 60, `rc_seq` 60, `dll` 47, `gpio` 45, `rc_tl` 32, the common blocks 25: behaviour two compliant stacks on an error-free or injected line do not produce (the LTSSM's error, timeout and compliance exits, the power states `link` ties off, decoder and elastic-buffer extremes, as in the [`link` holes](../link/DV_PLAN.md#coverage-holes)), the `rc_tl` checks `core` never trips, `rc_seq`'s failure paths (every e2e program passes), and the adapter's error and restart branches. |
| Not hit by the stacked IP's own DV either | 125 | The holes of the unit plans: `rc_seq` 72 (elaboration-only package functions, [`rc_seq` holes](../rc_seq/DV_PLAN.md#coverage-holes)), `mac` 44 (elaboration-only functions, [`mac` holes](../mac/DV_PLAN.md#coverage-holes)), `pcs` 4, `dll` 2 (`noroom`), `rc_tl` 2 ([`rc_tl` holes](../rc_tl/DV_PLAN.md#coverage-holes)), `gpio` 1 ([`gpio` holes](../gpio/DV_PLAN.md#coverage-holes)). |

The 57697 toggle points not hit, by cause:

| Cause | Points | Signals |
| --- | ---: | --- |
| Hit in another parameter set | 25442 | `rc_tl` 7189, `dll` 6751, the common blocks 4637, `rc_seq` 2929, `mac` 1771, `pcie_rc.sv` 1396, `pcie_link.sv` 515, `pcs` 155, `phy` 99. |
| Reached by the stacked IP's own DV | 20416 | `rc_tl` 11613, `dll` 3757, `mac` 2304, `rc_seq` 1494, `pcs` 550, the common blocks 331, `gpio` 223, `pcie_link.sv` 108, `phy` 36: data, address and header patterns, error and malformed paths, credit and queue extremes and parameter values the programs and `core` do not produce. |
| Not hit by the stacked IP's own DV either | 9813 | `dll` 3164, `rc_tl` 2903, `rc_seq` 2820, `mac` 580, `pcs` 130, `pcie_link.sv` 121, `gpio` 47, the common blocks 42, `phy` 6: the causes of each unit plan ([`rc_tl`](../rc_tl/DV_PLAN.md#coverage-holes), [`rc_seq`](../rc_seq/DV_PLAN.md#coverage-holes), [`gpio`](../gpio/DV_PLAN.md#coverage-holes), [`dll`](../dll/DV_PLAN.md#coverage-holes), [`link`](../link/DV_PLAN.md#coverage-holes)): constant header fields and register bits, error-vector slices, step fields, counter top bits, release ports tied off. |
| Points the owner's builds do not have | 298 | The common blocks 294 and `dll` 4: bits of parameterizations only this stack builds (wider or deeper RAM queues and CDC FIFOs), at the same causes as their narrower copies: fill extremes and constant bits. |
| `pcie_rc.sv`'s own nets | 1728 | The MSI data and offset the programs configure (656); `fail_step_o` (256: every e2e program passes; the failure paths are `rc_seq`'s DV); the step fields `cmd.addr` and `cmd.len`, constant over the programs (288); credits available above the advertisement and the release data bits of 1-DW requests (448); `dl_err_o` bits 3 to 6, which do not pulse on these lines (64; reaching them costs a line model that holds a TLP lost past four replay timeouts, acknowledges an unsent sequence number, overruns the advertised credits or sends a good-LCRC malformed TLP, and would re-check only the straight wiring from the `link` that bits 0 to 2 exercise; the `dll` DV checks each event at its own ports, in `rollover_directed`, `replay_directed` and `rx_directed`); `done_o` and `pass_o` never falling within a run (16). |

How the points were sorted, so a re-measurement reproduces the counts: a
line point is keyed by file, line, column and kind, a toggle point by
file, signal bit and direction (any hierarchy and copy). A point not hit
is "another parameter set" when its key is hit anywhere in the `rc` run;
otherwise "reached by the stacked IP's own DV" when it is hit in the
merged coverage of the IP that owns the file (the shared unit for common
blocks), "not hit either" when it is a point there but not hit, and "the
owner's builds do not have" when that run has no such point.
