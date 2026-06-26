"""Patitas markdown content adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from furatena.catalog.ast_store import document_to_json
from furatena.catalog.content_ir import content_ir_to_toc, extract_content_ir
from furatena.catalog.context import RenderContext, reset_render_context, set_render_context
from furatena.catalog.incremental import htmx_swap_hints, invalidation_regions
from furatena.catalog.models import ContentIR, TocEntry
from furatena.catalog.render import DocsRenderer
from furatena.catalog.sources.types import AdaptedContent, PageSource
from furatena.catalog.text import derive_body_text, derive_sections


class PatitasMarkdownAdapter:
    """Adapt Patitas markdown sources into normalized catalog content."""

    content_format = "patitas-markdown"

    def __init__(self, renderer: DocsRenderer | None = None) -> None:
        self._renderer = renderer or DocsRenderer()

    @property
    def renderer(self) -> DocsRenderer:
        return self._renderer

    def parse(self, body: str) -> tuple[object | None, ContentIR | None]:
        document, content_ir = self._renderer.parse(body)
        return document, content_ir

    def parse_incremental(
        self,
        body: str,
        previous: object | None,
        *,
        previous_body: str = "",
    ) -> tuple[object | None, ContentIR | None]:
        if previous is None:
            return self.parse(body)
        document, content_ir = self._renderer.parse_incremental(
            body,
            previous,
            previous_source=previous_body,
        )
        return document, content_ir

    def invalidation_regions(self, old: object | None, new: object | None) -> set[str]:
        return invalidation_regions(old, new)  # type: ignore[arg-type]

    def invalidation_hints(self, old: object | None, new: object | None) -> tuple[str, ...]:
        return htmx_swap_hints(self.invalidation_regions(old, new))

    def adapt(
        self,
        source: PageSource,
        *,
        stubs,
        render_markdown: Callable[..., str],
        get_backlinks: Callable[[str], list[dict[str, str]]],
        content_root: Path,
        include_stack: set[str] | None = None,
        include_depth: int = 0,
        document: object | None = None,
        mount: str | None = None,
    ) -> AdaptedContent:
        stack = include_stack if include_stack is not None else set()
        ctx = RenderContext(
            content_root=content_root,
            source_rel=source.source_path,
            current_slug=source.slug,
            stubs=stubs,
            render_markdown=render_markdown,
            get_backlinks=get_backlinks,
            include_stack=stack,
            include_depth=include_depth,
        )
        token = set_render_context(ctx)
        try:
            if document is None:
                markup, content_ir, document = self._renderer.render(
                    source.body,
                    ctx,
                    source_mount=mount,
                )
            else:
                content_ir = extract_content_ir(document)  # type: ignore[arg-type]
                markup = self._renderer.render_document(document, source=source.body)  # type: ignore[arg-type]
        finally:
            reset_render_context(token)

        toc = content_ir_to_toc(content_ir) if content_ir else ()
        body_text = derive_body_text(content_ir, document, source=source.body)
        sections = derive_sections(content_ir, document, source=source.body)
        ast_json = document_to_json(document) if document is not None else None
        return AdaptedContent(
            body_html=str(markup),
            content_ir=content_ir,
            toc=toc,
            body_text=body_text,
            sections=sections,
            native_ast=ast_json,
            native_document=document,
        )
