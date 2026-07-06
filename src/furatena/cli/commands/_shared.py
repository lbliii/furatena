"""Shared parser-to-runner contract and CLI environment helpers."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from furatena.cli.contracts import CommandResult, ExitCode


@dataclass(frozen=True, slots=True)
class CommandModule:
    """One command owns parser registration and execution dispatch."""

    name: str
    configure: Callable[[Any], None]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _default_app_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "docs.yaml").is_file():
        return cwd
    if (cwd / "app" / "docs.yaml").is_file():
        return cwd / "app"
    repo_app = _repo_root() / "app"
    if (repo_app / "docs.yaml").is_file():
        return repo_app
    return cwd


def _app_root(args: argparse.Namespace | None = None) -> Path:
    raw = getattr(args, "app_root", None) if args is not None else None
    raw = raw or os.environ.get("FURA_APP_ROOT")
    return Path(raw).expanduser().resolve() if raw else _default_app_root().resolve()


def _docs_yaml(args: argparse.Namespace | None = None) -> Path:
    raw = getattr(args, "config", None) if args is not None else None
    return Path(raw).expanduser().resolve() if raw else _app_root(args) / "docs.yaml"


def _repo_for_app(app_root: Path) -> Path:
    return app_root.parent if app_root.name == "app" else app_root


def _autodoc_config(args: argparse.Namespace | None, repo_root: Path) -> Path | None:
    raw = getattr(args, "autodoc_config", None) if args is not None else None
    path = Path(raw).expanduser().resolve() if raw else repo_root / "config" / "autodoc.yaml"
    return path if path.is_file() else None


def _ensure_pythonpath() -> None:
    repo = _repo_root()
    src = str(repo / "src")
    app = str(_default_app_root())
    existing = os.environ.get("PYTHONPATH", "")
    parts = [part for part in (src, app, existing) if part]
    os.environ["PYTHONPATH"] = os.pathsep.join(parts)


def _json_output(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _finish_result(result: CommandResult, *, json_output: bool) -> None:
    if json_output:
        result.write_json()
    if result.exit_code:
        raise SystemExit(int(result.exit_code))


def _coerce_exit_code(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return int(ExitCode.INTERNAL_ERROR)
