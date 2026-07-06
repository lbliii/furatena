"""CLI command ownership and parser-dispatch contracts."""

from __future__ import annotations

import argparse
import importlib
import inspect

from furatena.cli.commands import COMMANDS
from furatena.cli.commands._shared import CommandModule
from furatena.cli.main import _build_parser


def test_every_top_level_command_is_owned_by_a_command_module() -> None:
    parser = _build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )

    assert set(subparsers.choices) == {command.name for command in COMMANDS}
    assert all(isinstance(command, CommandModule) for command in COMMANDS)
    assert len({command.configure.__module__ for command in COMMANDS}) == len(COMMANDS)


def test_main_module_contains_only_parser_wiring_and_dispatch() -> None:
    cli_main = importlib.import_module("furatena.cli.main")

    source = inspect.getsource(cli_main)

    assert "def _run_" not in source
    assert len(source.splitlines()) < 70
    assert "command.configure(subparsers)" in source
    assert "def run_command" in source
