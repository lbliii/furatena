"""Stable CLI result contracts for automation surfaces."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from furatena.catalog.exceptions import CatalogError


class ExitCode(IntEnum):
    """Documented exit codes for ``fura`` automation."""

    SUCCESS = 0
    WARNING = 1
    VALIDATION_ERROR = 2
    CONFIG_ERROR = 3
    SOURCE_ERROR = 4
    INTERNAL_ERROR = 70


_SOURCE_LINE_RE = re.compile(r"^(?P<source>[^:\n]+):(?P<line>\d+):\s*(?P<message>.*)$")
_SOURCE_RE = re.compile(r"^(?P<source>[^:\n]+):\s*(?P<message>.*)$")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Machine-readable diagnostic emitted by a CLI command."""

    severity: str
    message: str
    source_path: str | None = None
    line: int | None = None
    mount: str | None = None
    node_id: str | None = None
    rule_id: str | None = None
    next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "message": self.message,
        }
        for key in ("source_path", "line", "mount", "node_id", "rule_id", "next_action"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        return payload


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Standard JSON envelope for ``fura`` commands."""

    command: str
    ok: bool
    exit_code: int = ExitCode.SUCCESS
    summary: str = ""
    diagnostics: tuple[Diagnostic, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    terminal_lines: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "exit_code": int(self.exit_code),
            "summary": self.summary,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "data": _jsonable(self.data),
        }

    def write_json(self) -> None:
        print(json.dumps(self.to_dict(), indent=2, sort_keys=True))


def diagnostic_from_message(
    message: str,
    *,
    severity: str,
    rule_id: str | None = None,
    next_action: str | None = None,
) -> Diagnostic:
    """Parse common ``path:line: message`` diagnostics into structured fields."""

    parsed = _SOURCE_LINE_RE.match(message)
    if parsed is not None:
        return Diagnostic(
            severity=severity,
            message=parsed.group("message"),
            source_path=parsed.group("source"),
            line=int(parsed.group("line")),
            rule_id=rule_id,
            next_action=next_action,
        )
    parsed = _SOURCE_RE.match(message)
    if parsed is not None:
        return Diagnostic(
            severity=severity,
            message=parsed.group("message"),
            source_path=parsed.group("source"),
            rule_id=rule_id,
            next_action=next_action,
        )
    return Diagnostic(
        severity=severity,
        message=message,
        rule_id=rule_id,
        next_action=next_action,
    )


def diagnostic_from_catalog_error(error: CatalogError) -> Diagnostic:
    """Render one typed domain error through the stable CLI diagnostic contract."""

    return Diagnostic(
        severity="error",
        message=error.message,
        source_path=error.path,
        mount=error.mount,
        rule_id=error.code,
        next_action=(
            f"Review {error.operation or 'the failing operation'}"
            + (f" for slug {error.slug}" if error.slug else "")
            + " and retry after correcting the reported context."
        ),
    )


def command_result_from_catalog_error(command: str, error: CatalogError) -> CommandResult:
    """Return the standard failed command envelope for a typed domain error."""

    return CommandResult(
        command=command,
        ok=False,
        exit_code=error.exit_code,
        summary=f"{command} failed: {error.message}",
        diagnostics=(diagnostic_from_catalog_error(error),),
        data={"error": error.to_dict()},
    )


def command_name(args: Any) -> str:
    """Return a stable command identifier for nested argparse commands."""

    command = getattr(args, "command", "") or ""
    if command == "theme":
        theme_command = getattr(args, "theme_command", None)
        return f"theme {theme_command}" if theme_command else "theme"
    if command == "author":
        author_command = getattr(args, "author_command", None)
        return f"author {author_command}" if author_command else "author"
    if command == "activation":
        activation_command = getattr(args, "activation_command", None)
        return f"activation {activation_command}" if activation_command else "activation"
    if command == "promotion":
        promotion_command = getattr(args, "promotion_command", None)
        return f"promotion {promotion_command}" if promotion_command else "promotion"
    return command


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
