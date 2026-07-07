"""Typed domain errors for recoverable Furatena boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class CatalogError(Exception):
    """Base error with a stable code and machine-readable context."""

    code = "fura.catalog"
    exit_code = 2

    def __init__(
        self,
        message: str,
        *,
        path: str | Path | None = None,
        mount: str | None = None,
        slug: str | None = None,
        operation: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = str(path) if path is not None else None
        self.mount = mount
        self.slug = slug
        self.operation = operation

    @property
    def context(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "path": self.path,
                "mount": self.mount,
                "slug": self.slug,
                "operation": self.operation,
            }.items()
            if value is not None
        }

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}


class CatalogConfigError(CatalogError, ValueError):
    code = "fura.config"
    exit_code = 3


class SourceSyncError(CatalogError, RuntimeError):
    code = "fura.source_sync"
    exit_code = 4


class ContentParseError(CatalogError, ValueError):
    code = "fura.content_parse"


class AccessPolicyError(CatalogError, ValueError):
    code = "fura.access"


class AccessDeniedError(CatalogError, PermissionError):
    code = "fura.access_denied"


class CatalogLoadError(CatalogError, FileNotFoundError):
    code = "fura.catalog_load"
    exit_code = 4


class ExportError(CatalogError, RuntimeError):
    code = "fura.export"
    exit_code = 4
