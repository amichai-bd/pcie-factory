# pcie_mac architecture model and link partner

[`pcie_mac_arch.py`](../../../../src/arch/mac/pcie_mac_arch.py) states the
[`pcie_mac` HAS](HAS.md) as a cycle model at the PIPE MAC side
([arch BKM](../../../bkm/arch.md)), and is the DV link partner (decision
record [0002](../../decisions/0002-m1-grill.md), Q4). It follows the
[LTSSM](LTSSM.md), [arcs](ARCS.md) and [symbols](SYMBOLS.md) pages; where
they and the model differ, the pages win and the model is fixed.

## Structure

- `Mac`: one MAC, stepped once per `clk_i` cycle (`step(PipeRx) -> PipeTx`),
  with `port_role`, the four [parameters](HAS.md#parameters), the
  upper-layer requests as attributes, a packet queue each way, and a log of
  every arc taken. The upper interface is packet-level, not beat-level.
- `Link`: two ideal PCS and a channel between two PIPE MAC sides: receiver
  detection, PowerDown acknowledgement, loopback, polarity (an inverted
  wire inverts data bytes) and symbol errors; no elastic buffer (the `phy`
  model has it). `Pair` runs two `Mac` back to back.
- `MacBudget`: the latencies and SKP interval the `phy` model's `Stack`
  takes; the self-test plugs it into
  [`pcie_phy_arch.py`](../../../../src/arch/phy/pcie_phy_arch.py) and checks
  the stack targets. The `phy` model's reduced LTSSM is replaced by `Mac`
  with the stack integration ([#86](https://github.com/amichai-bd/pcie-factory/issues/86)).
- `ARCS`: the [arc list](ARCS.md). Self-tests fail if the arcs page, the
  [encoding table](LTSSM.md#state-encoding) or the codes in `pcie_mac_pkg`
  differ from the model.

## Injection hooks

| Hook | On | Effect |
| --- | --- | --- |
| `mute` | `Mac.inject` | Transmitter held in electrical idle in every state. |
| `freeze` | `Mac.inject` | No arc taken; the state's transmission continues and counters run. |
| `ts` | `Mac.inject` | Fields forced into every TS sent: kind, link, lane, N_FTS, control bits. |
| `ts_script` | `Mac.inject` | A sequence of such field sets, one per TS sent, before `ts` applies again; with `freeze`, an exact TS sequence. |
| `fts_extra` | `Mac.inject` | FTS sent beyond the partner's N_FTS on an L0s exit. |
| `skp_adjust` | `Mac.inject` | One SKP more or less in the next SKP ordered set: later packets start in `[15:8]`. |
| `no_eios` | `Mac.inject` | Enter electrical idle without an EIOS. |
| `idle_to_rlock` | `Mac.inject` | Preset the counter (device side; see [below](#partner-only-recipes)). |
| `present`, `invert`, `corrupt`, `status` | `Pcs` | Receiver termination, inverted wire, symbol errors, RxStatus codes. |

## DV use

The RTL tests import the model, as the arch BKM allows
([DV and arch](../../../bkm/arch.md#dv-and-arch)): a test appends
`src/arch/mac` (found from its own path) to `sys.path` and imports
`pcie_mac_arch`, as the [smoke test](../../../../src/dv/mac/tests/test_mac.py)
does. Each cycle it samples the RTL's PIPE outputs into a `PipeTx`, steps
`Link` with the partner's `PipeTx`, and drives the RTL's PIPE inputs from
the returned `PipeRx`. The sweep module needs the model bound with
`pcie_mac_sweep.use(pcie_mac_arch)`, because the model imports the sweep
(`./build arch` restores `sys.path` after loading the model).

## Arc sweep

[`pcie_mac_sweep.py`](../../../../src/arch/mac/pcie_mac_sweep.py) runs
directed episodes on two models: `a` is the device, `b` the partner. An
episode steers `a` only through `b`'s requests and hooks, the `Link` hooks
and `a`'s own upper-layer inputs, reacting to `a`'s state as a test reads
`ltssm_state_o`, so each episode is a recipe the RTL tests replay against
the partner ([#77](https://github.com/amichai-bd/pcie-factory/issues/77),
[#78](https://github.com/amichai-bd/pcie-factory/issues/78)). With `a` in
each port role the sweep reaches **64 of 64 arcs**; no arc is out of reach
in either role.

| Run | Where | Covers |
| --- | --- | --- |
| Smoke: training, traffic, L0s, retrain, L1, L2, Hot Reset, Disabled, Loopback, each role | `arc_smoke` perf scenario, every `ci-gate` that touches `src/arch/mac` | 41 arcs per role, a regression floor |
| Full: every episode, each role | level-2 test `partner_arc_sweep` ([DV plan](../../dv/mac/DV_PLAN.md#plan)), nightly | 64 of 64 per role |

### Partner-only recipes

How the episodes reach the error arcs without touching the device. "Walk"
means the frozen partner plays a `ts_script` that takes the device through
Configuration two TS1 per arc (link 0 and lane PAD, then link 0 and lane 0
for the upstream device's second arc).

| Arcs | Recipe |
| --- | --- |
| `detect_active__detect_quiet` | Partner leaves reset late (no termination). |
| `polling_active__polling_compliance`, `polling_compliance__polling_active` | Partner `mute`, released once the device is in Polling.Compliance. |
| `polling_active__detect_quiet` | Partner TS with link 7 in Polling. |
| `polling_configuration__detect_quiet`, `cfg_linkwidth_start__detect_quiet` | Partner frozen in Polling.Active (TS1), or in Polling.Configuration (TS2). |
| `cfg_linkwidth_accept__detect_quiet`, `cfg_lanenum_wait__detect_quiet`, `cfg_lanenum_accept__detect_quiet`, `cfg_lanenum_accept__cfg_lanenum_wait` | When the device reaches Configuration.Linkwidth.Start: freeze the partner, walk the device to the state, then TS1 with PAD fields, or lane 1. |
| `cfg_complete__detect_quiet`, `cfg_idle__recovery_rcvrlock` | Partner frozen in Configuration.Lanenum.Accept (TS1), or Complete (TS2). |
| `recovery_rcvrlock__cfg_linkwidth_start`, `recovery_rcvrlock__detect_quiet` | Retrain with partner TS link 3, or with the partner frozen in L0. |
| `recovery_rcvrcfg__cfg_linkwidth_start`, `recovery_rcvrcfg__detect_quiet` | Partner frozen in Recovery.RcvrLock (TS1); then TS1 with link 3 once the device is in RcvrCfg, or nothing more. |
| `recovery_idle__cfg_linkwidth_start`, `recovery_idle__recovery_rcvrlock` | Partner frozen in Recovery.RcvrCfg (TS2); then TS1 with lane PAD once the device is in Recovery.Idle, or nothing more. |
| `cfg_idle__detect_quiet`, `recovery_idle__detect_quiet` | The model sweep presets `idle_to_rlock` to 255. Partner-only: repeat the `..._recovery_rcvrlock` recipe 255 times without reaching L0 (Recovery.Idle times out, RcvrLock and RcvrCfg pass on the frozen partner's TS2), about 4.2 million cycles with both dividers at 1024: a level-2 test. |
| `loopback_entry__loopback_exit` | Partner frozen in Recovery.Idle while the device is the Loopback master. |
| `rx_l0s_fts__rx_off`, `rx_l0s_idle__rx_off` | Partner `fts_extra` 200; or a device retrain while the partner's transmitter is in L0s. |

## Perf scenarios

`./build arch --ip mac` writes `workdir/mac/arch/perf.json`.

| Scenario | Workload | Targets checked |
| --- | --- | --- |
| [`train_spec`](../../../../src/arch/mac/perf/train_spec.yaml) | Dividers 1, no skew | link-up 12.0 to 12.2 ms, training 65.536 to 80 µs |
| [`train_fast`](../../../../src/arch/mac/perf/train_fast.yaml) | Dividers 1024, skew up to 2000 cycles | link-up <= 100 µs, training 65.536 to 80 µs |
| [`retrain`](../../../../src/arch/mac/perf/retrain.yaml) | Retrain in L0 | recovery <= 4 µs |
| [`l0s`](../../../../src/arch/mac/perf/l0s.yaml) | L0s entry and exit, N_FTS 32 | entry <= 272, exit <= 80 cycles, lossless |
| [`l0s_max_fts`](../../../../src/arch/mac/perf/l0s_max_fts.yaml) | L0s exit, N_FTS 255 | exit <= 526 cycles, lossless |
| [`l1`](../../../../src/arch/mac/perf/l1.yaml), [`l2`](../../../../src/arch/mac/perf/l2.yaml) | L1 or L2 entry and exit | entry <= 64 cycles, exit <= 4 µs (L1), <= 100 µs (L2) |
| [`tlp_274`](../../../../src/arch/mac/perf/tlp_274.yaml), [`dllp`](../../../../src/arch/mac/perf/dllp.yaml) | Back-to-back TLPs or DLLPs | throughput >= 1.97, >= 1.49; lossless |
| [`mixed`](../../../../src/arch/mac/perf/mixed.yaml) | Random mix, 10% nullified, SKP every 1538 | lossless, no errors |
| [`arc_smoke`](../../../../src/arch/mac/perf/arc_smoke.yaml) | Smoke episode, each role | at least 41 arcs per role |
