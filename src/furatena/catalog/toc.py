"""TOC helpers for chirp-theme doc sidebar markup."""

from __future__ import annotations

from typing import Any


def node_toc_items(node) -> list[dict[str, Any]]:
    """Map catalog ``TocEntry`` tuples to Bengal ``build_toc_tree`` input."""
    if node is None:
        return []
    toc = getattr(node, "toc", None) or ()
    return [
        {"id": entry.anchor, "title": entry.text, "level": entry.depth}
        for entry in toc
    ]


def collection_toc_items(sections) -> list[dict[str, Any]]:
    """Map collection view sections to TOC rows for ``doc_toc.html``."""
    if not sections:
        return []
    return [
        {
            "id": section.anchor,
            "title": section.node.title,
            "level": 2,
        }
        for section in sections
    ]


def build_toc_tree(toc_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert flat TOC rows into nested ``children`` arrays for theme partials."""
    if not toc_items:
        return []

    root: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []

    for item in toc_items:
        level = int(item.get("level", 1))
        node = {
            "id": item.get("id", ""),
            "title": item.get("title", ""),
            "level": level,
            "children": [],
        }
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            root.append(node)
        stack.append((level, node))

    return root
