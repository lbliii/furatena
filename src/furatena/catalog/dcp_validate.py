"""Validate ``catalog.json`` exports against the DCP v3 JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from furatena.catalog.export import catalog_graph
from furatena.catalog.paths import catalog_v3_schema_path


def load_catalog_v3_schema() -> dict[str, Any]:
    path = catalog_v3_schema_path()
    if not path.is_file():
        raise FileNotFoundError(f"DCP schema missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_catalog_payload(
    payload: dict[str, Any],
    *,
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """Return human-readable schema validation errors (empty when valid)."""
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema is required for DCP validation (install docs group)"]

    schema_doc = schema or load_catalog_v3_schema()
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
    return validate_catalog_payload(payload)


def validate_catalog_json_file(path: Path) -> list[str]:
    """Validate a frozen or exported ``catalog.json`` file on disk."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return [f"{path}: expected JSON object"]
    return validate_catalog_payload(raw)
