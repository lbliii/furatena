"""docs-reference command parser and execution."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from furatena.cli.commands._shared import CommandModule
from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode, command_name


def _run_docs_reference(args: argparse.Namespace) -> CommandResult:
    from furatena.catalog.docs_reference import render_docs_reference

    output = Path(args.output).expanduser().resolve()
    rendered = render_docs_reference()
    if args.check:
        actual = output.read_text(encoding="utf-8") if output.is_file() else ""
        matches = actual == rendered
        return CommandResult(
            command=command_name(args),
            ok=matches,
            exit_code=ExitCode.SUCCESS if matches else ExitCode.VALIDATION_ERROR,
            summary="generated docs reference is current" if matches else "generated docs reference drifted",
            diagnostics=()
            if matches
            else (
                Diagnostic(
                    severity="error",
                    rule_id="fura.docs_reference.drift",
                    message=f"generated reference differs from {output}",
                    next_action="Run fura docs-reference --output PATH and commit the result.",
                ),
            ),
            data={"output": output, "matches": matches},
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return CommandResult(
        command=command_name(args),
        ok=True,
        summary=f"wrote generated docs reference to {output}",
        data={"output": output, "byte_count": len(rendered.encode())},
    )


def configure(sub: Any) -> None:
    parser = sub.add_parser("docs-reference", help="Generate or verify CLI/config reference")
    parser.add_argument("--output", required=True, help="Generated markdown path")
    parser.add_argument("--check", action="store_true", help="Fail if the output differs")
    parser.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    parser.set_defaults(handler=_run_docs_reference)


COMMAND = CommandModule("docs-reference", configure)
