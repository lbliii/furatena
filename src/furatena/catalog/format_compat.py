"""Compatibility diagnostics for non-canonical source formats."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_JSX_SELF_CLOSING = re.compile(r"<([A-Z][A-Za-z0-9_]*)\b([^>]*?)/>")
_JSX_BLOCK = re.compile(r"<([A-Z][A-Za-z0-9_]*)\b([^>]*)>.*?</\1>", re.DOTALL)
_RST_DIRECTIVE = re.compile(r"^(?P<indent>\s*)\.\.\s+(?P<name>[A-Za-z][\w-]*)::", re.MULTILINE)
_RST_ROLE = re.compile(r":(?P<name>(?:[A-Za-z][\w-]*:)?[A-Za-z][\w-]*):`(?P<target>[^`]+)`")
_MYST_FENCE_DIRECTIVE = re.compile(
    r"^(?P<fence>`{3,}|~{3,})\{(?P<name>[A-Za-z][\w-]*)\}",
    re.MULTILINE,
)
_MYST_COLON_DIRECTIVE = re.compile(r"^:{3,}\{(?P<name>[A-Za-z][\w-]*)\}", re.MULTILINE)
_MYST_ROLE = re.compile(r"\{(?P<name>[A-Za-z][\w-]*)\}`(?P<target>[^`]+)`")

_RST_ADMONITIONS = frozenset(
    {
        "admonition",
        "attention",
        "caution",
        "danger",
        "error",
        "hint",
        "important",
        "note",
        "tip",
        "warning",
    }
)


@dataclass(frozen=True, slots=True)
class FormatCompatibilityFinding:
    """One source-format compatibility observation."""

    severity: Literal["info", "warning"]
    source_path: str
    line: int | None
    construct: str
    behavior: str
    next_action: str

    def message(self) -> str:
        location = self.source_path if self.line is None else f"{self.source_path}:{self.line}"
        return f"{location}: {self.construct}: {self.behavior}; {self.next_action}"


def mdx_compatibility_findings(
    source: str,
    *,
    source_path: str,
    known_directives: frozenset[str],
) -> tuple[FormatCompatibilityFinding, ...]:
    """Report JSX components that are mapped or unsupported by MDX lowering."""
    findings: list[FormatCompatibilityFinding] = []
    seen: set[tuple[int, str]] = set()
    for match in sorted(
        [*_JSX_BLOCK.finditer(source), *_JSX_SELF_CLOSING.finditer(source)],
        key=lambda item: item.start(),
    ):
        name = match.group(1)
        key = (match.start(), name)
        if key in seen:
            continue
        seen.add(key)
        lowered = name.lower()
        if lowered in known_directives:
            findings.append(
                FormatCompatibilityFinding(
                    severity="info",
                    source_path=source_path,
                    line=_line_for_offset(source, match.start()),
                    construct=f"MDX JSX component <{name}>",
                    behavior=f"mapped to Patitas directive '{lowered}'",
                    next_action="Verify the rendered directive matches the original component.",
                )
            )
        else:
            findings.append(
                FormatCompatibilityFinding(
                    severity="warning",
                    source_path=source_path,
                    line=_line_for_offset(source, match.start()),
                    construct=f"MDX JSX component <{name}>",
                    behavior="lowered to an unregistered Patitas directive",
                    next_action="Add a directive mapping or manually port this component before migration.",
                )
            )
    return tuple(findings)


def rst_compatibility_findings(
    source: str,
    *,
    source_path: str,
) -> tuple[FormatCompatibilityFinding, ...]:
    """Report RST roles/directives and their adapter behavior."""
    findings: list[FormatCompatibilityFinding] = []
    for match in _RST_DIRECTIVE.finditer(source):
        name = match.group("name")
        lowered = name.lower()
        if lowered in _RST_ADMONITIONS:
            findings.append(
                FormatCompatibilityFinding(
                    severity="info",
                    source_path=source_path,
                    line=_line_for_offset(source, match.start()),
                    construct=f"RST directive '.. {name}::'",
                    behavior="mapped to Content IR as an admonition directive",
                    next_action="Verify admonition styling in the rendered page.",
                )
            )
        else:
            findings.append(
                FormatCompatibilityFinding(
                    severity="warning",
                    source_path=source_path,
                    line=_line_for_offset(source, match.start()),
                    construct=f"RST directive '.. {name}::'",
                    behavior="rendered by docutils HTML but not mapped to a Furatena directive contract",
                    next_action="Add an adapter mapping or replace with a supported Furatena directive.",
                )
            )
    for match in _RST_ROLE.finditer(source):
        name = match.group("name")
        target = match.group("target").strip()
        findings.append(
            FormatCompatibilityFinding(
                severity="warning",
                source_path=source_path,
                line=_line_for_offset(source, match.start()),
                construct=f"RST role ':{name}:'",
                behavior=f"preserved as docutils text/link output; target '{target}' is not resolved through Furatena inventories",
                next_action="Convert to a Furatena reference role or add an inventory-backed adapter mapping.",
            )
        )
    return tuple(findings)


def myst_compatibility_findings(
    source: str,
    *,
    source_path: str,
    known_directives: frozenset[str],
) -> tuple[FormatCompatibilityFinding, ...]:
    """Report MyST roles/directives and their adapter behavior."""
    findings: list[FormatCompatibilityFinding] = []
    seen_directives: set[tuple[int, str]] = set()
    for match in sorted(
        [*_MYST_FENCE_DIRECTIVE.finditer(source), *_MYST_COLON_DIRECTIVE.finditer(source)],
        key=lambda item: item.start(),
    ):
        name = match.group("name")
        lowered = name.lower()
        key = (match.start(), lowered)
        if key in seen_directives:
            continue
        seen_directives.add(key)
        if lowered in known_directives:
            findings.append(
                FormatCompatibilityFinding(
                    severity="info",
                    source_path=source_path,
                    line=_line_for_offset(source, match.start()),
                    construct=f"MyST directive '{{{name}}}'",
                    behavior=f"lowered to Patitas directive '{lowered}'",
                    next_action="Verify the rendered directive matches the source MyST page.",
                )
            )
        else:
            findings.append(
                FormatCompatibilityFinding(
                    severity="warning",
                    source_path=source_path,
                    line=_line_for_offset(source, match.start()),
                    construct=f"MyST directive '{{{name}}}'",
                    behavior="not registered as a Furatena directive",
                    next_action="Add a directive mapping or replace it with supported Patitas markdown.",
                )
            )

    for match in _MYST_ROLE.finditer(source):
        name = match.group("name")
        lowered = name.lower()
        target = match.group("target").strip()
        if lowered in {"ref", "doc"}:
            findings.append(
                FormatCompatibilityFinding(
                    severity="info",
                    source_path=source_path,
                    line=_line_for_offset(source, match.start()),
                    construct=f"MyST role '{{{name}}}'",
                    behavior="lowered to a markdown link and included in Content IR links",
                    next_action="Verify the resolved target is valid in fura check.",
                )
            )
        else:
            findings.append(
                FormatCompatibilityFinding(
                    severity="warning",
                    source_path=source_path,
                    line=_line_for_offset(source, match.start()),
                    construct=f"MyST role '{{{name}}}'",
                    behavior=f"preserved as Patitas role output; target '{target}' is not mapped to Content IR",
                    next_action="Convert to a supported MyST ref/doc role or add a role mapping.",
                )
            )
    return tuple(findings)


def warning_messages(findings: tuple[FormatCompatibilityFinding, ...]) -> list[str]:
    """Return check-compatible warning strings for warning-level findings."""
    return [finding.message() for finding in findings if finding.severity == "warning"]


def _line_for_offset(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1
