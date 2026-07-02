"""Content adapter registry."""

from __future__ import annotations

from furatena.catalog.render import DocsRenderer
from furatena.catalog.sources.adapters.html import HtmlAdapter
from furatena.catalog.sources.adapters.markdown import PatitasMarkdownAdapter
from furatena.catalog.sources.adapters.mdx import MdxAdapter
from furatena.catalog.sources.adapters.myst import MystMarkdownAdapter
from furatena.catalog.sources.adapters.rst import RstAdapter
from furatena.catalog.sources.types import ContentAdapter

_BUILTIN_FORMATS: dict[str, type] = {
    "patitas-markdown": PatitasMarkdownAdapter,
    "html": HtmlAdapter,
    "docutils-rst": RstAdapter,
    "mdx": MdxAdapter,
    "myst-markdown": MystMarkdownAdapter,
}

_ADAPTERS: dict[str, ContentAdapter] = {}


def register_adapter(adapter: ContentAdapter) -> None:
    _ADAPTERS[adapter.content_format] = adapter


def get_content_adapter(content_format: str, *, renderer: DocsRenderer | None = None) -> ContentAdapter:
    cached = _ADAPTERS.get(content_format)
    if cached is not None:
        return cached

    adapter_cls = _BUILTIN_FORMATS.get(content_format)
    if adapter_cls is None:
        raise KeyError(f"No content adapter registered for format: {content_format}")

    if adapter_cls in {PatitasMarkdownAdapter, MdxAdapter, MystMarkdownAdapter}:
        adapter = adapter_cls(renderer=renderer)
    else:
        adapter = adapter_cls()
    register_adapter(adapter)
    return adapter


def registered_formats() -> tuple[str, ...]:
    return tuple(sorted(set(_BUILTIN_FORMATS) | set(_ADAPTERS)))
