"""docs-inventory command parser and execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from furatena.cli.commands._shared import (
    CommandModule,
    _app_root,
    _autodoc_config,
    _docs_yaml,
    _repo_for_app,
)
from furatena.cli.contracts import CommandResult, command_name


def _run_docs_inventory(args: argparse.Namespace) -> CommandResult:
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.docs_inventory import build_documentation_inventory
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    roots = tuple(Path(path).expanduser().resolve() for path in args.docs_root) or (
        repo_root / "docs",
        repo_root / "content" / "furatena",
        app_root / "content",
    )
    output = Path(args.output).expanduser().resolve() if args.output else None
    baseline = Path(args.baseline).expanduser().resolve() if args.baseline else output
    previous = None
    if baseline is not None and baseline.is_file():
        previous = json.loads(baseline.read_text(encoding="utf-8"))
    docs = DocsApp.from_paths(
        _docs_yaml(args),
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    report = build_documentation_inventory(
        docs,
        documentation_roots=roots,
        previous=previous,
    )
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = report["summary"]
    return CommandResult(
        command=command_name(args),
        ok=True,
        summary=(
            f"inventoried {report['surface_count']} public surfaces: "
            f"{summary['documented']} documented, {summary['missing']} missing, "
            f"{summary['stale']} stale"
        ),
        data={"output": output, **report},
    )


def configure(sub: Any) -> None:
    parser = sub.add_parser(
        "docs-inventory",
        help="Inventory public surfaces and documentation coverage",
    )
    parser.add_argument("--docs-root", action="append", default=[], help="Documentation root")
    parser.add_argument("--baseline", default=None, help="Prior JSON inventory for stale detection")
    parser.add_argument(
        "--output", default=None, help="Write the plain inventory JSON to this path"
    )
    parser.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    parser.set_defaults(handler=_run_docs_inventory)


COMMAND = CommandModule("docs-inventory", configure)
