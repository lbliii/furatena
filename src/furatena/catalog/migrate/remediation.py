"""Migration playbooks and reversible automated remediation."""

from __future__ import annotations

import hashlib
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from furatena.catalog.migrate.mdx import migrate_mdx_text

_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class SafeRemediationResult:
    """Outcome of one conservative MDX-to-Markdown remediation."""

    source_path: Path
    target_path: Path
    status: str
    reason: str
    source_sha256: str | None = None
    target_sha256: str | None = None

    @property
    def safe(self) -> bool:
        return self.status in {"planned", "applied", "unchanged"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "target_path": str(self.target_path),
            "ecosystem": "mdx",
            "risk": "safe" if self.safe else "manual",
            "status": self.status,
            "reason": self.reason,
            "source_sha256": self.source_sha256,
            "target_sha256": self.target_sha256,
            "reversible": self.safe,
            "rollback": (
                f"Delete generated sibling {self.target_path}; source is unchanged."
                if self.safe
                else None
            ),
        }


def remediate_mdx_file_safely(path: Path, *, write: bool) -> SafeRemediationResult:
    """Create a canonical sibling only when conversion is deterministic and reversible."""
    source = path.expanduser().resolve()
    target = source.with_suffix(".md")
    if source.suffix.lower() != ".mdx":
        return _manual(source, target, f"not an MDX source: {source}")
    if not source.is_file():
        return _manual(source, target, f"source does not exist: {source}")

    with _WRITE_LOCK:
        source_bytes = source.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        try:
            converted, report = migrate_mdx_text(source_bytes.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            return _manual(
                source,
                target,
                f"source cannot be converted deterministically: {exc}",
                source_sha256=source_sha256,
            )
        target_bytes = converted.encode("utf-8")
        target_sha256 = hashlib.sha256(target_bytes).hexdigest()
        if report.unmigrated_components:
            components = ", ".join(report.unmigrated_components)
            return _manual(
                source,
                target,
                f"unmapped JSX components require manual review: {components}",
                source_sha256=source_sha256,
                target_sha256=target_sha256,
            )
        if report.warnings:
            return _manual(
                source,
                target,
                f"converted output is not parse-clean: {'; '.join(report.warnings)}",
                source_sha256=source_sha256,
                target_sha256=target_sha256,
            )
        if target.is_file():
            existing_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing_sha256 == target_sha256:
                return SafeRemediationResult(
                    source,
                    target,
                    "unchanged",
                    "canonical sibling already matches the deterministic conversion",
                    source_sha256,
                    target_sha256,
                )
            return _manual(
                source,
                target,
                "target sibling already exists with different content; refusing to overwrite",
                source_sha256=source_sha256,
                target_sha256=target_sha256,
            )
        if not write:
            return SafeRemediationResult(
                source,
                target,
                "planned",
                "conversion is deterministic, parse-clean, and leaves the source unchanged",
                source_sha256,
                target_sha256,
            )
        if not _write_exclusive(target, target_bytes):
            return _manual(
                source,
                target,
                "target appeared during remediation; refusing to overwrite",
                source_sha256=source_sha256,
                target_sha256=target_sha256,
            )
        return SafeRemediationResult(
            source,
            target,
            "applied",
            "created canonical sibling and preserved the original MDX source",
            source_sha256,
            target_sha256,
        )


def remediate_mdx_paths_safely(
    paths: list[Path],
    *,
    write: bool,
) -> list[SafeRemediationResult]:
    """Evaluate or apply safe remediation to an explicit deterministic path set."""
    return [remediate_mdx_file_safely(path, write=write) for path in paths]


def remediation_plan(report: dict[str, Any]) -> dict[str, Any]:
    """Group migration findings into ecosystem- and risk-aware playbooks."""
    items = [_plan_item(finding) for finding in report.get("findings", ())]
    blocked_sources = {
        item["source_path"] for item in items if item["risk"] in {"manual", "blocking"}
    }
    for item in items:
        if item["risk"] != "safe" or item["source_path"] not in blocked_sources:
            continue
        item["risk"] = "manual"
        item["action"] = "Resolve every manual blocker in this source before safe automation."
        item["automation"] = None
        item["reversible"] = False
    return {
        "schema_version": 1,
        "safe_candidate_count": sum(item["risk"] == "safe" for item in items),
        "manual_blocker_count": sum(item["risk"] in {"manual", "blocking"} for item in items),
        "groups": {
            "by_ecosystem": _group(items, "ecosystem"),
            "by_risk": _group(items, "risk"),
            "by_source_path": _group(items, "source_path"),
            "by_owner": _group(items, "owner"),
        },
        "items": items,
    }


def _plan_item(finding: dict[str, Any]) -> dict[str, Any]:
    source = str(finding.get("source_path") or "<catalog>")
    rule_id = str(finding.get("rule_id") or "")
    severity = str(finding.get("severity") or "warning")
    construct = str(finding.get("construct") or rule_id or "migration finding")
    ecosystem = _ecosystem(source, rule_id)
    if severity == "error":
        risk = "blocking"
    elif severity == "warning":
        risk = "manual"
    elif ecosystem == "mdx" and construct.startswith("MDX JSX component"):
        risk = "safe"
    else:
        risk = "review"
    automation = (
        {
            "command": f"fura migrate --apply-safe {source} --json",
            "writes": "canonical .md sibling",
            "preserves_source": True,
            "overwrites_existing": False,
        }
        if risk == "safe"
        else None
    )
    return {
        "source_path": source,
        "line": finding.get("line"),
        "owner": str(finding.get("owner") or "unassigned"),
        "ecosystem": ecosystem,
        "risk": risk,
        "construct": construct,
        "action": str(finding.get("next_action") or "Review before migration."),
        "automation": automation,
        "reversible": automation is not None,
    }


def _ecosystem(source_path: str, rule_id: str) -> str:
    lowered = source_path.lower()
    if lowered.endswith(".mdx") or rule_id.endswith(".mdx"):
        return "mdx"
    if lowered.endswith(".rst") or rule_id.endswith(".rst"):
        return "sphinx-rst"
    if lowered.endswith(".myst") or rule_id.endswith(".myst"):
        return "myst"
    if rule_id.startswith("fura.api") or "openapi" in lowered:
        return "openapi"
    return "cross-format"


def _manual(
    source: Path,
    target: Path,
    reason: str,
    *,
    source_sha256: str | None = None,
    target_sha256: str | None = None,
) -> SafeRemediationResult:
    return SafeRemediationResult(
        source,
        target,
        "manual",
        reason,
        source_sha256,
        target_sha256,
    )


def _write_exclusive(path: Path, payload: bytes) -> bool:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _group(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    grouped: dict[str, int] = {}
    for item in items:
        value = str(item[key])
        grouped[value] = grouped.get(value, 0) + 1
    return dict(sorted(grouped.items()))
