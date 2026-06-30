"""CI report renderers for standard Furatena command results."""

from __future__ import annotations

from collections.abc import Iterable
from html import escape

from furatena.cli.contracts import CommandResult, Diagnostic


def render_report(result: CommandResult, report_format: str) -> str:
    """Render a command result as a CI/review friendly report format."""
    diagnostics = tuple(result.diagnostics)
    if report_format == "github":
        return _github_annotations(diagnostics)
    if report_format == "junit":
        return _junit(result, diagnostics)
    if report_format == "checkstyle":
        return _checkstyle(diagnostics)
    if report_format == "markdown":
        return _markdown(result, diagnostics)
    raise ValueError(f"unsupported report format: {report_format}")


def _github_annotations(diagnostics: Iterable[Diagnostic]) -> str:
    lines = []
    for item in diagnostics:
        level = "error" if item.severity == "error" else "warning"
        props = []
        if item.source_path:
            props.append(f"file={_gha_prop(item.source_path)}")
        if item.line is not None:
            props.append(f"line={item.line}")
        if item.rule_id:
            props.append(f"title={_gha_prop(item.rule_id)}")
        suffix = f" {','.join(props)}" if props else ""
        lines.append(f"::{level}{suffix}::{_gha_message(item.message)}")
    return "\n".join(lines)


def _junit(result: CommandResult, diagnostics: tuple[Diagnostic, ...]) -> str:
    failures = [item for item in diagnostics if item.severity == "error"]
    tests = max(1, len(diagnostics))
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        (
            f'<testsuite name="{escape(result.command)}" tests="{tests}" '
            f'failures="{len(failures)}" errors="0">'
        ),
    ]
    if not diagnostics:
        lines.append('  <testcase classname="fura" name="no-findings" />')
    for index, item in enumerate(diagnostics, start=1):
        name = escape(item.rule_id or f"finding-{index}")
        classname = escape(item.source_path or "fura")
        lines.append(f'  <testcase classname="{classname}" name="{name}">')
        if item.severity == "error":
            lines.append(
                f'    <failure message="{escape(item.message)}">{escape(_diagnostic_detail(item))}</failure>'
            )
        elif item.severity == "warning":
            lines.append(f'    <system-out>{escape(_diagnostic_detail(item))}</system-out>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    return "\n".join(lines)


def _checkstyle(diagnostics: tuple[Diagnostic, ...]) -> str:
    by_file: dict[str, list[Diagnostic]] = {}
    for item in diagnostics:
        by_file.setdefault(item.source_path or "<unknown>", []).append(item)
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<checkstyle version="10.0">']
    for source, items in sorted(by_file.items()):
        lines.append(f'  <file name="{escape(source)}">')
        for item in items:
            severity = "error" if item.severity == "error" else "warning"
            line = item.line or 1
            source_name = escape(item.rule_id or "fura")
            lines.append(
                f'    <error line="{line}" severity="{severity}" '
                f'message="{escape(item.message)}" source="{source_name}" />'
            )
        lines.append("  </file>")
    lines.append("</checkstyle>")
    return "\n".join(lines)


def _markdown(result: CommandResult, diagnostics: tuple[Diagnostic, ...]) -> str:
    lines = [
        f"## Furatena {result.command} Report",
        "",
        f"- Status: {'pass' if result.ok else 'fail'}",
        f"- Summary: {result.summary}",
        f"- Findings: {len(diagnostics)}",
    ]
    if not diagnostics:
        return "\n".join(lines)
    lines.extend(("", "| Severity | Rule | Source | Message |", "| --- | --- | --- | --- |"))
    for item in diagnostics:
        source = item.source_path or ""
        if item.line is not None:
            source = f"{source}:{item.line}" if source else str(item.line)
        lines.append(
            "| "
            + " | ".join(
                _md_cell(value)
                for value in (
                    item.severity,
                    item.rule_id or "",
                    source,
                    item.message,
                )
            )
            + " |"
        )
    return "\n".join(lines)


def _diagnostic_detail(item: Diagnostic) -> str:
    parts = [item.message]
    if item.next_action:
        parts.append(f"next: {item.next_action}")
    return "\n".join(parts)


def _gha_prop(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A").replace(",", "%2C")


def _gha_message(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
