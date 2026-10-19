# pcie_rc DV plan

How [`pcie_rc`](../../arch/rc/HAS.md) is verified: a smoke test of the
stack alone, and the back-to-back e2e against the endpoint `core` over
the full `phy` stack, where `rc_seq`'s programs alone drive the host side
(goal items 4 to 8 of [0005](../../decisions/0005-m4-grill.md#goal)).
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
  (1024); `core` at its defaults, which the programs assume.
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
  (`core_e2e_*`) through the reused model, which only per-covergroup floors
  should hold ([#380](https://github.com/amichai-bd/pcie-factory/issues/380)).

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
delay returns to 0, renumbering and Ack translation round-trip.

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
| Goal item 5, [MMIO](../../rtl/rc_seq/PROGRAMS.md#mmio) (`MMIO`), with the two reads' completions split on the line; `rc_seq` checks the data, the shadow the reassembly | `e2e_mmio` / `e2e_mmio_sweep` | 1 / 2 | 1 / 3 |
| Goal item 6, [DMA and MSI](../../rtl/rc_seq/PROGRAMS.md#dma-and-msi) (`DMA_MSI`): one doorbell, 64 bytes each way, the MSI captured | `e2e_dma_msi` / `e2e_dma_msi_sweep` | 1 / 2 | 1 / 3 |
| Goal item 7, [errors and recovery](../../rtl/rc_seq/PROGRAMS.md#errors-and-recovery) (`ERRORS`, `CPL_TIMEOUT_DIV` 64): Unsupported Requests (bits 0, 7, 8), outbound refusals, an inbound Unsupported Request (bit 11), the MSI past a full capture (bit 12), completion timeout (bit 3), Hot Reset and a link down (the line cut during R12), each followed by re-enumeration and traffic | `e2e_errors` / `e2e_errors_sweep` | 1 / 2 | 1 / 3 |
| Goal item 7, [injected errors](../../rtl/rc_seq/PROGRAMS.md#injected-errors) (`INJECT`): Completer Abort (bit 1), poisoned (5), malformed (6) and unexpected (4) completions, ERR_FATAL from a malformed request toward `core` (9); the shadow reports exactly the three endpoint-side rule breaks | `e2e_inject` / `e2e_inject_sweep` | 1 / 2 | 1 / 3 |
| Goal item 7, link errors with replay: `MMIO` with the injector on both directions (LCRC errors, corrupted and dropped TLPs and DLLPs, from link-up); at least three TLPs hit, `dl_err_o` pulses, no link down, the run passes and the shadow sees every TLP once and in order | `e2e_link_errors` / `e2e_link_errors_sweep` | 1 / 2 | 1 / 4 |

**Level placement.** Every program runs in a few seconds of wall time at
`TIMEOUT_DIV` and `ERR_TIMEOUT_DIV` 1024 (`ENUM` about 13 000 cycles,
`ERRORS` about 90 000), so every acceptance case is level 1; level 2
holds seed sweeps. **ERRORS:** the cut line brings both ends to Detect one
floored Recovery timeout later (16384 cycles), which must fall inside
`rc_tl`'s completion timeout or R12's outstanding read ends `TIMEOUT`;
its entries set `CPL_TIMEOUT_DIV` 64 (32768 cycles), the
[programs page](../../rtl/rc_seq/PROGRAMS.md#boundary) records the rule.

**Mutants** (#377, one per [Q2](../../arch/rc/HAS.md#q2-observability)
area and the integration; each a deliberate RTL bug, run serially, the
entry's first seed; none committed):

| Mutant | Entry | Fails |
| --- | --- | --- |
| Enumeration: `rc_tl` configuration requests lose register bit 2 | `e2e_enum` | Shadow: a `CfgRd0` no pending command asks for. |
| MMIO: `rc_tl` does not advance a split read's next byte | `e2e_mmio` | Shadow: `MEM_RD` result `BAD`, predicted `OK` (only the split completions reach it). |
| DMA: the completer skips a host-memory DW | `e2e_dma_msi` | Shadow: completion data differs from host memory. |
| MSI: the capture offset off by one | `e2e_dma_msi` | Shadow: MSI capture offset 1, predicted 0. |
| Errors: error record bits 0 and 1 swapped | `e2e_errors` | Shadow: `ERR_RD` record 0x0182. |
| MSI drop: the capture drops at 3 held | `e2e_errors` | Shadow: bit 12 set before the fifth MSI. |
| Poisoned completion accepted | `e2e_inject` | `rc_seq`: J4 fails (`OK`, expected `BAD`). |
| Integration: `link` Hot Reset request tied to 0 in `pcie_rc` | `e2e_errors` | `rc_seq`: R10's `HOT_RESET` times out. |

## Gaps

| Feature | Owning issue |
| --- | --- |
| Board build: `rc` and `core` with the GPIO adapter | [#378](https://github.com/amichai-bd/pcie-factory/issues/378) |
| FPGA synthesis of `rc` (`ci-fpga` has no `rc` target until `src/fpga/rc/`) | [#379](https://github.com/amichai-bd/pcie-factory/issues/379) |
| Coverage floors | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |
| `regress --affected` does not select `rc` for a change to the core e2e's `tb/core_e2e_env.py` alone, which the rc e2e imports (the import scan follows `models/` and `arch/` folders only); a change to `core` RTL or its host models does select it | [#357](https://github.com/amichai-bd/pcie-factory/issues/357): a follow-up issue |
