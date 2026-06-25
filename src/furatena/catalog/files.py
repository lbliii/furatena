"""Shared file resolution for include / literalinclude directives."""

from __future__ import annotations

from pathlib import Path

MAX_INCLUDE_BYTES = 1_048_576


def resolve_content_path(content_root: Path, source_rel: str, rel_path: str) -> Path | None:
    """Resolve a path relative to the current source file or content root."""
    root = content_root.resolve()
    source_dir = (root / source_rel).parent
    candidates = [(source_dir / rel_path).resolve(), (root / rel_path).resolve()]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if candidate.is_symlink() or not candidate.is_file():
            continue
        return candidate
    return None


def read_bounded_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    if len(raw.encode("utf-8")) > MAX_INCLUDE_BYTES:
        raise OSError(f"File exceeds size limit: {path}")
    return raw


def slice_lines(raw: str, start_line: int | None, end_line: int | None) -> str:
    lines = raw.splitlines(keepends=True)
    start = (start_line - 1) if start_line else 0
    end = end_line if end_line else len(lines)
    return "".join(lines[start:end])
