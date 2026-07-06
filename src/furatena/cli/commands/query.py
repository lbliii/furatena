"""query command parser and execution."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from furatena.cli.commands._shared import (
    CommandModule,
    _app_root,
    _autodoc_config,
    _docs_yaml,
    _ensure_pythonpath,
    _finish_result,
    _repo_for_app,
)
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_query(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.query import query_catalog
    from furatena.catalog.registry import CatalogRegistry

    app_root = _app_root(args)
    repo = _repo_for_app(app_root)
    config = load_docs_config(_docs_yaml(args))
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    autodoc_config = _autodoc_config(args, repo)
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=repo,
        app_root=app_root,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        catalog_identity=config.identity.to_meta(),
        autodoc=not args.no_autodoc,
        autodoc_config=autodoc_config,
    )
    results = query_catalog(
        registry,
        directive=args.directive,
        heading=args.heading,
        mount=args.mount,
        edition=args.edition,
        tag=args.tag,
        url_prefix=args.url_prefix,
    )
    if args.json:
        exit_code = ExitCode.SUCCESS if results else ExitCode.WARNING
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=bool(results),
                exit_code=exit_code,
                summary=f"matched {len(results)} catalog node(s)",
                diagnostics=()
                if results
                else (
                    Diagnostic(
                        severity="warning",
                        message="query matched no catalog nodes",
                        next_action="Relax query filters or inspect available mounts and URLs.",
                    ),
                ),
                data={
                    "count": len(results),
                    "results": results,
                    "filters": {
                        "directive": args.directive,
                        "heading": args.heading,
                        "mount": args.mount,
                        "edition": args.edition,
                        "tag": args.tag,
                        "url_prefix": args.url_prefix,
                    },
                },
            ),
            json_output=True,
        )
        return
    else:
        for row in results:
            print(f"{row['url']}\t{row['title']}")
    if not results:
        raise SystemExit(1)


def configure(sub: Any) -> None:
    query = sub.add_parser("query", help="Query catalog content IR")
    query.add_argument("--directive", default=None)
    query.add_argument("--heading", default=None)
    query.add_argument("--mount", default=None)
    query.add_argument("--edition", default=None)
    query.add_argument("--tag", default=None)
    query.add_argument("--url-prefix", default=None)
    query.add_argument("--json", action="store_true")
    query.add_argument("--no-autodoc", action="store_true")
    query.set_defaults(handler=_run_query)


COMMAND = CommandModule("query", configure)
