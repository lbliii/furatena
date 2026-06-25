"""Patitas AST JSON persistence for incremental invalidation."""

from __future__ import annotations

from patitas.nodes import Document
from patitas.serialization import from_json, to_json


def document_to_json(document: Document) -> str:
    """Serialize a Patitas document AST to JSON."""
    return to_json(document)


def document_from_json(raw: str) -> Document:
    """Restore a Patitas document AST from JSON."""
    return from_json(raw)


def ast_roundtrip_error(raw: str) -> str | None:
    """Return an error message when frozen AST JSON cannot round-trip on Patitas."""
    try:
        document = from_json(raw)
        roundtrip = to_json(document)
        from_json(roundtrip)
    except (KeyError, TypeError, ValueError) as exc:
        return str(exc)
    return None
