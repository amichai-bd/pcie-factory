# Labels tool: `./build labels`

`./build labels` keeps the repository's GitHub labels equal to
`cfg/labels.yaml` and checks the label rules on one issue or pull request.
The code lives in `tools/labels/core.py`; the [DevOps BKM](../../bkm/devops.md#labels)
owns when each label is used.

```
./build labels sync  [--dry-run] [--prune] [--repo OWNER/NAME] [--src-root DIR]
./build labels check NUMBER                 [--repo OWNER/NAME] [--src-root DIR]
```

## Configuration: `cfg/labels.yaml`

The file is the source of truth for label names, colors and descriptions.
Its top level is a mapping with one key, `dimensions`. Each dimension is:

```yaml
dimensions:
  type:
    prefix: "type:"          # name prefix; "" for unprefixed (state) labels
    rule: exactly-one        # exactly-one | at-least-one | at-most-one | any
    labels:
      feat: {color: "1d76db", description: "New capability or behavior"}
  ip:
    prefix: "ip:"
    rule: any
    derived: ip              # one label per discovered IP
    color: "006b75"
    description: "Touches IP {id}"
```

A label's name is `prefix + value`. Validation fails (exit 2) when:

- a key is unknown or missing, or a dimension has no labels (unless derived);
- a color isn't 6 hex digits without `#` (stored lower case);
- a description is empty or longer than 100 characters (GitHub's limit);
- two dimensions share a prefix, two labels share a name (ignoring case,
  as GitHub does), or an unprefixed label starts with another dimension's
  prefix;
- a derived dimension has no prefix or its description lacks `{id}`.

`derived: ip` is the only derivation. It makes `ip:<id>` for every IP that
[IP discovery](../build/SPEC.md) finds under the source root (`--src-root`,
`$BUILD_SRC_ROOT`, or `src/`), so `ip:a` and `ip:a_b` for a flat sub-IP. A
missing source root yields no `ip:` labels. No IP name is written in the
config. When an IP id changes (for example a sub-IP id from the superseded
nested layout, `ip:a/b`, becoming `ip:a_b`), `sync` creates the new label and
reports the old one as `extra`; `sync --prune` deletes it (removing it from
the issues that carry it), or a maintainer renames it by hand in GitHub to
keep those issues labeled.

The current dimensions are `type:` (exactly one), `area:` (at least one),
`ip:` (any), `prio:` (at most one) and the unprefixed state labels (any).

## Repository

`--repo OWNER/NAME`, else `$GH_REPO`, else the `origin` remote of the
repository (https, ssh, or a proxy URL whose last two path parts are the
owner and name). Anything else is exit 2.

## GitHub access

All calls go through `gh api` (REST; `gh` must be installed and
authenticated, for example with `GH_TOKEN`). No GraphQL is used.

| Operation | REST call |
| --- | --- |
| List labels | `GET repos/{repo}/labels?per_page=100&page=N` until a short page |
| Create | `POST repos/{repo}/labels` (`name`, `color`, `description`) |
| Update | `PATCH repos/{repo}/labels/{url-encoded current name}` (`new_name`, `color`, `description`) |
| Delete | `DELETE repos/{repo}/labels/{url-encoded name}` |
| Issue or PR labels | `GET repos/{repo}/issues/{number}` |

A failing `gh` call, or one whose output is not JSON, stops the command with
exit 2 and the last line of its error (for example an HTTP 403).

## `sync`

Labels are matched by name ignoring case. For each configured label, in
name order: missing on GitHub is a **create** (`+`); a different name case,
color or description is an **update** (`~`, which renames to the configured
case). Then, for each GitHub label not in the config: with `--prune` a
**delete** (`-`), otherwise an **extra** (`?`) that is reported and kept.

**Prune guard:** when a derived dimension has no labels (no IP discovered,
which includes a missing or wrong source root), `--prune` refuses to delete
any label under that dimension's prefix: the command exits 2 before any
write call, also in `--dry-run`. This stops a bad `--src-root` from deleting
every `ip:` label and stripping it from all issues. Delete such a label by
hand if that is really intended. With at least one IP discovered, a stale
`ip:` label is pruned normally.

Output is one header line, one line per change, and a count line:

```
labels dry run: OWNER/NAME (23 labels in the config)
+ area:build: color d4c5f9, 'The ./build entry point and tools/'
~ in-progress: description '' -> 'Claimed; see the claim comment for owner and branch'
? bug: not in the config (kept; --prune deletes)
would apply: 22 create, 1 update, 0 delete; 10 extra kept
```

`--dry-run` makes no write call and prints `would apply`; otherwise the
changes are applied in the printed order and the line reads `applied`. A
second sync after a successful one shows no create, update or delete. Exit
0 on success.

The `labels` workflow (`.github/workflows/labels.yml`) runs `sync` without
`--prune` on a push to `main` that changes `cfg/labels.yaml`, `tools/labels/`,
`tools/build/ip.py` and `tools/build/filelist.py` (IP discovery and `-F` reachability), `tools/cli/`, `src/` or the workflow
itself, and on manual dispatch, with the workflow token (`issues: write`).
The runner needs `python3` and the GitHub CLI `gh` on `PATH`; a first step
fails with a clear error when `gh` is missing.

## `check`

Reads the labels of issue or pull request `NUMBER` and applies the rules:

- each label is assigned to the dimension with the longest matching prefix;
  an unprefixed label belongs to an unprefixed dimension only if listed;
- a label under a dimension's prefix that the config doesn't define (such as
  `type:nope` or the `ip:` label of a removed IP) is an error;
- the counts per dimension must satisfy its rule;
- labels outside every dimension (GitHub's defaults such as `bug`) are
  warnings;
- names are compared case-sensitively with the config (unlike `sync`). After
  a sync, GitHub's names have the configured case, so this only matters for
  a label renamed by hand since then.

It prints `warning:` and `error:` lines, then `#N: labels OK` (exit 0) or
`#N: labels FAIL` (exit 1). Configuration and GitHub errors exit 2.

## Tests

`tools/labels/tests/test_labels.py` never touches the network: it replaces
`subprocess.run` to check the exact `gh api` commands and pagination, and uses
an in-memory GitHub for sync and check. It covers config validation, `ip:`
derivation (including an old nested sub-IP label left as `extra`, or
deleted with `--prune`, next to its new flat label), slug resolution, non-JSON `gh` output, the plan and its order,
dry run with no writes, convergence after sync with and without `--prune`,
the prune guard, each check rule, and the CLI exit codes.
