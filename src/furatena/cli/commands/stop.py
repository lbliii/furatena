"""stop command parser and execution."""

from __future__ import annotations

import argparse
import os
from typing import Any

from furatena.cli.commands._shared import CommandModule, _repo_root
from furatena.cli.contracts import CommandResult, command_name


def _run_stop(args: argparse.Namespace) -> CommandResult:
    from furatena.catalog.dev_reload import stop_dev_server

    host = args.host or "127.0.0.1"
    port = args.port or int(os.environ.get("FURA_PORT", "8001"))
    stopped = stop_dev_server(_repo_root(), host=host, port=port)
    return CommandResult(
        command=command_name(args),
        ok=True,
        summary="dev server stopped" if stopped else "no dev server was listening",
        data={"host": host, "port": port, "stopped": stopped},
        terminal_lines=(
            f"stopped dev server on {host}:{port}"
            if stopped
            else f"no dev server listening on {host}:{port}",
        ),
    )


def configure(sub: Any) -> None:
    stop = sub.add_parser("stop", help="Stop a stray Furatena dev server for this workspace")
    stop.add_argument("--host", default=None, help="Bind host (default 127.0.0.1)")
    stop.add_argument("--port", type=int, default=None, help="Bind port (default 8001)")
    stop.add_argument("--json", action="store_true", help="Emit the standard command result JSON")
    stop.set_defaults(handler=_run_stop)


COMMAND = CommandModule("stop", configure)
