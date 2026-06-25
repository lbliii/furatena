"""Human-readable preview pages for machine-readable develop exports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DevelopExport:
    """One develop export with canonical raw URL and HTML preview route."""

    id: str
    label: str
    raw_href: str
    preview_href: str
    description: str
    content_type: str
    icon: str = "file-code"
    sample: bool = False


DEVELOP_EXPORTS: tuple[DevelopExport, ...] = (
    DevelopExport(
        id="catalog",
        label="Catalog JSON",
        raw_href="/catalog.json",
        preview_href="/develop/catalog/",
        description="Live document graph export for agents and tooling.",
        content_type="application/json",
        icon="file-code",
    ),
    DevelopExport(
        id="llms",
        label="llms.txt",
        raw_href="/llms.txt",
        preview_href="/develop/llms/",
        description="Agent-friendly page index with titles and descriptions.",
        content_type="text/plain; charset=utf-8",
        icon="article",
    ),
    DevelopExport(
        id="llms-full",
        label="llms-full.txt",
        raw_href="/llms-full.txt",
        preview_href="/develop/llms-full/",
        description="Full-text corpus export for offline agent ingestion.",
        content_type="text/plain; charset=utf-8",
        icon="article",
    ),
    DevelopExport(
        id="search",
        label="search.json",
        raw_href="/search.json",
        preview_href="/develop/search/",
        description="Keyword search index for programmatic lookup.",
        content_type="application/json",
        icon="magnifying-glass",
    ),
    DevelopExport(
        id="tools",
        label="tools.json",
        raw_href="/tools.json",
        preview_href="/develop/tools/",
        description="Agent tool manifest describing catalog operations.",
        content_type="application/json",
        icon="stack",
    ),
    DevelopExport(
        id="meta",
        label="meta.json",
        raw_href="/meta.json",
        preview_href="/develop/meta/",
        description="Site metadata summary for integrations.",
        content_type="application/json",
        icon="file-text",
    ),
    DevelopExport(
        id="surface",
        label="surface.json",
        raw_href="/surface.json",
        preview_href="/develop/surface/",
        description="Public surface manifest for routing and views.",
        content_type="application/json",
        icon="layers",
    ),
)

_EXPORT_BY_ID = {item.id: item for item in DEVELOP_EXPORTS}


def develop_export(export_id: str) -> DevelopExport | None:
    return _EXPORT_BY_ID.get(export_id.strip("/"))
