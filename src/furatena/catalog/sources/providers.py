"""Source provider implementations."""

from __future__ import annotations

import hashlib
from pathlib import Path

from furatena.catalog.sources.scanner import FilesystemScanner
from furatena.catalog.sources.types import (
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
