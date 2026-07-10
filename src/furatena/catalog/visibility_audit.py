"""Detect protected content canaries in public export artifacts."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from furatena.catalog.exceptions import ExportError


@dataclass(frozen=True, slots=True)
class VisibilityCanary:
    """Unique protected-source tokens that must never reach public output."""

    source_path: str
    boundary: str
    tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VisibilityLeakFinding:
    """One protected token found in a public artifact."""

    artifact: Path
    source_path: str
    boundary: str
    token: str

    def format(self) -> str:
        return (
            f"{self.artifact}: leaked {self.boundary} content from {self.source_path} "
            f"(matched {self.token!r})"
        )


@dataclass(frozen=True, slots=True)
class VisibilityAuditReport:
    """Result of scanning a public artifact tree for protected canaries."""

    output_dir: Path
    canaries: tuple[VisibilityCanary, ...]
    scanned_artifacts: int
    findings: tuple[VisibilityLeakFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


class StaticExportVisibilityError(ExportError):
    """Raised when protected source content appears in public artifacts."""

    def __init__(self, report: VisibilityAuditReport) -> None:
        super().__init__(
            f"public artifact visibility audit found {len(report.findings)} leak(s)",
            path=report.output_dir,
            operation="visibility_audit",
        )
        self.report = report


def _boundary(meta: dict[str, Any]) -> str:
    from furatena.catalog.lifecycle import visibility_state

    visibility = visibility_state(meta)
    if visibility == "draft" or bool(meta.get("draft")):
        return "draft"
    if visibility == "archived" or bool(meta.get("archived_at")):
        return "archived"
    if visibility == "private":
        return "private"
    return "protected"


def _candidate_tokens(record: Any) -> list[str]:
    meta = record.meta
    values = [record.url, record.slug, record.source_path, str(meta.get("title") or "")]
    for line in record.body.splitlines():
        candidate = line.strip().strip("#").strip()
        if 12 <= len(candidate) <= 240:
            values.append(candidate)
    return [value.strip() for value in values if len(value.strip()) >= 6]


def visibility_canaries(catalog: Any) -> tuple[VisibilityCanary, ...]:
    """Derive unique canaries for every source excluded by public policy."""
    from furatena.catalog.lifecycle import is_public_meta, lifecycle_records

    records, _errors = lifecycle_records(catalog)
    public_text = "\n".join(
        "\n".join(
            (
                record.url,
                record.slug,
                record.source_path,
                str(record.meta.get("title") or ""),
                record.body,
            )
        )
        for record in records
        if is_public_meta(record.meta)
    )
    canaries: list[VisibilityCanary] = []
    for record in records:
        if is_public_meta(record.meta):
            continue
        tokens = tuple(
            dict.fromkeys(token for token in _candidate_tokens(record) if token not in public_text)
        )
        if tokens:
            canaries.append(
                VisibilityCanary(
                    source_path=record.source_path,
                    boundary=_boundary(record.meta),
                    tokens=tokens,
                )
            )
    return tuple(canaries)


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    parts = [str(key) + "=" + str(value) for key, value in (reader.metadata or {}).items()]
    parts.extend(page.extract_text() or "" for page in reader.pages)
    return "\n".join(parts)


def _inventory_text(path: Path) -> str:
    from furatena.catalog.inventories.sphinx import parse_objects_inv_bytes

    entries = parse_objects_inv_bytes(path.read_bytes(), inventory_id=path.parent.name)
    return json.dumps(
        [
            {
                "name": item.name,
                "display_name": item.display_name,
                "uri": item.uri,
                "domain": item.domain,
                "objtype": item.objtype,
            }
            for item in entries
        ],
        ensure_ascii=False,
    )


def _artifact_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            return _pdf_text(path)
        except Exception:
            return path.read_bytes().decode("utf-8", errors="replace")
    if path.suffix.lower() == ".inv":
        try:
            return _inventory_text(path)
        except Exception:
            return path.read_bytes().decode("utf-8", errors="replace")
    return path.read_bytes().decode("utf-8", errors="replace")


def _token_forms(token: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                token,
                html.escape(token),
                json.dumps(token, ensure_ascii=False)[1:-1],
                quote(token, safe="/:"),
            )
        )
    )


def scan_visibility_leaks(
    output_dir: Path,
    canaries: tuple[VisibilityCanary, ...] | list[VisibilityCanary],
) -> VisibilityAuditReport:
    """Scan all generated files, including PDF and inventory payloads, for canaries."""
    root = output_dir.resolve()
    normalized_canaries = tuple(canaries)
    findings: list[VisibilityLeakFinding] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        scanned += 1
        text = _artifact_text(path)
        for canary in normalized_canaries:
            for token in canary.tokens:
                if any(form in text for form in _token_forms(token)):
                    findings.append(
                        VisibilityLeakFinding(
                            artifact=path.relative_to(root),
                            source_path=canary.source_path,
                            boundary=canary.boundary,
                            token=token,
                        )
                    )
                    break
    unique = {
        (item.artifact, item.source_path, item.boundary, item.token): item for item in findings
    }
    return VisibilityAuditReport(
        output_dir=root,
        canaries=normalized_canaries,
        scanned_artifacts=scanned,
        findings=tuple(
            unique[key] for key in sorted(unique, key=lambda item: tuple(map(str, item)))
        ),
    )
