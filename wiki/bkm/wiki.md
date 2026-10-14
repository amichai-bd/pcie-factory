# Wiki BKM

> Provenance: adapted from nand2anything `wiki/bkm/bkm_wiki.md` (commit
> `085ef4b`, same owner, no license file). Authority, concision and writing
> rules kept; its two-page set, word budgets and nav file replaced by this
> repository's decisions.

## Authority

The wiki owns specifications and methods: requirements, architecture,
interfaces, microarchitecture, DV plans, tool behavior and decisions. It links
to source, configuration and tests instead of copying them.
[`CLAUDE.md`](../../CLAUDE.md) is the agent contract and wins where it and a
page disagree until both are reconciled in the same issue.

- One fact, one owning page; everywhere else links to it.
- Current requirements are normative and in the present tense.
- Rationale worth keeping goes in a decision record under
  `wiki/project/decisions/`; delivery history stays in issues and PRs.
- Deferred work belongs to a named issue, never an ownerless TODO.

## Page set

| Folder | Pages | Required for |
| --- | --- | --- |
| `wiki/src/<ip>/` | `MAS.md`, `HAS.md`, `DV_PLAN.md` | Every IP |
| `wiki/src/<ip>/<subip>/` | `MAS.md`, `HAS.md`, `DV_PLAN.md` | Every sub-IP, recursively |
| `wiki/tools/<tool>/` | `SPEC.md` | Every tool under `tools/` |
| `wiki/bkm/` | One page per discipline | Fixed set ([index](README.md)) |
| `wiki/project/` | Architecture, layout, roadmap, decisions | Fixed set |
| `wiki/tracker/` | `index.md`, `data/` (nightly bot only) | Fixed set |

Among wiki pages, only `wiki/src/` names the design target or its IPs;
[`CLAUDE.md`](../../CLAUDE.md#design-neutrality) owns the full rule and its
exemptions.

### MAS: architecture, what and why

- Purpose and scope of the IP; what it is not.
- Position in the parent (block diagram, Mermaid allowed) and its sub-IPs.
- Key architectural choices with their rationale and rejected alternatives.
- Performance, area and feature targets the HAS must satisfy.
- Open questions, each linked to an issue.

### HAS: hardware architecture spec, how

- **Interfaces:** a port table (name, direction, width, clock domain,
  description) and protocol rules with timing diagrams where cycle behavior
  matters.
- **Parameters:** name, type, default, legal range, effect.
- **Behavior:** function, reset values, error and corner cases, stated
  precisely enough to write an independent model.
- **Microarchitecture:** datapath, state machines, pipelines, storage.
- **Timing:** latency and throughput per operation, clocks and resets,
  any crossing.
- **Files:** the `rtl/` files and what each holds, linked, not copied.

### DV_PLAN: feature → test → level

- Strategy: test layers, reference model, checkers, what is out of scope.
- One table: feature (linked to its HAS section) → regression entry `name` →
  level → seeds or note. The table matches `src/<ip>/dv/regress/level*.yaml`
  exactly ([DV BKM](dv.md#dv-plan-alignment)).
- Uncovered features listed with their owning issue.

### Tool SPEC

Outcomes, command line (subcommands, options, defaults, exit codes),
inputs and outputs (paths under `workdir/`), behavior, failure modes, and how
the unit tests prove it.

## Specs and code stay aligned

- Every PR that changes `src/` or `tools/` updates the owning MAS, HAS,
  DV_PLAN or SPEC in the same PR; every wiki change that alters behavior
  updates the code.
- A new IP or sub-IP arrives with its three pages; a new tool with its SPEC.
- The reviewer checks alignment by reading both sides. There is no
  automation for it; a mismatch is a REQUEST-CHANGES finding.

## Concision

- Lead with the contract; no "this page describes" preamble.
- Tables for interfaces, parameters, commands and test maps; prose only for
  rationale.
- Keep each BKM, project page and SPEC short (about 250 lines is a guideline,
  not a check). Split by owner rather than growing one page.
- No delivery narrative, changelogs or status reports in specs.

## Writing rules

- State units, widths, signedness, timing, reset and failure behavior
  explicitly.
- Separate requirements from design choices; label genuine open choices and
  link the issue that owns each.
- Repository-relative Markdown links only; link to files and headings that
  exist. A planned page is named in plain text with its owning issue until it
  lands.
- Stable headings: renaming a heading updates every link to its anchor.
- Moving or deleting a page updates every inbound link in the same PR; no
  redirect stubs.
- Content adapted from another repository carries a provenance line
  (source path, commit, license).

## Site

The wiki is published to GitHub Pages by a minimal generator in `tools/`
(issue #12 owns it and its SPEC). Navigation follows the `wiki/` tree, so a
new page needs no nav file edit. Validate links locally before review; the
site build is the link check once it exists.
