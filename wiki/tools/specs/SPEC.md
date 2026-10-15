# Specs tool: `./build specs`

`./build specs` keeps a local cache of the official reference documents the
design follows, so that agents and reviewers can check every citation
against the real text. The documents are copyrighted and their licenses
allow a local copy at most, so the cache lives **outside the repository**
and nothing from it is ever committed; the
[spec-cache check](../checks/SPEC.md#spec-cache-check) enforces that. The
code lives in `tools/specs/` (`core.py`, `cli.py`).

## Usage

```
./build specs fetch [--id ID]... [--timeout S]
./build specs list
./build specs path [ID] [--pdf | --local]
```

| Action | Does | Exit |
| --- | --- | --- |
| `fetch` | Brings each listed document (or each `--id`) into the cache, verifies its sha256, and extracts its text next to it. | 0, even when a download is unreachable or a local document is not provided; 1 when a document fails its sha256 pin; 2 on an invalid list or cache location. |
| `list` | One line per document: id, status, revision, title, publisher, and its URL or local path. | 0; 2 on an invalid list or cache location. |
| `path` | Without `ID`, the cache directory. With `ID`, the extracted text `<cache>/<id>.txt`; `--pdf` the cached document; `--local` where a captain-supplied copy goes. | 0; 1 when the printed text or document does not exist yet; 2 for an unknown id. |

| Option | Default | Meaning |
| --- | --- | --- |
| `--id ID` | every document | Fetch only these ids (repeatable). |
| `--timeout S` | 30 | Seconds for each download before it is skipped. |

Statuses: `present` (the document matches its pin, and its text exists),
`present-no-text` (no text extractor ran), `missing` (a downloadable
document not fetched yet), `local-not-provided` and `mismatch` (a cached
copy no longer matches its pin; the next `fetch` replaces it).

## Inputs and outputs

**Reference list:** `specs.yaml` at the root of the source tree. It names
the design's documents, so it lives in the design area, never in `cfg/`.
A missing file means no documents.

```yaml
documents:
  - id: alpha                 # unique: lower case, digits, '-'; an entry heading on wiki/src/references.md
    title: Alpha Bus Specification
    revision: "2.1"
    publisher: Example Body
    source: https://example.com/alpha.pdf   # the publisher's own URL, or `local`
    sha256: <64 hex digits>   # required for a URL; optional for `local`
    license: internal use only; never redistributed
```

A `source` is an `https://` URL on the publisher's own site (never a
mirror), `local` for a document only the captain can supply, or a
`file://` URL (tests). A missing or extra key, an empty value, a bad id,
a duplicate id, a URL without a pin or a malformed hash is an error (exit 2).

**Cache:** `$BUILD_SPECS_CACHE`, else
`${XDG_CACHE_HOME:-~/.cache}/<repo>/specs`, where `<repo>` is the name of
the main worktree's folder, so every worktree of a clone shares one cache.
A cache or local folder inside the repository (the current worktree or the
main one) is refused with exit 2. The cache holds `<id>.pdf` and
`<id>.txt`; partial files are written as hidden `.<id>.*.part` and renamed
when complete.

**Local documents:** a `local` document is read from
`<local>/<id>.pdf`, where `<local>` is `$BUILD_SPECS_LOCAL`, else
`<cache>/local`. The captain copies a document they hold under their own
license there; the factory works without it.

Printed paths under the home directory are shown as `~/...`, because CI
logs are public.

## Behavior

1. **Download** (URL source, not yet cached): stream the URL with a
   timeout, at most 256 MiB, into a partial file. An unreachable URL or a
   network refusal prints `<id>: unavailable, skipped (<reason>)` and the run
   continues. A cached copy that matches its pin is never downloaded again.
2. **Verify:** the sha256 of the new copy must equal the pin. A mismatch
   prints `ERROR <id>: sha256 mismatch for <source>: got <h>, pinned <h>;
   not cached`, deletes the copy and makes the exit 1; it is never accepted
   silently. A cached copy that no longer matches is deleted with its text
   and acquired again. An unpinned local copy is accepted and its hash
   printed, so it can be pinned.
3. **Extract:** `pdftotext -enc UTF-8 <id>.pdf <id>.txt`, when the text is
   missing or older than the document. Without `pdftotext` on `PATH`, or on
   its failure, the document stays cached and the run prints
   `<id>: text not extracted (<reason>)`.

**Where it runs.** The cloud session-start hook runs `fetch` after the
toolchain, best effort ([host spec](../host/SPEC.md#cloud-session-start-hook)),
and `ci-gate` runs it before `./build check` with `continue-on-error`,
so the spec-cache check compares against the documents on the CI host
([CI](../../bkm/devops.md#continuous-integration)). Neither ever fails on it.

**How agents use the cache:** read `$(./build specs path <id>)` to check a
citation or a claim, and never copy its text into the repository
([wiki BKM, citations](../../bkm/wiki.md#citations)).

## Files

| Path | Role |
| --- | --- |
| `tools/specs/core.py` | List parsing, cache location, fetch, verify, extract, status. |
| `tools/specs/cli.py` | The `fetch`, `list` and `path` actions. |
| `tools/specs/tests/test_specs_tool.py` | Unit tests. |

## Tests

No test uses the network or needs `pdftotext`: documents are served from
`file://` URLs and extraction is a stub.

| Behavior | Test |
| --- | --- |
| List parsing and every malformed case | `test_list_parses`, `test_list_rejects_malformed` |
| Cache default, override, refusal inside the repository | `test_cache_default_is_outside_repo_and_named_after_it`, `test_cache_env_override_and_inside_repo_rejected` |
| Download, verify, extract; a second run reuses the cache | `test_fetch_downloads_verifies_and_extracts` |
| sha256 mismatch: exit 1, nothing cached | `test_fetch_rejects_a_sha256_mismatch` |
| Unreachable URL is skipped, exit 0 | `test_fetch_unreachable_is_skipped_not_failed` |
| No extractor: document kept, `present-no-text` | `test_fetch_without_extractor_keeps_document` |
| Tampered cache: `mismatch`, then replaced | `test_tampered_cache_is_a_mismatch_and_refetched` |
| Local document: not provided, provided, wrong pin | `test_local_document_missing_then_provided`, `test_local_document_with_wrong_pin_fails` |
| Command line: `list`, `path`, `fetch --id`, unknown id, cache in the repository | `test_cli_list_path_and_fetch`, `test_cli_rejects_cache_inside_repo` |
