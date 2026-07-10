"""Content IR diff fallback for adapters without native AST."""

from __future__ import annotations

from furatena.catalog.incremental import htmx_swap_hints, needs_graph_rebuild
from furatena.catalog.models import ContentIR

__all__ = [
    "htmx_swap_hints",
    "ir_invalidation_regions",
    "needs_graph_rebuild",
]


def ir_invalidation_regions(
    old: ContentIR | None,
    new: ContentIR | None,
) -> frozenset[str]:
    """Map Content IR changes to catalog invalidation regions."""
    if old is None or new is None:
        return frozenset({"body", "toc", "content_ir", "meta", "graph", "nav"})

    regions: set[str] = set()
    if old.headings != new.headings:
        regions.update({"toc", "body"})
    if old.links != new.links or old.directives != new.directives:
        regions.add("graph")
    if old.headings != new.headings or old.links != new.links or old.directives != new.directives:
        regions.add("body")

    if not regions:
        return frozenset()

    regions.add("content_ir")
    return frozenset(regions)
