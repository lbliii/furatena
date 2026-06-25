"""Format-neutral text derivation from normalized catalog content."""

from __future__ import annotations

from furatena.catalog.models import ContentIR, SectionChunk


def derive_body_text(
    content_ir: ContentIR | None,
    document: object | None = None,
    *,
    source: str = "",
    description: str = "",
) -> str:
    """Build searchable plain text from normalized content."""
    if document is not None and source:
        from patitas import extract_text

        return extract_text(document, source=source).strip()  # type: ignore[arg-type]
    if content_ir and content_ir.headings:
        parts = [heading.text for heading in content_ir.headings if heading.text]
        if parts:
            return "\n".join(parts).strip()
    return description.strip()


def derive_sections(
    content_ir: ContentIR | None,
    document: object | None = None,
    *,
    source: str = "",
) -> tuple[SectionChunk, ...]:
    """Split a page into heading-bound sections for search and export."""
    if document is not None and source:
        from furatena.catalog.patitas_bridge import section_texts

        chunks: list[SectionChunk] = []
        for anchor, heading, text in section_texts(document, source=source):  # type: ignore[arg-type]
            if not text:
                continue
            chunks.append(
                SectionChunk(
                    id=anchor,
                    heading=heading,
                    depth=2,
                    text=text,
                )
            )
        if chunks:
            return tuple(chunks)

    if content_ir is None or not content_ir.headings:
        if source.strip():
            return (SectionChunk(id="body", heading="", depth=0, text=source.strip()),)
        return ()

    lines = source.splitlines()
    chunks: list[SectionChunk] = []
    for index, heading in enumerate(content_ir.headings):
        start = (heading.line or 1) - 1
        end = (
            (content_ir.headings[index + 1].line or len(lines) + 1) - 1
            if index + 1 < len(content_ir.headings)
            else len(lines)
        )
        section_lines = lines[start:end]
        text = "\n".join(section_lines).strip()
        if text:
            chunks.append(
                SectionChunk(
                    id=heading.anchor,
                    heading=heading.text,
                    depth=heading.level,
                    text=text,
                )
            )
    return tuple(chunks)


def sections_record(sections: tuple[SectionChunk, ...]) -> list[dict[str, object]]:
    return [
        {
            "id": section.id,
            "heading": section.heading,
            "depth": section.depth,
            "text": section.text,
        }
        for section in sections
    ]
