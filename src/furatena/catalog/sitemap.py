"""Generate sitemap.xml from the documentation catalog."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from furatena.catalog.access import AccessPermission, AccessSubject, accessible_nodes

if TYPE_CHECKING:
    from furatena.catalog.loader import DocCatalog
    from furatena.catalog.registry import CatalogRegistry


def _translation_index(catalog: DocCatalog | CatalogRegistry) -> dict[str, dict[str, str]]:
    index = getattr(catalog, "translation_index", None)
    if isinstance(index, dict):
        return index
    from furatena.catalog.i18n import build_translation_index

    nodes = getattr(catalog, "nodes", ())
    return build_translation_index(tuple(nodes))


def sitemap_xml(
    catalog: DocCatalog | CatalogRegistry,
    base_url: str = "",
    *,
    include_private: bool = False,
    subject: AccessSubject | None = None,
    mount: str | None = None,
) -> str:
    """Return sitemap XML for indexed pages, with hreflang alternates when i18n is enabled."""
    base = base_url.rstrip("/")
    active_shard = getattr(catalog, "_active_shard", None)
    if mount is not None and callable(active_shard):
        shard = active_shard(mount)
        raw_nodes = list(shard.nodes if shard is not None else ())
        from furatena.catalog.i18n import build_translation_index

        translation_index = build_translation_index(tuple(raw_nodes))
    else:
        raw_nodes = list(getattr(catalog, "nodes", ()))
        translation_index = _translation_index(catalog)
    has_alternates = any(len(urls) > 1 for urls in translation_index.values())
    xmlns = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
    if has_alternates:
        xmlns += ' xmlns:xhtml="http://www.w3.org/1999/xhtml"'

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f"<urlset {xmlns}>"]
    nodes = sorted(
        accessible_nodes(
            catalog,
            raw_nodes,
            subject=subject,
            permission=AccessPermission.EXPORT,
            include_private=include_private,
        ),
        key=lambda item: item.url,
    )

    for node in nodes:
        key = getattr(node, "translation_key", None)
        group_urls = translation_index.get(key or "", {}) if key else {}
        loc = f"{base}{node.url}" if base else node.url
        lines.append("  <url>")
        lines.append(f"    <loc>{escape(loc)}</loc>")

        if key and len(group_urls) > 1:
            for lang_code, href in sorted(group_urls.items()):
                alt_loc = f"{base}{href}" if base else href
                lines.append(
                    "    "
                    f'<xhtml:link rel="alternate" hreflang="{escape(lang_code)}" '
                    f'href="{escape(alt_loc)}" />'
                )

        lines.append("  </url>")

    lines.append("</urlset>")
    return "\n".join(lines) + "\n"
