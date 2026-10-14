# Checks tool: `./build check`

`./build check` runs the static checks that the
[DevOps BKM](../../bkm/devops.md#static-checks) requires: the filelist and
regression-list check, the RTL style checker, the neutrality check and
Verilator lint. It prints one summary line per check, then every violation,
then an overall verdict. The code lives in `tools/checks/` (`run.py`,
`filelists.py`, `style.py`, `neutrality.py`, `lint.py`).

```
./build check [--ip ID]... [--only filelist|style|neutrality|lint]...
              [--src-root DIR] [--workdir DIR]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | Restrict filelist, style and lint to these IPs (repeatable). Files not owned by a selected IP, such as `src/common/`, are skipped. Neutrality always scans the whole repository. |
| `--only NAME` | Run only these checks (repeatable). The default is all four. |
| `--src-root DIR` | The source tree to check, as for every command ([build spec](../build/SPEC.md#command-line)). Neutrality ignores it and scans the git repository that holds the launcher. |

Checks run in the order filelist, style, neutrality, lint (lint is the
slowest). Output:

```
check: 2 IP(s) under /abs/src
[filelist] PASS: 7 filelist(s), 2 IP(s) with regression lists, 5 RTL file(s) for orphans
[style] FAIL (1): 6 RTL file(s), 11 rule(s)
  blk/rtl/xy_blk.sv:10: plain-always: always
[neutrality] PASS: 84 tracked file(s) outside src/, wiki/src/, wiki/tracker/data/; ...
[lint] blk: top=xy_blk
[lint] PASS: 2 IP(s), 3 Verilator run(s), 0 waiver(s), 0 warning(s) waived
check: FAIL (style)
```

Paths in findings are relative to the source root (neutrality: to the
repository root). With no IPs under `src/`, every check still runs and says
what it covered (zero IPs and files), and the command passes.

**Exit codes:** `0` means every selected check passed. `1` means at least
one check found a violation. `2` means a usage or configuration error: an
unknown `--ip`, an invalid `cfg/checks.yaml` or waiver file, a git error,
or Verilator missing while there is something to lint.

## Filelist check

Rule owners: [layout](../../project/layout.md#per-ip-files) and the
[RTL BKM](../../bkm/rtl.md#filelists).

- Every `*.f` below an `rtl/` or `dv/` directory under the source root
  resolves with the [build tool's resolver](../build/SPEC.md#filelists).
- No source is listed twice after flattening. Each filelist in the `-F` tree
  counts once (a diamond include is fine), so a duplicate is a source named
  by two filelists of the tree, or twice by one.
- Every IP has `rtl/<n>.rtl.f`, `dv/<n>.dv.f`, `dv/<n>.sim.f` and
  `dv/regress/level{0,1,2}.yaml`, and its regression lists pass the
  [regress schema](../regress/SPEC.md#regression-lists) (including unique
  test names).
- **No orphans:** every `.sv`/`.svh` below an `rtl/` directory is reached by a
  filelist in that same `rtl/` directory (for an IP, its `.rtl.f`; for
  `src/common/rtl/`, whichever filelist lives there). "Reached" is the
  resolver's `reaches()`: listed as a source or under an include directory.
  `fpga/` wrappers are not in any filelist and are not orphans.

`src/common/` is an IP only if it has `rtl/common.rtl.f`; a shared-block
list with another name (for example `common_lib.rtl.f`) is checked as a
filelist but needs no `dv/` files.

## Style check

The RTL style checker applies to every `.sv`/`.svh` below an `rtl/` or
`fpga/` directory. It is lexical: comments and strings are blanked first,
then each rule is a pattern or a bracket-aware scan of module headers. Rules
it cannot check reliably (widths, latches, one driver, reset values,
positional connections, logic in `always_ff`) stay
[review items](../../bkm/rtl.md#review-checklist).

| Rule | Enforces | Owner |
| --- | --- | --- |
| `default-nettype` | A `.sv` file's first statement is `` `default_nettype none`` and its last is `` `default_nettype wire``. `.svh` include files are exempt: restoring `wire` inside an include would undo `none` in the file that includes it. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `net-keyword` | No `wire`, `reg`, `tri`, `tri0`, `tri1`, `wand`, `wor`, `uwire` (the `default_nettype` directive is ignored). | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `plain-always` | Only `always_comb` and `always_ff`: no `always` (including `always @*`) and no `always_latch`. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `interface-union` | No `interface`, `modport` or `union`. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `wildcard-connection` | No `.*` port connections. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `sim-ifdef` | No `` `ifdef``/`` `ifndef``/`` `elsif`` on `SYNTHESIS`, `SIMULATION` or `VERILATOR`. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `task-function` | No `task`; `function` only inside a package. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `typedef-outside-package` | `typedef` only inside `package ... endpackage`. | [rtl.md#types-and-constants-live-in-packages](../../bkm/rtl.md#types-and-constants-live-in-packages) |
| `file-name` | A `.sv` declares exactly one module or package, named after the file. A package name ends in `_pkg` and a module name does not. The name starts with the owner's prefix: `common_` under `src/common/`, otherwise `<prefix><ip>` (sub-IP `a/b` gives `<prefix>a_b`) followed by the end or `_`; `<prefix>` alone when no IP owns the file. `<prefix>` is read from the `Module/package/file prefix` row of `src/README.md`; a missing row is a finding. | [rtl.md#naming](../../bkm/rtl.md#naming) |
| `port-suffix` | Every module port is ANSI with a direction. Inputs end in `_i`, outputs in `_o`, inouts in `_io`, each optionally with `n` before the direction letter for active-low (`rst_ni`). `ref` ports are not allowed. | [rtl.md#naming](../../bkm/rtl.md#naming) |
| `tool-pragma` | No comment whose first word is `verilator`, `synthesis`, `synopsys`, `pragma` or `altera`: no inline lint waivers (`/* verilator lint_off */`) and no tool directives. | [rtl.md#lint-and-style-checks](../../bkm/rtl.md#lint-and-style-checks) |

Provenance: the comment/string stripper, the package-span test and the
net-keyword, SYNTHESIS-guard, module-function and pragma-comment ideas are
adapted from nand2anything `tools/style/rtl_style.py` (commit `085ef4b`,
same owner, no license file); its project inventories and macro rules were
dropped.

## Neutrality check

Rule owner: [`CLAUDE.md`](../../../CLAUDE.md#design-neutrality). The scan
covers every file `git ls-files --cached --others --exclude-standard` lists
(tracked plus untracked, not ignored), except:

- paths that start with a `neutrality.exclude` prefix (`src/`, `wiki/src/`,
  `wiki/tracker/data/`);
- `cfg/checks.yaml` itself, because it holds the word list. To keep that
  exemption narrow, the file is schema-checked (below), so nothing else can
  hide in it;
- binary files (containing a NUL byte).

Each word in `neutrality.words` matches case-insensitively as a substring;
a space, `-` or `_` inside a word matches any run of those characters
(`widget bus` also matches `Widget-Bus`). Both file contents and file
paths are scanned, so this also proves that the design target's name does
not appear in `tools/` code. A finding is `<path>:<line>: '<match>'`.

**Repository slug.** The slug is exempt only inside a GitHub URL:
`github.com/<owner>/<repo>`, `github.com:<owner>/<repo>` and
`<owner>.github.io/<repo>`, case-insensitive, where `<repo>` is not followed
by another name character (so `<repo>-other` is not exempt). The slug is
`neutrality.repo_slug` when set, else `<owner>/<repo>` taken from the last
two path components of the `origin` remote URL. With neither, nothing is
exempt.

IP names are not in the word list: bare IP names are ordinary words
(they collide with generic text), and prefixed names contain the prefix,
which the word list covers. Hard-coded bare IP names stay a review item.

### `cfg/checks.yaml`

```yaml
neutrality:
  words: [<word>, ...]           # required, non-empty
  exclude: [src/, ...]           # required; path prefixes from the repo root
  repo_slug: owner/repo          # optional
```

Any other key, a missing key, or a wrong type is a configuration error.

## Lint check

Rule owners: [RTL BKM](../../bkm/rtl.md#lint-and-style-checks) and
[FPGA BKM](../../bkm/fpga.md) (board wrapper).

For each selected IP, Verilator runs
`--lint-only -Wall -Wno-fatal [--top-module T] +incdir+... -D... <sources>`
with the include directories, defines and sources of the resolved
`rtl/<n>.rtl.f`. `-Wno-fatal` lets every warning print; the tool, not
Verilator, decides pass or fail: every `%Warning-<CODE>` or `%Error` not
covered by a waiver is a finding, as is a nonzero exit with no error
message. Verilator is found as for `sim` (`PATH`, `$VERILATOR_ROOT/bin`,
then `verilator.prefix` in `cfg/host.yaml`); see the
[build spec](../build/SPEC.md#simulation-build-sim).

- **Top:** `<prefix><ip>` (sub-IP `a/b`: `<prefix>a_b`) when a module of that
  name is declared in the IP's sources; otherwise no `--top-module`, and
  Verilator picks the single unreferenced module (several raise `MULTITOP`,
  which fails).
- **Board wrapper:** each `fpga/*.sv` of the IP is linted in a second run with
  the IP's sources plus the wrapper, with the wrapper's file stem as top.
- An IP whose `.rtl.f` does not resolve is a lint finding (the filelist check
  reports the cause).

### Waivers

Waivers live only in `<src-root>/lint_waivers.yaml` (in the repository,
`src/lint_waivers.yaml`). It is under `src/` because waivers name design
files. A missing file means no waivers. Inline pragmas are rejected by the
style check (`tool-pragma`), so this file is the only way to waive.

```yaml
waivers:
  - file: blk/rtl/xy_blk.sv        # required; relative to the source root
    rule: UNUSEDSIGNAL             # required; Verilator warning code
    line: 12                       # line or match (or both) required
    match: "'spare_i'"             # substring of the warning message
    reason: Reserved for the next revision; driven by the board.   # required
```

A waiver covers a warning (never an error) with the same code and file, and
the line and message substring when given. Unknown keys, a missing field or
a wrong type is a configuration error. A waiver that matches no warning is
**stale** and fails the check; the stale test runs only without `--ip`,
since a filtered run cannot see every warning.

## Unit tests

```
python3 -m pytest tools/checks -q
```

Fixtures live in `tools/checks/tests/fixtures/`:

| Fixture | Content |
| --- | --- |
| `good/src/` | IP `blk` (package, include header, top) with sub-IP `blk/sub`, a shared block under `common/rtl/`, and a board wrapper in `blk/fpga/`. Every check passes. |
| `bad/src/` | IP `blk` with a duplicate source, an orphan, a missing and an invalid level file, style violations and lint warnings; IP `broken` with a missing source. Filelist, style and lint fail. |

Tests also build variants in temporary directories: one bad case per style
rule (`test_style.py`), duplicate, diamond, orphan and regression cases
(`test_filelists_check.py`), a small git repository with forbidden words,
slug URLs, a look-alike slug and an invalid config (`test_neutrality.py`),
and waivers (used, stale, invalid), output parsing and the command line
(`test_lint.py`, `test_check_run.py`). Tests that run Verilator are skipped
when it is not installed. `test_check_run.py` also runs the neutrality check
on this repository.
