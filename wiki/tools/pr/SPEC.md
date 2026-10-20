# PR tool: `./build pr check`

`./build pr check` is a read-only machine pre-check of the evidence a pull
request body records. It reports what the
[pr-creation skill](../../../.claude/skills/pr-creation/SKILL.md#2-compose-the-body)
requires that is missing or malformed, so the gap is fixed before a reviewer
is spawned and the reviewer spends the round on substance. It also catches
the mechanical slips that cost review rounds: results labelled with a stale
SHA, `Closes` while the body says acceptance moved elsewhere, and design or
IP names added outside `src/` and `wiki/src/`. It never judges whether the
evidence is good enough (the reviewer's job), never runs a test and never
writes to GitHub.

It requires no full-L2, coverage or broad mutant evidence: PR evidence is
the affected L0+L1, which `ci-gate` runs and which may be cited, plus
targeted L2 entries and mutants of the changed code
([local evidence](../../bkm/devops.md#local-evidence)). Its level rules
apply only to the `./build regress` rows a body records.

It does not look for contradictions between a BKM and a spec. That is not
mechanically feasible: a contradiction is two prose rules that disagree in
meaning (a level, a budget, an owner, a procedure), the pages state their
rules in free text with no machine-readable form to compare, and a PR can
contradict a page it never touches. A textual heuristic would flag
rewordings and miss real conflicts. Spotting them stays with the review:
the reviewer reads the owning BKMs and specs of every area a PR touches
([pr-reviewer](../../../.claude/skills/pr-reviewer/SKILL.md#3-check), the
Code and wiki and BKM rows), and `CLAUDE.md` has a contradiction found
reconciled in the same issue. The code lives in `tools/pr/check.py`; the rules'
facts live in `cfg/pr.yaml` and `cfg/levels.yaml`.

The rules below and the skill's **(checked)** items are the same rules.
Change the skill, this page, `cfg/pr.yaml` and the tool together.

## Command line

```
./build pr check NUMBER [--repo OWNER/NAME]
./build pr check NUMBER --body-file FILE --head SHA [--files PATH...] [--labels A,B]
                  [--diff FILE]
```

- **GitHub mode** (default) reads PR `NUMBER` from the repository given by
  `--repo`, `$GH_REPO` or the `origin` remote, resolved as in the
  [labels tool](../labels/SPEC.md#repository).
- **File mode** (`--body-file`) reads the body from `FILE` and needs the
  full 40-character `--head` SHA. `--files` gives the changed paths and
  `--labels` the PR labels; when one is left out its rule is skipped with a
  `note:` line. `--diff` gives the PR's unified diff (`git diff
  origin/main...HEAD`) for the neutrality rule, and the changed paths when
  `--files` is absent. It makes no network call; the unit tests use it.
  `--head`, `--files`, `--labels` and `--diff` without `--body-file` are a
  usage error.

Exit codes: `0` every rule passes (`#N: evidence OK`), `1` at least one
finding (`#N: evidence FAIL`), `2` a usage, configuration, file or GitHub
error.

## GitHub access

Only `GET` calls, through `gh api` (REST, the labels tool's client):

| Data | REST call |
| --- | --- |
| Body, head SHA, labels | `GET repos/{repo}/pulls/{number}` |
| Changed files and their patches | `GET repos/{repo}/pulls/{number}/files?per_page=100&page=N` until a short page |

Files with status `removed` are left out: a deleted file has nothing to
mutate or scan. The added lines of each file come from its `patch`; a file
GitHub gives no patch for (binary or too large) gets a `note:` line and only
its path is scanned. A failing `gh` call exits 2 with its last error line.

## Configuration: `cfg/pr.yaml`

The full file, with its comments shortened:

```yaml
mutation_paths: [src/rtl/, src/dv/]          # changed files that need a mutation table
mutation_exempt: ["src/dv/*/regress/level*.yaml"]   # ...except a change confined to these (fnmatch; may be [])
seeded_commands: ["./build regress", "./build sim"]
versions:                                     # command substring -> tools whose version is required
  - {when: "pytest", need: [Python, pytest]}
  - {when: "./build regress", need: [Verilator, cocotb, Python]}
  - {when: "./build sim", need: [Verilator, cocotb, Python]}
```

The keys are exactly these four; each list but `mutation_exempt` is
non-empty, and each `versions` item has exactly `when` and `need`. Anything else exits 2. Level
CPU budgets are `budget_s` in `cfg/levels.yaml` (the
[regress spec](../regress/SPEC.md) validates that file).

## Parsing

- HTML comments are removed and fenced code blocks blanked before any rule
  runs, so a pasted log can't satisfy or break a rule. This is right for
  the keywords rule too: GitHub does not honour a closing keyword inside an
  HTML comment. Checked on PR #444: with `closes #268` in plain text GitHub
  listed #268 under the issues the PR may close, and with the same keyword
  inside `<!-- -->` it did not.
- The **Validation section** is the text under the `## Validation` heading,
  up to the next level-1 or level-2 heading. Every rule but keywords and
  labels reads only this section. Without it the body gets one
  `validation` finding and those rules are skipped.
- A **table** is a run of lines starting with `|`. Cells split on `|` outside
  code spans; `\|` is a literal bar. The line after the header is its
  separator row when every cell is `-`s with optional `:` ends.
- The **command table** is the first table whose first header cell is
  `Command`. Its result column is the one headed `Result`, else the last.
  A row's commands are the code spans of its first cell.

## Rules

Each finding prints as `error: #N: [<rule>] <message>`.

| Rule | Finding when |
| --- | --- |
| `head-sha` | No `At head <sha>` line; the SHA is not the full 40 characters; or it differs from the PR head. |
| `stale-sha` | A full 40-character SHA labels a result (`at <sha>`, `at head <sha>` or `@<sha>`) in the section and is not the PR head: a previous round's result left in place. Authors write baselines with a short SHA, which this rule ignores. |
| `commands` | No command table; it has no separator row or no rows; a row's first cell has no backtick-quoted command; a row's result is empty. |
| `versions` | A command contains a `versions[].when` substring and a tool in its `need` has no `<Tool> <x.y>` in the section (case-insensitive, `v` prefix allowed). |
| `seeds` | A command contains a `seeded_commands` substring and no line holds the word `seed`/`seeds` together with a digit, `crc32` or `#seeds`. |
| `levels` | A `./build regress` row runs `--level N` and its result lacks `L<N> <passed>/<total>` (or `level<N>:`), or has no `CPU <s> s` between that level's result and the next level's. |
| `budget` | That CPU is above level N's `budget_s` and the body has no `L<N> budget:` line explaining it. |
| `mutants` | A changed, non-removed file starts with a `mutation_paths` entry and matches no `mutation_exempt` glob, and the section has no table with a header cell containing `Mutant`, or that table has no separator row or no rows. A change confined to regression lists therefore needs no table; whether a new entry or seed still needs a mutant is the reviewer's call. |
| `not-run` | No `### Not run` subsection, or nothing under it before the next heading (`None.` is enough). |
| `keywords` | Over the whole body, outside code spans and code blocks: an issue is both `Part of #N` (also `Part of epic #N`) and next to a closing keyword (`close`, `closes`, `closed`, `fix`, `fixes`, `fixed`, `resolve`, `resolves`, `resolved`, optional `:`); closing keywords name more than one issue; or the body has neither a closing keyword nor `Part of`. |
| `rescope` | Over the whole body, outside code: the body closes an issue and says work moved to another issue (`moved`, `deferred`, `postponed`, `punted`, `split out`, `handed off`/`over`, `carried over`, `left` or `rescoped`, then `to #M` within the sentence), and no line `Acceptance moved: ... #M ... <issue-comment URL>` declares it with a link to the comment recording the rescope. |
| `neutrality` | A changed path outside `src/`, `wiki/src/` and `wiki/tracker/data/`, or an added line of it, holds a word of the [neutrality word list](../checks/SPEC.md#neutrality-check) or a discovered IP id used as an identifier: a whole code span or quoted string, a directory path segment (`<id>/`), `--ip <id>`, `` `<id>:<test>` `` or "<id> IP" / "<id> sub-IP" (the matcher in `tools/checks/neutrality.py`). The repository slug in a GitHub URL is exempt, and in `cfg/checks.yaml` the `words` list and `repo_slug` value; an id equal to a top-level repository entry is skipped in the path-segment and whole-span forms (they name that entry); minified `*.min.js`/`*.min.css` lines are not scanned. IPs come from the `--src-root` tree. |
| `labels` | The [labels check](../labels/SPEC.md#check) errors on the PR's labels. |

Output is a header line, `note:` lines for skipped rules, the findings in
the order above, and the verdict line:

```
pr check: #7 at 0123456789abcdef0123456789abcdef01234567
error: #7: [levels] `./build regress --level 0 --jobs 4`: no 'CPU <s> s' after L0
error: #7: [not-run] the Validation section has no '### Not run' subsection
#7: evidence FAIL
```

## Who runs it

The implementer runs it after opening the draft and after every push or
body edit; it must pass before a reviewer is spawned and before the PR is
marked ready ([pr-creation](../../../.claude/skills/pr-creation/SKILL.md#4-read-back)).
The firstmate runs it before spawning the reviewer and hands its output to
the reviewer, who treats it as given and cites it
([pr-reviewer](../../../.claude/skills/pr-reviewer/SKILL.md#3-check)).

## Tests

`tools/pr/tests/test_pr_check.py` uses fixture bodies in
`tools/pr/tests/fixtures/` (`good.md`, a full PR that passes every rule,
including a fenced log holding a closing keyword and a `## Validation` line;
`partial.md`, a `Part of`-only docs PR) and never touches the network:

- each rule is pinned by at least one mutation of `good.md` that must raise
  exactly that rule with its message (28 cases, three of them evidence
  moved into an HTML comment, which satisfies no rule), plus a head
  mismatch and a `Part of`-only PR that also closes its parent;
- each promised input form keeps `good.md` passing (`FORMS`): the
  `level<N>: P/T passed, CPU 60.2s` lines exactly as `./build regress`
  prints them, `--level=N`, the `#seeds` link to the seed rule, and an
  HTML comment holding a stale SHA and a stray table header, and one
  holding closing keywords for a second issue and for the `Part of` parent;
- stale SHA: the head cited twice passes; rescope: a declared
  `Acceptance moved:` line with the comment link passes, without it fails,
  and a `Part of` PR is never checked;
- neutrality: each identifier form and a word-list hit flagged, plain
  English, module imports, file stems, last path segments, the exempt trees,
  the slug, minified files and the `cfg/checks.yaml` word list passing; a
  path hit; the notes for unknown files, unknown added lines and missing
  patches; `parse_diff`; IP discovery skipping top-level names;
- a regression-list-only change needs no mutation table;
- the passing cases: a budget overrun with its `L<N> budget:` line, a
  mutation table not needed outside `mutation_paths` or with unknown files,
  closing keywords inside code spans, versions required only for tools run;
- the labels rule and its skip note, table and cell parsing, the report
  format;
- malformed and missing `cfg/pr.yaml`;
- GitHub mode with a fake client: pagination, removed files left out, added
  lines from patches, and only `GET` calls; CLI exit codes 0, 1 and 2 in
  both modes, and `--diff` in file mode.
