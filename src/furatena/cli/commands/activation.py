"""activation command parser and execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from furatena.catalog.activation import (
    EVENTS,
    JOURNEYS,
    activation_report,
    mark_activation_event,
    start_activation_session,
)
from furatena.cli.commands._shared import CommandModule
from furatena.cli.contracts import CommandResult, command_name


def _run_activation(args: argparse.Namespace) -> CommandResult:
    action = str(args.activation_command)
    if action == "start":
        path = _session_path(args.session)
        payload = start_activation_session(
            path,
            journey=args.journey,
            consent=args.consent,
            replace=args.replace,
        )
        return CommandResult(
            command=command_name(args),
            ok=True,
            summary=f"started opted-in {payload['journey']} activation session",
            data={"session": path, "journey": payload["journey"], "consent": True},
        )
    if action == "mark":
        path = _session_path(args.session)
        payload = mark_activation_event(
            path,
            event=args.event,
            automated_remediation_seconds=args.automated_seconds,
            manual_remediation_seconds=args.manual_seconds,
            replace=args.replace,
        )
        record = payload["events"][args.event]
        return CommandResult(
            command=command_name(args),
            ok=True,
            summary=f"recorded activation milestone {args.event}",
            data={"session": path, "journey": payload["journey"], "event": args.event, **record},
        )
    paths = tuple(_session_path(value) for value in args.session)
    report = activation_report(paths)
    output = Path(args.output).expanduser().resolve() if args.output else None
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return CommandResult(
        command=command_name(args),
        ok=True,
        summary=f"aggregated {report['session_count']} opted-in activation session(s)",
        data={"output": output, **report},
    )


def _session_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def configure(sub: Any) -> None:
    parser = sub.add_parser(
        "activation",
        help="Measure opt-in new-site and imported-site activation journeys",
    )
    actions = parser.add_subparsers(dest="activation_command", required=True)

    start = actions.add_parser("start", help="Start a local opted-in measurement session")
    start.add_argument("--journey", choices=sorted(JOURNEYS), required=True)
    start.add_argument("--session", required=True, help="Local session JSON path")
    start.add_argument(
        "--consent",
        action="store_true",
        help="Explicitly consent to local duration-only measurement",
    )
    start.add_argument("--replace", action="store_true", help="Replace an existing session file")
    start.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    start.set_defaults(handler=_run_activation)

    mark = actions.add_parser("mark", help="Record an activation milestone")
    mark.add_argument("--session", required=True, help="Local session JSON path")
    mark.add_argument("--event", choices=sorted(EVENTS), required=True)
    mark.add_argument("--automated-seconds", type=float, default=0.0)
    mark.add_argument("--manual-seconds", type=float, default=0.0)
    mark.add_argument("--replace", action="store_true", help="Replace an existing milestone")
    mark.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    mark.set_defaults(handler=_run_activation)

    report = actions.add_parser("report", help="Aggregate sanitized activation durations")
    report.add_argument("--session", action="append", required=True, help="Session JSON path")
    report.add_argument("--output", default=None, help="Write the sanitized report JSON")
    report.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    report.set_defaults(handler=_run_activation)


COMMAND = CommandModule("activation", configure)
