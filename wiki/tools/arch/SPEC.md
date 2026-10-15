# Arch tool: `./build arch`

Runs a top-level IP's architecture-model perf scenarios and writes the
results to `perf.json`. The [arch BKM](../../bkm/arch.md) owns the method
(what a model is, the layout, DV's use of it); this page owns the command,
the model and scenario contracts and the output. Code: `tools/arch/`
(`perf.py`, `cli.py`).

```
./build arch [--src-root DIR] [--workdir DIR] --ip ID [--scenario NAME]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | A top-level IP (one no other IP's `.rtl.f` reaches, [IP discovery](../build/SPEC.md#ip-discovery)) with a `src/arch/<ip>/` folder. Required. |
| `--scenario NAME` | Run only `src/arch/<ip>/perf/NAME.yaml`. Default: every scenario, sorted by name. |

Exit codes: `0` every scenario passed, `1` a scenario failed or errored,
`2` a usage or configuration error (unknown IP, a sub-IP, no `src/arch/<ip>/`, no
module prefix in `src/README.md`, no model, a model that cannot be imported or has no `perf`, no
scenarios, an unknown `--scenario`, an invalid scenario file).

## Model

`src/arch/<ip>/` holds the model module `<prefix><ip>_arch.py`, exactly
that name (`<ip>` is the IP id; the prefix comes from
[`src/README.md`](../../../src/README.md)); other files, such as
`test_<prefix><ip>_arch.py` or a helper, are never the model. The tool imports it by path with `src/arch/<ip>/` first on `sys.path`, so it
may import sibling modules by bare name, and calls:

```python
perf(workload: dict, seed: int) -> dict[str, int | float]
```

once per scenario, with a copy of the scenario's `workload` and its `seed`.
The model validates its own workload (raising on a bad one). Metrics are
finite numbers, never booleans.

## Scenarios: `src/arch/<ip>/perf/<name>.yaml`

`<name>` matches `[a-z0-9][a-z0-9_]*`.

```yaml
description: Back-to-back stream, no bubbles.   # required, non-empty
seed: 1                                         # required, integer >= 0
workload: {bytes: 4096, valid_ratio: 1.0}       # required mapping, passed to perf()
targets:                                        # optional
  throughput_bytes_per_cycle: {min: 1.0}        # min and/or max, numbers
  latency_cycles_max: {max: 1}
```

Unknown keys, a missing required key, a wrong type, a target bound other
than `min`/`max`, or `min > max` is a configuration error naming the file.

## Behavior and output

For each scenario the tool calls `perf`, then checks every target:
`min <= value <= max` (a missing bound is unbounded). A scenario's status is:

| Status | Meaning |
| --- | --- |
| `pass` | Every target met. |
| `fail` | At least one target missed. |
| `error` | `perf` raised, returned something other than a mapping of names to finite numbers, or did not report a metric a target names; `detail` says which. |

Every scenario runs even after a failure. The tool prints one line per
scenario, each target with its bounds and status, and the other metrics,
then writes `<workdir>/<ip>/arch/perf.json` (replacing it):

```json
{
  "schema": 1, "ip": "<id>", "model": "<file name>", "status": "pass|fail",
  "scenarios": [
    {"name": "...", "description": "...", "seed": 1, "workload": {},
     "metrics": {"<metric>": 1.0},
     "targets": [{"metric": "<metric>", "min": 1.0, "value": 1.0, "status": "pass"}],
     "status": "pass|fail|error", "detail": "only on error"}
  ]
}
```

Keys are sorted and the file has no timestamps, so the same sources give
the same bytes. Because `arch/` lives in `workdir/<ip>/`, no test may be
named `arch` (`RESERVED_TEST_NAMES` in `tools/build/ip.py`).

## Import rule

Arch never imports DV, RTL or tools code ([arch BKM](../../bkm/arch.md#dv-and-arch)).
`tools/arch/imports.py` checks it statically for `./build check` (the
[wiki mirror check](../checks/SPEC.md#wiki-mirror-check)): it parses every
`*.py` under `src/arch/<ip>/` and reports each imported top-level module
(`import x.y`, `from x import y`, and `importlib.import_module` or
`__import__` with a constant name) that names

- `tools` or `src` themselves (`import tools.x`, `from src.dv... import`),
- a tools name importable at top level: `tools/<name>.py`, any folder
  `tools/<name>/` (a package or not), and the modules directly in a tool
  folder without `__init__.py`, which runs with that folder on `sys.path`
  (`tools/<tool>/<module>.py`),
- a Python module or package under any other kind folder (`src/<kind>/`
  other than `arch/`: DV models and tests, and any Python beside RTL or
  flow inputs), or
- a DV framework (`cocotb`, `pyuvm`),

unless the name is a sibling module in the same `src/arch/<ip>/` (which is
first on `sys.path`) or a standard-library module. A file that does not
parse is a finding too. Relative imports and `sys.path` edits are not
followed; review covers them.

## Callers

- `./build ci unit` runs it as the `perf:<ip>` suite of `ci-gate`
  ([CI spec](../ci/SPEC.md#build-ci-unit)).
- The nightly runs it for every IP with `src/arch/<ip>/`, and
  `./build metrics collect` keeps a whitelisted part of `perf.json` in the
  nightly record ([metrics spec](../metrics/SPEC.md#arch)).
- `./build check` requires `src/arch/<ip>/` with a model and a scenario when
  the IP's HAS has a `## Targets` section
  ([checks spec](../checks/SPEC.md#wiki-mirror-check)), and rejects
  `src/arch/<sub>/` for a sub-IP ([layout check](../checks/SPEC.md#layout-check)).

## Tests

`tools/arch/tests/test_arch_perf.py`, on a synthetic tree (IP `blk` with
flat sub-IP `blk_sub`, reached by `blk.rtl.f`) in a temporary directory: a passing run and its
`perf.json`, byte-identical reruns, a missed target, model exceptions and
bad return values, a target on an unreported metric, `--scenario`
selection and an unknown name, every malformed scenario case, a bad
scenario name, a sub-IP, model lookup (only the exact
`<prefix><ip>_arch.py`; tests, other prefixes and near names ignored; no
declared prefix), a
missing `src/arch/<ip>/` or scenario directory, an import failure, a model without
`perf`, sibling imports (and `sys.path` restored), and the command line
exit codes.
