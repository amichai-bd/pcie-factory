# Arch BKM

> Written for this repository; not adapted from another source.

## Scope

This page owns how a top-level IP's architecture is captured as Python
models in `src/arch/<ip>/`. An architecture model is for **definition and
performance analysis**, not verification: it makes the
[HAS](wiki.md#has-high-level-architecture-specification-what-and-why)
executable and measures workloads against its targets before, and
alongside, the RTL. The HAS stays the normative statement; the
[arch tool SPEC](../tools/arch/SPEC.md) owns the exact command line, file
formats and `perf.json`.

- **Top-level IPs only.** A sub-IP has no `src/arch/<ip>/`; its parent's
  model covers it (sub-IPs start at `wiki/src/rtl/<ip>/MAS.md`).
- **Required when the HAS defines targets.** A HAS with a `## Targets`
  section (any level-2 heading that starts with the word, such as
  `## Targets (draft)`; not inside fenced code) needs `src/arch/<ip>/` with a model and at least one scenario
  that reports against them. `./build check` enforces this
  ([checks SPEC](../tools/checks/SPEC.md#wiki-mirror-check)).
- **Written from the HAS**, not from the RTL or the DV model, so it is an
  independent statement of the intent.

## Layout

| Path | Holds |
| --- | --- |
| `src/arch/<ip>/<prefix><ip>_arch.py` | The model: the behavior as a port-level or transaction model, and `perf(workload, seed) -> {metric: number}`. Exactly one per IP; helpers beside it use the same prefix. |
| `src/arch/<ip>/test_<prefix><ip>_arch.py` | Self-tests (pytest, no simulator): the HAS behavior examples and that `perf` meets the targets. |
| `src/arch/<ip>/perf/<scenario>.yaml` | Perf scenarios: a description, a seed, the workload passed to `perf`, and `min`/`max` targets on the returned metrics. |

`src/arch/<ip>/` has no `__init__.py`; modules are imported by bare name, as in
`src/dv/<ip>/models/` ([DV BKM](dv.md#reusing-a-sub-ip-model)).

## Rules

- Python standard library, plus pinned dependencies from
  `tools/requirements.txt` only; no simulator.
- Deterministic: every random choice comes from `random.Random(seed)` with
  the scenario's recorded seed, so a scenario gives the same metrics every
  run.
- Metric names carry their unit (`throughput_bytes_per_cycle`,
  `latency_cycles_max`). A scenario's targets restate a HAS target as
  bounds; its comment links the HAS section, and a change to a HAS target
  changes the scenarios in the same PR.
- A behavior change updates the HAS, the model and the RTL together
  ([specs and code](wiki.md#specs-and-code-stay-aligned)).

## DV and arch

The dependency points one way: **DV may import arch, arch never imports
DV** (or RTL, or tools); `./build check` scans the imports of every Python
file in `src/arch/<ip>/` ([import rule](../tools/arch/SPEC.md#import-rule)). A DV model or test may use the architecture model
as a second reference, for example to cross-check the DV reference model,
by appending `src/arch/<ip>` (located from its own file) to `sys.path`. The
DV reference model stays the oracle for the RTL ([DV BKM](dv.md#scope-and-oracle)).

A change under `src/arch/<ip>/` selects that IP in `regress --affected` and
`./build ci unit` ([CI spec](../tools/ci/SPEC.md#selection-rules-cfgciyaml)).

## Evidence

`ci-gate` runs, for each selected IP, the self-tests (`arch:<ip>`) and the
perf scenarios (`perf:<ip>`, which is `./build arch --ip <ip>`) through
`./build ci unit`. Before review, a `src/arch/<ip>/` change records:

```text
./build check
python3 -m pytest src/arch/<ip>
./build arch --ip <ip>          # writes workdir/<ip>/arch/perf.json
```

The PR quotes each scenario's status and its target metrics. The nightly
runs `./build arch --ip <ip>` for every IP with `src/arch/<ip>/` (its `arch`
step); the nightly record keeps each scenario's status and target-metric
values ([metrics spec](../tools/metrics/SPEC.md#arch)), and the
[tracker](../tracker/index.md) charts them per IP and scenario next to the
FPGA and ASIC series, so a change in modeled throughput or latency shows
over time. Model code counts as its own code kind, `arch_py`.
