"""Fura CLI entry point."""

from furatena.cli.contracts import CommandResult, Diagnostic, ExitCode
from furatena.cli.main import main, run_command

__all__ = ["CommandResult", "Diagnostic", "ExitCode", "main", "run_command"]
