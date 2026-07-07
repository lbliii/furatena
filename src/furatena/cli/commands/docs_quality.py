"""docs-quality command parser and execution."""

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
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_docs_quality(args: argparse.Namespace) -> CommandResult:
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.docs_quality import (
        build_docs_quality_report,
        load_docs_quality_exemptions,
    )
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    roots = tuple(Path(path).expanduser().resolve() for path in args.docs_root) or (
        repo_root / "docs",
        repo_root / "content" / "furatena",
        app_root / "content",
    )
    exemptions_path = (
        Path(args.exemptions).expanduser().resolve()
        if args.exemptions
        else repo_root / "docs" / "docs-quality-exemptions.json"
    )
    baseline_path = (
        Path(args.inventory_baseline).expanduser().resolve()
        if args.inventory_baseline
        else repo_root / "docs" / "public-surface-inventory.json"
    )
    previous = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.is_file()
        else None
    )
    docs = DocsApp.from_paths(
        _docs_yaml(args),
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    report = build_docs_quality_report(
        docs,
        documentation_roots=roots,
        exemptions=load_docs_quality_exemptions(exemptions_path),
        previous_inventory=previous,
    )
    diagnostics = [
        Diagnostic(
            severity="error",
            message=str(item["message"]),
            source_path=item.get("source_path"),
            rule_id=str(item["rule_id"]),
            next_action=(
                f"Owner: {item['owner']}. Recommended {item['recommended_page_type']} page. "
                f"{item['next_action']}"
            ),
        )
        for item in report["findings"]
    ]
    diagnostics.extend(
        Diagnostic(
            severity="error",
            message=f"unused docs-quality exemption: {item['finding']}",
            source_path=str(exemptions_path),
            rule_id="fura.docs_quality.exemption",
            next_action="Remove the exemption or update it to the current finding id.",
        )
        for item in report["unused_exemptions"]
    )
    ok = bool(report["ok"])
    summary = report["summary"]
    return CommandResult(
        command=command_name(args),
        ok=ok,
        exit_code=ExitCode.SUCCESS if ok else ExitCode.VALIDATION_ERROR,
        summary=(
            f"docs quality found {summary['active_count']} active issue(s), "
            f"{summary['exempted_count']} exempted, and "
            f"{summary['unused_exemption_count']} unused exemption(s)"
        ),
        diagnostics=tuple(diagnostics),
        data={"exemptions_path": exemptions_path, **report},
    )


def configure(sub: Any) -> None:
    parser = sub.add_parser(
        "docs-quality",
        help="Gate orphan, navigation, link, and public-feature coverage",
    )
    parser.add_argument("--docs-root", action="append", default=[], help="Documentation root")
    parser.add_argument("--exemptions", default=None, help="Reasoned exemption JSON path")
    parser.add_argument(
        "--inventory-baseline",
        default=None,
        help="Prior inventory used to detect stale public-feature coverage",
    )
    parser.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    parser.set_defaults(handler=_run_docs_quality)


COMMAND = CommandModule("docs-quality", configure)
