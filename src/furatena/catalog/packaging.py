"""Shared packaging policies for frozen and static publication targets."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from furatena.catalog.exceptions import ExportError
from furatena.catalog.lifecycle import check_lifecycle_sources

_ROOT_PATH_ATTRS = (
    "href",
    "src",
    "action",
    "content",
    "hx-get",
    "hx-post",
    "hx-push-url",
    "formaction",
)
_ROOT_PATH_RE = re.compile(
    rf"""(?P<prefix>\s(?:{"|".join(_ROOT_PATH_ATTRS)})=(["']))(?P<url>/[^"']*)""",
    re.IGNORECASE,
)
_JSON_URL_KEYS = ("canonical_url", "href", "og_url", "page_url", "self", "url")
_JSON_URL_RE = re.compile(
    rf'("(?:{"|".join(_JSON_URL_KEYS)}|[^"]*(?:_href|_url))"\s*:\s*")(/[^"]*)(")'
)


class PackagingLifecycleError(ExportError):
    """Raised when a publication target would violate lifecycle safety."""

    def __init__(self, target: str, errors: list[str], warnings: list[str]) -> None:
        super().__init__(
            f"{target} blocked by lifecycle safety checks",
            operation=target,
        )
        self.target = target
        self.errors = errors
        self.warnings = warnings


def validate_packaging_lifecycle(
    catalog: Any,
    *,
    target: str,
    allow_errors: bool = False,
) -> tuple[list[str], list[str]]:
    """Apply the shared source-lifecycle gate for a publication target."""

    if allow_errors:
        return [], []
    errors, warnings = check_lifecycle_sources(catalog)
    if errors:
        raise PackagingLifecycleError(target, errors, warnings)
    return errors, warnings


def prune_stale_files(
    root: Path,
    keep_paths: set[Path],
    *,
    suffixes: tuple[str, ...] | None = None,
    preserve: frozenset[Path] = frozenset(),
) -> int:
    """Remove files outside ``keep_paths`` under one packaging target root."""

    if not root.is_dir():
        return 0
    normalized_keep = {path if path.is_absolute() else root / path for path in keep_paths}
    removed = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if suffixes is not None and not path.name.endswith(suffixes):
            continue
        if path.relative_to(root) in preserve or path in normalized_keep:
            continue
        path.unlink()
        removed += 1
    return removed


def normalize_base_path(base_path: str) -> str:
    """Return a leading-slash path without trailing slash, or empty for site root."""

    raw = base_path.strip()
    if not raw or raw == "/":
        return ""
    return "/" + raw.strip("/").rstrip("/")


def prefix_root_paths(text: str, base_path: str) -> str:
    """Prefix root-relative URLs in HTML or JSON sidecars."""

    normalized = normalize_base_path(base_path)
    if not normalized:
        return text

    def html_repl(match: re.Match[str]) -> str:
        url = match.group("url")
        if url.startswith("//") or url == normalized or url.startswith(f"{normalized}/"):
            return match.group(0)
        return f"{match.group('prefix')}{normalized}{url}"

    def json_repl(match: re.Match[str]) -> str:
        url = match.group(2)
        if url.startswith("//") or url == normalized or url.startswith(f"{normalized}/"):
            return match.group(0)
        return f"{match.group(1)}{normalized}{url}{match.group(3)}"

    text = _ROOT_PATH_RE.sub(html_repl, text)
    return _JSON_URL_RE.sub(json_repl, text)


def prefix_markdown_links(text: str, base_path: str) -> str:
    """Prefix root-relative markdown links while preserving code spans and fences."""

    normalized = normalize_base_path(base_path)
    if not normalized:
        return text
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = line.lstrip()[:3]
        if marker in {"```", "~~~"}:
            fence = None if fence == marker else marker
            lines.append(line)
        elif fence is None:
            parts = re.split(r"(`+[^`]*`+)", line)
            lines.append(
                "".join(
                    part if index % 2 else re.sub(r"\]\((/[^)]*)\)", rf"]({normalized}\1)", part)
                    for index, part in enumerate(parts)
                )
            )
        else:
            lines.append(line)
    return "".join(lines)
