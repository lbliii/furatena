"""impact command parser and execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from furatena.cli.commands._shared import (
    CommandModule,
    _app_root,
    _autodoc_config,
    _docs_yaml,
    _ensure_pythonpath,
    _finish_result,
    _json_output,
    _repo_for_app,
)
from furatena.cli.contracts import CommandResult, command_name, diagnostic_from_message


def _run_impact(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.impact import stale_impact_report
    from furatena.catalog.lifecycle import check_stale_public_outputs
    from furatena.catalog.registry import CatalogRegistry

    app_root = _app_root(args)
    repo = _repo_for_app(app_root)
    config = load_docs_config(_docs_yaml(args))
    mounts_path = config.mounts_path or app_root / "mounts.yaml"
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=repo,
        app_root=app_root,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        catalog_identity=config.identity.to_meta(),
        autodoc=not args.no_autodoc,
        autodoc_config=_autodoc_config(args, repo),
    )
    frozen_dir = Path(args.frozen).expanduser().resolve() if args.frozen else app_root / "frozen"
    stale_public_outputs = tuple(dict.fromkeys(check_stale_public_outputs(registry, frozen_dir)))
    report = stale_impact_report(
        registry,
        slug=args.slug,
        stale_public_outputs=stale_public_outputs,
        include_private=args.include_private,
    )
    diagnostics = tuple(
        diagnostic_from_message(
            message,
            severity="warning",
            rule_id="fura.impact.stale_public_output",
            next_action="Refresh frozen public output with fura freeze or fura export --fresh.",
        )
        for message in stale_public_outputs
    )
    if _json_output(args):
        _finish_result(
            CommandResult(
                command=command_name(args),
                ok=True,
                summary=f"impact report completed with {report['stale_count']} stale item(s)",
                diagnostics=diagnostics,
                data=report,
            ),
            json_output=True,
        )
        return
    if report["stale_count"] == 0:
        print("impact: no stale content detected")
        return
    print(f"impact: {report['stale_count']} stale item(s)")
    if report["task_markdown"]:
        print(report["task_markdown"])


def configure(sub: Any) -> None:
    impact = sub.add_parser("impact", help="Report stale content impact for agents and CI")
    impact.add_argument(
        "--slug", default=None, help="Optional slug used to scope stale-impact entries"
    )
    impact.add_argument(
        "--frozen", default=None, help="Frozen catalog directory (default app/frozen)"
    )
    impact.add_argument(
        "--include-private", action="store_true", help="Include private graph context"
    )
    impact.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    impact.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    impact.set_defaults(handler=_run_impact)


COMMAND = CommandModule("impact", configure)
