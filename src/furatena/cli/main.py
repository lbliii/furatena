"""Fura CLI parser wiring and command dispatch."""

from __future__ import annotations

import argparse

from furatena.cli.commands import COMMANDS


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


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    args.handler(args)


if __name__ == "__main__":
    main()
