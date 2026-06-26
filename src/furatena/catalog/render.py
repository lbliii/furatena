"""Markdown rendering with chirp-ui native Patitas directives."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kida.template import Markup

from furatena.catalog.code_blocks import wrap_doc_html
from furatena.catalog.content_ir import extract_content_ir
from furatena.catalog.context import RenderContext, reset_render_context, set_render_context
from furatena.catalog.directives.html import rewrite_doc_links
from furatena.catalog.directives.registry import create_directive_registry
from furatena.catalog.models import ContentIR
from furatena.catalog.patitas_bridge import single_edit_region
from furatena.catalog.references.context import (
    ReferenceContext,
    reset_reference_context,
    set_reference_context,
)
from furatena.catalog.roles.registry import create_role_registry

if TYPE_CHECKING:
    from patitas.nodes import Document


class DocsRenderer:
    """Render doc markdown to chirp-ui HTML at catalog index time."""

    def __init__(self) -> None:
        from patitas import Markdown

        self._directive_registry = create_directive_registry()
        self._role_registry = create_role_registry()
        self._md = Markdown(
            plugins=["all"],
            highlight=True,
            directive_registry=self._directive_registry,
            role_registry=self._role_registry,
        )
        self._reference_catalog = None
        self._inventory_store = None

    def attach_reference_context(self, *, catalog=None, inventory_store=None) -> None:
        self._reference_catalog = catalog
        self._inventory_store = inventory_store

    @property
    def directive_registry(self):
        return self._directive_registry

    def parse(self, source: str) -> tuple[Document, ContentIR]:
        """Parse markdown into a Patitas document and structured content IR."""
        document = self._md.parse(source)
        return document, extract_content_ir(document)

    def parse_incremental(
        self,
        source: str,
        previous: Document | None,
        *,
        previous_source: str = "",
    ) -> tuple[Document, ContentIR]:
        """Re-parse only the edited region when possible, else fall back to full parse."""
        if previous is None or not previous_source or previous_source == source:
            return self.parse(source)

        region = single_edit_region(previous_source, source)
        if region is None:
            return self.parse(source)

        from patitas.incremental import parse_incremental

        edit_start, edit_end, new_length = region
        document = parse_incremental(
            source,
            previous,
            edit_start,
            edit_end,
            new_length,
            directive_registry=self._directive_registry,
        )
        return document, extract_content_ir(document)

    def render_document(self, document: Document, *, source: str = "") -> Markup:
        """Render a parsed Patitas document to HTML."""
        from patitas.renderers.html import HtmlRenderer

        renderer = HtmlRenderer(
            source=source,
            highlight=True,
            directive_registry=self._directive_registry,
            role_registry=self._role_registry,
        )
        html = renderer.render(document)
        html = rewrite_doc_links(html)
        return Markup(wrap_doc_html(html))

    def render(
        self,
        source: str,
        ctx: RenderContext,
        *,
        document: Document | None = None,
        source_mount: str | None = None,
    ) -> tuple[Markup, ContentIR, Document]:
        token = set_render_context(ctx)
        ref_token = set_reference_context(
            ReferenceContext(
                catalog=getattr(self, "_reference_catalog", None),
                inventory_store=getattr(self, "_inventory_store", None),
                source_mount=source_mount,
            )
        )
        try:
            if document is None:
                document, content_ir = self.parse(source)
            else:
                content_ir = extract_content_ir(document)
            return self.render_document(document, source=source), content_ir, document
        finally:
            reset_reference_context(ref_token)
            reset_render_context(token)
