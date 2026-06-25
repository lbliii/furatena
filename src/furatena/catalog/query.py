"""Catalog query helpers for ``fura query``."""

from __future__ import annotations

from typing import Any

from furatena.catalog.content_ir import content_ir_record


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
