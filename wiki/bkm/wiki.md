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
- Rationale worth keeping goes in a decision record: factory decisions under
  `wiki/project/decisions/`, design decisions under `wiki/src/decisions/`.
  Records are numbered once across both folders (`NNNN-<slug>.md`), and
  `wiki/src/README.md` indexes the design records. Delivery history stays in
  issues and PRs.
- Deferred work belongs to a named issue, never an ownerless TODO.

## Page set

`wiki/src/` mirrors the kind-first `src/` exactly, and `wiki/tools/<tool>/`
mirrors `tools/<tool>/`. Each spec sits in the folder named like the source
folder it describes:

```
src/                                wiki/src/
  arch/<ip>/  architecture models     arch/<ip>/HAS.md   top-level IPs only
  rtl/<ip>/   *.sv, <ip>.rtl.f        rtl/<ip>/MAS.md    every IP and sub-IP
  dv/<ip>/    tests, models, regress  dv/<ip>/DV_PLAN.md every IP and sub-IP
  fpga/<ip>/  SDC, QSF Tcl, wrapper   fpga/<ip>/FPGA.md  iff src/fpga/<ip>/ exists
  asic/<ip>/  SDC, <platform>.mk      asic/<ip>/ASIC.md  iff src/asic/<ip>/ exists
  rtl/common/ shared blocks           rtl/common/<block>.md  MAS, every shared block
  dv/common/  shared unit tests       dv/common/<block>.md   DV plan, every shared block
```

A sub-IP `<ip>_<sub>` is a flat sibling under each kind; which IPs are
top-level follows from filelist `-F` reachability and the `<parent>_` id
prefix, so an integrated top-level IP keeps its own HAS
([layout](../project/layout.md#ips-and-sub-ips)).

| Folder | Pages | Required for |
| --- | --- | --- |
| `wiki/src/arch/<ip>/` | `HAS.md` | Every top-level IP (no IP `P` with id prefix `P_` reaches it); sub-IPs have none |
| `wiki/src/rtl/<ip>/` | `MAS.md` | Every IP and sub-IP |
| `wiki/src/dv/<ip>/` | `DV_PLAN.md` | Every IP and sub-IP |
| `wiki/src/rtl/common/`, `wiki/src/dv/common/` | `<block>.md`: the block's MAS, and its DV plan | Every shared block `src/rtl/common/<block>.rtl.f` ([layout](../project/layout.md#shared-blocks)); nothing else |
| `wiki/src/fpga/<ip>/` | `FPGA.md` | Exactly the IPs with `src/fpga/<ip>/` |
| `wiki/src/asic/<ip>/` | `ASIC.md` | Exactly the IPs with `src/asic/<ip>/` |
| `wiki/src/` | `README.md`, `roadmap.md`, `references.md`, `decisions/NNNN-<slug>.md` | Fixed set of design-level pages: index and decision index, product roadmap, the official references (see [citations](#citations)), design decision records |
| `wiki/tools/<tool>/` | `SPEC.md` | Every tool under `tools/` |
| `wiki/bkm/` | One page per discipline | Fixed set ([index](README.md)) |
| `wiki/project/` | Architecture, layout, roadmap, decisions | Fixed set |
| `wiki/tracker/` | `index.md`, `data/` (nightly bot only) | Fixed set |

Every folder under `wiki/src/` has a `src/` counterpart, and every page
there sits in an IP folder `<kind>/<ip>/` (or below one), except the
design-level pages above (`decisions/` is flat and holds only records) and
each shared block's pages directly in `rtl/common/` and `dv/common/`. A
shared block has no HAS, so its MAS also owns its purpose, interfaces,
parameters and behavior, as a sub-IP's does. The
one other exception: a top-level IP's `arch/<ip>/` needs `src/arch/<ip>/` only once its HAS
defines targets (a `## Targets` section: a level-2 heading starting with
the word `Targets`, outside fenced code); then the
[architecture model](arch.md) must exist and report against them.
`./build check` enforces the page set and the mirror
([checks SPEC](../tools/checks/SPEC.md#wiki-mirror-check)).

Among wiki pages, only `wiki/src/` names the design target or its IPs;
[`CLAUDE.md`](../../CLAUDE.md#design-neutrality) owns the full rule and its
exemptions.

### HAS: High-level Architecture Specification, what and why

Top-level IPs only; a sub-IP's role is described in its parent's HAS.

- Purpose and scope of the IP; what it is not; use cases.
- Position in the system (block diagram, Mermaid allowed) and its sub-IPs.
- **Interfaces:** the architectural port table (name, direction, width,
  clock domain, description) and protocol rules.
- **Parameters:** name, type, default, legal range, effect.
- **Behavior:** function, reset values, error and corner cases, stated
  precisely enough to write an independent model.
- Architectural choices with their rationale and rejected alternatives.
- **Targets:** performance, area and feature targets the MAS must satisfy,
  in a `## Targets` section. With it, the IP's
  [architecture model](arch.md) reports against them; the MAS links to
  them instead of restating them.
- Open questions, each linked to an issue.

### MAS: Micro-Architecture Specification, how

- How the RTL meets the HAS: datapath, pipeline, state machines, storage,
  and the RTL structure (modules, instances, packages).
- Implementation choices with their rationale and rejected alternatives.
- **Timing:** latency and throughput per operation, clocks and resets, any
  crossing, the critical path.
- **Files:** the `rtl/` files and what each holds, linked, not copied.
- A sub-IP has no HAS, so its MAS also owns its purpose, interfaces,
  parameters, behavior and targets (the HAS sections above).

### FPGA and ASIC pages

The implementation of one IP on a flow: the board wrapper (pins, reset,
I/O) or the clock target, constraints and flow configuration, how to run the
flow, and the files in `src/fpga/<ip>/` or `src/asic/<ip>/`. The
[FPGA](fpga.md) and [PD](pd.md) BKMs own the method; results go to metrics
and the tracker, not to the page.

### DV_PLAN: feature → test → level

- Strategy: test layers, reference model, checkers, what is out of scope.
- One table: feature (linked to its HAS section, or its MAS section for a
  sub-IP) → regression entry `name` → level → seeds or note. The table
  matches `src/dv/<ip>/regress/level*.yaml` exactly
  ([DV BKM](dv.md#dv-plan-alignment)).
- Uncovered features listed with their owning issue.

### Tool SPEC

Outcomes, command line (subcommands, options, defaults, exit codes),
inputs and outputs (paths under `workdir/`), behavior, failure modes, and how
the unit tests prove it.

## Specs and code stay aligned

- Every PR that changes `src/` or `tools/` updates the owning HAS, MAS,
  DV_PLAN, FPGA, ASIC or SPEC page in the same PR; every wiki change that alters behavior
  updates the code.
- A new IP or sub-IP arrives with its [page set](#page-set); a new tool with
  its SPEC.
- The reviewer checks alignment by reading both sides; `./build check`
  only proves that the pages exist where the mirror puts them. A content
  mismatch is a REQUEST-CHANGES finding.

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

## Citations

Specs and code that rely on an external document paraphrase it and cite it.
They never reproduce it. An external document is anything that someone else
owns and copyrights: a protocol, interface or file format standard, a board
or vendor manual, or a datasheet. "Standard" below means any of them.

- **Paraphrase only.** Restate each requirement in your own words, as
  precisely as the page needs. Never copy sentences, tables, figures,
  register or field layout tables, state diagrams or pseudo-code from a
  standard. Names that the standard defines, such as signals, fields and
  states, may be used as names.
- **Field layouts.** A page that needs a standard's packet, frame, register
  or ordered-set fields describes **this design's** use of each one: its
  index or bit position, its name, what the design sends, and how it
  interprets the field on receive. Positions, widths and code values are
  functional facts, so they may be stated. The standard's table layout,
  column structure, prose and figures are never copied. The design index
  links a worked example ([worked examples](../src/README.md#worked-examples)).
- **Cite every fact taken from a standard**, in Markdown and in code
  comments alike, in exactly this form:
  `[cite: <standard>, rev <revision>, §<section>]`, for example
  `[cite: Example Bus Spec, rev 2.1, §4.3.2]`. `<standard>` is the published
  name, `<revision>` is its version or year, and `<section>` is one section
  number (cite each section separately). In Markdown a citation may wrap
  across lines like any other text.
- **Section forms are reserved.** In `src/` and `wiki/src/`, `§` and
  "section N.M" appear only inside a citation, whatever they refer to.
  Internal cross-references link by heading name instead
  (`[Timing](MAS.md#timing)`).
- **Self-contained specs.** A spec page writes out, in its own words,
  every rule it relies on, complete enough to implement and verify the
  design without the standard. A citation points to the source for
  cross-checking only; it never stands in for the rule, so "behaves per
  §4.2" is not a rule. A point the author is unsure of goes in the page's
  Open items table with its owning issue, never as a guess.
- **References page.** Every standard the design follows has one entry on
  `wiki/src/references.md`: a stable id as its heading, the citation name
  used as `<standard>`, revision, publisher, the publisher's own link,
  access terms and the pages that rely on it. It holds links and metadata
  only, and is public.
- **Local copies, never committed.** Where the license allows a local copy,
  `./build specs fetch` caches the document and its text outside the
  repository ([specs tool](../tools/specs/SPEC.md)). When
  `./build specs list` shows it present, the author checks every citation
  and every claim drawn from it against `./build specs path --absolute <id>`, and the
  page must agree with it; the reviewer spot-checks against it. Still
  paraphrase only. A standard's document, its text or excerpts of it are
  never committed: not in specs, code, tests, fixtures or archives. They
  never appear in commit messages, issues, pull request descriptions or
  review comments either, which are just as public.

`./build check` flags long block quotes and quoted runs, and citation-form
errors, in `src/` and `wiki/src/`
([checks SPEC](../tools/checks/SPEC.md#citation-check)), and anywhere in the
repository any PDF, any copy of a cached document or its text, and any run
of cached text longer than a quote
([spec-cache check](../tools/checks/SPEC.md#spec-cache-check)). Both are heuristics:
neither can tell a paraphrase from reworded copied text. Review is the main control, so
the reviewer confirms paraphrase-only on every PR that touches cited
material ([pr-reviewer](../../.claude/skills/pr-reviewer/SKILL.md#3-check)).

## Site

The repository is public and the wiki is published to GitHub Pages as a
**public** site (captain decisions, see the
[decision record](../project/decisions/0001-m0-grill.md#updates)). It is built
by a minimal generator in `tools/` (see its [SPEC](../tools/wiki_site/SPEC.md)).
Navigation follows the `wiki/` tree, so a new page needs no nav file edit.
Validate links locally before review with `./build wiki build --strict`; it
is the link check.

### Publishability

The whole repository is public, so this applies to every committed file; it
matters most for `wiki/`, including `wiki/src/` specs and `wiki/tracker/data/`,
which is also published as a site. Nothing committed may contain:

- credentials, tokens, keys or other secrets, or URLs that embed them;
- personal data (personal email addresses, phone numbers, home paths);
- machine details beyond tool versions, install paths and the CI host's
  role name (no network addresses, internal host names or user accounts other
  than the runner service account);
- unredistributable vendor content (copied tool reports or licensed IP
  text). Standards, manuals and datasheets are never quoted at all, nor
  committed in any form; they are paraphrased and cited, and linked from
  the references page ([citations](#citations)).

`./build check` runs the
[publishability check](../tools/checks/SPEC.md#publishability-check) on every
file in the repository: it fails on private keys, access tokens,
credentials in URLs, IP addresses, home-directory paths and personal email
addresses. A flagged match that is safe to publish, such as a value planted
in a test, is exempted only by a reviewed entry with a reason in
`cfg/checks.yaml`. The check is a pattern scan, not a proof: reviewers
still check every item above on every PR. Content that cannot be public
stays out of the repository; ask the captain where it belongs.
