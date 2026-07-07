"""Durable, privacy-safe audit storage contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from furatena.catalog.audit_store import InMemoryAuditStore, JsonLinesAuditStore
from furatena.catalog.benchmarks import assert_free_threading


def _event(index: int = 1, **overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "actor": "agent-ci",
        "tenant": "acme",
        "action": "author_apply_edit",
        "target": "docs/get-started",
        "outcome": "ok",
        "operation_id": f"operation-{index}",
        "inputs": {
            "privileged_token": "top-secret",
            "old_text": "private old text",
            "new_text": "private new text",
            "query": "private search",
        },
    }
    event.update(overrides)
    return event


def test_memory_store_normalizes_redacts_filters_and_retains() -> None:
    now = [1_000_000.0]
    store = InMemoryAuditStore(retention_days=1, clock=lambda: now[0])

    first = store.append(_event(timestamp=now[0] - 10))
    store.append(_event(2, tenant="other", timestamp=now[0]))

    assert first["schema_version"] == 1
    assert first["event_id"]
    assert first["correlation_id"] == "operation-1"
    assert first["inputs"] == {
        "privileged_token": "<redacted>",
        "old_text": "<redacted:content>",
        "new_text": "<redacted:content>",
        "query": "<redacted:content>",
    }
    assert store.query(tenant="acme") == [first]
    assert store.query(limit=0) == []
    first["inputs"]["query"] = "mutated"
    assert store.query(tenant="acme")[0]["inputs"]["query"] == "<redacted:content>"

    now[0] += 86_401
    assert store.query() == []


@pytest.mark.parametrize("missing", ["actor", "action", "outcome"])
def test_store_rejects_events_without_required_identity(missing: str) -> None:
    event = _event()
    event.pop(missing)

    with pytest.raises(ValueError, match=f"audit {missing} is required"):
        InMemoryAuditStore().append(event)


def test_jsonl_store_survives_restart_and_exports(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "events.jsonl"
    store = JsonLinesAuditStore(path, retention_days=30)
    persisted = store.append(_event())

    restarted = JsonLinesAuditStore(path, retention_days=30)
    exported = restarted.export()
    export_path = tmp_path / "exports" / "audit.json"
    restarted.write_export(export_path)

    assert exported["backend"] == "jsonl"
    assert exported["retention_days"] == 30
    assert exported["count"] == 1
    assert exported["entries"] == [persisted]
    assert json.loads(export_path.read_text(encoding="utf-8")) == exported
    assert path.stat().st_mode & 0o777 == 0o600
    assert export_path.stat().st_mode & 0o777 == 0o600
    assert "top-secret" not in path.read_text(encoding="utf-8")
    assert "private search" not in path.read_text(encoding="utf-8")


def test_jsonl_store_purges_expired_records_atomically(tmp_path: Path) -> None:
    now = [2_000_000.0]
    path = tmp_path / "events.jsonl"
    store = JsonLinesAuditStore(path, retention_days=1, clock=lambda: now[0])
    store.append(_event(timestamp=now[0]))

    now[0] += 86_401

    assert store.purge() == 1
    assert JsonLinesAuditStore(path).query() == []
    assert path.read_text(encoding="utf-8") == ""


def test_jsonl_store_is_free_threading_safe(tmp_path: Path) -> None:
    assert_free_threading()
    path = tmp_path / "events.jsonl"
    store = JsonLinesAuditStore(path)

    with ThreadPoolExecutor(max_workers=64) as pool:
        persisted = list(pool.map(lambda index: store.append(_event(index)), range(256)))

    restarted = JsonLinesAuditStore(path)
    events = restarted.query()
    assert len(events) == 256
    assert len({event["event_id"] for event in events}) == 256
    assert {event["correlation_id"] for event in events} == {
        f"operation-{index}" for index in range(256)
    }
    assert len(path.read_text(encoding="utf-8").splitlines()) == 256
    assert len(persisted) == 256
