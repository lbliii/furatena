"""Explicit trusted-HTML boundaries for catalog rendering."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

from kida.template import Markup

_BLOCKED_ELEMENTS = frozenset({"script", "style", "iframe", "object", "embed", "template"})
_URL_ATTRIBUTES = frozenset({"action", "formaction", "href", "poster", "src", "xlink:href"})
_DANGEROUS_ATTRIBUTES = frozenset({"srcdoc", "style"})
_SAFE_SCHEMES = frozenset({"http", "https", "mailto", "tel"})
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_CONTROL_OR_SPACE_RE = re.compile(r"[\x00-\x20]+")


class _RenderedHTMLSanitizer(HTMLParser):
    """Preserve renderer markup while removing executable author HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.blocked: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.blocked:
            if tag in _BLOCKED_ELEMENTS:
                self.blocked.append(tag)
            return
        if tag in _BLOCKED_ELEMENTS:
            self.blocked.append(tag)
            return
        self.parts.append(f"<{tag}{self._attrs(attrs)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.blocked or tag in _BLOCKED_ELEMENTS:
            return
        self.parts.append(f"<{tag}{self._attrs(attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
            return
        if tag not in _BLOCKED_ELEMENTS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.blocked:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.blocked:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.blocked:
            self.parts.append(f"&#{name};")

    def _attrs(self, attrs: list[tuple[str, str | None]]) -> str:
        rendered: list[str] = []
        for raw_name, value in attrs:
            name = raw_name.lower()
            if name.startswith("on") or name in _DANGEROUS_ATTRIBUTES:
                continue
            if value is None:
                rendered.append(f" {html.escape(name, quote=True)}")
                continue
            if name in _URL_ATTRIBUTES and not _safe_url(value):
                continue
            rendered.append(f' {html.escape(name, quote=True)}="{html.escape(value, quote=True)}"')
        return "".join(rendered)


def sanitize_rendered_html(value: str) -> Markup:
    """Remove executable constructs and return catalog HTML safe for insertion.

    Markdown, RST, MDX, raw-HTML adapters, includes, and generated autodoc HTML
    pass through this boundary before publication. Renderer-owned tags are
    preserved; script-capable elements, event/style/srcdoc attributes, and unsafe
    URL schemes are removed.
    """
    parser = _RenderedHTMLSanitizer()
    parser.feed(str(value))
    parser.close()
    return Markup("".join(parser.parts))


def trusted_renderer_fragment(value: str) -> Markup:
    """Mark a nested fragment already emitted by the renderer pipeline."""
    return Markup(value)


def trusted_escaped_markup(value: str) -> Markup:
    """Mark limited markup whose author text was escaped before tag insertion."""
    return Markup(value)


def _safe_url(value: str) -> bool:
    normalized = _CONTROL_OR_SPACE_RE.sub("", html.unescape(value)).lower()
    match = _SCHEME_RE.match(normalized)
    return match is None or match.group(1) in _SAFE_SCHEMES
