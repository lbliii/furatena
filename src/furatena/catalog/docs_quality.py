"""Actionable documentation completeness gates and reasoned exemptions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from furatena.catalog.access import AccessPermission, accessible_nodes
from furatena.catalog.check import check_broken_internal_links
from furatena.catalog.docs_inventory import build_documentation_inventory
from furatena.catalog.graph import build_federated_backlinks

_DISPOSITIONS = {"deferred", "internal"}
_OWNER_BY_SURFACE = {
    "cli_command": "platform-docs",
    "config_field": "platform-docs",
    "deployment_profile": "security-operations",
    "diagnostic": "platform-docs",
    "mcp_resource": "agent-platform",
    "mcp_tool": "agent-platform",
    "route": "docs-product",
    "sidecar": "agent-platform",
}


@dataclass(frozen=True, slots=True)
class DocsQualityFinding:
    """One actionable documentation completeness finding."""

    kind: str
    target: str
    owner: str
    recommended_page_type: str
    message: str
    next_action: str
    source_path: str | None = None

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.target}"

    @property
    def rule_id(self) -> str:
        return f"fura.docs_quality.{self.kind}"

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "rule_id": self.rule_id, **asdict(self)}


def load_docs_quality_exemptions(path: Path | None) -> tuple[dict[str, str], ...]:
    """Load strict internal/deferred exemptions from a JSON contract."""

    if path is None or not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("docs-quality exemptions require schema_version 1")
    exemptions: list[dict[str, str]] = []
    for index, raw in enumerate(payload.get("exemptions", [])):
        finding = str(raw.get("finding") or "").strip()
        disposition = str(raw.get("disposition") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not finding or disposition not in _DISPOSITIONS or not reason:
            raise ValueError(
                f"docs-quality exemption {index} requires finding, "
                "internal/deferred disposition, and reason"
            )
        exemptions.append(
            {
                "finding": finding,
                "disposition": disposition,
                "reason": reason,
            }
        )
    return tuple(exemptions)


def _nav_urls(items: list[dict[str, Any]]) -> set[str]:
    urls: set[str] = set()
    for item in items:
        href = str(item.get("href") or "")
        if href:
            urls.add(href)
        urls.update(_nav_urls(list(item.get("children") or ())))
    return urls


def _page_type(value: str) -> str:
    path = value.lower()
    if "/get-started/" in path:
        return "tutorial"
    if "/concepts/" in path or "/about/" in path:
        return "explanation"
    if "/reference/" in path:
        return "reference"
    return "how-to"


def _owner(node: Any | None) -> str:
    if node is None:
        return "docs-product"
    meta = getattr(node, "meta", {})
    return str(meta.get("owner") or meta.get("team") or "docs-product")


def _broken_link_findings(catalog: Any) -> list[DocsQualityFinding]:
    by_source = {
        str(node.source_path or node.slug or node.url): node for node in catalog.nodes
    }
    findings: list[DocsQualityFinding] = []
    for message in check_broken_internal_links(catalog):
        source = message.split(":", 1)[0]
        node = by_source.get(source)
        findings.append(
            DocsQualityFinding(
                kind="link",
                target=message,
                owner=_owner(node),
                recommended_page_type=_page_type(getattr(node, "url", source)),
                message=message,
                next_action="Fix the target or link, then rerun fura docs-quality.",
                source_path=source,
            )
        )
    return findings


def _navigation_findings(docs_app: Any) -> list[DocsQualityFinding]:
    catalog = docs_app.catalog
    public = accessible_nodes(
        catalog,
        catalog.nodes,
        permission=AccessPermission.READ,
    )
    nav_urls = _nav_urls(catalog.nav_tree())
    default_mount = str(getattr(catalog.default_mount, "id", ""))
    return [
        DocsQualityFinding(
            kind="navigation",
            target=node.url,
            owner=_owner(node),
            recommended_page_type=_page_type(node.url),
            message=f"public page {node.url} is missing from default catalog navigation",
            next_action="Add the page to generated navigation or record a reasoned exemption.",
            source_path=node.source_path,
        )
        for node in public
        if node.mount == default_mount and node.url != "/" and node.url not in nav_urls
    ]


def _orphan_findings(docs_app: Any) -> list[DocsQualityFinding]:
    catalog = docs_app.catalog
    public = accessible_nodes(
        catalog,
        catalog.nodes,
        permission=AccessPermission.READ,
    )
    nav_urls = _nav_urls(catalog.nav_tree())
    backlinks = build_federated_backlinks(list(public), catalog=catalog)
    return [
        DocsQualityFinding(
            kind="orphan",
            target=node.url,
            owner=_owner(node),
            recommended_page_type=_page_type(node.url),
            message=f"public page {node.url} has no inbound content or navigation link",
            next_action="Link the page from its owning journey or record a reasoned exemption.",
            source_path=node.source_path,
        )
        for node in public
        if node.url != "/" and node.url not in nav_urls and not backlinks.get(node.url)
    ]


def _public_feature_findings(inventory: dict[str, object]) -> list[DocsQualityFinding]:
    findings: list[DocsQualityFinding] = []
    for record in inventory["surfaces"]:
        if record["coverage"] not in {"missing", "stale"}:
            continue
        kind = str(record["kind"])
        target = str(record["id"])
        state = str(record["coverage"])
        findings.append(
            DocsQualityFinding(
                kind="public_feature",
                target=target,
                owner=_OWNER_BY_SURFACE.get(kind, "docs-product"),
                recommended_page_type="reference",
                message=f"{target} has {state} public documentation coverage",
                next_action=(
                    "Add or refresh reference coverage with the stable identifier, "
                    "or record a reasoned exemption."
                ),
                source_path=str(record["implementation"]),
            )
        )
    return findings


def build_docs_quality_report(
    docs_app: Any,
    *,
    documentation_roots: tuple[Path, ...],
    exemptions: tuple[dict[str, str], ...] = (),
    previous_inventory: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the unified docs-quality report."""

    inventory = build_documentation_inventory(
        docs_app,
        documentation_roots=documentation_roots,
        previous=previous_inventory,
    )
    findings = sorted(
        [
            *_broken_link_findings(docs_app.catalog),
            *_navigation_findings(docs_app),
            *_orphan_findings(docs_app),
            *_public_feature_findings(inventory),
        ],
        key=lambda item: item.id,
    )
    by_id = {item.id: item for item in findings}
    exemption_by_id = {item["finding"]: item for item in exemptions}
    active = [item for item in findings if item.id not in exemption_by_id]
    applied = [
        {**exemption_by_id[item.id], "finding_detail": item.to_dict()}
        for item in findings
        if item.id in exemption_by_id
    ]
    unused = [item for item in exemptions if item["finding"] not in by_id]
    return {
        "schema_version": 1,
        "ok": not active and not unused,
        "summary": {
            "finding_count": len(findings),
            "active_count": len(active),
            "exempted_count": len(applied),
            "unused_exemption_count": len(unused),
        },
        "findings": [item.to_dict() for item in active],
        "exemptions": applied,
        "unused_exemptions": list(unused),
        "inventory_summary": inventory["summary"],
    }
