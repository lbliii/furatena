"""Format-agnostic documentation source ingestion."""

from furatena.catalog.sources.registry import (
    get_content_adapter,
    register_adapter,
    registered_formats,
)
from furatena.catalog.sources.scanner import FilesystemScanner, file_to_url
from furatena.catalog.sources.types import AdaptedContent, MountSourceConfig, PageSource

__all__ = [
    "AdaptedContent",
    "FilesystemScanner",
    "MountSourceConfig",
    "PageSource",
    "file_to_url",
    "get_content_adapter",
    "register_adapter",
    "registered_formats",
]
