"""Reference resolution for internal and inventory-backed xrefs."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from furatena.catalog.inventories.store import InventoryStore
    from furatena.catalog.models import DocNode


@dataclass(frozen=True, slots=True)
class ResolvedRef:
    """One resolved author reference."""

    href: str
    text: str
    resolved: bool = True
    domain: str | None = None
    inventory_id: str | None = None
    mount: str | None = None
    node_id: str | None = None
    edition: str | None = None


class CatalogLookup(Protocol):
    def get(self, url: str) -> DocNode | None: ...

    def get_by_slug(self, slug: str, *, mount: str | None = None) -> DocNode | None: ...

    def resolve_link(
        self,
        target: str,
        *,
        source_mount: str | None = None,
        edition: str | None = None,
    ) -> DocNode | None: ...


def _normalize_slug(raw: str) -> str:
    return raw.strip("/").removeprefix("docs/")


def _catalog_mount_ids(catalog: CatalogLookup) -> set[str]:
    mounts = getattr(catalog, "mounts", None)
    if mounts is None:
        return set()
    return {mount.id for mount in mounts}


def _catalog_edition_ids(catalog: CatalogLookup) -> set[str]:
    editions: set[str] = set()
    active = getattr(catalog, "active_channel", None)
    if active:
        editions.add(str(active))
    channels = getattr(catalog, "channels", None)
    if channels is not None:
        for channel in channels:
            edition_id = getattr(channel, "id", None)
            if edition_id:
                editions.add(str(edition_id))
    return editions


def _split_qualified_target(
    target: str,
    catalog: CatalogLookup | None,
) -> tuple[str | None, str | None, str]:
    """Parse ``mount:edition:slug``, ``mount:slug``, or ``edition:slug``."""
    if ":" not in target or target.startswith("http"):
        return None, None, target
    parts = target.split(":")
    if catalog is None:
        return None, None, target
    mount_ids = _catalog_mount_ids(catalog)
    edition_ids = _catalog_edition_ids(catalog)
    if len(parts) >= 3 and parts[0] in mount_ids and parts[1] in edition_ids:
        return parts[0], parts[1], ":".join(parts[2:])
    if len(parts) >= 2 and parts[0] in mount_ids:
        return parts[0], None, parts[1]
    if len(parts) >= 2 and parts[0] in edition_ids:
        return None, parts[0], parts[1]
    return None, None, target


def _slug_variants(raw: str) -> set[str]:
    cleaned = raw.strip("/")
    normalized = _normalize_slug(cleaned)
    variants = {cleaned, normalized, f"docs/{normalized}"}
    if cleaned.startswith("docs/"):
        variants.add(cleaned)
    return variants


def _find_catalog_node(
    catalog: CatalogLookup,
    slug: str,
    *,
    mount: str | None = None,
    edition: str | None = None,
    source_mount: str | None = None,
) -> DocNode | None:
    resolve_link = getattr(catalog, "resolve_link", None)
    if resolve_link is not None:
        if slug.startswith("/"):
            node = catalog.get(slug)
            if node is not None and (not edition or node.edition == edition):
                return node
            return None
        if mount:
            for variant in _slug_variants(slug):
                node = catalog.get_by_slug(variant, mount=mount)
                if node is not None and (not edition or node.edition == edition):
                    return node
            return None
        return resolve_link(slug, source_mount=source_mount, edition=edition)

    for variant in _slug_variants(slug):
        node = catalog.get_by_slug(variant, mount=mount) if mount else catalog.get_by_slug(variant)
        if node is None:
            continue
        if edition and node.edition != edition:
            continue
        return node
    if slug.startswith("/"):
        node = catalog.get(slug)
        if node is not None and (not edition or node.edition == edition):
            return node
    return None


def resolve_reference(
    target: str,
    *,
    catalog: CatalogLookup | None,
    inventory_store: InventoryStore | None,
    role_name: str = "xref",
    source_mount: str | None = None,
) -> ResolvedRef:
    """Resolve ``mount:edition:slug``, ``domain:name``, or internal path targets."""
    target = target.strip()
    if not target:
        return ResolvedRef(href="#", text="", resolved=False)

    mount_hint, edition_hint, remainder = _split_qualified_target(target, catalog)

    if mount_hint and catalog is not None and mount_hint in _catalog_mount_ids(catalog):
        node = _find_catalog_node(
            catalog,
            remainder,
            mount=mount_hint,
            edition=edition_hint,
        )
        if node is not None:
            return ResolvedRef(
                href=node.url,
                text=node.title,
                mount=mount_hint,
                edition=node.edition,
                node_id=node.node_id,
            )
        return ResolvedRef(
            href="#",
            text=remainder,
            resolved=False,
            mount=mount_hint,
            edition=edition_hint,
        )

    if ":" in remainder and not remainder.startswith("http"):
        left, right = remainder.split(":", 1)
        if left in {"http", "https"}:
            pass
        elif inventory_store is not None and left not in _catalog_mount_ids(catalog or object()):
            entry = inventory_store.lookup(left, right)
            if entry is None:
                entry = inventory_store.lookup_role(role_name, right)
            if entry is not None:
                return ResolvedRef(
                    href=entry.uri,
                    text=entry.display_name or entry.name,
                    domain=entry.domain,
                    inventory_id=entry.inventory_id,
                )
            return ResolvedRef(href="#", text=target, resolved=False, domain=left)

    if edition_hint and catalog is not None and not mount_hint:
        node = _find_catalog_node(catalog, remainder, edition=edition_hint)
        if node is not None:
            return ResolvedRef(
                href=node.url,
                text=node.title,
                mount=node.mount,
                edition=node.edition,
                node_id=node.node_id,
            )

    if catalog is not None:
        node = _find_catalog_node(
            catalog,
            remainder,
            edition=edition_hint,
            source_mount=source_mount,
        )
        if node is not None:
            return ResolvedRef(
                href=node.url,
                text=node.title,
                mount=node.mount,
                edition=node.edition,
                node_id=node.node_id,
            )

    if inventory_store is not None:
        entry = inventory_store.lookup_role(role_name, remainder)
        if entry is not None:
            return ResolvedRef(
                href=entry.uri,
                text=entry.display_name or entry.name,
                domain=entry.domain,
                inventory_id=entry.inventory_id,
            )

    href = remainder if remainder.startswith(("/", "http")) else f"/{remainder.strip('/')}/"
    return ResolvedRef(href=href, text=remainder.rsplit("/", 1)[-1], resolved=False)


def render_reference_html(resolved: ResolvedRef) -> str:
    text = escape(resolved.text or resolved.href)
    if not resolved.resolved:
        return f'<span class="xref-unresolved" title="Unresolved reference">{text}</span>'
    if resolved.href.startswith("http"):
        return f'<a class="xref external" href="{escape(resolved.href)}" rel="noopener">{text}</a>'
    return f'<a class="xref" href="{escape(resolved.href)}">{text}</a>'
