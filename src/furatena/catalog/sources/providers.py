"""Source provider implementations."""

from __future__ import annotations

import hashlib
from pathlib import Path

from furatena.catalog.exceptions import CatalogConfigError
from furatena.catalog.sources.git import repo_web_url
from furatena.catalog.sources.scanner import FilesystemScanner
from furatena.catalog.sources.types import (
    GitSourceConfig,
    MountSourceConfig,
    PageSource,
    SourceFingerprint,
    SourceProvenance,
)


class FilesystemSourceProvider:
    """SourceProvider implementation backed by local files."""

    id = "filesystem"

    def __init__(self, config: MountSourceConfig) -> None:
        self._scanner = FilesystemScanner(config)

    @property
    def scanner(self) -> FilesystemScanner:
        return self._scanner

    def enumerate(
        self,
        content_root: Path,
        *,
        url_prefix: str = "",
        include_private: bool = False,
    ) -> list[PageSource]:
        return self._scanner.scan(
            content_root,
            url_prefix=url_prefix,
            include_private=include_private,
        )

    def read(self, source: PageSource) -> str:
        return source.path.read_text(encoding="utf-8")

    def fingerprint(self, source: PageSource) -> SourceFingerprint:
        stat = source.path.stat()
        digest = hashlib.sha256(source.path.read_bytes()).hexdigest()
        return SourceFingerprint(
            value=digest,
            size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
        )

    def provenance(self, source: PageSource, *, mount: str) -> SourceProvenance:
        return SourceProvenance.filesystem(source, mount=mount)


class GitSourceProvider(FilesystemSourceProvider):
    """SourceProvider implementation backed by a synced git snapshot."""

    id = "git"

    def __init__(self, config: MountSourceConfig) -> None:
        super().__init__(config)
        if config.git is None:
            raise CatalogConfigError("GitSourceProvider requires MountSourceConfig.git")
        self._git = config.git

    def provenance(self, source: PageSource, *, mount: str) -> SourceProvenance:
        return SourceProvenance(
            provider="git",
            repo=self._git.repo,
            ref=self._git.resolved_ref or self._git.ref,
            source_url=_source_blob_url(self._git, source.source_path),
            path=source.source_path,
            mount=mount,
            last_sync_at=SourceProvenance.filesystem(source, mount=mount).last_sync_at,
        )


def source_provider_for_config(config: MountSourceConfig) -> FilesystemSourceProvider:
    """Return the source provider for a mount configuration."""
    if config.provider == "git" or config.git is not None:
        return GitSourceProvider(config)
    return FilesystemSourceProvider(config)


def _source_blob_url(config: GitSourceConfig, source_path: str) -> str | None:
    base = config.source_url or _repo_web_url(config.repo)
    if not base:
        return None
    ref = config.resolved_ref or config.ref
    parts = [part for part in (config.path, source_path) if part]
    full_path = "/".join(part.strip("/") for part in parts)
    if base.startswith("file://"):
        return f"{base.rstrip('/')}/{full_path}" if full_path else base
    if full_path:
        return f"{base.rstrip('/')}/blob/{ref}/{full_path}"
    return f"{base.rstrip('/')}/tree/{ref}"


def _repo_web_url(repo: str) -> str | None:
    return repo_web_url(repo)
