"""evals command parser and execution."""

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
    _json_output,
    _repo_for_app,
)
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_evals(args: argparse.Namespace) -> None:
    _ensure_pythonpath()
    sys.path.insert(0, str(_app_root(args)))
    from furatena.catalog.agent_evals import run_agent_evaluations
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = _app_root(args)
    repo_root = _repo_for_app(app_root)
    docs = DocsApp.from_paths(
        _docs_yaml(args),
        repo_root=repo_root,
        autodoc_config=_autodoc_config(args, repo_root),
        autodoc=not args.no_autodoc,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        workers=args.workers,
    )
    report = run_agent_evaluations(
        docs,
        include_private=args.include_private,
        categories=args.category,
    )
    fail_count = int(report["fail_count"])
    diagnostics = tuple(
        Diagnostic(
            severity="error",
            message=result["message"],
            source_path=result["id"],
            rule_id=f"fura.evals.{result['category']}",
            next_action="Inspect the observed and expected eval data, then fix retrieval or MCP metadata.",
        )
        for result in report["results"]
        if result["status"] == "fail"
    )
    exit_code = ExitCode.VALIDATION_ERROR if fail_count else ExitCode.SUCCESS
    result = CommandResult(
        command=command_name(args),
        ok=fail_count == 0,
        exit_code=exit_code,
        summary=(
            f"agent evals completed with {report['pass_count']} pass(es), "
            f"{report['fail_count']} failure(s), and {report['skip_count']} skip(s)"
        ),
        diagnostics=diagnostics,
        data=report,
    )
    if _json_output(args):
        _finish_result(result, json_output=True)
        return
    print(result.summary)
    for item in report["results"]:
        print(f"{item['status']}: {item['id']} - {item['message']}")
    if fail_count:
        raise SystemExit(int(exit_code))


def configure(sub: Any) -> None:
    evals = sub.add_parser(
        "evals", help="Run deterministic agent retrieval and tool-selection evals"
    )
    evals.add_argument(
        "--category",
        action="append",
        default=[],
        help="Run one eval category or case id; may be repeated",
    )
    evals.add_argument(
        "--include-private", action="store_true", help="Exercise include-private author MCP evals"
    )
    evals.add_argument("--no-autodoc", action="store_true", help="Skip autodoc slice")
    evals.add_argument("--workers", type=int, default=None, help="Parallel index workers")
    evals.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    evals.set_defaults(handler=_run_evals)


COMMAND = CommandModule("evals", configure)
