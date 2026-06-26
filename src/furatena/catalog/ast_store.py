"""Patitas AST JSON persistence for incremental invalidation."""

from __future__ import annotations

import json

from patitas.nodes import Document
from patitas.serialization import from_json, to_json


def document_to_json(document: Document) -> str:
    """Serialize a Patitas document AST to JSON."""
    return to_json(document)


def document_from_json(raw: str) -> Document:
    """Restore a Patitas document AST from JSON."""
    return from_json(_normalize_directive_options(raw))


def ast_roundtrip_error(raw: str) -> str | None:
    """Return an error message when frozen AST JSON cannot round-trip on Patitas."""
    try:
        document = document_from_json(raw)
        roundtrip = to_json(document)
        document_from_json(roundtrip)
    except (KeyError, TypeError, ValueError) as exc:
        return str(exc)
    return None


def _normalize_directive_options(raw: str) -> str:
    """Strip Patitas directive option payloads that cannot be deserialized by 0.4.x."""
    data = json.loads(raw)

    def walk(value):
        if isinstance(value, dict):
            if value.get("_type") == "DirectiveOptions":
                return {"_type": "DirectiveOptions"}
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return json.dumps(walk(data), sort_keys=True)
