"""Agent-native structure indexes — directives and headings across the catalog."""

from __future__ import annotations

from typing import Any

from furatena.catalog.content_ir import content_ir_record
from furatena.catalog.lifecycle import public_nodes


def build_structure_index(catalog, *, include_private: bool = False) -> dict[str, Any]:
    """Build flat directive and heading indexes from Content IR."""
    directives: list[dict[str, Any]] = []
    headings: list[dict[str, Any]] = []
    directive_names: set[str] = set()

    nodes = list(catalog.nodes) if include_private else public_nodes(catalog.nodes)
    for node in nodes:
        if node.meta.get("draft"):
            continue
        content = content_ir_record(node.content_ir)
        if content is None:
            continue
        for item in content.get("directives") or []:
            name = str(item.get("name") or "")
            if not name:
                continue
            directive_names.add(name)
            directives.append(
                {
                    "name": name,
                    "node_id": node.node_id,
                    "url": node.url,
                    "title": node.title,
                    "mount": node.mount,
                    "edition": node.edition,
                    "line": item.get("line"),
                    "options": item.get("options") or {},
                }
            )
        for item in content.get("headings") or []:
            headings.append(
                {
                    "text": str(item.get("text") or ""),
                    "anchor": str(item.get("anchor") or ""),
                    "level": int(item.get("level") or 0),
                    "node_id": node.node_id,
                    "url": node.url,
                    "title": node.title,
                    "mount": node.mount,
                    "edition": node.edition,
                    "line": item.get("line"),
                }
            )

    return {
        "schema_version": 1,
        "channel": catalog.active_channel,
        "edition": catalog.active_channel,
        "directive_count": len(directives),
        "heading_count": len(headings),
        "directive_names": sorted(directive_names),
        "directives": directives,
        "headings": headings,
    }
