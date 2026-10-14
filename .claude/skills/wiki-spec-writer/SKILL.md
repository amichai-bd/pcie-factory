---
name: wiki-spec-writer
description: Use when creating, updating or condensing a wiki specification or method page - an IP or sub-IP MAS, HAS or DV_PLAN under wiki/src/, a tool SPEC under wiki/tools/, a BKM, or a project page - and whenever code changes in src/ or tools/ need their owning page brought back into alignment.
---

# Wiki spec writer

<!-- Provenance: adapted from nand2anything .agents/skills/wiki-spec-writer/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

The [wiki BKM](../../../wiki/bkm/wiki.md) owns authority, the page set, what
each page holds, concision and writing rules. This skill is the procedure and
the skeletons; it never redefines those rules. Work inside an issue worktree
per [agent-flow](../agent-flow/SKILL.md).

## 1. Find the owner

| Change | Owning page |
| --- | --- |
| IP scope, targets, sub-IP split | `wiki/src/<ip>/MAS.md` |
| Ports, parameters, behavior, timing, files | `wiki/src/<ip>/HAS.md` |
| Feature → test → level | `wiki/src/<ip>/DV_PLAN.md` |
| Same, for a sub-IP | `wiki/src/<ip>/<subip>/{MAS,HAS,DV_PLAN}.md` |
| A tool's command line or behavior | `wiki/tools/<tool>/SPEC.md` |
| A repository-wide method | the matching page in `wiki/bkm/` |
| Layout, architecture, decisions | `wiki/project/` |

One fact, one page ([authority](../../../wiki/bkm/wiki.md#authority)). If
the fact already lives elsewhere, link to it; if it is duplicated, keep the
owner and replace the copy with a link.

## 2. Write

- Contract first, present tense, exact enough to implement and test
  independently: units, widths, signedness, reset, timing, failure behavior
  ([writing rules](../../../wiki/bkm/wiki.md#writing-rules)).
- Section contents: [MAS](../../../wiki/bkm/wiki.md#mas-architecture-what-and-why),
  [HAS](../../../wiki/bkm/wiki.md#has-hardware-architecture-spec-how),
  [DV_PLAN](../../../wiki/bkm/wiki.md#dv_plan-feature--test--level) and
  [tool SPEC](../../../wiki/bkm/wiki.md#tool-spec) per the
  [page set](../../../wiki/bkm/wiki.md#page-set).
- Link to source, config and tests; never paste code, filelists or YAML.
- Open choices and gaps name their owning issue; no ownerless TODO.
- Only `wiki/src/` names the design target or IPs; BKMs, tool SPECs and
  project pages stay neutral ([design neutrality](../../../CLAUDE.md#design-neutrality)).
- Adapted text carries a provenance line (source path, commit, license).

Skeletons (omit empty sections; headings stay stable once linked):

```markdown
# <IP> MAS
<2-4 sentences: purpose, parent, consumers; what it is not.>
## Position        parent, sub-IPs, block diagram (Mermaid allowed)
## Decisions       choice | rationale | rejected alternatives
## Targets         performance, area, features the HAS must meet
## Open questions  issue links
```

```markdown
# <IP> HAS
## Interfaces       | Port | Dir | Width | Clock | Description |, protocol rules
## Parameters       | Name | Type | Default | Range | Effect |
## Behavior         function, reset values, errors and corner cases
## Microarchitecture datapath, state machines, storage
## Timing           latency, throughput, clocks, resets, crossings
## Files            | Path | Holds |  (links into src/<ip>/rtl/)
```

```markdown
# <IP> DV plan
## Strategy   layers, reference model, checkers, out of scope
## Plan       | Feature (HAS link) | Entry name | Level | Seeds / note |
## Gaps       | Feature | Owning issue |
```

```markdown
# <Tool>
<2-4 sentences: who runs it, for what.>
## Usage      synopsis, | Option | Default | Meaning |, exit codes
## Inputs and outputs   paths, workdir/ layout
## Behavior   rules, failure modes
## Files      | Path | Role |
## Tests      | Behavior | Test |
```

## 3. Keep spec and code aligned

Alignment is a duty of every PR, not a later cleanup
([specs and code](../../../wiki/bkm/wiki.md#specs-and-code-stay-aligned)):

- [ ] A `src/` change updates its MAS/HAS/DV_PLAN; a `tools/` change updates
      its SPEC; a behavior change in the wiki updates the code.
- [ ] A new IP or sub-IP arrives with all three pages; a new tool with its
      SPEC.
- [ ] HAS port and parameter tables match the RTL top exactly; the Files
      table matches `rtl/<ip>.rtl.f`.
- [ ] DV_PLAN rows match `src/<ip>/dv/regress/level*.yaml` by name and level
      ([DV plan alignment](../../../wiki/bkm/dv.md#dv-plan-alignment)).
- [ ] A SPEC's options, defaults and exit codes match the tool's `--help`.

## 4. Cut and check

- Cut per [concision](../../../wiki/bkm/wiki.md#concision): no preamble, no
  history, tables over prose; split by owner instead of growing a page past
  about 250 lines.
- Renamed heading or moved page: update every inbound link in the same PR
  (`grep -rn '<old-anchor-or-path>' wiki .claude CLAUDE.md README.md`).
- Link check: build the site strictly
  (`python3 tools/wiki_site/wiki_site.py build --strict`, see the
  [wiki site SPEC](../../../wiki/tools/wiki_site/SPEC.md#links); `./build wiki`
  once it exists). Links from outside `wiki/` are checked by hand.
- Neutrality check per the [DevOps BKM](../../../wiki/bkm/devops.md#static-checks).
- Record both results in the PR, then [pr-creation](../pr-creation/SKILL.md).
