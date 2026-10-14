# Checks tool: `./build check`

`./build check` runs the static checks that the
[DevOps BKM](../../bkm/devops.md#static-checks) requires: the filelist and
regression-list check, the RTL style checker, the wiki mirror check, the
neutrality check and Verilator lint. It prints one summary line per check,
then every violation, then an overall verdict. The code lives in
`tools/checks/` (`run.py`, `filelists.py`, `style.py`, `wiki_mirror.py`,
`neutrality.py`, `lint.py`).

```
./build check [--ip ID]... [--only filelist|style|wiki|neutrality|lint]...
              [--src-root DIR] [--workdir DIR]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | Restrict filelist, style, wiki and lint to these IPs (repeatable). Files not owned by a selected IP, such as `src/common/`, are skipped. Neutrality always scans the whole repository. |
| `--only NAME` | Run only these checks (repeatable). The default is all five. |
| `--src-root DIR` | The source tree to check, as for every command ([build spec](../build/SPEC.md#command-line)). The wiki check reads its sibling `wiki/src/`. Neutrality ignores it and scans the git repository that holds the launcher. |

Checks run in the order filelist, style, wiki, neutrality, lint (lint is the
slowest). Output:

```
check: 2 IP(s) under /abs/src
[filelist] PASS: 7 filelist(s), 2 IP(s) with regression lists, 5 RTL file(s) for orphans
[style] FAIL (1): 6 RTL file(s), 12 rule(s); 0 other includable or fpga/ file(s) for directives
  blk/rtl/xy_blk.sv:10: plain-always: always
[wiki] PASS: 6 required page(s), 8 wiki folder(s) under wiki/src/ (all IPs)
[neutrality] PASS: 135 file(s) (tracked and untracked, not ignored) outside src/, ...
[lint] blk: top=xy_blk
[lint] PASS: 2 IP(s), 3 Verilator run(s) with -E audit, 0 waiver(s), 0 warning(s) waived
check: FAIL (style)
```

Paths in findings are relative to the source root (neutrality: to the
repository root; wiki: to the source root's parent, so `wiki/src/...`). With no IPs under `src/`, every check still runs and says
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
- **SystemVerilog only.** These rules close every route by which a file
  could carry a waiver past the style check and the reviewed waiver file:
  a listed control file (`.vlt`), or any file reached by `` `include``,
  which accepts any file name.
  - Every source in the closure of a filelist under an `rtl/` directory
    ends in `.sv` or `.svh` and sits below an `rtl/` directory.
  - Every `+incdir+` in such a closure resolves to a directory below an
    `rtl/` directory inside the source root (never `dv/` or elsewhere).
  - **Allowlist:** below an `rtl/` directory only `.sv`, `.svh`, `.f` and
    `.md` files exist, listed or not.
  - Below an `fpga/` directory, no `.v`, `.vh`, `.vlt`, `.vc`, `.vhd` or
    `.vhdl` file exists (constraint and flow files are fine).
  - `dv/` filelists are not restricted.
- **No orphans:** every `.sv`/`.svh` below an `rtl/` directory is reached by a
  filelist in that same `rtl/` directory (for an IP, its `.rtl.f`; for
  `src/common/rtl/`, whichever filelist lives there). "Reached" is the
  resolver's `reaches()`: listed as a source or under an include directory.
  `fpga/` wrappers are not in any filelist and are not orphans.

`src/common/` is an IP only if it has `rtl/common.rtl.f`; a shared-block
list with another name (for example `common_lib.rtl.f`) is checked as a
filelist but needs no `dv/` files.

## Style check

The RTL style checker applies every rule to each `.sv`/`.svh` below an
`rtl/` or `fpga/` directory. It also applies the two directive rules,
`verilator-config` and `tool-pragma`, to every other text file that could
reach Verilator: every file of any type below an include directory of an
`rtl/` filelist closure (inside the source root), and every file below an
`fpga/` directory. Binary files (a NUL byte) are skipped. The summary line
counts both sets. It is lexical: comments and strings are blanked first,
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
| `verilator-config` | No `` `verilator_config`` block in any checked file, including includable files of any type (a listed or `` `include``d file would otherwise carry `lint_off` rules past the waiver file). | [rtl.md#lint-and-style-checks](../../bkm/rtl.md#lint-and-style-checks) |
| `tool-pragma` | No tool directive comment: `verilator`, `synthesis`, `synopsys`, `pragma` or `altera` followed by a directive word (`lint_off`, `translate_off`, `full_case`, `public`, `protect`, `message_off`, ...). This covers inline lint waivers (`/* verilator lint_off */`). Prose that only starts with the tool name (`// Synthesis infers a flop`) is not flagged. | [rtl.md#lint-and-style-checks](../../bkm/rtl.md#lint-and-style-checks) |

Provenance: the comment/string stripper, the package-span test and the
net-keyword, SYNTHESIS-guard, module-function and pragma-comment ideas are
adapted from nand2anything `tools/style/rtl_style.py` (commit `085ef4b`,
same owner, no license file); its project inventories and macro rules were
dropped.

## Wiki mirror check

Rule owner: the [wiki BKM page set](../../bkm/wiki.md#page-set) (captain
decision in the [decision record](../../project/decisions/0001-m0-grill.md#updates)).
The wiki tree is `<src-root>/../wiki/src` (in the repository, `wiki/src/`
next to `src/`); a missing wiki tree is an empty one. Code: `wiki_mirror.py`.

- **Required pages.** Every top-level IP (an IP directly under the source
  root) has `arch/HAS.md`. Every IP and sub-IP has `rtl/MAS.md` and
  `dv/DV_PLAN.md`. An IP with a `fpga/` directory has `fpga/FPGA.md`; one
  with `asic/` has `asic/ASIC.md`. The pages are found at
  `wiki/src/<ip id>/`; a missing one is
  `wiki/src/<id>/<page>: missing (required for IP <id>)`.
- **Counterparts.** Every folder under the wiki tree has a folder at the same
  relative path under the source root, so a page cannot outlive its IP or
  sub-IP, and `fpga/` or `asic/` pages exist only where the source folders
  do. The one exception: a top-level IP's `arch/` itself, because
  `src/<ip>/arch/` is optional until the architecture model tooling lands.
  A folder without a counterpart is reported once and not descended.
- **No sub-IP `arch/`.** An `arch/` folder of a sub-IP is a finding, even
  when the source has one.
- **Placement.** Every file sits in, or below, an `arch/`, `rtl/`, `dv/`,
  `fpga/` or `asic/` folder of an IP (the innermost IP whose source
  directory contains the folder's counterpart). This rejects the old flat
  layout (`wiki/src/<ip>/MAS.md`) and pages for source folders that are not
  IPs (such as `src/common/` without `common.rtl.f`). Extra pages inside a
  mirror folder are allowed.

Hidden files and folders (names starting with `.`) are skipped. With `--ip`,
required pages are checked for the selected IPs only, and the folder and
placement rules only for wiki folders that the selected IPs own; a folder
that no IP owns is reported only without `--ip`. The check proves that
pages exist where the mirror puts them, not that their content matches the
code; that stays a review item.

## Neutrality check

Rule owner: [`CLAUDE.md`](../../../CLAUDE.md#design-neutrality). The scan
covers every file `git ls-files --cached --others --exclude-standard` lists
(tracked, plus untracked files that are not ignored), except:

- paths under the exempt trees the contract names: `src/`, `wiki/src/` and
  `wiki/tracker/data/`. They are hard-coded (`EXEMPT` in `neutrality.py`),
  not configurable, so a config line cannot widen them;
- binary files (containing a NUL byte).

`cfg/checks.yaml` is scanned like any other file, except that the value of
`words` (a one-line flow list, `words: [...]`) and the value of `repo_slug`
are blanked first. Its comments, keys and every other line are scanned, so
the word list is the only text in it that may name the target.

Each word in `neutrality.words` matches case-insensitively as a substring.
A space, `-` or `_` inside a word matches any run of those characters,
including none (`widget bus` also matches `Widget-Bus` and `widgetbus`).
Both file contents and file paths are scanned, so this also proves that the
design target's name does not appear in `tools/` code. A finding is
`<path>:<line>: '<match>'`. The word list holds the target, its protocol
names and protocol-specific terms; very short terms are left out because
they would match ordinary text.

**Repository slug.** The slug is exempt only inside a GitHub URL:
`github.com/<owner>/<repo>`, `github.com:<owner>/<repo>` and
`<owner>.github.io/<repo>`, case-insensitive, with a name boundary on both
sides: the host or owner is not preceded by a name character, `.` or `-`
(so `evilgithub.com/...` and `x<owner>.github.io/...` are not exempt), and
`<repo>` is not followed by a name character or `-` (so `<repo>-other` is
not exempt). The slug is `neutrality.repo_slug` when set, else
`<owner>/<repo>` taken from the last two path components of the `origin`
remote URL. With neither, nothing is exempt.

IP names are not in the word list: bare IP names are ordinary words
(they collide with generic text), and prefixed names contain the prefix,
which the word list covers. Hard-coded bare IP names stay a review item.

### `cfg/checks.yaml`

```yaml
neutrality:
  words: [<word>, ...]           # required, non-empty, one flow list on one line
  repo_slug: owner/repo          # optional
```

Any other key (including an attempt to configure exempt paths), a missing
key, a wrong type, a `words` list that is not one single-line flow list, or
more than one `words` or `repo_slug` line (a duplicate YAML key could hide a
value) is a configuration error.

## Lint check

Rule owners: [RTL BKM](../../bkm/rtl.md#lint-and-style-checks) and
[FPGA BKM](../../bkm/fpga.md) (board wrapper).

For each selected IP, Verilator runs
`--lint-only -Wall -Wno-fatal [--top-module T] +incdir+... -D... <sources>`
with the include directories, defines and sources of the resolved
`rtl/<n>.rtl.f`. `-Wno-fatal` lets every warning print; the tool, not
Verilator, decides pass or fail: every `%Warning-<CODE>` or `%Error` not
covered by a waiver is a finding, as is any `%Warning`/`%Error` line that
does not parse and a nonzero exit with no error message. Standard output
and standard error are joined with a newline before parsing. Verilator is found as for `sim` (`PATH`, `$VERILATOR_ROOT/bin`,
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
- An IP whose `.rtl.f` closure lists a source that is not `.sv`/`.svh` (for
  example a `.vlt` control file), or has an include directory outside
  `rtl/`, is not linted; that is a lint finding, so such a file can never
  waive a warning.
- **Preprocessed audit.** Before each lint run, `verilator -E` runs with the
  same include directories, defines and sources, and its output is checked
  against what Verilator actually reads:
  - every file it entered (each `` `line N "<file>" `` marker) must be inside
    the source root and below an `rtl/` directory (any IP's or
    `src/common/`'s, since closures include sub-IP and common lists), or be
    the board wrapper of that run;
  - the output must contain no `` `verilator_config`` and no
    `/*verilator <directive>*/` metacomment (`-E` turns every comment that
    starts with `verilator` into one, so both `//` and `/* */` forms appear,
    whatever file or include route they came from). Directive words are the
    `tool-pragma` list;
  - a nonzero `-E` exit is a finding.

### Waivers

Waivers live only in `<src-root>/lint_waivers.yaml` (in the repository,
`src/lint_waivers.yaml`). It is under `src/` because waivers name design
files. A missing file means no waivers. This file is the only way to waive.
The authoritative guarantee is the lint check's preprocessed audit: it
inspects Verilator's own `-E` view of every lint run, so no waiver
(`` `verilator_config`` or a `verilator` metacomment) and no file outside
`rtl/` can reach a lint run by any route, including `../` from an allowed
include directory, macros, or a listed source elsewhere. The filelist rules
(SystemVerilog-only closures below `rtl/`, include directories below `rtl/`,
the `rtl/` allowlist) and the style rules (`tool-pragma`, `verilator-config`
over every includable file) are fast pre-checks that point at the cause.

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
| `good/src/`, `good/wiki/src/` | IP `blk` (package, include header, top) with sub-IP `blk/sub`, a shared block under `common/rtl/`, and a board wrapper in `blk/fpga/`; the wiki mirror holds every required page. Every check passes. |
| `bad/src/`, `bad/wiki/src/` | IP `blk` with a duplicate source, an orphan, a missing and an invalid level file, style violations and lint warnings; IP `broken` with a missing source. The wiki mirror lacks pages, has a flat-layout page, an `fpga/` page with no `src/blk/fpga/` and an orphan folder. Filelist, style, wiki and lint fail. |

Tests also build variants in temporary directories: one bad case per style
rule (`test_style.py`), duplicate, diamond, orphan and regression cases
(`test_filelists_check.py`), each wiki mirror rule: every required page,
`asic/` and `fpga/` pages following the source folders, the top-level
`arch/` exemption and its limit, a sub-IP `arch/`, the flat layout, orphan
and empty folders, extra pages, a non-IP source folder, hidden entries,
`--ip`, and a missing wiki tree (`test_wiki_mirror.py`), a small git repository with forbidden words,
slug URLs, a look-alike slug and an invalid config (`test_neutrality.py`),
and waivers (used, stale, invalid), output parsing and the command line
(`test_lint.py`, `test_check_run.py`). Negative cases include a `.vlt`
and a `.v` file in an `rtl/` closure, a `w.txt` waiver reached by
`` `include`` through an include directory under `rtl/` and through
`+incdir+../dv` (both as a `` `verilator_config`` block and as a
`// verilator lint_off` comment), `` `include "../dv/w.txt"`` from an
allowed include directory and a listed source under `dv/` (both caught by
the preprocessed audit), prose versus directive comments, a
comment and a widened exempt path in the config, and look-alike slug hosts.
Tests that run Verilator are skipped when it is not installed. `test_check_run.py` also runs the neutrality and wiki
mirror checks on this repository.
