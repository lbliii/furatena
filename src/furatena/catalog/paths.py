"""Filesystem anchors for the installed catalog package."""

from __future__ import annotations

from pathlib import Path


def catalog_root() -> Path:
    """Root directory of the ``catalog`` Python package."""
    return Path(__file__).resolve().parent


def schemas_root() -> Path:
    """JSON Schema and protocol artifacts shipped with the runtime."""
    return catalog_root() / "schemas"


def catalog_schema_path(version: int) -> Path:
    return schemas_root() / f"catalog-v{version}.schema.json"


def catalog_v3_schema_path() -> Path:
    return catalog_schema_path(3)


def fixtures_root() -> Path:
    """Compatibility and sample artifacts shipped with the runtime."""
    return catalog_root() / "fixtures"


def dcp_fixtures_root() -> Path:
    return fixtures_root() / "dcp"
