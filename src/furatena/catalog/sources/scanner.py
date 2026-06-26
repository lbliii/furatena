"""Discover documentation source files on disk."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from furatena.catalog.sources.parse import parse_source_text
from furatena.catalog.sources.types import MountSourceConfig, PageSource


def apply_url_prefix(url: str, prefix: str) -> str:
    if not prefix:
        return url
    if url == "/":
        return prefix
    return prefix.rstrip("/") + url


def file_to_url(
    content_root: Path,
    path: Path,
    *,
    url_prefix: str = "",
    index_files: frozenset[str] | None = None,
) -> tuple[str, str]:
    """Map a content file to ``(url, slug)`` under the content root."""
    index_names = index_files or frozenset({"_index.md"})
    rel = path.relative_to(content_root)
    parts = list(rel.parts)
    filename = parts[-1]
    if filename in index_names:
        parts = parts[:-1]
        slug = "/".join(parts)
        url = f"/{slug}/" if slug else "/"
    else:
        parts[-1] = Path(parts[-1]).stem
        slug = "/".join(parts)
        url = f"/{slug}/" if slug else "/"
    return apply_url_prefix(url, url_prefix), slug


class FilesystemScanner:
    """Scan a content root for configured source extensions."""

    def __init__(self, config: MountSourceConfig) -> None:
        self._config = config

    @property
    def config(self) -> MountSourceConfig:
        return self._config

    def scan(self, content_root: Path, *, url_prefix: str = "") -> list[PageSource]:
        extensions = self._config.tracked_extensions()
        files: list[Path] = []
        for ext in extensions:
            files.extend(content_root.rglob(f"*{ext}"))
        files = sorted({path.resolve() for path in files})

        slug_to_url: dict[str, str] = {}
        for path in files:
            url, slug = file_to_url(
                content_root,
                path,
                url_prefix=url_prefix,
                index_files=self._config.index_files,
            )
            slug_to_url[slug] = url

        pages: list[PageSource] = []
        for path in sorted(files, key=lambda item: str(item)):
            rel_path = path.relative_to(content_root)
            source = path.read_text(encoding="utf-8")
            content_format = self._config.content_format_for(path)
            meta, body = parse_source_text(source, content_format=content_format)
            if meta.get("draft"):
                continue
            url, slug = file_to_url(
                content_root,
                path,
                url_prefix=url_prefix,
                index_files=self._config.index_files,
            )
            pages.append(
                PageSource(
                    path=path,
                    content_format=self._config.content_format_for(path),
                    meta=dict(meta),
                    body=body,
                    source_path=str(rel_path),
                    url=url,
                    slug=slug,
                )
            )
        return pages

    def resolve_wikilinks(
        self,
        pages: list[PageSource],
        slug_to_url: dict[str, str],
        *,
        federated_slug_urls: dict[str, str] | None = None,
    ) -> list[PageSource]:
        """Resolve markdown wikilinks, including mount-qualified targets."""
        import re

        wikilink_re = re.compile(r"\[\[(?:([^:\]|]+):)?([^|\]]+)(?:\|([^\]]+))?\]\]")
        federated = federated_slug_urls or {}

        def replace(body: str, url_map: dict[str, str]) -> str:
            def sub(match: re.Match[str]) -> str:
                mount = match.group(1)
                raw_path = match.group(2).strip("/")
                label = match.group(3)
                if mount:
                    slug_candidates = [raw_path]
                    if not raw_path.startswith("docs/"):
                        slug_candidates.append(f"docs/{raw_path}")
                    href = None
                    for slug_candidate in slug_candidates:
                        href = federated.get(f"{mount}:{slug_candidate}")
                        if href:
                            break
                    slug = raw_path
                else:
                    slug = raw_path if raw_path.startswith("docs/") else f"docs/{raw_path}"
                    href = url_map.get(slug) or federated.get(slug)
                if not href:
                    href = f"/{slug}/" if slug else "/"
                text = label or raw_path.rsplit("/", 1)[-1].replace("-", " ").title()
                return f"[{text}]({href})"

            return wikilink_re.sub(sub, body)

        resolved: list[PageSource] = []
        for page in pages:
            body = page.body
            if page.content_format == "patitas-markdown" or page.content_format == "mdx":
                body = replace(body, slug_to_url)
            resolved.append(
                PageSource(
                    path=page.path,
                    content_format=page.content_format,
                    meta=page.meta,
                    body=body,
                    source_path=page.source_path,
                    url=page.url,
                    slug=page.slug,
                )
            )
        return resolved

    def build_slug_to_url(self, pages: list[PageSource]) -> dict[str, str]:
        return {page.slug: page.url for page in pages}

    def page_dicts(self, pages: list[PageSource]) -> list[dict[str, Any]]:
        """Convert scanned pages to the loader's internal page dict shape."""
        return [
            {
                "path": page.path,
                "url": page.url,
                "slug": page.slug,
                "meta": page.meta,
                "body": page.body,
                "source_path": page.source_path,
                "content_format": page.content_format,
            }
            for page in pages
        ]
