"""Catalog query helpers for ``fura query`` and headless graph APIs."""

from __future__ import annotations

from typing import Any

from furatena.catalog.content_ir import content_ir_record
from furatena.catalog.export import catalog_graph
from furatena.catalog.graph_schema import graph_node_records
from furatena.catalog.record_types import EdgeRecord, GraphQueryRecord, PageRecord

DEFAULT_GRAPH_QUERY_LIMIT = 100
MAX_GRAPH_QUERY_LIMIT = 500


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _clean_lower(value: str | None) -> str:
    return _clean(value).lower()


def _match_text(value: Any, expected: str) -> bool:
    if not expected:
        return True
    if value in (None, ""):
        return False
    return str(value).lower() == expected


def _page_matches(
    page: PageRecord,
    *,
    mount: str,
    tag: str,
    format_value: str,
    owner: str,
    locale: str,
) -> bool:
    if mount and page.get("mount") != mount:
        return False
    if tag and tag not in {str(item).lower() for item in page.get("tags") or ()}:
        return False
    if format_value:
        candidates = {
            str(page.get("content_format") or "").lower(),
            str(page.get("source") or "").lower(),
            str(page.get("source_kind") or "").lower(),
        }
        if format_value not in candidates:
            return False
    if owner:
        provenance = page.get("provenance") or {}
        candidates = {
            str(page.get("owner") or "").lower(),
            str(page.get("team") or "").lower(),
            str(provenance.get("owner") or "").lower(),
            str(provenance.get("team") or "").lower(),
        }
        if owner not in candidates:
            return False
    return not (locale and not _match_text(page.get("lang"), locale))


def _selector_matches_page(selector: str, page: PageRecord) -> bool:
    if not selector:
        return True
    normalized = selector.strip()
    slug = str(page.get("slug") or "").strip("/")
    url = str(page.get("url") or "")
    candidates = {
        str(page.get("node_id") or ""),
        slug,
        f"/{slug}/" if slug else "/",
        url,
        url.rstrip("/"),
    }
    return normalized in candidates or normalized.rstrip("/") in candidates


def _selector_matches_target(
    selector: str,
    target: str,
    pages_by_id: dict[str, PageRecord],
) -> bool:
    if not selector:
        return True
    if target == selector or target.rstrip("/") == selector.rstrip("/"):
        return True
    page = pages_by_id.get(target)
    return bool(page and _selector_matches_page(selector, page))


def query_catalog_graph(
    catalog,
    *,
    mount: str | None = None,
    tag: str | None = None,
    format: str | None = None,
    owner: str | None = None,
    locale: str | None = None,
    edge_kind: str | None = None,
    source: str | None = None,
    target: str | None = None,
    include_private: bool = False,
    subject: Any | None = None,
    limit: int = DEFAULT_GRAPH_QUERY_LIMIT,
    offset: int = 0,
) -> GraphQueryRecord:
    """Filter the DCP catalog graph for headless consumers.

    Page filters narrow the source page set. Edge filters then reduce the graph
    neighborhood and return only participating pages, so callers can traverse
    relationships without downloading the full catalog.
    """
    if isinstance(limit, bool) or not 1 <= limit <= MAX_GRAPH_QUERY_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_GRAPH_QUERY_LIMIT}")
    if isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be zero or greater")
    snapshot = getattr(catalog, "query_graph_snapshot", None)
    graph = (
        snapshot(include_private=include_private, subject=subject)
        if callable(snapshot)
        else catalog_graph(catalog, include_private=include_private, subject=subject)
    )
    mount_value = _clean(mount)
    tag_value = _clean_lower(tag)
    format_value = _clean_lower(format)
    owner_value = _clean_lower(owner)
    locale_value = _clean_lower(locale)
    edge_kind_value = _clean_lower(edge_kind)
    source_value = _clean(source)
    target_value = _clean(target)

    pages = [
        page
        for page in graph.get("pages", [])
        if _page_matches(
            page,
            mount=mount_value,
            tag=tag_value,
            format_value=format_value,
            owner=owner_value,
            locale=locale_value,
        )
    ]
    pages_by_id = {str(page.get("node_id")): page for page in graph.get("pages", [])}
    selected_ids = {str(page.get("node_id")) for page in pages}
    edge_filters_active = any((edge_kind_value, source_value, target_value))

    edges: list[EdgeRecord] = []
    for edge in graph.get("edges", []):
        source_id = str(edge.get("source") or "")
        target_id = str(edge.get("target") or "")
        if source_id not in selected_ids:
            continue
        if target_id in pages_by_id and target_id not in selected_ids and not edge_filters_active:
            continue
        if edge_kind_value and str(edge.get("kind") or "").lower() != edge_kind_value:
            continue
        if source_value and not _selector_matches_target(source_value, source_id, pages_by_id):
            continue
        if target_value and not _selector_matches_target(target_value, target_id, pages_by_id):
            continue
        edges.append(edge)

    if edge_filters_active:
        participating = {str(edge.get("source") or "") for edge in edges}
        participating.update(
            str(edge.get("target") or "")
            for edge in edges
            if str(edge.get("target") or "") in selected_ids
        )
        pages = [page for page in pages if str(page.get("node_id") or "") in participating]

    total = len(pages)
    edge_total = len(edges)
    pages = pages[offset : offset + limit]
    page_ids = {str(page.get("node_id") or "") for page in pages}
    edges = [edge for edge in edges if str(edge.get("source") or "") in page_ids]
    next_offset = offset + len(pages) if offset + len(pages) < total else None

    return {
        "schema_version": graph.get("schema_version", 3),
        "version": graph.get("version", graph.get("schema_version", 3)),
        "channel": graph.get("channel"),
        "edition": graph.get("edition"),
        "query": {
            "mount": mount_value or None,
            "tag": tag_value or None,
            "format": format_value or None,
            "owner": owner_value or None,
            "locale": locale_value or None,
            "edge_kind": edge_kind_value or None,
            "source": source_value or None,
            "target": target_value or None,
            "include_private": include_private,
            "limit": limit,
            "offset": offset,
        },
        "page_count": len(pages),
        "edge_count": len(edges),
        "total": total,
        "edge_total": edge_total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "pages": pages,
        "edges": edges,
        "graph_nodes": graph_node_records(edges),
        "namespaces": graph.get("namespaces", []),
    }


def query_catalog(
    catalog,
    *,
    directive: str | None = None,
    heading: str | None = None,
    mount: str | None = None,
    edition: str | None = None,
    tag: str | None = None,
    url_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Filter catalog nodes by Content IR structure and namespace."""
    results: list[dict[str, Any]] = []
    needle = (heading or "").strip().lower()
    mount_value = (mount or "").strip()
    edition_value = (edition or "").strip()
    tag_value = (tag or "").strip().lower()
    prefix = (url_prefix or "").strip()
    if prefix and not prefix.startswith("/"):
        prefix = f"/{prefix}"
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"

    for node in catalog.nodes:
        if mount_value and node.mount != mount_value:
            continue
        if edition_value and node.edition != edition_value:
            continue
        if tag_value and tag_value not in {t.lower() for t in node.tags}:
            continue
        if prefix and not node.url.startswith(prefix):
            continue

        content = content_ir_record(node.content_ir)
        if directive:
            directives = (content or {}).get("directives") or []
            if not any(item.get("name") == directive for item in directives):
                continue
        if needle:
            headings = (content or {}).get("headings") or []
            if not any(needle in str(item.get("text", "")).lower() for item in headings):
                continue

        record: dict[str, Any] = {
            "node_id": node.node_id,
            "url": node.url,
            "title": node.title,
            "slug": node.slug,
            "mount": node.mount,
            "edition": node.edition,
        }
        if content is not None:
            record["content"] = content
        results.append(record)
    return results
