"""Incremental invalidation from Patitas AST diffs."""

from __future__ import annotations

from patitas.context import context_paths_for
from patitas.differ import diff_documents
from patitas.nodes import Document, Link, Node

from furatena.catalog.models import DocNode

_CONTEXT_TO_REGION: dict[str, str] = {
    "page.toc": "toc",
    "page.headings": "toc",
    "page.body": "body",
    "page.meta": "meta",
    "page.footnotes": "body",
}

_HTMX_SWAP_HINTS: dict[str, str] = {
    "body": "page-root",
    "toc": "toc-panel",
    "meta": "head-meta",
    "nav": "docs-sidebar",
}

_FULL_PAGE_HINTS: frozenset[str] = frozenset(_HTMX_SWAP_HINTS.values())

_NAV_FIELDS = ("title", "weight", "section", "section_root")
_GRAPH_FIELDS = ("slug", "url", "tags", "lang", "translation_key")


def invalidation_regions(
    old: Document | None,
    new: Document,
) -> frozenset[str]:
    """Map an AST diff to catalog invalidation regions."""
    if old is None:
        return frozenset({"body", "toc", "content_ir", "meta", "graph", "nav"})

    changes = diff_documents(old, new, recursive=True)
    if not changes:
        return frozenset()

    regions: set[str] = set()
    for change in changes:
        node = _change_node(change.new_node, change.old_node)
        if node is None:
            regions.update({"body", "toc", "content_ir"})
            continue
        if isinstance(node, Link):
            regions.add("graph")
        for context_path in context_paths_for(node):
            regions.add(_CONTEXT_TO_REGION.get(context_path, "body"))

    if regions:
        regions.add("content_ir")
    return frozenset(regions)


def htmx_swap_hints(regions: frozenset[str]) -> tuple[str, ...]:
    """Suggest htmx swap targets for changed regions (Wave 12 hint map)."""
    hints: list[str] = []
    for region in ("meta", "nav", "toc", "body"):
        if region in regions:
            hint = _HTMX_SWAP_HINTS.get(region)
            if hint is not None and hint not in hints:
                hints.append(hint)
    return tuple(hints)


def is_partial_reload(hints: tuple[str, ...]) -> bool:
    """Return whether hints describe a selective reload (not a full page)."""
    if not hints:
        return False
    return frozenset(hints) != _FULL_PAGE_HINTS


def needs_graph_rebuild(regions: frozenset[str]) -> bool:
    """Whether backlink/edge indexes must be rebuilt."""
    if not regions:
        return False
    return "graph" in regions or "nav" in regions


def metadata_invalidation_regions(old: DocNode | None, new: DocNode) -> frozenset[str]:
    """Map frontmatter-derived node changes to their affected indexes and views."""
    if old is None:
        return frozenset({"meta", "nav", "graph"})
    regions: set[str] = set()
    if old.meta != new.meta:
        regions.add("meta")
    if any(getattr(old, field) != getattr(new, field) for field in _NAV_FIELDS):
        regions.add("nav")
    if any(getattr(old, field) != getattr(new, field) for field in _GRAPH_FIELDS):
        regions.add("graph")
    return frozenset(regions)


def _change_node(new_node: object | None, old_node: object | None) -> Node | None:
    if isinstance(new_node, Node):
        return new_node
    if isinstance(old_node, Node):
        return old_node
    return None
