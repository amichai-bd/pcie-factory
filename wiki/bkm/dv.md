# DV BKM

> Provenance: adapted from nand2anything `wiki/bkm/bkm_dv.md` (commit
> `085ef4b`, same owner, no license file). Generic Python/cocotb practice
> kept; project tests, targets and launchers dropped.

## Scope and oracle

This page owns repository-wide design-verification practice. An IP's
`wiki/src/arch/<ip>/HAS.md` (for a sub-IP, its `wiki/src/rtl/<ip>/MAS.md`)
defines expected behavior and its `wiki/src/dv/<ip>/DV_PLAN.md` maps features to tests and levels ([wiki BKM](wiki.md#page-set)). Oracles come from the spec,
never from the RTL structure. DV may import a top-level IP's architecture
model as a second reference; the architecture model never imports DV
([arch BKM](arch.md#dv-and-arch)).

- Verilator is the only simulator. Python with cocotb is the only DV
  language for stimulus, models, scoreboards and checks; the cocotb pin is in
  the host configuration (`cfg/`), not here.
- Drive and observe documented ports (and internal signals the MAS names for bind
  checkers). Never read Verilator-generated internals.
- Add infrastructure only when a real test needs it.

## Directory structure

Per IP and sub-IP, under `src/dv/<ip>/` (paths from the
[layout page](../project/layout.md)):

| Path | Holds |
| --- | --- |
| `<ip>.dv.f` | Simulation-only SystemVerilog: testbench tops, bind and assertion modules, SV models. Same `.f` rules as the [RTL BKM](rtl.md#filelists). |
| `<ip>.sim.f` | Exactly `-F ../../rtl/<ip>/<ip>.rtl.f` then `-F <ip>.dv.f`. Nothing else. |
| `tb/` | SV testbench tops and bind files (listed in `.dv.f`), and the Python environment: clock/reset helpers, drivers, monitors, scoreboards. |
| `tests/` | cocotb test modules, `test_<feature>.py`, each with one or more `@cocotb.test()` testcases. |
| `models/` | Pure-Python reference models, with pytest unit tests that run without a simulator. |
| `regress/level{0,1,2}.yaml` | This IP's regression entries per level. |

Shared DV helpers live in `src/dv/common/` once a second IP needs them.
Python imports use paths relative to the IP's `src/dv/<ip>/` folder; the sim tool
SPEC owns how `PYTHONPATH` is set.

### Reusing a sub-IP model

A parent IP's model reuses a sub-IP's model instead of re-implementing it:

- Model modules carry their IP's full name (`<prefix><ip>_<sub>_model.py`),
  so they never collide, and `models/` directories have no `__init__.py`.
- The parent's model appends the sibling `src/dv/<ip>_<sub>/models/`,
  located relative to its own file
  (`Path(__file__).resolve().parents[2] / "<ip>_<sub>" / "models"`), to
  `sys.path` and imports the sub-IP module by name. This works the same
  under the sim tool, which puts only the IP's own `src/dv/<ip>/` on the
  path, and under plain pytest.
- `regress --affected` follows this import by rule, not by tracing it: a
  change under a sub-IP's `models/` also selects every ancestor IP
  ([regress selection](../tools/regress/SPEC.md#selection)), and `ci-gate`
  runs the model unit tests of every selected IP
  ([`./build ci unit`](../tools/ci/SPEC.md#build-ci-unit)).

## Fast test ladder

Run the cheapest test that can reject the change:

1. Pure-Python model unit tests.
2. The focused sub-IP test for the changed behavior.
3. Boundary, error and reset cases of the changed block.
4. The smallest parent-IP test that covers the changed connections.
5. Wider regression only when the changed contract can affect it.

Every IP keeps focused sub-IP tests that localize failures; a slow
system-level test never replaces them.

## Regression lists

Each IP's `regress/level{0,1,2}.yaml` lists its own tests only. The
[regress tool SPEC](../tools/regress/SPEC.md#regression-lists) owns the
entry schema: fields, defaults, seeds (a count or an explicit list) and
validation.

- **Flat per IP.** A parent IP doesn't inherit its sub-IPs' entries; each
  sub-IP has its own lists. A global run is the union of all IPs.
- **Exclusive levels.** `--level 1` runs only `level1.yaml`. Combine
  explicitly: `--level 0 --level 1`. Never copy an entry into a second level
  to make it "also run".
- **All three files exist.** A level with no tests is `tests: []`.
- **One entry, one configuration.** Each entry is one simulation
  configuration, and its `name` is what reports, output paths and the DV
  plan refer to.

Level placement follows budgets in the [DevOps BKM](devops.md#levels-and-budgets):
L0 holds smoke and the fastest directed tests, L1 the full directed set and a
few random seeds, L2 long tests and wide seed sweeps.

## DV plan alignment

`wiki/src/dv/<ip>/DV_PLAN.md` holds a feature → test → level table.
Alignment is a review duty (no automation):

- Every planned row names an entry `name` and a level, and that entry exists
  in exactly that `regress/level<N>.yaml`.
- Every regression entry maps to at least one plan row.
- A feature with no test is listed with its owning issue, never silently
  omitted.

A PR that adds, moves or removes a regression entry updates the DV plan in
the same PR, and vice versa.

## Determinism and failure quality

- Directed tests are deterministic. Random tests take the seed that the
  regression tool passes to cocotb for each run (see its SPEC), print it at
  start, and draw all randomness from it. A failure is reproducible from
  `name` and seed (`./build sim --ip <ip> --test <name> --seed <seed>`).
- Every test bounds simulated cycles and wall time and fails nonzero.
- Mismatch messages report operation, expected value, observed value and the
  cycle or transaction index.
- Reference models favor obvious correctness over mirroring the RTL;
  scoreboards compare externally observable transactions or state.
- Assertions (bind modules, [RTL BKM](rtl.md#assertions)) check local
  invariants; the test owns the observable pass condition.
- Each test checks something it could fail on: include a negative or
  after-drain check where a pass could otherwise be vacuous.
- Warnings fail unless a reviewed waiver explains them.

## Test value

Each test protects an observable behavior or contract. Before adding one,
name the regression it would catch that existing tests miss. Remove or merge
a test only when the plan row it covers stays covered.

## Coverage

Coverage measures how much of the spec and the code the tests reach; it
never replaces a checker. `./build regress --coverage` merges three metrics
per IP: Verilator line and toggle coverage, and functional coverage from the
`regress.funcov` helper (the
[regress SPEC](../tools/regress/SPEC.md#coverage) owns the command, the
helper's API, outputs and floor rules).

- **Functional bins come from the spec.** Declare covergroups for the DV
  plan's features: control values, data classes, corner cases and the
  crosses the HAS or MAS says interact. Sample them in the test where the
  stimulus is driven or the outcome observed, so a bin means "this happened",
  never "this was attempted". Don't mirror RTL state you can't observe.
- **One covergroup per concern,** named after the IP and the concern, so
  merged results stay readable across tests.
- **Holes are findings.** An uncovered bin (`workdir/<ip>/coverage/functional.json`)
  or line (`verilator_coverage --annotate`) is either a missing test, an
  unreachable case to explain in the DV plan, or a bin to remove with a
  reason; never a bin to delete silently.
- **Floors are optional and per IP.** Add `coverage_floors` to a level file
  once the IP's DV plan has no gaps, at or just below the merged value that
  level's runs reach (usually in `level2.yaml`, which the nightly runs).
  Raise a floor when coverage improves; lowering one needs a reason in the
  PR, like a budget.
- Nightly runs L0+L1+L2 with `--coverage`; the tracker charts each IP's
  coverage over time ([DevOps BKM](devops.md#nightly)).

## Output and evidence

- Simulation builds, logs, results XML and waves go under
  `workdir/<ip>/<test>/<seed>/` (the sim tool SPEC owns the exact path).
  Waves are opt-in debug output.
- PR evidence records the exact command, seeds, pass/fail counts, runtime
  and any non-pass entry with its diagnosis, at the reviewed head. A PR that
  adds tests or covergroups, or sets a floor, also records the merged
  coverage table of a `--coverage` run. Never commit outputs.
- Levels to run per change and the merge-gate rule are in the
  [DevOps BKM](devops.md#local-evidence).
