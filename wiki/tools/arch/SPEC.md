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
| `--ip ID` | A top-level IP (a direct child of the source root) with an `arch/` directory. Required. |
| `--scenario NAME` | Run only `arch/perf/NAME.yaml`. Default: every scenario, sorted by name. |

Exit codes: `0` every scenario passed, `1` a scenario failed or errored,
`2` a usage or configuration error (unknown IP, a sub-IP, no `arch/`, no or
several models, a model that cannot be imported or has no `perf`, no
scenarios, an unknown `--scenario`, an invalid scenario file).

## Model

`src/<ip>/arch/` holds exactly one model module whose file name ends in
`<name>_arch.py` (`<name>` is the IP directory name; the prefix comes from
[`src/README.md`](../../../src/README.md)) and does not start with
`test_`. The tool imports it by path with `arch/` first on `sys.path`, so it
may import sibling modules by bare name, and calls:

```python
perf(workload: dict, seed: int) -> dict[str, int | float]
```

once per scenario, with a copy of the scenario's `workload` and its `seed`.
The model validates its own workload (raising on a bad one). Metrics are
finite numbers, never booleans.

## Scenarios: `arch/perf/<name>.yaml`

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
named `arch` (`RESERVED_TEST_NAMES` in `tools/build/ip.py`), and `arch` is
never a sub-IP directory ([IP discovery](../build/SPEC.md#ip-discovery)).

## Callers

- `./build ci unit` runs it as the `perf:<ip>` suite of `ci-gate`
  ([CI spec](../ci/SPEC.md#build-ci-unit)).
- `./build check` requires `src/<ip>/arch/` with a model and a scenario when
  the IP's HAS has a `## Targets` section, and rejects `arch/` under a sub-IP
  ([checks spec](../checks/SPEC.md#wiki-mirror-check)).

## Tests

`tools/arch/tests/test_arch_perf.py`, on a synthetic tree (IP `blk` with
sub-IP `blk/sub`) in a temporary directory: a passing run and its
`perf.json`, byte-identical reruns, a missed target, model exceptions and
bad return values, a target on an unreported metric, `--scenario`
selection and an unknown name, every malformed scenario case, a bad
scenario name, a sub-IP, model lookup (none, two, tests ignored), a
missing `arch/` or scenario directory, an import failure, a model without
`perf`, sibling imports (and `sys.path` restored), and the command line
exit codes.
