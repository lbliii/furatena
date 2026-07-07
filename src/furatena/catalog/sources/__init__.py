"""Format-agnostic documentation source ingestion."""

from furatena.catalog.sources.git import GitSyncResult, sync_git_source
from furatena.catalog.sources.providers import (
    FilesystemSourceProvider,
    GitSourceProvider,
    source_provider_for_config,
)
from furatena.catalog.sources.registry import (
    get_content_adapter,
    register_adapter,
    registered_formats,
)
from furatena.catalog.sources.scanner import FilesystemScanner, file_to_url
from furatena.catalog.sources.types import (
    AdaptedContent,
    ContentAdapter,
    GitSourceConfig,
    MountSourceConfig,
    PageSource,
    SourceFingerprint,
    SourceProvenance,
    SourceProvider,
)

__all__ = [
    "AdaptedContent",
    "ContentAdapter",
    "FilesystemScanner",
    "FilesystemSourceProvider",
    "GitSourceConfig",
    "GitSourceProvider",
    "GitSyncResult",
    "MountSourceConfig",
    "PageSource",
    "SourceFingerprint",
    "SourceProvenance",
    "SourceProvider",
    "file_to_url",
    "get_content_adapter",
    "register_adapter",
    "registered_formats",
    "source_provider_for_config",
    "sync_git_source",
]
