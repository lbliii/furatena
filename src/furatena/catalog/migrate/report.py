"""Migration readiness reports for source corpora."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from furatena.catalog.directives.registry import create_directive_registry
from furatena.catalog.format_compat import (
    FormatCompatibilityFinding,
    mdx_compatibility_findings,
    myst_compatibility_findings,
    rst_compatibility_findings,
)
from furatena.catalog.migrate.remediation import remediation_plan


@dataclass(frozen=True, slots=True)
class MigrationReportFinding:
    """One migration readiness finding."""

    severity: str
    source_path: str
    construct: str
    message: str
    next_action: str
    line: int | None = None
    rule_id: str | None = None
    owner: str = "unassigned"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "source_path": self.source_path,
            "construct": self.construct,
            "message": self.message,
            "next_action": self.next_action,
            "owner": self.owner,
        }
        if self.line is not None:
            payload["line"] = self.line
        if self.rule_id:
            payload["rule_id"] = self.rule_id
        return payload


def build_migration_report(catalog, *, diagnostics: tuple[Any, ...] = ()) -> dict[str, Any]:
    """Build a grouped migration readiness report for a catalog."""
    source_owners = _source_owners(catalog)
    findings = [
        *_compatibility_findings(catalog),
        *(
            _from_diagnostic(
                item,
                owner=source_owners.get(
                    str(getattr(item, "source_path", None) or "<catalog>"),
                    "unassigned",
                ),
            )
            for item in diagnostics
        ),
    ]
    findings = _dedupe_findings(findings)
    report = {
        "summary": _summary(findings),
        "groups": {
            "by_severity": _group_counts(findings, "severity"),
            "by_source_path": _group_counts(findings, "source_path"),
            "by_construct": _group_counts(findings, "construct"),
            "by_next_action": _group_counts(findings, "next_action"),
            "by_owner": _group_counts(findings, "owner"),
        },
        "findings": [finding.to_dict() for finding in findings],
    }
    report["remediation_plan"] = remediation_plan(report)
    return report


def render_migration_report(report: dict[str, Any]) -> str:
    """Render a migration report for humans."""
    summary = report["summary"]
    lines = [
        "Migration readiness report",
        (
            f"{summary['finding_count']} finding(s): "
            f"{summary['error_count']} error(s), "
            f"{summary['warning_count']} warning(s), "
            f"{summary['info_count']} info"
        ),
    ]
    findings = tuple(report["findings"])
    if not findings:
        return "\n".join([*lines, "No migration risks found."])
    current_severity = None
    for finding in findings:
        severity = str(finding["severity"])
        if severity != current_severity:
            lines.extend(("", severity.upper()))
            current_severity = severity
        location = str(finding["source_path"])
        if finding.get("line") is not None:
            location = f"{location}:{finding['line']}"
        lines.append(
            f"- {location}: {finding['construct']} (owner: {finding['owner']}): {finding['message']} "
            f"(next: {finding['next_action']})"
        )
    return "\n".join(lines)


def _compatibility_findings(catalog) -> tuple[MigrationReportFinding, ...]:
    registry = create_directive_registry()
    findings: list[MigrationReportFinding] = []
    for node in getattr(catalog, "nodes", ()):
        if getattr(node, "meta", {}).get("source") == "autodoc":
            continue
        body = getattr(node, "body_md", "")
        if not body:
            continue
        source = (
            getattr(node, "source_path", "")
            or getattr(node, "slug", "")
            or getattr(node, "url", "")
        )
        owner = str(
            getattr(node, "meta", {}).get("owner")
            or getattr(node, "meta", {}).get("team")
            or "unassigned"
        )
        content_format = getattr(node, "content_format", "")
        if content_format == "mdx":
            findings.extend(
                _from_compatibility(
                    item,
                    rule_id="fura.migration.compat.mdx",
                    owner=owner,
                )
                for item in mdx_compatibility_findings(
                    body,
                    source_path=source,
                    known_directives=registry.names,
                )
            )
        elif content_format == "docutils-rst":
            findings.extend(
                _from_compatibility(
                    item,
                    rule_id="fura.migration.compat.rst",
                    owner=owner,
                )
                for item in rst_compatibility_findings(body, source_path=source)
            )
        elif content_format == "myst-markdown":
            findings.extend(
                _from_compatibility(
                    item,
                    rule_id="fura.migration.compat.myst",
                    owner=owner,
                )
                for item in myst_compatibility_findings(
                    body,
                    source_path=source,
                    known_directives=registry.names,
                )
            )
    return tuple(findings)


def _from_compatibility(
    finding: FormatCompatibilityFinding,
    *,
    rule_id: str,
    owner: str,
) -> MigrationReportFinding:
    return MigrationReportFinding(
        severity=finding.severity,
        source_path=finding.source_path,
        line=finding.line,
        construct=finding.construct,
        message=finding.behavior,
        next_action=finding.next_action,
        rule_id=rule_id,
        owner=owner,
    )


def _from_diagnostic(diagnostic: Any, *, owner: str) -> MigrationReportFinding:
    message = str(getattr(diagnostic, "message", ""))
    rule_id = str(getattr(diagnostic, "rule_id", "") or "fura.check")
    return MigrationReportFinding(
        severity=str(getattr(diagnostic, "severity", "warning")),
        source_path=str(getattr(diagnostic, "source_path", None) or "<catalog>"),
        line=getattr(diagnostic, "line", None),
        construct=_diagnostic_construct(rule_id, message),
        message=message,
        next_action=str(
            getattr(diagnostic, "next_action", None) or "Review this finding before migration."
        ),
        rule_id=rule_id,
        owner=owner,
    )


def _diagnostic_construct(rule_id: str, message: str) -> str:
    lowered = message.lower()
    if "broken internal link" in lowered:
        return "internal link"
    if "unresolved reference" in lowered:
        return "reference role"
    if "cross-edition link" in lowered:
        return "cross-edition link"
    if "unknown directive" in lowered:
        return "directive"
    if "unsupported directive" in lowered or "unsupported directives" in lowered:
        return "unsupported directive"
    if "inventory" in lowered:
        return "reference inventory"
    if rule_id.startswith("fura.lifecycle"):
        return "publication lifecycle"
    return rule_id


def _dedupe_findings(findings: list[MigrationReportFinding]) -> list[MigrationReportFinding]:
    seen: set[tuple[object, ...]] = set()
    deduped: list[MigrationReportFinding] = []
    for finding in sorted(
        findings,
        key=lambda item: (
            _severity_sort(item.severity),
            item.source_path,
            item.line or 0,
            item.construct,
            item.message,
        ),
    ):
        key = (
            finding.severity,
            finding.source_path,
            finding.line,
            finding.construct,
            finding.message,
            finding.next_action,
            finding.owner,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _summary(findings: list[MigrationReportFinding]) -> dict[str, int]:
    return {
        "finding_count": len(findings),
        "error_count": sum(1 for item in findings if item.severity == "error"),
        "warning_count": sum(1 for item in findings if item.severity == "warning"),
        "info_count": sum(1 for item in findings if item.severity == "info"),
    }


def _group_counts(findings: list[MigrationReportFinding], attr: str) -> dict[str, int]:
    groups: dict[str, int] = {}
    for finding in findings:
        value = str(getattr(finding, attr))
        groups[value] = groups.get(value, 0) + 1
    return dict(sorted(groups.items()))


def _source_owners(catalog: Any) -> dict[str, str]:
    owners: dict[str, str] = {}
    for node in getattr(catalog, "nodes", ()):
        source = str(
            getattr(node, "source_path", "")
            or getattr(node, "slug", "")
            or getattr(node, "url", "")
        )
        if not source:
            continue
        meta = getattr(node, "meta", {})
        owners[source] = str(meta.get("owner") or meta.get("team") or "unassigned")
    return owners


def _severity_sort(severity: str) -> int:
    order = {"error": 0, "warning": 1, "info": 2}
    return order.get(severity, 3)
