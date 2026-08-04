"""Regression proof for the deployed bulk-artifact verifier."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_verifier() -> ModuleType:
    path = REPO / "scripts" / "verify-live-artifacts.py"
    spec = importlib.util.spec_from_file_location("furatena_verify_live_artifacts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payloads() -> dict[str, bytes]:
    return {
        "/catalog.json": json.dumps({"page_count": 3, "pages": [{}, {}, {}]}).encode(),
        "/catalog/query.json": json.dumps(
            {"page_count": 2, "pages": [{}, {}], "total": 3, "limit": 2, "offset": 0}
        ).encode(),
        "/search.json": json.dumps({"page_count": 2, "entries": [{}, {}]}).encode(),
        "/semantic.json": json.dumps({"chunk_count": 2, "chunks": [{}, {}]}).encode(),
        "/llms.txt": b"agent index\n",
        "/llms-full.txt": b"complete agent corpus\n",
    }


def test_paginated_query_and_search_subset_are_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _load_verifier()
    payloads = _payloads()
    monkeypatch.setattr(
        verifier,
        "_fetch",
        lambda origin, path, *, timeout: payloads[path],
    )

    assert verifier.verify_live_artifacts("https://example.test") == {
        "page_count": 3,
        "query_page_count": 2,
        "search_page_count": 2,
        "semantic_chunk_count": 2,
        "llms_index_bytes": 12,
        "llms_full_bytes": 22,
    }


@pytest.mark.parametrize("path", ("/llms.txt", "/llms-full.txt"))
def test_empty_agent_indexes_fail(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    verifier = _load_verifier()
    payloads = _payloads()
    payloads[path] = b"\n"
    monkeypatch.setattr(
        verifier,
        "_fetch",
        lambda origin, requested_path, *, timeout: payloads[requested_path],
    )

    with pytest.raises(RuntimeError, match=path):
        verifier.verify_live_artifacts("https://example.test")


@pytest.mark.parametrize(
    ("path", "field", "value", "message"),
    (
        ("/catalog/query.json", "page_count", 3, "delivered collection"),
        ("/catalog/query.json", "total", 4, "frozen catalog"),
        ("/search.json", "page_count", 3, "delivered collection"),
    ),
)
def test_inconsistent_declared_counts_fail(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    field: str,
    value: int,
    message: str,
) -> None:
    verifier = _load_verifier()
    payloads = _payloads()
    changed = json.loads(payloads[path])
    changed[field] = value
    payloads[path] = json.dumps(changed).encode()
    monkeypatch.setattr(
        verifier,
        "_fetch",
        lambda origin, requested_path, *, timeout: payloads[requested_path],
    )

    with pytest.raises(RuntimeError, match=message):
        verifier.verify_live_artifacts("https://example.test")
