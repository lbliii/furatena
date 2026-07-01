"""Shared types for format-agnostic documentation ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from furatena.catalog.models import ContentIR, SectionChunk, TocEntry

_DEFAULT_FORMAT_BY_EXTENSION = {
    ".md": "patitas-markdown",
    ".markdown": "patitas-markdown",
    ".html": "html",
    ".htm": "html",
    ".rst": "docutils-rst",
    ".mdx": "mdx",
}

_DEFAULT_INDEX_FILES = {
    ".md": ("_index.md",),
    ".html": ("index.html",),
    ".htm": ("index.html",),
    ".rst": ("index.rst", "_index.rst"),
}


@dataclass(frozen=True, slots=True)
class MountSourceConfig:
    """Filesystem scan settings for one documentation mount."""

    extensions: frozenset[str] = frozenset({".md"})
    index_files: frozenset[str] = frozenset({"_index.md"})
    format_map: dict[str, str] = field(
        default_factory=lambda: {".md": "patitas-markdown", ".markdown": "patitas-markdown"}
    )
    default_format: str = "patitas-markdown"

    @classmethod
    def from_mount_dict(cls, item: dict[str, Any]) -> MountSourceConfig:
        extensions_raw = item.get("extensions") or [".md"]
        extensions = frozenset(
            ext if str(ext).startswith(".") else f".{ext}" for ext in extensions_raw
        )
        index_defaults: list[str] = ["_index.md"]
        for ext in extensions:
            index_defaults.extend(_DEFAULT_INDEX_FILES.get(ext, ()))
        index_files = frozenset(
            str(name) for name in (item.get("index_files") or index_defaults)
        )
        format_map_raw = item.get("format_map") or {}
        format_map = {
            (key if str(key).startswith(".") else f".{key}"): str(value)
            for key, value in format_map_raw.items()
        }
        default_format = str(item.get("default_format") or "patitas-markdown")
        if not format_map:
            format_map = {
                ext: _DEFAULT_FORMAT_BY_EXTENSION.get(ext, default_format)
                for ext in extensions
            }
        return cls(
            extensions=extensions,
            index_files=index_files,
            format_map=format_map,
            default_format=default_format,
        )

    def content_format_for(self, path: Path) -> str:
        return self.format_map.get(path.suffix.lower(), self.default_format)

    def tracked_extensions(self) -> frozenset[str]:
        mapped = frozenset(self.format_map.keys())
        return self.extensions | mapped


@dataclass(frozen=True, slots=True)
class PageSource:
    """One discovered documentation source file before adaptation."""

    path: Path
    content_format: str
    meta: dict[str, Any]
    body: str
    source_path: str
    url: str
    slug: str


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """Stable-ish content fingerprint for one provider source."""

    value: str
    algorithm: str = "sha256"
    size: int | None = None
    modified_ns: int | None = None


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Provider provenance projected into DCP/export metadata."""

    provider: str
    path: str
    mount: str
    repo: str | None = None
    ref: str | None = None
    last_sync_at: str | None = None

    @classmethod
    def filesystem(cls, source: PageSource, *, mount: str) -> SourceProvenance:
        modified = None
        if source.path.is_file():
            modified = datetime.fromtimestamp(source.path.stat().st_mtime, UTC).isoformat()
        return cls(
            provider="filesystem",
            path=source.source_path,
            mount=mount,
            last_sync_at=modified,
        )

    def to_meta(self) -> dict[str, Any]:
        provenance = {
            "provider": self.provider,
            "repo": self.repo,
            "ref": self.ref,
            "path": self.path,
            "mount": self.mount,
            "last_indexed_at": self.last_sync_at,
        }
        return {
            "source_provider": self.provider,
            "source_repo": self.repo,
            "source_ref": self.ref,
            "last_indexed_at": self.last_sync_at,
            "provenance": provenance,
        }


class SourceProvider(Protocol):
    """Source backend contract for filesystem, git, archive, and future SaaS mounts."""

    id: str

    def enumerate(
        self,
        content_root: Path,
        *,
        url_prefix: str = "",
        include_private: bool = False,
    ) -> list[PageSource]: ...

    def read(self, source: PageSource) -> str: ...

    def fingerprint(self, source: PageSource) -> SourceFingerprint: ...

    def provenance(self, source: PageSource, *, mount: str) -> SourceProvenance: ...


@dataclass(frozen=True, slots=True)
class AdaptedContent:
    """Normalized output from a format adapter at index time."""

    body_html: str
    content_ir: ContentIR | None
    toc: tuple[TocEntry, ...]
    body_text: str
    sections: tuple[SectionChunk, ...] = ()
    native_ast: str | None = None
    native_document: object | None = None


class ContentAdapter(Protocol):
    """Parse and render one supported documentation source format."""

    content_format: str

    def adapt(
        self,
        source: PageSource,
        *,
        stubs,
        render_markdown,
        get_backlinks,
        content_root: Path,
        include_stack: set[str] | None = None,
        include_depth: int = 0,
        document: object | None = None,
    ) -> AdaptedContent: ...

    def parse(self, body: str) -> tuple[object | None, ContentIR | None]: ...

    def parse_incremental(
        self,
        body: str,
        previous: object | None,
        *,
        previous_body: str = "",
    ) -> tuple[object | None, ContentIR | None]: ...

    def invalidation_regions(self, old: object | None, new: object | None) -> set[str]: ...
