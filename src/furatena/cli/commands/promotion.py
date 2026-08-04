"""Read-only publication promotion state commands."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from furatena.catalog.publication_promotion import (
    PromotionEnvironment,
    PublicationPromotionError,
    PublicationPromotionReader,
)
from furatena.cli.commands._shared import CommandModule
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_promotion(args: argparse.Namespace) -> CommandResult:
    reader = PublicationPromotionReader(Path(args.state_root))
    action = str(args.promotion_command)
    try:
        if action == "current":
            environment = PromotionEnvironment(args.environment)
            value = reader.current(environment)
            data: dict[str, object] = {
                "environment": environment.value,
                "current": value,
            }
            summary = f"read current {environment.value} publication artifact"
        elif action == "history":
            environment = PromotionEnvironment(args.environment)
            history = reader.history(environment)
            data = {
                "environment": environment.value,
                "history": history,
                "count": len(history),
            }
            summary = f"read {len(history)} {environment.value} promotion record(s)"
        else:
            value = reader.status(str(args.operation_id))
            data = {"operation": value}
            summary = f"read promotion operation {args.operation_id}"
    except PublicationPromotionError as exc:
        return CommandResult(
            command=command_name(args),
            ok=False,
            exit_code=ExitCode.VALIDATION_ERROR,
            summary=f"publication promotion read failed: {exc}",
            diagnostics=(
                Diagnostic(
                    severity="error",
                    message=str(exc),
                    rule_id=exc.code,
                    next_action=exc.remediation,
                ),
            ),
            data={"error_code": exc.code},
        )
    return CommandResult(
        command=command_name(args),
        ok=True,
        summary=summary,
        data=data,
    )


def _common(parser: Any) -> None:
    parser.add_argument(
        "--state-root",
        required=True,
        help="Private durable publication-promotion state directory",
    )
    parser.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    parser.set_defaults(handler=_run_promotion)


def configure(sub: Any) -> None:
    parser = sub.add_parser(
        "promotion",
        help="Read current artifact, immutable history, and promotion operation status",
    )
    actions = parser.add_subparsers(dest="promotion_command", required=True)
    environments = tuple(item.value for item in PromotionEnvironment if item != "artifact")

    current = actions.add_parser("current", help="Read one environment's current artifact")
    current.add_argument("--environment", choices=environments, required=True)
    _common(current)

    history = actions.add_parser("history", help="Read immutable promotion history")
    history.add_argument("--environment", choices=environments, required=True)
    _common(history)

    status = actions.add_parser("status", help="Read one promotion operation receipt")
    status.add_argument("--operation-id", required=True)
    _common(status)


COMMAND = CommandModule("promotion", configure)
