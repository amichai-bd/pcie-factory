"""``./build specs`` arguments and handler (wiki/tools/specs/SPEC.md)."""

from __future__ import annotations

import argparse
import sys

from build.ip import Context

from . import core


def add_args(p: argparse.ArgumentParser) -> None:
    act = p.add_subparsers(dest="action", required=True, metavar="action")
    f = act.add_parser("fetch", help="download or copy each listed document into the cache, "
                       "verify its sha256 and extract its text")
    f.add_argument("--id", action="append", help="only this document id; repeatable")
    f.add_argument("--timeout", type=float, default=30.0,
                   help="seconds per download before it is skipped (default: 30)")
    act.add_parser("list", help="show each document as present, missing or local-not-provided")
    pa = act.add_parser("path", help="print the cache directory, or a document's text path")
    pa.add_argument("id", nargs="?", help="document id (default: print the cache directory)")
    kind = pa.add_mutually_exclusive_group()
    kind.add_argument("--pdf", action="store_true", help="the cached document, not its text")
    kind.add_argument("--local", action="store_true",
                      help="where a captain-supplied copy of a local document goes")
    pa.add_argument("--absolute", action="store_true",
                    help="print the absolute path (for scripts) instead of the ~/ form")


def _select(docs: list[core.Doc], ids: list[str] | None) -> list[core.Doc]:
    if not ids:
        return docs
    known = {d.id: d for d in docs}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise core.SpecsError(f"unknown document id(s) {', '.join(unknown)} "
                              f"(known: {', '.join(known) or 'none'})")
    return [known[i] for i in dict.fromkeys(ids)]


def _rel(ctx: Context, path) -> str:
    try:
        return str(path.relative_to(ctx.repo_root))
    except ValueError:
        return core.show(path)


def run(ctx: Context, args) -> int:
    docs = core.load_list(ctx.src_root)
    cache = core.cache_dir(ctx.repo_root)
    local = core.local_dir(ctx.repo_root, cache)
    if args.action == "fetch":
        docs = _select(docs, args.id)
        print(f"specs: {len(docs)} document(s) from {_rel(ctx, ctx.src_root / core.LIST_FILE)}; "
              f"cache {core.show(cache)}; text extractor {core.extractor_version()}")
        return core.fetch(docs, cache, local, timeout=args.timeout,
                          out=lambda s: print(s, flush=True))
    if args.action == "list":
        print(f"cache: {core.show(cache)}")
        width = max((len(d.id) for d in docs), default=0)
        for d in docs:
            st = core.status(d, cache)
            where = d.source if not d.local else f"local ({core.show(local / (d.id + '.pdf'))})"
            print(f"{d.id:<{width}}  {st:<18}  rev {d.revision}  {d.title} ({d.publisher}); {where}")
        return 0
    shown = (lambda q: str(q)) if args.absolute else core.show
    if args.id is None:
        print(shown(cache))
        return 0
    doc = _select(docs, [args.id])[0]
    path = (local / f"{doc.id}.pdf" if args.local else
            core.pdf_path(cache, doc) if args.pdf else core.text_path(cache, doc))
    print(shown(path))
    if not args.local and not path.is_file():
        print(f"specs: {shown(path)} does not exist (run ./build specs fetch)", file=sys.stderr)
        return 1
    return 0
