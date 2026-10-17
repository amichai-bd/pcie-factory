# PR tool: `./build pr check`

`./build pr check` is a read-only machine pre-check of the evidence a pull
request body records. It reports what the
[pr-creation skill](../../../.claude/skills/pr-creation/SKILL.md#2-compose-the-body)
requires that is missing or malformed, so the gap is fixed before a reviewer
is spawned and the reviewer spends the round on substance. It never judges
whether the evidence is good enough (the reviewer's job), never runs a test
and never writes to GitHub. The code lives in `tools/pr/check.py`; the rules'
facts live in `cfg/pr.yaml` and `cfg/levels.yaml`.

The rules below and the skill's **(checked)** items are the same rules.
Change the skill, this page, `cfg/pr.yaml` and the tool together.

## Command line

```
./build pr check NUMBER [--repo OWNER/NAME]
./build pr check NUMBER --body-file FILE --head SHA [--files PATH...] [--labels A,B]
```

- **GitHub mode** (default) reads PR `NUMBER` from the repository given by
  `--repo`, `$GH_REPO` or the `origin` remote, resolved as in the
  [labels tool](../labels/SPEC.md#repository).
- **File mode** (`--body-file`) reads the body from `FILE` and needs the
  full 40-character `--head` SHA. `--files` gives the changed paths and
  `--labels` the PR labels; when one is left out its rule is skipped with a
  `note:` line. It makes no network call; the unit tests use it. `--head`,
  `--files` and `--labels` without `--body-file` are a usage error.

Exit codes: `0` every rule passes (`#N: evidence OK`), `1` at least one
finding (`#N: evidence FAIL`), `2` a usage, configuration, file or GitHub
error.

## GitHub access

Only `GET` calls, through `gh api` (REST, the labels tool's client):

| Data | REST call |
| --- | --- |
| Body, head SHA, labels | `GET repos/{repo}/pulls/{number}` |
| Changed files | `GET repos/{repo}/pulls/{number}/files?per_page=100&page=N` until a short page |

Files with status `removed` are left out: a deleted file has nothing to
mutate. A failing `gh` call exits 2 with its last error line.

## Configuration: `cfg/pr.yaml`

```yaml
mutation_paths: [src/rtl/, src/dv/]          # changed files that need a mutation table
seeded_commands: ["./build regress", "./build sim"]
versions:                                     # command substring -> tools whose version is required
  - {when: "pytest", need: [Python, pytest]}
  - {when: "./build regress", need: [Verilator, cocotb, Python]}
```

The keys are exactly these three; each list is non-empty, and each
`versions` item has exactly `when` and `need`. Anything else exits 2. Level
CPU budgets are `budget_s` in `cfg/levels.yaml` (the
[regress spec](../regress/SPEC.md) validates that file).

## Parsing

- HTML comments are removed and fenced code blocks blanked before any rule
  runs, so a pasted log can't satisfy or break a rule.
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
| `commands` | No command table; it has no separator row or no rows; a row's first cell has no backtick-quoted command; a row's result is empty. |
| `versions` | A command contains a `versions[].when` substring and a tool in its `need` has no `<Tool> <x.y>` in the section (case-insensitive, `v` prefix allowed). |
| `seeds` | A command contains a `seeded_commands` substring and no line holds the word `seed`/`seeds` together with a digit, `crc32` or `#seeds`. |
| `levels` | A `./build regress` row runs `--level N` and its result lacks `L<N> <passed>/<total>` (or `level<N>:`), or has no `CPU <s> s` between that level's result and the next level's. |
| `budget` | That CPU is above level N's `budget_s` and the body has no `L<N> budget:` line explaining it. |
| `mutants` | A changed, non-removed file starts with a `mutation_paths` entry and the section has no table with a header cell containing `Mutant`, or that table has no separator row or no rows. |
| `not-run` | No `### Not run` subsection, or nothing under it before the next heading (`None.` is enough). |
| `keywords` | Over the whole body, outside code spans and code blocks: an issue is both `Part of #N` (also `Part of epic #N`) and next to a closing keyword (`close`, `closes`, `closed`, `fix`, `fixes`, `fixed`, `resolve`, `resolves`, `resolved`, optional `:`); closing keywords name more than one issue; or the body has neither a closing keyword nor `Part of`. |
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
  exactly that rule with its message (21 cases), plus a head mismatch and a
  `Part of`-only PR that also closes its parent;
- the passing cases: a budget overrun with its `L<N> budget:` line, a
  mutation table not needed outside `mutation_paths` or with unknown files,
  closing keywords inside code spans, versions required only for tools run;
- the labels rule and its skip note, table and cell parsing, the report
  format;
- malformed and missing `cfg/pr.yaml`;
- GitHub mode with a fake client: pagination, removed files left out, and
  only `GET` calls; CLI exit codes 0, 1 and 2 in both modes.
