# Checks tool: `./build check`

`./build check` runs the static checks that the
[DevOps BKM](../../bkm/devops.md#static-checks) requires: the source layout
check, the filelist and regression-list check, the RTL style checker, the
wiki mirror check, the flow-input check, the neutrality check, the citation
check and Verilator lint. It prints one
summary line per check, then every violation, then an overall verdict. The
code lives in `tools/checks/` (`run.py`, `layout.py`, `filelists.py`,
`style.py`, `wiki_mirror.py`, `flows.py`, `neutrality.py`, `citation.py`,
`lint.py`).

```
./build check [--ip ID]... [--only layout|filelist|style|wiki|flows|neutrality|citation|lint]...
              [--src-root DIR] [--workdir DIR]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | Restrict layout, filelist, style, wiki, flows and lint to these IPs (repeatable). Files not owned by a selected IP, such as `src/rtl/common/`, are skipped. Neutrality always scans the whole repository, and citation the whole of `src/` and `wiki/src/`. |
| `--only NAME` | Run only these checks (repeatable). The default is all eight. |
| `--src-root DIR` | The source tree to check, as for every command ([build spec](../build/SPEC.md#command-line)). The wiki and citation checks read its sibling `wiki/src/`. Neutrality ignores it and scans the git repository that holds the launcher. |

Checks run in the order layout, filelist, style, wiki, flows, neutrality, citation, lint
(lint is the slowest). Output:

```
check: 2 IP(s) under /abs/src
[layout] PASS: 6 IP or shared folder(s) under rtl/, dv/, arch/, fpga/, asic/
[filelist] PASS: 7 filelist(s), 2 IP(s) with regression lists, 5 RTL file(s) for orphans
[style] FAIL (1): 6 RTL file(s), 12 rule(s); 0 other includable or fpga/ file(s) for directives
  rtl/blk/xy_blk.sv:10: plain-always: always
[wiki] PASS: 6 required page(s), 6 wiki folder(s) under wiki/src/ (all IPs)
[flows] PASS: 1 FPGA and 0 ASIC target(s) with their flows' dry-run validation; cfg/fpga.yaml
[neutrality] PASS: 135 file(s) (tracked and untracked, not ignored) outside src/, ...
[citation] PASS: 40 text file(s) under src/, wiki/src/; quotes up to 25 words; ...
[lint] blk: top=xy_blk
[lint] PASS: 2 IP(s), 3 Verilator run(s) with -E audit, 0 waiver(s), 0 warning(s) waived
check: FAIL (style)
```

Paths in findings are relative to the source root (neutrality: to the
repository root; wiki and citation: to the source root's parent, so
`wiki/src/...`). With no IPs under `src/`, every check still runs and says
what it covered (zero IPs and files), and the command passes.

**Exit codes:** `0` means every selected check passed. `1` means at least
one check found a violation. `2` means a usage or configuration error: an
unknown `--ip`, an invalid `cfg/checks.yaml` (either section) or waiver file, a git error,
or Verilator missing while there is something to lint. The flow settings
(`cfg/fpga.yaml`, the ORFS entry of `cfg/host.yaml`) are flow inputs, not
the check's own configuration: a problem there is a `flows` finding.

## Layout check

Rule owner: the [layout page](../../project/layout.md#ips-and-sub-ips)
(captain decision in the [decision record](../../project/decisions/0001-m0-grill.md#updates)).
IPs, sub-IPs and top-level IPs are as the
[build spec](../build/SPEC.md#ip-discovery) discovers them. Code: `layout.py`.

- Every folder directly under the source root is a kind folder: `rtl/`,
  `dv/`, `arch/`, `fpga/` or `asic/` (files there, such as `README.md`, are
  fine). The superseded IP-first tree (`<ip>/<kind>/`) fails here:
  `<ip>/: not a kind folder (...)`.
- A kind folder holds only folders, each named after an IP (an
  `rtl/<id>/<id>.rtl.f` exists) or, under `rtl/` and `dv/`, the shared
  folder `common/`; anything else is `<kind>/<name>/: names no IP`.
- `arch/<id>/`, `fpga/<id>/` and `asic/<id>/` exist only for top-level IPs.
- An IP's `rtl/<id>/` holds no filelist but `<id>.rtl.f` (at any depth), and
  its `dv/<id>/` none but `<id>.dv.f` and `<id>.sim.f`, so a sub-IP cannot
  nest inside its parent's folder.
- **Flat sub-IP naming.** A sub-IP's id starts with the id of an IP that
  reaches it plus `_` (`<ip>_<sub>`).

With `--ip`, only the selected IPs' folders and naming are checked; the
kind-folder rule runs only without `--ip`.

## Filelist check

Rule owners: [layout](../../project/layout.md#per-ip-files) and the
[RTL BKM](../../bkm/rtl.md#filelists).

- Every `*.f` below `rtl/` or `dv/` under the source root resolves with the [build tool's resolver](../build/SPEC.md#filelists).
- No source is listed twice after flattening. Each filelist in the `-F` tree
  counts once (a diamond include is fine), so a duplicate is a source named
  by two filelists of the tree, or twice by one.
- Every IP has `rtl/<id>/<id>.rtl.f`, `dv/<id>/<id>.dv.f`,
  `dv/<id>/<id>.sim.f` and `dv/<id>/regress/level{0,1,2}.yaml`, and its regression lists pass the
  [regress schema](../regress/SPEC.md#regression-lists) (including unique
  test names and any [coverage floors](../regress/SPEC.md#coverage-floors)).
- **SystemVerilog only.** These rules close every route by which a file
  could carry a waiver past the style check and the reviewed waiver file:
  a listed control file (`.vlt`), or any file reached by `` `include``,
  which accepts any file name.
  - Every source in the closure of a filelist under `rtl/` ends in `.sv` or
    `.svh` and sits below `rtl/`.
  - Every `+incdir+` in such a closure resolves to a directory below
    `<src-root>/rtl/` (never `dv/` or elsewhere).
  - **Allowlist:** below `rtl/` only `.sv`, `.svh`, `.f` and `.md` files
    exist, listed or not.
  - Below `fpga/`, no `.v`, `.vh`, `.vlt`, `.vc`, `.vhd` or
    `.vhdl` file exists (constraint and flow files are fine).
  - `dv/` filelists are not restricted.
- **No orphans:** every `.sv`/`.svh` below `rtl/<name>/` is reached by a
  filelist directly in `rtl/<name>/` (for an IP, its `.rtl.f`; for
  `rtl/common/`, whichever filelist lives there). "Reached" is the
  resolver's `reaches()`: listed as a source or under an include directory.
  `fpga/` wrappers are not in any filelist and are not orphans.

`rtl/common/` is never an IP; its filelists (for example `common_lib.rtl.f`)
are checked as filelists but need no `dv/` files.

## Style check

The RTL style checker applies every rule to each `.sv`/`.svh` below `rtl/`
or `fpga/`. It also applies the four directive rules,
`verilator-config`, `tool-pragma`, `sim-ifdef` and `external-ifdef`, to
every other text file that could reach Verilator: every file of any type below an include directory of an
`rtl/` filelist closure (inside the source root), and every file below
`fpga/`. Binary files (a NUL byte) are skipped. The summary line
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
| `sim-ifdef` | No `` `ifdef``/`` `ifndef``/`` `elsif`` on `SYNTHESIS`, `SIMULATION` or `VERILATOR`, even when the RTL defines it. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `external-ifdef` | No `` `ifdef``/`` `ifndef``/`` `elsif`` on a macro the RTL closure does not define (a vendor or tool macro such as `ALTERA_RESERVED_QIS`, or one nothing defines): such code is hidden from lint and simulation while another flow compiles it. A macro is defined when the file itself, or any closure that reaches it, `` `define``s it or passes it with `+define+`. A closure is an `rtl/` filelist's resolved sources, every file below its include directories (inside the source root) and its `+define+` names; a file reached by several closures gets their union, and an `fpga/<id>/` file also gets its IP's `.rtl.f` closure (the lint check links the wrapper with it). So an include guard (`` `ifndef X_SVH`` then `` `define X_SVH``) passes. Each name in a parenthesized expression (`` `ifdef (A && B)``) is checked; a directive with no name is a finding. The macro name (or the opening parenthesis) must be on the directive's line: the scan is lexical and fails closed, so a name on a later line, `SYNTHESIS` included, is reported as `` `ifdef without a macro name``. Macro names may contain `$` after the first character. `SYNTHESIS`, `SIMULATION` and `VERILATOR` are left to `sim-ifdef`. Two cases are deliberately permissive: a file reached by several closures may condition on a macro only one of them defines (a sub-IP on a parent-only macro, which its own lint run does not define), and every file below an include directory counts as defining its macros, whether or not it is `` `include``d. In both cases the macro must still be `` `define``d by a file in the source tree of a closure that reaches the file, so a macro that only a vendor or tool flow defines never passes. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `task-function` | No `task`; `function` only inside a package. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
| `typedef-outside-package` | `typedef` only inside `package ... endpackage`. | [rtl.md#types-and-constants-live-in-packages](../../bkm/rtl.md#types-and-constants-live-in-packages) |
| `file-name` | A `.sv` declares exactly one module or package, named after the file. A package name ends in `_pkg` and a module name does not. The name starts with the owner's prefix: `common_` under `rtl/common/` or `dv/common/`, otherwise `<prefix><id>` of the owning IP (sub-IP `a_b` gives `<prefix>a_b`) followed by the end or `_`; `<prefix>` alone when no IP owns the file. `<prefix>` is read from the `Module/package/file prefix` row of `src/README.md`; a missing row is a finding. | [rtl.md#naming](../../bkm/rtl.md#naming) |
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

- **Required pages.** Every IP has `rtl/<id>/MAS.md` and
  `dv/<id>/DV_PLAN.md`; every top-level IP also has `arch/<id>/HAS.md`. An
  IP with `src/fpga/<id>/` has `fpga/<id>/FPGA.md`; one with
  `src/asic/<id>/` has `asic/<id>/ASIC.md`. A missing one is
  `wiki/src/<kind>/<id>/<page>: missing (required for IP <id>)`.
- **Design-level pages.** The only files outside an IP folder are
  `README.md` and `roadmap.md` at the root of the wiki tree and decision
  records `decisions/NNNN-<slug>.md` (four digits, a lower-case
  hyphenated slug). Any other file outside an IP folder is
  `<path>: not in an IP folder <kind>/<ip>/ (...)`; a misnamed file in
  `decisions/` is `<path>: not a decision record NNNN-<slug>.md`, and a
  folder in it is `<path>/: decisions/ holds no folders`.
- **Kinds and IPs.** Every other folder directly under the wiki tree is a kind
  folder, and every folder directly under a kind folder names an IP
  (`wiki/src/<kind>/<name>/: names no IP`, which also covers `common/`).
  This rejects the superseded IP-first tree (`wiki/src/<ip>/<kind>/`).
- **Counterparts.** Every IP folder and every folder below one has a folder
  at the same relative path under the source root, so `fpga/` or `asic/`
  pages exist only where the source folders do. The one exception: a
  top-level IP's `arch/<id>/` itself, whose `src/arch/<id>/` is required
  only by the next rule. A folder without a counterpart is reported once
  and not descended.
- **Arch model.** A top-level IP whose `arch/<id>/HAS.md` has a level-2
  heading starting with the word `Targets` (`## Targets`,
  `## Targets (draft)`, any case; it defines performance targets), outside
  fenced code blocks, needs
  `src/arch/<id>/`; a missing one is
  `src/arch/<id>/: missing (wiki/src/arch/<id>/HAS.md defines targets; ...)`.
  Any `src/arch/<id>/` of a top-level IP, required or not, holds the model
  `<prefix><id>_arch.py` and at least one `perf/*.yaml` scenario
  ([arch spec](../arch/SPEC.md#model)); their content is checked by
  `./build arch`, not here. No Python file in it imports DV, RTL or tools
  code ([import rule](../arch/SPEC.md#import-rule)): each such import is
  `src/arch/<id>/<file>:<line>: imports <module> (<origin>); ...`.
- **No sub-IP `arch/`.** `wiki/src/arch/<sub>/` is a finding here;
  `src/arch/<sub>/` is a [layout check](#layout-check) finding.
- **Placement.** Every file sits in, or below, an IP folder
  `<kind>/<id>/`; a file directly in the wiki tree or in a kind folder is a
  finding. Extra pages inside an IP folder are allowed.

Hidden files and folders (names starting with `.`) are skipped. With `--ip`,
required pages are checked for the selected IPs only, and the folder and
placement rules only inside the selected IPs' folders; other findings are
reported only without `--ip`. The check proves that
pages exist where the mirror puts them, not that their content matches the
code; that stays a review item.

## Flow-input check

Rule owners: the [FPGA tool spec](../fpga/SPEC.md#inputs) and the
[ASIC tool spec](../asic/SPEC.md#inputs). Code: `flows.py`.

The check runs the same validation as `./build fpga --dry-run` and
`./build asic --dry-run`, by calling the flows' own planning functions, so
the two never drift apart. It writes nothing and runs no vendor tool, so it
passes in a cloud session and in `ci-gate`, where Quartus and ORFS are absent.

- **FPGA**, for every selected IP with `src/fpga/<id>/`: `cfg/fpga.yaml` is
  valid; the board settings file `<board>.qsf.tcl` exists; there is exactly
  one `*.sdc` and exactly one `*.sv` board wrapper; the wrapper declares the
  module its file stem names and is not in the IP's `.rtl.f`; every source
  has a supported type; and every path quotes for the generated Tcl.
- **ASIC**, for every selected IP with `src/asic/<id>/`, on each platform it
  has a `<platform>.mk` for (the default platform when it has none, so the
  missing config is reported): exactly one `*.sdc`; the `.mk` sets
  `DESIGN_NAME` and none of the tool-owned variables; the `.rtl.f` has
  sources. Then the ORFS settings in `cfg/host.yaml` load with the
  `ORFS_RUNNER` and `ORFS_ROOT` overrides ignored, so a recorded image must
  be pinned by digest.

Each flow stops at its first error, so a target reports one finding per
platform; a finding is the flow's own error message with source-root and
repository paths shown relative. The cfg files are read only when at least
one selected IP is a target of that flow. With `--ip`, only the selected IPs
are planned.

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
citation:                        # see the citation check
  max_quote_words: 25
```

The top level holds `neutrality` and `citation` and nothing else. Both are
required: the neutrality check rejects a missing `neutrality` and any
unknown top-level key, and the citation check rejects a missing `citation`.
Any other key (including an attempt to configure exempt paths), a missing
key, a wrong type, a `words` list that is not one single-line flow list, or
more than one `words` or `repo_slug` line (a duplicate YAML key could hide a
value) is a configuration error.

## Citation check

Rule owner: the [wiki BKM, citations](../../bkm/wiki.md#citations). Code:
`citation.py`. Config: `citation.max_quote_words` in `cfg/checks.yaml`
(the section and the key are required; the value is a positive integer;
any other key is a configuration error).

The check scans every text file below the source root and its sibling
`wiki/src/` (in the repository, `src/` and `wiki/src/`), whatever the file
type. Hidden files and folders and binary files (a NUL byte) are skipped.
In Markdown files (`.md`), fenced code blocks and code spans (which do not cross a blank line) are
blanked first: code is not prose. No pattern can backtrack exponentially
(each whitespace run in a citation matches one way only), so a long
malformed input cannot stall the check. A word is a run of letters and digits, with
inner `'` or `-`. Findings, `<path>:<line>: <message>`:

| Finding | Flags |
| --- | --- |
| `block quote of N words` | Markdown only: a run of consecutive `>` lines with more than `max_quote_words` words. |
| `quoted run of N words` | Text between typographic double quotes, or between straight double quotes, with more than `max_quote_words` words. In Markdown a straight-quoted run may wrap lines within a paragraph (not across a blank line). In any other file it stays on one line, and a quote that is part of `""` or `"""` is not a delimiter, so Python docstrings are not quotes. |
| `malformed citation` | `[cite:` (any case or spacing) that does not open exactly `[cite: <standard>, rev <revision>, §<section>]`. `<standard>` is words of letters, digits and `./+&()-` joined by single spaces; it starts with a letter or digit and ends with one or `)`. `<revision>` is letters, digits, `.` and `-`. `<section>` is one alphanumeric section number with `.` or `-` separators. In Markdown, which is hard-wrapped, each space of the form may instead be one line break (with indentation), so a wrapped citation is well-formed; in other files the citation stays on one line. The finding runs to the `]`, or to the end of the line (Markdown: of the paragraph) when there is none. |
| `section reference ... outside a citation` | Every `§`, and every dotted section number (`4.3.2`, at least two levels) after `section`, `sec.`, `chapter` or `clause` (any case), outside a citation, whatever it refers to: in `src/` and `wiki/src/` these forms are reserved for citations, and internal cross-references link by heading name ([wiki BKM](../../bkm/wiki.md#citations)). One inside a malformed citation is reported once, as that. |

**Limits.** The check is a heuristic. It cannot prove that text was not
copied: a paraphrase and a copied sentence without quote marks look the
same, and it never reads the source document.

- **Misses:** a free-form reference with no section number or with a
  single-level one ("section 4"), text in single typographic quotes
  (‘...’), a quote split by stray quote marks, and a copied table without
  quote marks.
- **False positives:** an internal reference written as `§` or
  "section N.M" (link by heading name instead); a stray quote mark in a
  Markdown paragraph that pairs with the next one; and a legitimate block
  quote of more than `max_quote_words` words in `wiki/src/`, such as a
  provenance note (keep it short or write it as a plain paragraph). Review
remains the main control: the
[pr-reviewer](../../../.claude/skills/pr-reviewer/SKILL.md#3-check) checklist
requires the reviewer to confirm paraphrase-only for any PR that touches
cited material. The threshold is set high enough that ordinary messages and
string literals in tests pass.

## Lint check

Rule owners: [RTL BKM](../../bkm/rtl.md#lint-and-style-checks) and
[FPGA BKM](../../bkm/fpga.md) (board wrapper).

For each selected IP, Verilator runs
`--lint-only -Wall -Wno-fatal [--top-module T] +incdir+... -D... <sources>`
with the include directories, defines and sources of the resolved
`rtl/<id>/<id>.rtl.f`. `-Wno-fatal` lets every warning print; the tool, not
Verilator, decides pass or fail: every `%Warning-<CODE>` or `%Error` not
covered by a waiver is a finding, as is any `%Warning`/`%Error` line that
does not parse and a nonzero exit with no error message. Standard output
and standard error are joined with a newline before parsing. Verilator is found as for `sim` (`PATH`, `$VERILATOR_ROOT/bin`,
then `verilator.prefix` in `cfg/host.yaml`); see the
[build spec](../build/SPEC.md#simulation-build-sim).

- **Top:** `<prefix><id>` (sub-IP `a_b`: `<prefix>a_b`) when a module of that
  name is declared in the IP's sources; otherwise no `--top-module`, and
  Verilator picks the single unreferenced module (several raise `MULTITOP`,
  which fails).
- **Board wrapper:** each `fpga/<id>/*.sv` of the IP is linted in a second run with
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
    the source root and below `rtl/` (any IP's or `rtl/common/`, since
    closures include sub-IP and common lists), or be
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
  - file: rtl/blk/xy_blk.sv        # required; relative to the source root
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
| `good/src/`, `good/wiki/src/` | Kind-first tree: IP `blk` (package, include header, top) with flat sub-IP `blk_sub`, a shared block under `rtl/common/`, and a board wrapper, SDC and board settings file in `fpga/blk/`; the wiki mirror holds every required page. Every check passes. |
| `flows_bad/src/` | IP `blk` whose `fpga/blk/` has two SDC files and no board settings file or wrapper, and whose `asic/blk/sky130hd.mk` has no SDC beside it. Only the flow-input check is meant to fail on it (with one finding per flow). |
| `bad/src/`, `bad/wiki/src/` | IP `blk` with a duplicate source, an orphan, a missing and an invalid level file, style violations and lint warnings; IP `broken` with a missing source; an `asic/ghost/` folder that names no IP. The wiki mirror lacks pages, has a page outside an IP folder, an `fpga/blk/` page with no `src/fpga/blk/` and a folder that names no IP. Layout, filelist, style, wiki and lint fail. |

Tests also build variants in temporary directories: the superseded
IP-first tree, a nested sub-IP, a sub-IP with `arch/`, `fpga/` or `asic/`, a
folder that names no IP and an unprefixed sub-IP id (`test_layout.py`), one bad case per style
rule plus `external-ifdef` closure cases (a vendor and an undefined macro
fail; include guards, `` `define``s in an include file, `+define+`, a parent
closure and a board wrapper pass) (`test_style.py`), duplicate, diamond, orphan and regression cases
(`test_filelists_check.py`), each wiki mirror rule: every required page,
`asic/` and `fpga/` pages following the source folders, the top-level
`arch/` exemption without targets and its limit, HAS targets requiring
`src/arch/<ip>/` (and only a level-2 heading counting), a source `arch/`
without a model or scenario or with two models, a sub-IP `arch/` page, a
sub-IP turning top-level when no IP reaches it, the IP-first wiki tree, the
design-level pages and look-alikes (misnamed root pages, bad record names,
a folder in `decisions/`, a design-level page inside a kind folder), orphan
and empty folders, extra pages, a non-IP source folder, hidden entries,
`--ip`, and a missing wiki tree (`test_wiki_mirror.py`), a small git repository with forbidden words,
slug URLs, a look-alike slug and an invalid config (`test_neutrality.py`),
one case per flow-input finding (board settings, SDC and wrapper counts,
wrapper module and listing, `cfg/fpga.yaml`, a missing `.mk`, SDC counts,
a tool-owned variable, no `DESIGN_NAME`, an unpinned image, no ORFS, ORFS
environment overrides ignored, `--ip`, nothing written) on a copy of the
good tree with `asic/blk/` inputs and its own `cfg/` (`test_flows.py`),
each citation finding with its pass and fail boundary (exactly
`max_quote_words` words versus one more, every malformed citation variant, wrapped citations in Markdown (and not over a blank line), long indented malformed citations and unclosed backticks finishing quickly (a regression guard against backtracking),
code blocks and spans, triple quotes, binary and hidden files) and every
config error (`test_citation.py`), and waivers (used, stale, invalid),
output parsing and the command line (`test_lint.py`, `test_check_run.py`). Negative cases include a `.vlt`
and a `.v` file in an `rtl/` closure, a `w.txt` waiver reached by
`` `include`` through an include directory under `rtl/` and through
`+incdir+` of a `dv/` folder (both as a `` `verilator_config`` block and as a
`// verilator lint_off` comment), an `` `include`` of a `dv/` file from an
allowed include directory and a listed source under `dv/` (both caught by
the preprocessed audit), prose versus directive comments, a
comment and a widened exempt path in the config, and look-alike slug hosts.
Tests that run Verilator are skipped when it is not installed. `test_check_run.py` also runs the neutrality, wiki
mirror and citation checks on this repository.
