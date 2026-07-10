"""Validate ``catalog.json`` exports against supported DCP JSON Schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from furatena.catalog.export import catalog_graph
from furatena.catalog.paths import catalog_schema_path, catalog_v3_schema_path, dcp_fixtures_root

SUPPORTED_DCP_VERSIONS = (2, 3)


def load_catalog_v3_schema() -> dict[str, Any]:
    path = catalog_v3_schema_path()
    if not path.is_file():
        raise FileNotFoundError(f"DCP schema missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_catalog_schema(version: int) -> dict[str, Any]:
    """Load the JSON Schema for a supported DCP catalog version."""
    if version == 3:
        return load_catalog_v3_schema()
    if version not in SUPPORTED_DCP_VERSIONS:
        supported = ", ".join(str(item) for item in SUPPORTED_DCP_VERSIONS)
        raise ValueError(f"Unsupported DCP schema version {version}; supported: {supported}")
    path = catalog_schema_path(version)
    if not path.is_file():
        raise FileNotFoundError(f"DCP schema missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def dcp_fixture_paths() -> tuple[Path, ...]:
    """Bundled compatibility sample exports for supported DCP versions."""
    root = dcp_fixtures_root()
    if not root.is_dir():
        return ()
    return tuple(sorted(root.glob("catalog-v*.json")))


def payload_schema_version(payload: dict[str, Any]) -> int:
    """Return the DCP schema version claimed by a catalog payload."""
    raw = payload.get("schema_version", payload.get("version"))
    try:
        return int(raw)
    except TypeError, ValueError:
        return 0


def validate_catalog_payload(
    payload: dict[str, Any],
    *,
    schema: dict[str, Any] | None = None,
    schema_version: int | None = None,
) -> list[str]:
    """Return human-readable schema validation errors (empty when valid)."""
    version = schema_version or payload_schema_version(payload)
    if version not in SUPPORTED_DCP_VERSIONS:
        supported = ", ".join(str(item) for item in SUPPORTED_DCP_VERSIONS)
        return [f"root: unsupported DCP schema_version {version!r} (supported: {supported})"]
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is required for DCP validation (install docs group)"]

    schema_doc = schema or load_catalog_schema(version)
    validator = jsonschema.Draft202012Validator(schema_doc)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.path)):
        location = ".".join(str(part) for part in error.path) or "root"
        errors.append(f"{location}: {error.message}")
    return errors


def validate_catalog_graph(
    catalog,
    *,
    schema_version: int = 3,
) -> list[str]:
    """Validate a live registry/shard export graph."""
    payload = catalog_graph(catalog, schema_version=schema_version)
    return validate_catalog_payload(payload, schema_version=schema_version)


def validate_catalog_json_file(path: Path) -> list[str]:
    """Validate a frozen or exported ``catalog.json`` file on disk."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return [f"{path}: expected JSON object"]
    return [f"{path}: {error}" for error in validate_catalog_payload(raw)]
