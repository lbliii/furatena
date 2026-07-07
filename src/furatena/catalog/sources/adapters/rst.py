"""reStructuredText documentation adapter (docutils)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from furatena.catalog.content_ir import content_ir_to_toc, slugify_heading
from furatena.catalog.models import ContentDirective, ContentHeading, ContentIR, ContentLink
from furatena.catalog.sources.ir_diff import htmx_swap_hints, ir_invalidation_regions
from furatena.catalog.sources.types import AdaptedContent, PageSource
from furatena.catalog.text import derive_body_text, derive_sections


def _require_docutils():
    try:
        import docutils  # noqa: F401
        from docutils import nodes
        from docutils.core import publish_parts
        from docutils.frontend import get_default_settings
        from docutils.parsers.rst import Parser
        from docutils.utils import new_document
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ImportError(
            "docutils is required for .rst ingestion. "
            "Install with: pip install 'furatena[formats]'"
        ) from exc
    return nodes, publish_parts, get_default_settings, Parser, new_document


def _parse_rst_document(source: str):
    _nodes, _publish_parts, get_default_settings, Parser, new_document = _require_docutils()
    parser = Parser()
    document = new_document(
        "<furatena-rst>",
        settings=get_default_settings(Parser),
    )
    parser.parse(source, document)
    return document


def extract_rst_content_ir(source: str) -> ContentIR:
    nodes, _publish_parts, _get_default_settings, _Parser, _new_document = _require_docutils()
    document = _parse_rst_document(source)

    headings: list[ContentHeading] = []
    links: list[ContentLink] = []
    extensions: list[ContentDirective] = []

    for section in document.findall(nodes.section):
        if not section.children:
            continue
        title_node = section.children[0]
        if not isinstance(title_node, nodes.title):
            continue
        text = title_node.astext().strip()
        if not text:
            continue
        depth = 1
        parent = section.parent
        while parent is not None:
            if isinstance(parent, nodes.section):
                depth += 1
            parent = parent.parent
        headings.append(
            ContentHeading(
                level=min(depth, 6),
                text=text,
                anchor=slugify_heading(text),
                line=getattr(section, "line", None),
            )
        )

    for node in document.findall(nodes.reference):
        refuri = node.get("refuri")
        if not refuri:
            continue
        links.append(
            ContentLink(
                href=str(refuri),
                text=node.astext().strip(),
                line=getattr(node, "line", None),
            )
        )

    for node in document.findall(nodes.Admonition):
        classes = list(getattr(node, "attributes", {}).get("classes") or ())
        name = classes[0] if classes else node.tagname
        extensions.append(
            ContentDirective(
                name=str(name),
                options={"classes": " ".join(classes)},
                line=getattr(node, "line", None),
            )
        )

    return ContentIR(
        headings=tuple(headings),
        links=tuple(links),
        directives=tuple(extensions),
    )


def render_rst_html(source: str) -> str:
    _nodes, publish_parts, _get_default_settings, _Parser, _new_document = _require_docutils()
    parts = publish_parts(
        source,
        writer_name="html",
        settings_overrides={
            "initial_header_level": 2,
            "syntax_highlight": "short",
            "report_level": 5,
            "halt_level": 5,
        },
    )
    body = (parts.get("body") or parts.get("html_body") or "").strip()
    if not body:
        return ""
    return f'<article class="doc-rst">{body}</article>'


def rst_body_text(source: str) -> str:
    return _parse_rst_document(source).astext().strip()


class RstAdapter:
    """Adapt reStructuredText sources via docutils."""

    content_format = "docutils-rst"

    def parse(self, body: str) -> tuple[ContentIR | None, ContentIR | None]:
        content_ir = extract_rst_content_ir(body)
        return content_ir, content_ir

    def parse_incremental(
        self,
        body: str,
        previous: object | None,
        *,
        previous_body: str = "",
    ) -> tuple[ContentIR | None, ContentIR | None]:
        _ = (previous, previous_body)
        return self.parse(body)

    def invalidation_regions(self, old: object | None, new: object | None) -> set[str]:
        old_ir = old if isinstance(old, ContentIR) else None
        new_ir = new if isinstance(new, ContentIR) else None
        return set(ir_invalidation_regions(old_ir, new_ir))

    def invalidation_hints(self, old: object | None, new: object | None) -> tuple[str, ...]:
        return htmx_swap_hints(frozenset(self.invalidation_regions(old, new)))

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
        _ = (stubs, render_markdown, get_backlinks, content_root, include_stack, include_depth, mount)
        content_ir = document if isinstance(document, ContentIR) else extract_rst_content_ir(source.body)
        body_html = render_rst_html(source.body)
        toc = content_ir_to_toc(content_ir)
        body_text = rst_body_text(source.body)
        sections = derive_sections(content_ir, None, source=source.body)
        if not body_text:
            body_text = derive_body_text(
                content_ir,
                None,
                source=source.body,
                description=str(source.meta.get("description") or ""),
            )
        return AdaptedContent(
            body_html=body_html,
            content_ir=content_ir,
            toc=toc,
            body_text=body_text,
            sections=sections,
            native_document=content_ir,
        )
