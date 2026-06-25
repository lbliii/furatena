"""Documentation chunks for semantic retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from furatena.catalog.models import SectionChunk
from furatena.catalog.patitas_bridge import document_for_node, excerpt_text, meta_description, section_texts

if TYPE_CHECKING:
    from patitas.nodes import Document

    from furatena.catalog.models import DocNode


@dataclass(frozen=True, slots=True)
class DocChunk:
    """One retrievable slice of a documentation page."""

    chunk_id: str
    node_id: str
    url: str
    title: str
    heading: str
    text: str
    mount: str
    edition: str


def _document_for_chunks(
    node: DocNode,
    documents: dict[str, Document] | None,
) -> Document | None:
    document = document_for_node(node, documents)
    if document is not None or not node.body_md:
        return document
    from furatena.catalog.render import DocsRenderer

    document, _ = DocsRenderer().parse(node.body_md)
    return document


def chunk_node(
    node: DocNode,
    *,
    documents: dict[str, Document] | None = None,
) -> tuple[DocChunk, ...]:
    """Split a page into stable chunks (page summary + heading sections)."""
    document = _document_for_chunks(node, documents)
    chunks: list[DocChunk] = []
    base_text = node.description.strip() or meta_description(
        node,
        document,
        source=node.body_md,
        max_chars=500,
    )
    if not base_text:
        base_text = excerpt_text(node, document, source=node.body_md, max_chars=500)
    if base_text:
        chunks.append(
            DocChunk(
                chunk_id=f"{node.node_id}#summary",
                node_id=node.node_id,
                url=node.url,
                title=node.title,
                heading=node.title,
                text=base_text,
                mount=node.mount,
                edition=node.edition,
            )
        )

    sections = list(node.sections)
    if not sections and node.content_ir is not None:
        from furatena.catalog.text import derive_sections

        sections = list(
            derive_sections(node.content_ir, document, source=node.body_md)
        )
    if not sections and document is not None:
        from furatena.catalog.patitas_bridge import section_texts

        sections = [
            SectionChunk(id=anchor, heading=heading, depth=2, text=text)
            for anchor, heading, text in section_texts(document, source=node.body_md)  # type: ignore[arg-type]
            if text
        ]
    if sections:
        for section in sections:
            chunks.append(
                DocChunk(
                    chunk_id=f"{node.node_id}#{section.id}",
                    node_id=node.node_id,
                    url=f"{node.url.rstrip('/')}#{section.id}",
                    title=node.title,
                    heading=section.heading or node.title,
                    text=section.text,
                    mount=node.mount,
                    edition=node.edition,
                )
            )
        return tuple(chunks)

    sections_legacy = section_texts(document, source=node.body_md) if document is not None else []
    if sections_legacy:
        for anchor, heading, section_text in sections_legacy:
            if not section_text:
                continue
            chunks.append(
                DocChunk(
                    chunk_id=f"{node.node_id}#{anchor}",
                    node_id=node.node_id,
                    url=f"{node.url}#{anchor}",
                    title=node.title,
                    heading=heading,
                    text=section_text,
                    mount=node.mount,
                    edition=node.edition,
                )
            )
        return tuple(chunks)

    body = excerpt_text(node, document, source=node.body_md, max_chars=4000)
    if body and not chunks:
        chunks.append(
            DocChunk(
                chunk_id=f"{node.node_id}#body",
                node_id=node.node_id,
                url=node.url,
                title=node.title,
                heading=node.title,
                text=body,
                mount=node.mount,
                edition=node.edition,
            )
        )
    return tuple(chunks)


def chunk_catalog_nodes(
    nodes: list[DocNode],
    *,
    documents: dict[str, Document] | None = None,
) -> tuple[DocChunk, ...]:
    items: list[DocChunk] = []
    for node in nodes:
        items.extend(chunk_node(node, documents=documents))
    return tuple(items)
