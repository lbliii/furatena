"""Boost internal doc links for htmx navigation."""

from __future__ import annotations

import re
from collections.abc import Callable
from html import escape

from kida.template import Markup

_OPENING_A_RE = re.compile(r'<a\s+href="(/[^"#][^"]*)"([^>]*)>', re.IGNORECASE)
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_ASSET_PATH_SUFFIXES = (".json", ".txt", ".xml", ".inv")
_ASSET_PATHS = frozenset(
    {
        "/objects.inv",
        "/inventories.json",
        "/meta.json",
        "/surface.json",
        "/sitemap.xml",
    }
)


def is_shell_boost_href(href: str) -> bool:
    """Return False for machine-readable exports that must not use htmx shell boost."""
    if not isinstance(href, str) or not href.startswith("/") or href.startswith("//"):
        return False
    path = href.split("?", 1)[0].split("#", 1)[0]
    if path in _ASSET_PATHS:
        return False
    return not any(path.endswith(suffix) for suffix in _ASSET_PATH_SUFFIXES)


def shell_boost_attrs() -> dict[str, object]:
    """Default htmx attrs for in-app doc navigation."""
    return {
        "hx-boost": "true",
        "hx-target": "#main",
        "hx-swap": "innerHTML",
        "hx-select": "#page-root",
        "hx-sync": "#main:replace",
    }


def shell_unboost_attrs() -> dict[str, object]:
    """Opt out of inherited ``#main`` htmx boost for full-page asset responses."""
    return {"hx-boost": "false"}


def shell_link_attrs(href: str) -> dict[str, object]:
    """Route-aware shell attrs for templates and ``boost_doc_links``."""
    if not isinstance(href, str) or not href.startswith("/") or href.startswith("//"):
        return {}
    if is_shell_boost_href(href):
        return shell_boost_attrs()
    return shell_unboost_attrs()


def _render_attr_string(attrs: dict[str, object]) -> str:
    chunks: list[str] = []
    for key, value in attrs.items():
        if value is None or value is False:
            continue
        escaped_key = escape(str(key), quote=True)
        if value is True:
            chunks.append(f" {escaped_key}")
        else:
            chunks.append(f' {escaped_key}="{escape(str(value), quote=True)}"')
    return "".join(chunks)


def boost_internal_links(
    html: str,
    link_attrs: Callable[[str], dict[str, object]],
) -> Markup:
    """Add route-aware htmx attrs to internal ``<a href="/...">`` tags."""

    def replace(match: re.Match[str]) -> str:
        href = match.group(1)
        existing = match.group(2)
        if href.startswith("//") or _URI_SCHEME_RE.match(href):
            return match.group(0)
        if "hx-boost" in existing:
            return match.group(0)
        attrs = link_attrs(href)
        if not attrs:
            return match.group(0)
        return f'<a href="{href}"{existing}{_render_attr_string(attrs)}>'

    return Markup(_OPENING_A_RE.sub(replace, html))
