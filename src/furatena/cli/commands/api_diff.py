"""api diff command parser and execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from furatena.cli.commands._shared import CommandModule, _finish_result, _json_output
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_api_diff(args: argparse.Namespace) -> None:
    from furatena.catalog.api_governance import diff_openapi_specs

    old_path = Path(args.old).expanduser().resolve()
    new_path = Path(args.new).expanduser().resolve()
    try:
        payload = diff_openapi_specs(old_path, new_path)
    except Exception as exc:
        result = CommandResult(
            command=command_name(args),
            ok=False,
            exit_code=ExitCode.VALIDATION_ERROR,
            summary="api diff failed",
            diagnostics=(
                Diagnostic(
                    severity="error",
                    message=f"Failed to diff OpenAPI specs: {exc}",
                    rule_id="fura.api",
                    next_action="Fix the OpenAPI spec paths or YAML syntax and rerun fura api-diff.",
                ),
            ),
            data={"old_spec": str(old_path), "new_spec": str(new_path)},
        )
        _finish_result(result, json_output=_json_output(args))
        return
    result = CommandResult(
        command=command_name(args),
        ok=True,
        summary=(
            "api diff completed: "
            f"{payload['summary']['added']} added, "
            f"{payload['summary']['removed']} removed, "
            f"{payload['summary']['changed']} changed, "
            f"{payload['summary']['breaking']} breaking"
        ),
        data=payload,
    )
    if _json_output(args):
        _finish_result(result, json_output=True)
        return
    print(result.summary)
    for label in ("added", "removed", "changed", "breaking"):
        items = payload[label]
        if not items:
            continue
        print(f"\n{label.title()}:")
        for item in items:
            op = f"{item['method']} {item['path']}"
            if item.get("operation_id"):
                op = f"{op} ({item['operation_id']})"
            changes = item.get("changes")
            suffix = f" - {', '.join(changes)}" if changes else ""
            print(f"- {op}{suffix}")


def configure(sub: Any) -> None:
    api_diff = sub.add_parser("api-diff", help="Compare two OpenAPI specs for operation changes")
    api_diff.add_argument("old", help="Old OpenAPI YAML/JSON spec")
    api_diff.add_argument("new", help="New OpenAPI YAML/JSON spec")
    api_diff.add_argument(
        "--json", action="store_true", help="Emit the standard command result JSON"
    )
    api_diff.set_defaults(handler=_run_api_diff)


COMMAND = CommandModule("api-diff", configure)
