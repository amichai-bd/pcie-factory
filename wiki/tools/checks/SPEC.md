# Checks tool: `./build check`

`./build check` runs the static checks that the
[DevOps BKM](../../bkm/devops.md#static-checks) requires: the source layout
check, the filelist and regression-list check, the RTL style checker, the
wiki mirror check, the flow-input check, the neutrality check, the citation
check, the publishability check, the spec-cache check, Verilator lint and the structural
clock-domain-crossing (CDC) check. It prints one summary
line per check, then every violation, then an overall verdict. The code
lives in `tools/checks/` (`run.py`, `layout.py`, `filelists.py`,
`style.py`, `wiki_mirror.py`, `flows.py`, `neutrality.py`, `citation.py`,
`publishability.py`, `spec_cache.py`, `lint.py`, `cdc.py`).

```
./build check [--ip ID]...
              [--only layout|filelist|style|wiki|flows|neutrality|citation|publishability|spec-cache|lint|cdc]...
              [--src-root DIR] [--workdir DIR]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | Restrict layout, filelist, style, wiki, flows, lint and cdc to these IPs (repeatable; cdc analyzes the top-level IPs among them and their ancestors). Files not owned by a selected IP, such as `src/rtl/common/`, are skipped. Neutrality, publishability and spec-cache always scan the whole repository, and citation the whole of `src/` and `wiki/src/`. |
| `--only NAME` | Run only these checks (repeatable). The default is all eleven. |
| `--src-root DIR` | The source tree to check, as for every command ([build spec](../build/SPEC.md#command-line)). The wiki, citation and spec-cache checks read its sibling `wiki/src/`; spec-cache reads its `specs.yaml`. Neutrality, publishability and spec-cache scan the git repository that holds the launcher. |

Checks run in the order layout, filelist, style, wiki, flows, neutrality,
citation, publishability, spec-cache, lint, cdc (lint and cdc are the slowest; lint's
preprocessed audit covers the sources cdc elaborates). Output:

```
check: 2 IP(s) under /abs/src
[layout] PASS: 6 IP or shared folder(s) under rtl/, dv/, arch/, fpga/, asic/
[filelist] PASS: 9 filelist(s), 2 IP(s) and the shared unit with regression lists, 5 RTL file(s) for orphans
[style] FAIL (1): 6 RTL file(s), 12 rule(s); 0 other includable or fpga/ file(s) for directives
  rtl/blk/xy_blk.sv:10: plain-always: always
[wiki] PASS: 6 required page(s), 6 wiki folder(s) under wiki/src/ (all IPs)
[flows] PASS: 1 FPGA and 0 ASIC target(s) with their flows' dry-run validation; cfg/fpga.yaml
[neutrality] PASS: 135 file(s) (tracked and untracked, not ignored) outside src/, ...
[citation] PASS: 40 text file(s) under src/, wiki/src/; quotes up to 25 words; ...
[publishability] PASS: 180 file(s) (tracked and untracked, not ignored); 7 rule(s); 5 allow entry(ies)
[spec-cache] PASS: 180 file(s) (tracked and untracked, not ignored); 2 listed document(s); 2 cached file(s) in ~/.cache/<repo>/specs; runs of 26 words
[lint] blk: top=xy_blk
[lint] common/common_lib: top=common_lib
[lint] PASS: 2 IP(s), 1 shared block(s), 4 Verilator run(s) with -E audit, 0 waiver(s), 0 warning(s) waived
[cdc] blk: top=xy_blk
[cdc] common/common_lib: top=common_lib
[cdc] PASS: 1 top-level IP(s), 1 shared block(s), 2 Verilator --json-only run(s), 2 flop(s), 0 cdc-cell directive(s)
check: FAIL (style)
```

Paths in findings are relative to the source root (neutrality and
publishability: to the repository root; wiki and citation: to the source
root's parent, so `wiki/src/...`). With no IPs under `src/`, every check still runs and says
what it covered (zero IPs and files), and the command passes.

**Exit codes:** `0` means every selected check passed. `1` means at least
one check found a violation. `2` means a usage or configuration error: an
unknown `--ip`, an invalid `cfg/checks.yaml` (any section), waiver file or
reference list (`src/specs.yaml`), a reference cache inside the repository, a git error,
or Verilator missing while there is something to lint or analyze. The flow settings
(`cfg/fpga.yaml`, the ORFS entry of `cfg/host.yaml`) are flow inputs, not
the check's own configuration: a problem there is a `flows` finding.

## Layout check

Rule owner: the [layout page](../../project/layout.md#ips-and-sub-ips)
(captain decisions in the [M0 record](../../project/decisions/0001-m0-grill.md#updates)
and Q19 of [0002](../../src/decisions/0002-m1-grill.md#decisions)).
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
- **Shared folders** ([layout](../../project/layout.md#shared-blocks)):
  `rtl/common/` holds filelists only directly in it, each a shared block
  named `common_<name>.rtl.f`, and `dv/common/` holds no filelist but
  `common.dv.f` and `common.sim.f` (the shared unit). Anything else is
  `<path>: rtl/common/ holds no filelist but ...`.
- **Flat sub-IP naming.** Naming is what makes an IP a sub-IP: an IP
  reached by `P`'s filelist is a sub-IP only when its id is `P_<name>`.
  Any other reached IP is an integrated top-level IP, which keeps its own
  `arch/`, `fpga/` and `asic/` (the `integration/` fixture).
- **Confirmed parent.** An id `P_<name>`, where `P` is another IP's id,
  claims `P` as its parent, so `P`'s `.rtl.f` must reach it (directly or
  through another IP); being reached only by an integrating IP is not
  enough. Otherwise: `rtl/<id>/: id names P as its parent, but
  rtl/P/P.rtl.f does not reach it; include it there with -F
  ../<id>/<id>.rtl.f, or rename it`.

With `--ip`, only the selected IPs' folders and parents are checked; the
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

`rtl/common/` is never an IP; its filelists, the shared blocks (for example
`common_lib.rtl.f`), are checked as filelists. Without `--ip`, the
[shared unit](../build/SPEC.md#shared-unit) is checked when a shared block
or `dv/common/common.sim.f` exists:

- `dv/common/common.dv.f`, `dv/common/common.sim.f` and
  `dv/common/regress/level{0,1,2}.yaml` exist (`common: missing <path>`),
  and the regression lists pass the regress schema (including any coverage
  floors);
- `common.sim.f` reaches every shared block's filelist, so every block is
  tested (`rtl/common/<block>.rtl.f: shared block not reached by
  dv/common/common.sim.f`).

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
| `external-ifdef` | No `` `ifdef``/`` `ifndef``/`` `elsif`` on a macro the RTL closure does not define (a vendor or tool macro such as `ALTERA_RESERVED_QIS`, or one nothing defines): such code is hidden from lint and simulation while another flow compiles it. A macro is defined when the file itself, or any closure that reaches it, `` `define``s it or passes it with `+define+`. A closure is an `rtl/` filelist's resolved sources, every file below its include directories (inside the source root) and its `+define+` names; a file reached by several closures gets their union, and an `fpga/<id>/` file also gets its IP's `.rtl.f` closure (the lint check links the wrapper with it). So an include guard (`` `ifndef X_SVH`` then `` `define X_SVH``) passes. Each name in a parenthesized expression (`` `ifdef (A && B)``) is checked; a directive with no name is a finding. The macro name (or the opening parenthesis) must be on the directive's line: the scan is lexical and fails closed, so a name on a later line, `SYNTHESIS` included, is reported as `` `ifdef/`ifndef/`elsif without a macro name`` (naming the directive found). Macro names may contain `$` after the first character. `SYNTHESIS`, `SIMULATION` and `VERILATOR` are left to `sim-ifdef`. Two cases are deliberately permissive: a file reached by several closures may condition on a macro only one of them defines (a sub-IP on a parent-only macro, which its own lint run does not define), and every file below an include directory counts as defining its macros, whether or not it is `` `include``d. In both cases the macro must still be `` `define``d by a file in the source tree, or passed with a filelist `+define+`, of a closure that reaches the file, so a macro that only a vendor or tool flow defines never passes. | [rtl.md#systemverilog-rules](../../bkm/rtl.md#systemverilog-rules) |
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
  `README.md`, `roadmap.md` and `references.md` at the root of the wiki tree and decision
  records `decisions/NNNN-<slug>.md` (four digits, a lower-case
  hyphenated slug). Any other file outside an IP folder is
  `<path>: not in an IP folder <kind>/<ip>/ (...)`; a misnamed file in
  `decisions/` is `<path>: not a decision record NNNN-<slug>.md`, and a
  folder in it is `<path>/: decisions/ holds no folders`.
- **Kinds and IPs.** Every other folder directly under the wiki tree is a kind
  folder, and every folder directly under a kind folder names an IP
  (`wiki/src/<kind>/<name>/: names no IP`) or is the shared folder
  `rtl/common/` or `dv/common/` (below).
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
- **Shared blocks.** Every shared block `src/rtl/common/<block>.rtl.f`
  has its MAS `rtl/common/<block>.md` and its DV plan `dv/common/<block>.md`
  (`...: missing (required for shared block <block>)`). The folders
  `rtl/common/` and `dv/common/` hold nothing else: a page that names no
  block is `wiki/src/<kind>/common/<page>: names no shared block`, and a
  subfolder is a finding. `common/` under any other kind names no IP.
- **No sub-IP `arch/`.** `wiki/src/arch/<sub>/` is a finding here;
  `src/arch/<sub>/` is a [layout check](#layout-check) finding.
- **Placement.** Every file sits in, or below, an IP folder
  `<kind>/<id>/`, or is a shared block's page or a design-level page; any
  other file directly in the wiki tree or in a kind folder is a finding. Extra pages inside an IP folder are allowed.

Hidden files and folders (names starting with `.`) are skipped. With `--ip`,
required pages are checked for the selected IPs only (shared block pages
not at all), and the folder and
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
publishability:                  # see the publishability check
  allow: [...]
```

The top level holds `neutrality`, `citation` and `publishability` and
nothing else. All three are required: the neutrality check rejects a
missing `neutrality` and any unknown top-level key, the citation check
rejects a missing `citation`, and the
[publishability check](#publishability-check) a missing `publishability`.
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

## Publishability check

Rule owner: the [wiki BKM, publishability](../../bkm/wiki.md#publishability).
Code: `publishability.py`. The scan covers every file
`git ls-files --cached --others --exclude-standard` lists, in every tree:
`src/`, `wiki/src/` and `wiki/tracker/data/` are public too, so nothing is
exempt by location. Binary files (a NUL byte) are skipped. A symbolic link
is never followed: its target string is scanned instead, so the check never
reads outside the repository. File paths are scanned as well as contents. A finding is `<path>:<line>: <rule>: '<match>'`,
or `<path> (path): <rule>: '<match>'` for a match in the path itself.

| Rule | Flags | Not a finding |
| --- | --- | --- |
| `private-key` | A PEM private key header: `-----BEGIN ... PRIVATE KEY-----`. | Public keys and certificates. |
| `token` | An access token shape, as a whole word: GitHub (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_` and 36 or more letters and digits; `github_pat_` and 22 or more), AWS key ids (`AKIA` or `ASIA` and 16 upper-case letters or digits), Google API keys (`AIza` and 35), Slack (`xoxb-` and similar), `sk-` keys with 32 or more characters, GitLab (`glpat-`) and npm (`npm_`) tokens. | Shorter look-alikes such as a `ghp_` prefix in prose. |
| `url-credentials` | `<scheme>://<user>:<password>@` in any URL; the match is `<user>:<password>`. | A user without a password (`http://proxy@host`). |
| `ipv4` | A dotted-quad IPv4 address. | Loopback (`127.0.0.0/8`), `0.0.0.0`, the documentation ranges (RFC 5737), invalid octets (`999.1.1.1`), longer dotted runs (`1.2.3.4.5`) and a section number right after `§`, as in the [citation form](../../bkm/wiki.md#citations) (`§4.3.2.1`). |
| `ipv6` | A valid IPv6 address with at least three non-empty groups, or with two when one of them is at least three hex digits long. | `::1`, `::`, `2001:db8::/32` (RFC 3849), and text that is not an address or is too short to tell from code: `a[1::2]`, `pkg::name`, `a::b`, `fe::1`, times, MAC addresses. |
| `home-path` | `/home/<name>` or `/Users/<name>`: a home directory names a user. | `/home/` alone and placeholders such as `/home/<user>`. |
| `email` | `<local>@<domain>.<tld>`, except inside a `url-credentials` match. | No-reply addresses (local part `noreply` or `no-reply`, optionally with a `+`, `.`, `_` or `-` suffix; a local part ending in `+noreply`; a domain with a `noreply.` label, such as `users.noreply.github.com`), the reserved domains of RFC 2606 and RFC 6761 (`example.com`, `example.net`, `example.org`, `.example`, `.invalid`, `.test`, `.localhost`) and a domain without a letter TLD (`actions/checkout@v7.0.1`). |

The rules and their built-in "not a finding" cases are fixed in the tool:
they are the definition of the check. Any other match is exempt only by a
reviewed entry in `publishability.allow`:

```yaml
publishability:
  allow:                         # required; may be empty
    - rule: email                # required; one of the rules above
      match: git@github.com      # required; the exact matched text
      path: tools/*              # optional fnmatch glob ('*' crosses '/'); default: every file
      reason: the SSH user of a Git host remote URL, not a person   # required
```

An entry exempts a match only when the rule and the exact text are equal
and the file path matches the glob. An entry that exempts nothing is
**stale** and is a finding,
`cfg/checks.yaml: publishability.allow[<i>] (<rule> '<match>'[ in <path>]): stale, matches nothing`.
`cfg/checks.yaml` itself is scanned like any other file, except that a
match equal to the `match` of an entry with the same rule is never a
finding there, wherever the entry's glob points, so the allow list can
name what it exempts. That mention is not a use: an entry whose text
appears only in `cfg/checks.yaml` is stale. A missing or extra key, an unknown rule, an empty
string or a wrong type is a configuration error (exit 2).

**Limits.** The check is a pattern scan. It does not find a secret that has
no recognizable shape (a password in prose or in a config value), phone
numbers, host names, names of people, a home path written another way
(`~jdoe`, `C:\Users\jdoe`), a two-group IPv6 address whose groups are both
one or two hex digits (`fe::1`), or anything in a file that is not UTF-8 or
ASCII-compatible: UTF-16 text has NUL bytes and is skipped as binary. Known
false positives, each cleared by an allow entry when it is genuine:
Python slices `x[<a>::<b>]` with a bound of three or more digits (IPv6), file names
such as `<name>@2x.png` (email), templated URLs such as
`https://<user>:${TOKEN}@<host>` (url-credentials) and four-part version strings
that form a valid dotted quad (ipv4). Reviewers keep the whole
[publishability list](../../bkm/wiki.md#publishability) as a review item;
an `allow` entry is reviewed like code.

## Spec-cache check

Rule owner: the [wiki BKM, citations](../../bkm/wiki.md#citations); the
cache belongs to the [specs tool](../specs/SPEC.md). Code: `spec_cache.py`.
It scans the same files as the publishability check (every file `git
ls-files --cached --others --exclude-standard` lists; symbolic links are not
followed) and fails on:

| Rule | Finding |
| --- | --- |
| `pdf` | `<path>: pdf: ...` for any file named `*.pdf` (any case) or starting with `%PDF-`. No allow list: reference documents, datasheets and manuals are never committed, so a PDF has no place in the repository. |
| `cached-file` | `<path>: cached-file: identical to the cached <name>`: the file's sha256 equals a cached document's or extracted text's. |
| `cached-text` | `<path>:<line>: cached-text: <n> or more consecutive words match the cached <name>; ...`: a text file shares a run of `n` = `citation.max_quote_words` + 1 words with an extracted text. Words are lower-case runs of letters and digits, so layout, case and punctuation do not hide a copy; `<line>` is where the first such run starts. |
| list | `src/specs.yaml: document '<id>' has no entry heading on wiki/src/references.md`: every listed id is a level-2 to level-6 heading of exactly that id on the public references page. |

The cache is found as `./build specs` finds it. Without one (a host that
never fetched, or a blocked network), `cached-file` and `cached-text` have
nothing to compare and only `pdf` and the list rule apply; the summary
says so. `cached-text` is a heuristic like the citation check: it cannot
find a reworded copy, so review stays the main control.

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
- **Shared blocks:** without `--ip`, each `rtl/common/<block>.rtl.f` is
  linted the same way, labelled `common/<block>`, with top `<block>` when a
  module of that name is declared (otherwise Verilator picks). The summary
  counts them as `N shared block(s)`.
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

## CDC check

Rule owner: [RTL BKM](../../bkm/rtl.md#clock-domain-crossings).

The check is structural: it finds every path from a flop on one clock to a
flop on another and fails unless the path goes through an approved
crossing cell. It does not prove a crossing correct; the cell's MAS and its
tests do that.

**Targets.** Without `--ip`, each top-level IP (one that is no other IP's
sub-IP) is one target, with top `<prefix><id>` as for lint, and each
`rtl/common/<block>.rtl.f` is another, with top `<block>`. A top-level IP
that another integrates is analyzed on its own and again inside the
integrator, where its clocks come from the integrator's ports. With
`--ip`, the targets are the top-level IPs among the selected IPs and their
ancestors (parents and integrators), because a sub-IP's clocks are known
only at its top; shared blocks are skipped. Board wrappers in `fpga/<ip>/`
are not analyzed.

**Netlist.** For each target, Verilator runs
`--json-only -Wno-fatal [--top-module T] +incdir+... -D... <sources>` with
the resolved `.rtl.f`, writing the JSON tree and meta files to a temporary
directory that is removed afterwards (nothing is written under `workdir/`).
The JSON format is not stable across Verilator releases, so the check
reads `verilator.version` in `cfg/host.yaml` and fails with one finding,
without analyzing anything, when `verilator --version` reports another
version. A nonzero Verilator exit is a finding. Warnings are left to lint.

**Directives.** Every source of every target is scanned as text. A
comment line that starts with `cdc-cell` is a directive and must match
`// cdc-cell: src=<port|async> dst=<port>` exactly, with `src` and `dst`
different, on the line directly above a `module` line. Only a
`common_cdc_*` module in `rtl/common/` may carry one, and every such module
must. Each broken rule is a finding at the directive's line. A cell with a
valid directive is then checked against its elaborated ports, once per
module name:

- every port is `src_*` or `dst_*`;
- `dst` names a `dst_*` input port, and `src` a `src_*` input port or `async`;
- a `src=async` cell has no `src_*` output;
- when the cell is a target itself, each of its flops is clocked by a port
  its directive names.

A cell that passes is **approved**. A `common_cdc_*` module that fails is
analyzed like any other module, so its own synchronizer flops are then
reported as raw crossings too.

**Graph.** The netlist is flattened per instance into a variable-level
graph. Edges are classed `alias` (one whole variable to another, as in a
port connection or `assign a = b`), `wire` (only routing: constant selects,
concatenations and extensions) or `logic` (anything else, including any
assignment under a condition in combinational code, a dynamic index and a
function call):

- **Combinational edges** come from continuous assignments, `always_comb`,
  `always_latch`, `always` without an edge, blocking assignments inside a
  clocked block, and port connections of ordinary instances. An assignment's
  target depends on its right-hand side, the indices on its left-hand side,
  and the conditions of the `if`, `case` and loop statements around it.
- **Flops** are the targets of nonblocking assignments in a block with an
  edge (`posedge`, `negedge` or `edge`) in its sensitivity list. A flop's
  data input is everything its assignments read, enclosing conditions such
  as a synchronous reset included. With several edges (an asynchronous
  reset), the clock is the one signal of the list that the block does not
  read; when that is not exactly one signal, or an edge is on an
  expression, the flop is a finding.
- **Approved cell instances** are black boxes. Each output is a source in
  the domain of its side's clock pin; each input other than the clocks is a
  sink in its side's domain, except the `src_*` inputs of a `src=async`
  cell. A clock pin that does not connect to a plain signal is a finding.

**Domains.** A clock signal is followed backward through `alias` edges
(ports and plain assignments) while it has exactly one driver. When that
ends at an input port of the target's top, the domain is that port's name;
each top input is its own domain, so two inputs that carry related clocks
are still different domains. When it ends at a flop or a cell output, the
domain is that signal, marked `(generated)`; anywhere else (a clock mux, a
gate, an inverter), it is that signal, marked `(derived)`. A generated or
derived clock is therefore a domain of its own, and a path between it and
its source clock is reported. A `posedge` and a `negedge` flop on the same
clock share a domain.

**Rule.** Every flop and every cell output's set of reaching sources is
propagated forward through combinational edges to a fixed point (loops
terminate). For each flop and each clocked cell input:

- a source flop or approved-cell output in another domain is a finding
  that names both, both clocks and both source lines, and says whether the
  path is `directly` (alias or wire edges only) or `through combinational
  logic`;
- input ports of the top carry no domain, so a path from one is never a
  finding.

For each `src=async` cell input, the inputs it reads are walked backward
through alias and wire edges until a flop, a cell output or a top input; a
logic edge on the way, or logic in the connection expression itself, is a
finding that names where the logic is.

Findings are prefixed with the target's label (`<ip>` or `common/<block>`);
directive findings are not. The summary counts targets, Verilator runs,
flops in the analyzed netlists and valid directives.

### Limits

Each is a known gap, not a promise; review and each cell's MAS cover them.

- **No reconvergence, glitch or timing analysis.** Two synchronizers whose
  outputs recombine, a multi-bit value that changes more than one bit per
  update, pulse spacing and the FIFO's read-path timing are not checked.
- **No reset-domain analysis.** A reset is read like any other data input,
  so a reset from another clock is reported as a crossing, but reset
  assertion and release ordering is not checked.
- **Variable granularity.** Dependencies are tracked per variable, not per
  bit or field: a vector or struct that mixes bits from two domains is one
  node, which can over-report. Arrays (memories) are one node, so a write
  in one domain and a combinational read in another is not a crossing
  until a flop samples it.
- **Clocks are signals, not waveforms.** A clock mux or gate is its own
  `(derived)` domain; a divided clock is its own `(generated)` domain.
  Related clocks at the top (one source, two ports) are treated as
  unrelated. Clock expressions in a sensitivity list are a finding, not
  analyzed.
- **IP boundary.** A top input port has no domain, so a crossing whose
  launching flop is outside the analyzed top is not seen; it is seen at the
  top-level IP that holds both flops.
- **What Verilator keeps.** The JSON is Verilator's elaborated netlist
  before inlining: parameters are resolved, each parameterization is its
  own module, generate blocks are unrolled, and constant-only drivers may
  be folded into initial values. Logic that elaboration removes cannot be
  reported; logic it keeps is analyzed as written. Variables of packages
  are constants here and are not tracked.
- **Unsupported constructs** that the check reports rather than guess:
  hierarchical references, interface instances, an instance of a module
  Verilator did not elaborate. Task output arguments are not modeled, and
  a variable assigned with a blocking assignment in a clocked block is
  treated as combinational, even if it holds state across clock edges.

## Unit tests

```
python3 -m pytest tools/checks -q
```

Fixtures live in `tools/checks/tests/fixtures/`:

| Fixture | Content |
| --- | --- |
| `good/src/`, `good/wiki/src/` | Kind-first tree: IP `blk` (package, include header, top) with flat sub-IP `blk_sub`, a shared block `common_lib` under `rtl/common/` with the shared unit's files in `dv/common/`, and a board wrapper, SDC and board settings file in `fpga/blk/`; the wiki mirror holds every required page, including the block's two pages. Every check passes. |
| `integration/src/`, `integration/wiki/src/` | Top-level IP `sys` integrates top-level IPs `a` and `b` through `-F`; `a` and `b` each keep `arch/` (model and scenario), `fpga/` and `asic/`, and `a_x` is a sub-IP of `a`. Every check passes, and the `regress --affected` tests select `sys` when `a`, `a_x` or `b`'s models change. |
| `flows_bad/src/` | IP `blk` whose `fpga/blk/` has two SDC files and no board settings file or wrapper, and whose `asic/blk/sky130hd.mk` has no SDC beside it. It is used only by the flow-input tests (one finding per flow). |
| `bad/src/`, `bad/wiki/src/` | IP `blk` with a duplicate source, an orphan, a missing and an invalid level file, style violations and lint warnings; IP `broken` with a missing source; an `asic/ghost/` folder that names no IP. The wiki mirror lacks pages, has a page outside an IP folder, an `fpga/blk/` page with no `src/fpga/blk/` and a folder that names no IP. Layout, filelist, style, wiki and lint fail. |

Tests also build variants in temporary directories: the superseded
IP-first tree, a nested sub-IP, a sub-IP with `arch/`, `fpga/` or `asic/`, a
folder that names no IP, misnamed or nested filelists in the shared folders, and a reached IP without its includer's prefix
staying top-level with `arch/`, `fpga/` and `asic/` while a prefixed one is a sub-IP, and
an unconfirmed parent prefix (`test_layout.py`), one bad case per style
rule plus `external-ifdef` closure cases (a vendor and an undefined macro
fail; include guards, `` `define``s in an include file, `+define+`, a parent
closure and a board wrapper pass) (`test_style.py`), duplicate, diamond, orphan and regression cases
the shared unit's files, lists and block coverage, and a `-F` cycle between two top-level IPs (`test_filelists_check.py`), each wiki mirror rule: every required page,
`asic/` and `fpga/` pages following the source folders, the top-level
`arch/` exemption without targets and its limit, HAS targets requiring
`src/arch/<ip>/` (and only a level-2 heading counting), a source `arch/`
without a model or scenario or with two models, a sub-IP `arch/` page, a
sub-IP turning top-level when no IP reaches it, the IP-first wiki tree, the
design-level pages and look-alikes (misnamed root pages, bad record names,
a folder in `decisions/`, a design-level page inside a kind folder), orphan
and empty folders, extra pages, shared block pages (required, and nothing else in the shared folders), a non-IP source folder, hidden entries,
`--ip`, and a missing wiki tree (`test_wiki_mirror.py`), a small git repository with forbidden words,
slug URLs, a look-alike slug and an invalid config (`test_neutrality.py`),
one case per flow-input finding (board settings, SDC and wrapper counts,
wrapper module and listing, an unsupported source type, a define and a
path that Tcl cannot quote (a `{`), `cfg/fpga.yaml`, a missing `.mk`, SDC counts,
a tool-owned variable, no `DESIGN_NAME`, an unpinned image, no ORFS, ORFS
environment overrides ignored, `--ip`, nothing written) on a copy of the
good tree with `asic/blk/` inputs and its own `cfg/` (`test_flows.py`),
each citation finding with its pass and fail boundary (exactly
`max_quote_words` words versus one more, every malformed citation variant, wrapped citations in Markdown (and not over a blank line), long indented malformed citations and unclosed backticks finishing quickly (a regression guard against backtracking),
code blocks and spans, triple quotes, binary and hidden files) and every
config error (`test_citation.py`),
a fixture that fails for every publishability rule and token shape,
harmless look-alikes for each rule, every tree and file paths scanned,
ignored and binary files skipped, symbolic links not followed, allow
entries (exact match, path glob, per rule, stale, values named in the
config, a path-less entry named only in the config being stale), linear
scan time on 40k-character adversarial lines, and every config error
(`test_publishability.py`),
a temporary git repository with a fake cache: a PDF by name and by
content (with and without a cache), a copy of a cached text, a run one word
longer than the quote limit across a line break with other case and
punctuation versus a run at the limit, ignored files, a listed id with no
page heading, an invalid list and a cache inside the repository
(`test_spec_cache.py`); no fixture PDF or document text is stored,
waivers (used, stale, invalid), output parsing and the command line
(`test_lint.py`, `test_check_run.py`), and the CDC check on a small IP
beside a copy of the repository's crossing cells (`test_cdc.py`): a single
clock, a legal crossing through each cell, an asynchronous reset, a raw
crossing (both flops and clocks named), a crossing through combinational
logic and through the hierarchy, a cell output used in the wrong domain, a
clocked cell input fed from the wrong domain, a generated clock, logic in
front of a `src=async` input (in the connection, one and two assignments
away) against plain wires, every directive finding, a missing and a
malformed directive (the cell is then looked inside), port and clock
mismatches, `--ip` selection (a sub-IP and an integrated top-level IP), the version pin, and the repository itself. Negative cases include a `.vlt`
and a `.v` file in an `rtl/` closure, a `w.txt` waiver reached by
`` `include`` through an include directory under `rtl/` and through
`+incdir+` of a `dv/` folder (both as a `` `verilator_config`` block and as a
`// verilator lint_off` comment), an `` `include`` of a `dv/` file from an
allowed include directory and a listed source under `dv/` (both caught by
the preprocessed audit), prose versus directive comments, a
comment and a widened exempt path in the config, and look-alike slug hosts.
Tests that run Verilator are skipped when it is not installed. `test_check_run.py` also runs the neutrality, wiki
mirror and citation checks on this repository, and `test_publishability.py` runs the
publishability check on it.
