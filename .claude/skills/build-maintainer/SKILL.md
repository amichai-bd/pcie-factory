---
name: build-maintainer
description: Use before and while changing factory infrastructure - the ./build launcher, Python tools under tools/, generic configuration under cfg/, GitHub workflows and templates under .github/ - including adding a subcommand, check, tool or workflow, so that it stays generic, tested and specified.
---

# Build maintainer

<!-- Provenance: adapted from nand2anything .agents/skills/build-maintainer/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

The [DevOps BKM](../../../wiki/bkm/devops.md) owns the entry point, tool and
configuration rules, static checks, levels, CI, nightly, metrics and labels.
Each tool's `wiki/tools/<tool>/SPEC.md` (the wiki mirror of `tools/<tool>/`)
owns its command line and behavior.
This skill is the procedure; it never redefines those rules. Work inside an
issue worktree per [agent-flow](../agent-flow/SKILL.md).

## 1. Before changing

1. Read the owning SPEC under `wiki/tools/<tool>/` and the matching
   [DevOps BKM](../../../wiki/bkm/devops.md) section. For a new tool with no
   SPEC, the issue owns the contract; write the SPEC in the same PR.
2. List consumers: `build` subcommands, workflows in `.github/workflows/`,
   other tools, skills and BKMs that quote the command line
   (`grep -rn '<subcommand>' .claude wiki .github tools`).
3. Decide where each fact lives: varying facts in `cfg/` or in `src/<kind>/<ip>/`,
   behavior in `tools/`, never facts in Python conditionals
   ([tools and configuration](../../../wiki/bkm/devops.md#tools-and-configuration)).

## 2. While changing

- `build` stays a thin launcher; logic goes in `tools/<tool>/`.
- Discover IPs and sub-IPs from `src/` (`build.ip.discover`: `src/rtl/<id>/`
  folders and `-F` reachability); never hard-code an IP name, the
  module prefix or the design target in `tools/`, `cfg/` or `.github/`
  ([design neutrality](../../../CLAUDE.md#design-neutrality)). Read the prefix
  from [`src/README.md`](../../../src/README.md) when a tool needs it.
- Python standard library, PyYAML and pytest only, unless the issue approves
  more. Python dependencies are pinned in `tools/requirements.txt` (from
  #9); a new one needs issue approval and a pin there.
- Validate inputs before expensive work, print what will run, exit nonzero on
  failure, keep output paths deterministic and under `workdir/`.
- No new abstraction or wrapper until two real consumers need it.

## 3. Workflows (`.github/`)

- [ ] `runs-on: [self-hosted, linux, x64]`; no hosted runners, no design
      names in job names, paths or labels.
- [ ] Every job a `pull_request` can trigger carries
      `if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository`
      (push and dispatch still run). The guard is defense in depth only: a
      fork's copy of the workflow can drop it, so the real controls are the
      [Forks rules](../../../wiki/bkm/devops.md#continuous-integration) (no
      approved fork runs, review and re-push). Never `pull_request_target`
      with a PR checkout; never echo secrets.
- [ ] Jobs call `./build <subcommand>`, never a tool script directly.
- [ ] Explicit `permissions:` (least privilege), `timeout-minutes`, and
      `concurrency` that cancels superseded PR runs but keeps every push to
      `main` ([continuous integration](../../../wiki/bkm/devops.md#continuous-integration)).
- [ ] Actions pinned to an exact version; toolchain versions come from
      `cfg/host.yaml`, not the workflow.
- [ ] `ci-gate` stays the single required check; affected-IP selection
      never lives in workflow YAML: its facts are in `cfg/ci.yaml`
      ([CI spec](../../../wiki/tools/ci/SPEC.md)).
- [ ] A header comment points to the owning SPEC or BKM section.

## 4. Tests

Unit tests live in `tools/<tool>/tests/` and run without a simulator or
network:

- [ ] Parsing and validation of every input file and option, including a
      malformed-input case that must exit nonzero with a clear message.
- [ ] Planning and selection logic (which IPs, levels, files) on a small
      synthetic `src/` tree built in a temporary directory, with neutral
      placeholder IP names.
- [ ] Failure propagation: a failing child command yields a nonzero exit.
- [ ] Determinism: same inputs, same output paths and bytes.

```text
python3 -m pytest tools/<tool>/tests -q        # focused
python3 -m pytest tools -q                     # all tool tests
./build check                                  # layout, filelists, RTL style, wiki mirror, flow inputs, neutrality, citation, publishability, lint
```

The [build SPEC](../../../wiki/tools/build/SPEC.md#unit-tests)
owns the test layout (`tools/pytest.ini`, fixture trees) and the `COMMANDS`
dispatch table a new subcommand plugs into.

## 5. Spec and neutrality

- [ ] `wiki/tools/<tool>/SPEC.md` updated in the same PR: outcomes, command
      line, behavior, failures, tests ([tool SPEC](../../../wiki/bkm/wiki.md#tool-spec)).
- [ ] The [DevOps BKM](../../../wiki/bkm/devops.md) subcommand table, a
      skill or `CLAUDE.md` updated only if they name a changed command (keep
      the edit minimal and call it out in the PR).
- [ ] Neutrality is clean outside `src/` and `wiki/src/`:
      `./build check --only neutrality` (word list in `cfg/checks.yaml`;
      [checks SPEC](../../../wiki/tools/checks/SPEC.md#neutrality-check)).

## 6. Before the PR

Run the [local evidence](../../../wiki/bkm/devops.md#local-evidence) set for
`tools/`/`cfg/` changes (checks, tool unit tests, L0 of all IPs) once on the
head commit. A workflow change that cannot run locally records a dry run
(e.g. YAML parse plus the exact `./build` commands it invokes run locally)
and says so. Then [pr-creation](../pr-creation/SKILL.md).
