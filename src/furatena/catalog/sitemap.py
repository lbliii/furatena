"""Generate sitemap.xml from the documentation catalog."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

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


def sitemap_xml(catalog: DocCatalog | CatalogRegistry, base_url: str = "") -> str:
    """Return sitemap XML for indexed pages, with hreflang alternates when i18n is enabled."""
    base = base_url.rstrip("/")
    translation_index = _translation_index(catalog)
    has_alternates = any(len(urls) > 1 for urls in translation_index.values())
    xmlns = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
    if has_alternates:
        xmlns += ' xmlns:xhtml="http://www.w3.org/1999/xhtml"'

    lines = [f'<?xml version="1.0" encoding="UTF-8"?>', f"<urlset {xmlns}>"]
    nodes = sorted(getattr(catalog, "nodes", ()), key=lambda item: item.url)

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
