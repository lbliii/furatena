"""Parse raw source files into metadata and body."""

from __future__ import annotations

from typing import Any

from furatena.catalog.patitas_bridge import split_frontmatter


def parse_source_text(source: str, *, content_format: str) -> tuple[dict[str, Any], str]:
    """Split optional YAML front matter from a source file body."""
    stripped = source.lstrip()
    if stripped.startswith("---"):
        meta, body = split_frontmatter(source)
        return meta or {}, body
    if content_format in {"html", "docutils-rst", "mdx"}:
        return {}, source
    meta, body = split_frontmatter(source)
    return meta or {}, body
