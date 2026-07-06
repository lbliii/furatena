"""Fura CLI parser wiring and command dispatch."""

from __future__ import annotations

import argparse

from furatena.cli.commands import COMMANDS
from furatena.cli.commands._shared import _finish_result, _json_output
from furatena.cli.contracts import CommandResult


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fura",
        description="Fura — CLI for Furatena (hypermedia docs catalog)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"Furatena {__import__('furatena').__version__}",
    )
    parser.add_argument(
        "--app-root",
        default=None,
        help="Furatena app root containing docs.yaml (default: cwd, ./app, or packaged dogfood app)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to docs.yaml (default: APP_ROOT/docs.yaml)",
    )
    parser.add_argument(
        "--autodoc-config",
        default=None,
        help="Path to autodoc.yaml (default: REPO/config/autodoc.yaml when present)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in COMMANDS:
        command.configure(subparsers)
    return parser


def _invoke(argv: list[str] | None = None) -> tuple[argparse.Namespace, CommandResult | None]:
    args = _build_parser().parse_args(argv)
    return args, args.handler(args)


def run_command(argv: list[str]) -> CommandResult | None:
    """Run command logic in-process without terminal or JSON presentation."""

    return _invoke(argv)[1]


def main(argv: list[str] | None = None) -> None:
    args, result = _invoke(argv)
    if result is not None:
        _finish_result(result, json_output=_json_output(args))


if __name__ == "__main__":
    main()
