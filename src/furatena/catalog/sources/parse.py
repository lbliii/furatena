"""Parse raw source files into metadata and body."""

from __future__ import annotations

from typing import Any

import yaml

from furatena.catalog.patitas_bridge import split_frontmatter


def parse_source_text(source: str, *, content_format: str) -> tuple[dict[str, Any], str]:
    """Split optional YAML front matter from a source file body."""
    frontmatter_error = frontmatter_parse_error(source)
    if frontmatter_error is not None:
        raise ValueError(frontmatter_error)
    stripped = source.lstrip()
    if stripped.startswith("---"):
        meta, body = split_frontmatter(source)
        return meta or {}, body
    if content_format in {"html", "docutils-rst", "mdx"}:
        return {}, source
    meta, body = split_frontmatter(source)
    return meta or {}, body


def frontmatter_parse_error(source: str) -> str | None:
    """Return a stable validation error for explicit YAML frontmatter."""

    stripped = source.lstrip()
    if not stripped.startswith("---"):
        return None
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if end is None:
        return "closing frontmatter marker was not found"
    frontmatter = "\n".join(lines[1:end])
    if not frontmatter.strip():
        return None
    try:
        loaded = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        return str(exc).splitlines()[0]
    if loaded is not None and not isinstance(loaded, dict):
        return "frontmatter must be a mapping"
    return None
