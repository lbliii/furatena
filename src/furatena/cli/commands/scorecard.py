"""scorecard command parser and execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from furatena.catalog.adoption_scorecard import (
    build_adoption_scorecard,
    load_adoption_evidence,
    write_adoption_scorecard,
)
from furatena.cli.commands._shared import CommandModule
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_scorecard(args: argparse.Namespace) -> CommandResult:
    source = Path(args.input).expanduser().resolve()
    output = Path(args.output).expanduser().resolve() if args.output else None
    report = build_adoption_scorecard(load_adoption_evidence(source))
    write_adoption_scorecard(report, output)
    diagnostics = tuple(
        Diagnostic(
            severity="error",
            message=f"adoption gate is unmet: {item['gate_id']}",
            source_path=str(source),
            rule_id=f"fura.scorecard.{item['gate_id']}",
            next_action=f"Owner {item['owner']}: {item['remediation']}",
        )
        for item in report["unmet_gates"]
    )
    ok = bool(report["ok"])
    return CommandResult(
        command=command_name(args),
        ok=ok,
        exit_code=ExitCode.SUCCESS if ok else ExitCode.VALIDATION_ERROR,
        summary=(
            f"adoption decision: {report['decision']} "
            f"({report['passed_gate_count']}/{report['gate_count']} gates passed)"
        ),
        diagnostics=diagnostics,
        data={"input": source, "output": output, **report},
        terminal_lines=(
            f"Decision: {report['decision'].upper()} on {report['decision_date']}",
            f"Gates: {report['passed_gate_count']}/{report['gate_count']} passed",
            *(
                f"UNMET {item['gate_id']} — {item['owner']}: {item['remediation']}"
                for item in report["unmet_gates"]
            ),
        ),
    )


def configure(sub: Any) -> None:
    scorecard = sub.add_parser(
        "scorecard",
        help="Evaluate the versioned beta adoption-readiness gates",
    )
    scorecard.add_argument("--input", required=True, help="Versioned evidence manifest JSON")
    scorecard.add_argument("--output", default=None, help="Write the scorecard report JSON")
    scorecard.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    scorecard.set_defaults(handler=_run_scorecard)


COMMAND = CommandModule("scorecard", configure)
