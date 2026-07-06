"""Agent contract semantic diff command."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from furatena.cli.commands._shared import CommandModule
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_agent_diff(args: argparse.Namespace) -> CommandResult:
    from furatena.catalog.agent_contract_diff import diff_agent_contract_fixtures

    old_path = Path(args.old).expanduser().resolve()
    new_path = Path(args.new).expanduser().resolve()
    try:
        payload = diff_agent_contract_fixtures(
            old_path,
            new_path,
            compatibility_decision=args.decision,
        )
    except Exception as exc:
        return CommandResult(
            command=command_name(args),
            ok=False,
            exit_code=ExitCode.VALIDATION_ERROR,
            summary="agent contract diff failed",
            diagnostics=(
                Diagnostic(
                    severity="error",
                    message=f"Failed to diff agent contract fixtures: {exc}",
                    rule_id="fura.agent.contract_diff",
                    next_action="Fix the fixture paths or JSON and rerun fura agent-diff.",
                ),
            ),
            data={"old_fixture": str(old_path), "new_fixture": str(new_path)},
        )

    summary = payload["summary"]
    line = (
        "agent contract diff completed: "
        f"{summary['added']} added, {summary['removed']} removed, "
        f"{summary['changed']} changed, {summary['breaking']} breaking"
    )
    requires_decision = payload["compatibility"]["status"] == "requires-decision"
    diagnostics: tuple[Diagnostic, ...] = ()
    if requires_decision:
        diagnostics = (
            Diagnostic(
                severity="error",
                message="Breaking agent contract changes require an explicit compatibility decision",
                rule_id="fura.agent.breaking_change",
                next_action=(
                    "Rerun with --decision describing the major-version, migration, or acceptance policy."
                ),
            ),
        )
    terminal_lines = [line]
    for label in ("added", "removed", "changed", "breaking"):
        if not payload[label]:
            continue
        terminal_lines.extend(("", f"{label.title()}:"))
        for item in payload[label]:
            reason = f" - {item['reason']}" if item.get("reason") else ""
            terminal_lines.append(f"- {item['path']}{reason}")
    return CommandResult(
        command=command_name(args),
        ok=not requires_decision,
        exit_code=ExitCode.VALIDATION_ERROR if requires_decision else ExitCode.SUCCESS,
        summary=line,
        diagnostics=diagnostics,
        data=payload,
        terminal_lines=tuple(terminal_lines),
    )


def configure(sub: Any) -> None:
    parser = sub.add_parser(
        "agent-diff",
        help="Compare versioned agent-output fixtures for semantic compatibility",
    )
    parser.add_argument("old", help="Previous agent contract JSON fixture")
    parser.add_argument("new", help="Candidate agent contract JSON fixture")
    parser.add_argument(
        "--decision",
        default=None,
        help="Explicit compatibility decision required when breaking changes are present",
    )
    parser.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    parser.set_defaults(handler=_run_agent_diff)


COMMAND = CommandModule("agent-diff", configure)
