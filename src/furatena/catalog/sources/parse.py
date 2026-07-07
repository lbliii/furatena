"""Parse raw source files into metadata and body."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

import yaml

from furatena.catalog.exceptions import ContentParseError
from furatena.catalog.patitas_bridge import split_frontmatter

_NUMERIC_FIELDS = frozenset({"order", "priority", "weight"})


def parse_source_text(
    source: str,
    *,
    content_format: str,
    path: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Split optional YAML front matter from a source file body."""
    stripped = source.lstrip()
    if source.startswith("---"):
        parsed = _parse_explicit_frontmatter(source)
        if isinstance(parsed, str):
            raise ContentParseError(parsed, path=path, operation="parse_frontmatter")
        return parsed
    frontmatter_error = frontmatter_parse_error(source)
    if frontmatter_error is not None:
        raise ContentParseError(frontmatter_error, path=path, operation="parse_frontmatter")
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


def _parse_explicit_frontmatter(source: str) -> tuple[dict[str, Any], str] | str:
    """Parse and validate explicit frontmatter with one YAML traversal."""
    first_newline = source.find("\n")
    if first_newline == -1:
        return "closing frontmatter marker was not found"
    position = first_newline + 1
    closing_start: int | None = None
    closing_end: int | None = None
    while position < len(source):
        next_newline = source.find("\n", position)
        if next_newline == -1:
            if source[position:].strip() == "---":
                closing_start = position
                closing_end = len(source)
            break
        if source[position:next_newline].strip() == "---":
            closing_start = position
            closing_end = next_newline + 1
            break
        position = next_newline + 1
    if closing_start is None or closing_end is None:
        return "closing frontmatter marker was not found"

    frontmatter = source[first_newline + 1 : closing_start].strip()
    body = source[closing_end:].strip()
    if not frontmatter:
        return {}, body
    try:
        loaded = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        return str(exc).splitlines()[0]
    if loaded is None:
        return {}, body
    if not isinstance(loaded, dict):
        return "frontmatter must be a mapping"
    for key in _NUMERIC_FIELDS:
        value = loaded.get(key)
        if value is None:
            continue
        with suppress(TypeError, ValueError):
            loaded[key] = float(value)
    return loaded, body
