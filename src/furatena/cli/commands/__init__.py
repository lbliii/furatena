"""Per-command parser and execution modules for the Fura CLI."""

from furatena.cli.commands import (
    api_diff,
    author,
    check,
    evals,
    export,
    freeze,
    impact,
    init,
    mcp,
    migrate,
    pdf,
    query,
    recipes,
    serve,
    stop,
    theme,
)
from furatena.cli.commands._shared import CommandModule

COMMANDS: tuple[CommandModule, ...] = (
    init.COMMAND,
    serve.COMMAND,
    stop.COMMAND,
    freeze.COMMAND,
    export.COMMAND,
    pdf.COMMAND,
    query.COMMAND,
    check.COMMAND,
    api_diff.COMMAND,
    impact.COMMAND,
    recipes.COMMAND,
    evals.COMMAND,
    mcp.COMMAND,
    author.COMMAND,
    theme.COMMAND,
    migrate.COMMAND,
)

__all__ = ["COMMANDS", "CommandModule"]
