"""Shared Patitas helpers for Furatena (frontmatter, text, incremental edits)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from patitas.nodes import Document

    from furatena.catalog.models import DocNode


def split_frontmatter(source: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter using Patitas' parser."""
    from patitas.frontmatter import parse_frontmatter

    meta, body = parse_frontmatter(source)
    return meta or {}, body


def single_edit_region(old: str, new: str) -> tuple[int, int, int] | None:
    """Return one contiguous edit region when ``old`` became ``new``, else ``None``."""
    if old == new:
        return None
    prefix = 0
    max_prefix = min(len(old), len(new))
    while prefix < max_prefix and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < len(old) - prefix
        and suffix < len(new) - prefix
        and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]
    ):
        suffix += 1
    edit_start = prefix
    edit_end = len(old) - suffix
    new_length = len(new) - prefix - suffix
    return edit_start, edit_end, new_length


def document_for_node(
    node: DocNode,
    documents: dict[str, Document] | None = None,
) -> Document | None:
    """Resolve a Patitas document for a catalog node."""
    from patitas.nodes import Document

    from furatena.catalog.ast_store import document_from_json

    if documents is not None:
        cached = documents.get(node.node_id) or documents.get(node.slug)
        if isinstance(cached, Document):
            return cached
    if node.ast_json:
        try:
            return document_from_json(node.ast_json)
        except (KeyError, TypeError, ValueError):
            return None
    return None


def plain_text(node: DocNode, document: Document | None = None, *, source: str = "") -> str:
    """Extract searchable plain text from a node via Patitas ``extract_text``."""
    from patitas import extract_text

    doc = document or document_for_node(node)
    body = source or node.body_md
    if doc is None and body:
        from furatena.catalog.render import DocsRenderer

        doc, _ = DocsRenderer().parse(body)
    if doc is None:
        return (node.description or "").strip()
    return extract_text(doc, source=body).strip()


def excerpt_text(
    node: DocNode,
    document: Document | None = None,
    *,
    source: str = "",
    max_chars: int = 750,
) -> str:
    """Return a structurally correct excerpt using Patitas ``extract_excerpt``."""
    from patitas import extract_excerpt

    doc = document or document_for_node(node)
    body = source or node.body_md
    if doc is None and body:
        from furatena.catalog.render import DocsRenderer

        doc, _ = DocsRenderer().parse(body)
    if doc is None:
        return (node.description or body[:max_chars]).strip()
    return extract_excerpt(doc, source=body, max_chars=max_chars).strip()


def meta_description(
    node: DocNode,
    document: Document | None = None,
    *,
    source: str = "",
    max_chars: int = 160,
) -> str:
    """Return a short description using Patitas ``extract_meta_description``."""
    from patitas import extract_meta_description

    doc = document or document_for_node(node)
    body = source or node.body_md
    if doc is None and body:
        from furatena.catalog.render import DocsRenderer

        doc, _ = DocsRenderer().parse(body)
    if doc is None:
        return (node.description or "").strip()
    return extract_meta_description(doc, source=body, max_chars=max_chars).strip()


def llm_text(
    node: DocNode,
    document: Document | None = None,
    *,
    source: str = "",
) -> str:
    """Render LLM-safe corpus text via Patitas ``render_llm``."""
    from patitas import render_llm
    from patitas.nodes import Document

    doc = document if isinstance(document, Document) else None
    doc = doc or document_for_node(node)
    if not isinstance(doc, Document):
        doc = None
    body = source or node.body_md
    if doc is None and body:
        from furatena.catalog.render import DocsRenderer

        parsed, _ = DocsRenderer().parse(body)
        doc = parsed if isinstance(parsed, Document) else None
    if doc is None:
        return body.strip()
    return render_llm(doc, source=body).strip()


def section_texts(
    document: Document,
    *,
    source: str = "",
    max_level: int = 4,
    max_chars: int = 4000,
) -> list[tuple[str, str, str]]:
    """Split a document into heading sections as plain text ``(anchor, heading, text)``."""
    from patitas import extract_text
    from patitas.nodes import Block, Heading

    from furatena.catalog.content_ir import slugify_heading

    sections: list[tuple[str, str, str]] = []
    current_anchor = ""
    current_heading = ""
    current_blocks: list[Block] = []

    def flush() -> None:
        if not current_heading or not current_blocks:
            return
        text = "\n\n".join(
            extract_text(block, source=source).strip()
            for block in current_blocks
            if extract_text(block, source=source).strip()
        ).strip()
        if text:
            sections.append((current_anchor, current_heading, text[:max_chars]))

    for block in document.children:
        if isinstance(block, Heading):
            flush()
            current_heading = extract_text(block, source=source).strip()
            current_anchor = block.explicit_id or slugify_heading(current_heading)
            current_blocks = []
            if block.level > max_level:
                current_heading = ""
                current_anchor = ""
            continue
        if current_heading:
            current_blocks.append(block)

    flush()
    return sections
