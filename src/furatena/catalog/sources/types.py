"""Shared types for format-agnostic documentation ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from furatena.catalog.models import ContentIR, SectionChunk, TocEntry

_DEFAULT_FORMAT_BY_EXTENSION = {
    ".md": "patitas-markdown",
    ".markdown": "patitas-markdown",
    ".html": "html",
    ".htm": "html",
    ".rst": "docutils-rst",
    ".mdx": "mdx",
    ".myst": "myst-markdown",
}

_DEFAULT_INDEX_FILES = {
    ".md": ("_index.md",),
    ".html": ("index.html",),
    ".htm": ("index.html",),
    ".rst": ("index.rst", "_index.rst"),
    ".myst": ("index.myst", "_index.myst"),
}

_EDITION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_EDITION_STATUSES = frozenset({"current", "legacy", "deprecated", "preview", "eol"})
_EDITION_SOURCES = {"tags": "tags", "git-tags": "tags", "releases/tags": "tags"}
_EDITION_SORTS = frozenset({"semver-desc", "name-desc"})


@dataclass(frozen=True, slots=True)
class GitEditionOverride:
    """Lifecycle metadata that can retain one release beyond the count window."""

    status: str = "legacy"
    release_date: str | None = None
    end_of_life: str | None = None
    banner: str | None = None

    @classmethod
    def from_mapping(
        cls,
        raw: dict[str, Any],
        *,
        mount_id: str,
        edition_id: str,
    ) -> GitEditionOverride:
        status = str(raw.get("status") or "legacy").strip().lower()
        if status not in _EDITION_STATUSES - {"current"}:
            expected = ", ".join(sorted(_EDITION_STATUSES - {"current"}))
            raise ValueError(
                f"mount {mount_id!r} edition override {edition_id!r} has unsupported status "
                f"{status!r}; expected one of: {expected}"
            )
        release_date = _optional_date(raw.get("release_date"), mount_id, edition_id, "release_date")
        end_of_life = _optional_date(raw.get("end_of_life"), mount_id, edition_id, "end_of_life")
        return cls(
            status=status,
            release_date=release_date,
            end_of_life=end_of_life,
            banner=str(raw.get("banner") or "").strip() or None,
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "release_date": self.release_date,
            "end_of_life": self.end_of_life,
            "banner": self.banner,
        }


@dataclass(frozen=True, slots=True)
class GitEditionPolicy:
    """Bengal-compatible tag discovery policy for one git-backed mount."""

    source: str = "tags"
    count: int = 0
    pattern: str = "v*"
    strip_prefix: str = "v"
    sort: str = "semver-desc"
    include_prereleases: bool = False
    aliases: dict[str, str] = field(
        default_factory=lambda: {"latest": "latest", "stable": "latest"}
    )
    overrides: dict[str, GitEditionOverride] = field(default_factory=dict)

    @classmethod
    def from_mount_dict(
        cls,
        item: dict[str, Any],
        *,
        mount_id: str,
        git_backed: bool,
    ) -> GitEditionPolicy | None:
        raw = item.get("editions")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError(f"mount {mount_id!r} editions must be a mapping")
        if not git_backed:
            raise ValueError(
                f"mount {mount_id!r} editions require source.provider: git and a repository"
            )
        source_raw = str(raw.get("source") or "tags").strip()
        source = _EDITION_SOURCES.get(source_raw)
        if source is None:
            expected = ", ".join(sorted(_EDITION_SOURCES))
            raise ValueError(
                f"mount {mount_id!r} editions.source {source_raw!r} is unsupported; "
                f"expected one of: {expected}"
            )
        count_raw = raw.get("count", 0)
        if isinstance(count_raw, bool) or not isinstance(count_raw, int) or count_raw < 0:
            raise ValueError(f"mount {mount_id!r} editions.count must be a non-negative integer")
        pattern = str(raw.get("pattern") or "v*").strip()
        _validate_glob(pattern, mount_id=mount_id)
        sort = str(raw.get("sort") or "semver-desc").strip()
        if sort not in _EDITION_SORTS:
            expected = ", ".join(sorted(_EDITION_SORTS))
            raise ValueError(
                f"mount {mount_id!r} editions.sort {sort!r} is unsupported; "
                f"expected one of: {expected}"
            )
        include_prereleases = raw.get("include_prereleases", False)
        if not isinstance(include_prereleases, bool):
            raise ValueError(
                f"mount {mount_id!r} editions.include_prereleases must be true or false"
            )
        aliases_raw = raw.get("aliases")
        if aliases_raw is None:
            aliases_raw = {"latest": "latest", "stable": "latest"}
        if not isinstance(aliases_raw, dict):
            raise ValueError(
                f"mount {mount_id!r} editions.aliases must be a mapping; "
                "configure alias-to-edition entries."
            )
        aliases: dict[str, str] = {}
        for alias_raw, target_raw in aliases_raw.items():
            alias = str(alias_raw).strip()
            target = str(target_raw).strip()
            if not _EDITION_ID_RE.fullmatch(alias):
                raise ValueError(
                    f"mount {mount_id!r} edition alias {alias!r} must be URL-safe; "
                    "use letters, numbers, dots, underscores, or hyphens."
                )
            if target != "latest" and not _EDITION_ID_RE.fullmatch(target):
                raise ValueError(
                    f"mount {mount_id!r} edition alias {alias!r} target {target!r} "
                    "must be 'latest' or a URL-safe edition id; correct the target."
                )
            aliases[alias] = target
        overrides_raw = raw.get("overrides") or {}
        if not isinstance(overrides_raw, dict):
            raise ValueError(f"mount {mount_id!r} editions.overrides must be a mapping")
        overrides: dict[str, GitEditionOverride] = {}
        for edition_raw, override_raw in overrides_raw.items():
            edition_id = str(edition_raw).strip()
            validate_edition_id(edition_id, mount_id=mount_id, label="override")
            if not isinstance(override_raw, dict):
                raise ValueError(
                    f"mount {mount_id!r} edition override {edition_id!r} must be a mapping"
                )
            overrides[edition_id] = GitEditionOverride.from_mapping(
                override_raw,
                mount_id=mount_id,
                edition_id=edition_id,
            )
        return cls(
            source=source,
            count=count_raw,
            pattern=pattern,
            strip_prefix=str(raw["strip_prefix"] if raw.get("strip_prefix") is not None else "v"),
            sort=sort,
            include_prereleases=include_prereleases,
            aliases=aliases,
            overrides=overrides,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "count": self.count,
            "pattern": self.pattern,
            "strip_prefix": self.strip_prefix,
            "sort": self.sort,
            "include_prereleases": self.include_prereleases,
            "aliases": dict(sorted(self.aliases.items())),
            "overrides": {
                edition_id: override.to_dict()
                for edition_id, override in sorted(self.overrides.items())
            },
        }


@dataclass(frozen=True, slots=True)
class GitEditionSnapshot:
    """One discovered and materialized edition with immutable git provenance."""

    id: str
    ref: str
    resolved_ref: str
    content_root: Path
    status: str
    prerelease: bool
    discovered_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ref": self.ref,
            "resolved_ref": self.resolved_ref,
            "content_root": str(self.content_root),
            "status": self.status,
            "prerelease": self.prerelease,
            "discovered_at": self.discovered_at,
        }

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> GitEditionSnapshot:
        return cls(
            id=str(raw["id"]),
            ref=str(raw["ref"]),
            resolved_ref=str(raw["resolved_ref"]),
            content_root=Path(str(raw["content_root"])).expanduser().resolve(),
            status=str(raw["status"]),
            prerelease=bool(raw.get("prerelease")),
            discovered_at=str(raw["discovered_at"]),
        )


def validate_edition_id(value: str, *, mount_id: str, label: str = "edition") -> None:
    if value == "latest" or not _EDITION_ID_RE.fullmatch(value):
        raise ValueError(
            f"mount {mount_id!r} {label} id {value!r} must be URL-safe and cannot be 'latest'"
        )


def _validate_glob(pattern: str, *, mount_id: str) -> None:
    if not pattern:
        raise ValueError(f"mount {mount_id!r} editions.pattern cannot be empty")
    opened = False
    for char in pattern:
        if char == "[":
            if opened:
                raise ValueError(f"mount {mount_id!r} editions.pattern {pattern!r} is malformed")
            opened = True
        elif char == "]":
            if not opened:
                raise ValueError(f"mount {mount_id!r} editions.pattern {pattern!r} is malformed")
            opened = False
    if opened:
        raise ValueError(f"mount {mount_id!r} editions.pattern {pattern!r} is malformed")


def _optional_date(value: Any, mount_id: str, edition_id: str, label: str) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"mount {mount_id!r} edition override {edition_id!r} {label} must be YYYY-MM-DD"
        ) from exc
    return normalized


@dataclass(frozen=True, slots=True)
class GitSourceConfig:
    """Git-backed mount source settings and resolved sync state."""

    repo: str
    ref: str = "HEAD"
    path: str = ""
    sync_root: str | None = None
    resolved_ref: str | None = None
    source_url: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> GitSourceConfig | None:
        repo = str(
            raw.get("repo")
            or raw.get("repository")
            or raw.get("url")
            or raw.get("source_url")
            or ""
        ).strip()
        if not repo:
            return None
        return cls(
            repo=repo,
            ref=str(raw.get("ref") or raw.get("branch") or raw.get("commit") or "HEAD").strip()
            or "HEAD",
            path=_normalize_git_path(str(raw.get("path") or raw.get("sparse_path") or "").strip()),
            sync_root=str(raw.get("sync_root") or "").strip() or None,
        )

    def with_sync_state(
        self,
        *,
        resolved_ref: str,
        source_url: str | None,
    ) -> GitSourceConfig:
        return GitSourceConfig(
            repo=self.repo,
            ref=self.ref,
            path=self.path,
            sync_root=self.sync_root,
            resolved_ref=resolved_ref,
            source_url=source_url,
        )


def _normalize_git_path(value: str) -> str:
    return value.strip().strip("/")


@dataclass(frozen=True, slots=True)
class MountSourceConfig:
    """Filesystem scan settings for one documentation mount."""

    extensions: frozenset[str] = frozenset({".md"})
    index_files: frozenset[str] = frozenset({"_index.md"})
    format_map: dict[str, str] = field(
        default_factory=lambda: {".md": "patitas-markdown", ".markdown": "patitas-markdown"}
    )
    default_format: str = "patitas-markdown"
    provider: str = "filesystem"
    git: GitSourceConfig | None = None

    @classmethod
    def from_mount_dict(cls, item: dict[str, Any]) -> MountSourceConfig:
        source_raw = item.get("source") if isinstance(item.get("source"), dict) else {}
        source = cast(dict[str, Any], source_raw).copy()
        for key in (
            "provider",
            "repo",
            "repository",
            "url",
            "source_url",
            "ref",
            "branch",
            "commit",
            "path",
            "sparse_path",
            "sync_root",
        ):
            if key in item and key not in source:
                source[key] = item[key]
        provider = (
            str(source.get("provider") or item.get("source_provider") or "filesystem").strip()
            or "filesystem"
        )
        git = (
            GitSourceConfig.from_mapping(source)
            if provider == "git" or source.get("repo")
            else None
        )
        if git is not None:
            provider = "git"
        extensions_raw = item.get("extensions") or [".md"]
        extensions = frozenset(
            ext if str(ext).startswith(".") else f".{ext}" for ext in extensions_raw
        )
        index_defaults: list[str] = ["_index.md"]
        for ext in extensions:
            index_defaults.extend(_DEFAULT_INDEX_FILES.get(ext, ()))
        index_files = frozenset(str(name) for name in (item.get("index_files") or index_defaults))
        format_map_raw = item.get("format_map") or {}
        format_map = {
            (key if str(key).startswith(".") else f".{key}"): str(value)
            for key, value in format_map_raw.items()
        }
        default_format = str(item.get("default_format") or "patitas-markdown")
        if not format_map:
            format_map = {
                ext: _DEFAULT_FORMAT_BY_EXTENSION.get(ext, default_format) for ext in extensions
            }
        return cls(
            extensions=extensions,
            index_files=index_files,
            format_map=format_map,
            default_format=default_format,
            provider=provider,
            git=git,
        )

    def with_git_sync_state(
        self,
        *,
        resolved_ref: str,
        source_url: str | None,
    ) -> MountSourceConfig:
        if self.git is None:
            return self
        return MountSourceConfig(
            extensions=self.extensions,
            index_files=self.index_files,
            format_map=self.format_map,
            default_format=self.default_format,
            provider=self.provider,
            git=self.git.with_sync_state(
                resolved_ref=resolved_ref,
                source_url=source_url,
            ),
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
    source_url: str | None = None
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
            "source_url": self.source_url,
            "mount": self.mount,
            "last_indexed_at": self.last_sync_at,
        }
        return {
            "source_provider": self.provider,
            "source_repo": self.repo,
            "source_ref": self.ref,
            "source_url": self.source_url,
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
        mount: str = "",
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
