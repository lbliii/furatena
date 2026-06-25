"""Build inventories from live catalog nodes (autodoc / API slice)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from furatena.catalog.inventories.models import InventoryEntry

if TYPE_CHECKING:
    from furatena.catalog.models import DocNode


def catalog_inventory_entries(
    nodes: list[DocNode],
    *,
    inventory_id: str,
    mount: str = "",
    domain: str = "py",
    slug_prefix: str = "",
) -> tuple[InventoryEntry, ...]:
    """Expose catalog API pages as inventory entries for ``{py}`` roles."""
    entries: list[InventoryEntry] = []
    prefix = slug_prefix.strip("/")
    for node in nodes:
        if mount and node.mount != mount:
            continue
        if prefix and not node.slug.startswith(prefix):
            continue
        if node.meta.get("source") != "autodoc" and not node.slug.startswith("api/"):
            continue
        qualified = node.meta.get("qualified_name") or node.title
        if not qualified:
            continue
        entries.append(
            InventoryEntry(
                domain=domain or "py",
                name=str(qualified),
                objtype="class" if node.meta.get("element_type") == "class" else "func",
                uri=node.url,
                display_name=node.title,
                priority=1,
                inventory_id=inventory_id,
            )
        )
    return tuple(entries)


def catalog_doc_inventory_entries(
    nodes: list[DocNode],
    *,
    inventory_id: str,
    mount: str = "",
    domain: str = "doc",
) -> tuple[InventoryEntry, ...]:
    """Expose catalog doc pages as inventory entries for ``{doc}`` roles."""
    entries: list[InventoryEntry] = []
    for node in nodes:
        if mount and node.mount != mount:
            continue
        if node.meta.get("draft"):
            continue
        if node.meta.get("source") == "autodoc":
            continue
        entries.append(
            InventoryEntry(
                domain=domain or "doc",
                name=node.slug,
                objtype="doc",
                uri=node.url,
                display_name=node.title,
                priority=1,
                inventory_id=inventory_id,
            )
        )
    return tuple(entries)
