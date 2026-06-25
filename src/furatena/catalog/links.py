"""Boost internal doc links for htmx navigation."""

from __future__ import annotations

import re
from collections.abc import Callable
from html import escape

from kida.template import Markup

_OPENING_A_RE = re.compile(r'<a\s+href="(/[^"#][^"]*)"([^>]*)>', re.IGNORECASE)
_URI_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


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
