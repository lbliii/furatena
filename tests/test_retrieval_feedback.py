"""Privacy-safe retrieval feedback and free-threaded sink contracts."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from chirp.testing import TestClient

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.mcp import FuraMCPServer, MCPAccessPolicy
from furatena.catalog.retrieval_feedback import (
    JsonlRetrievalFeedbackSink,
    MemoryRetrievalFeedbackSink,
    RetrievalFeedbackCollector,
    RetrievalFeedbackKind,
    RetrievalFeedbackPolicy,
)

REPO = Path(__file__).resolve().parents[1]


def test_collection_is_disabled_by_default() -> None:
    sink = MemoryRetrievalFeedbackSink()
    collector = RetrievalFeedbackCollector(sink=sink)

    assert (
        collector.record_query(
            "private words",
            tenant="acme",
            surface="browser",
            result_count=0,
        )
        == ()
    )
    assert (
        collector.record_selection(
            tenant="acme",
            surface="browser",
            node_id="docs:latest:index",
        )
        is None
    )
    assert sink.events("acme") == ()


def test_queries_zero_results_selections_and_tools_are_sanitized() -> None:
    sink = MemoryRetrievalFeedbackSink()
    collector = RetrievalFeedbackCollector(
        policy=RetrievalFeedbackPolicy(enabled=True, digest_salt="test-salt"),
        sink=sink,
    )

    query_events = collector.record_query(
        "orion-cinder recovery sequence",
        tenant="acme",
        surface="browser",
        result_count=0,
        metadata={"token": "do-not-store", "mount": "docs"},
    )
    selection = collector.record_selection(
        tenant="acme",
        surface="browser",
        node_id="docs:latest:guide",
        chunk_id="docs:latest:guide#install",
        rank=1,
    )
    tool = collector.record_tool_outcome(
        tenant="acme",
        surface="mcp:local",
        tool="semantic_search",
        status="ok",
        duration_ms=4.2,
        metadata={"authorization": "Bearer secret"},
    )

    assert [event.kind for event in query_events] == [
        RetrievalFeedbackKind.QUERY,
        RetrievalFeedbackKind.ZERO_RESULT,
    ]
    assert selection is not None and tool is not None
    events = sink.events("acme")
    assert {event.kind for event in events} == set(RetrievalFeedbackKind)
    for event in query_events:
        assert "query" not in event.data
        assert len(event.data["query_sha256"]) == 64
        assert event.data["metadata"]["token"] == "<redacted>"
    assert tool.data["metadata"]["authorization"] == "<redacted>"
    assert sink.events("other-tenant") == ()


def test_sampling_and_raw_query_collection_require_explicit_opt_in() -> None:
    sink = MemoryRetrievalFeedbackSink()
    dropped = RetrievalFeedbackCollector(
        policy=RetrievalFeedbackPolicy(enabled=True, sample_rate=0.0),
        sink=sink,
    )
    assert (
        dropped.record_query(
            "never stored",
            tenant="acme",
            surface="browser",
            result_count=1,
        )
        == ()
    )

    raw = RetrievalFeedbackCollector(
        policy=RetrievalFeedbackPolicy(enabled=True, query_mode="raw"),
        sink=sink,
    )
    event = raw.record_query(
        "explicitly stored",
        tenant="acme",
        surface="browser",
        result_count=1,
    )[0]
    assert event.data["query"] == "explicitly stored"


def test_jsonl_sink_is_tenant_isolated_retained_and_free_thread_safe(tmp_path: Path) -> None:
    assert_free_threading()
    clock = [datetime(2026, 1, 1, tzinfo=UTC)]
    sink = JsonlRetrievalFeedbackSink(
        tmp_path / "feedback",
        retention_days=90,
        now=lambda: clock[0],
    )
    collector = RetrievalFeedbackCollector(
        policy=RetrievalFeedbackPolicy(enabled=True, retention_days=30),
        sink=sink,
        now=lambda: clock[0],
    )
    collector.record_selection(
        tenant="acme/../../unsafe",
        surface="browser",
        node_id="old",
    )
    clock[0] += timedelta(days=31)

    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(
            pool.map(
                lambda index: collector.record_selection(
                    tenant="acme/../../unsafe",
                    surface="browser",
                    node_id=f"node-{index}",
                ),
                range(64),
            )
        )

    stored = sink.events("acme/../../unsafe")
    assert len(stored) == 64
    assert {event.data["node_id"] for event in stored} == {f"node-{index}" for index in range(64)}
    assert len({event.event_id for event in events if event is not None}) == 64
    assert sink.events("other") == ()
    assert list((tmp_path / "feedback").glob("tenant-*.jsonl"))
    assert not (tmp_path / "unsafe.jsonl").exists()


def test_browser_queries_and_mcp_tool_outcomes_use_injected_sink() -> None:
    sink = MemoryRetrievalFeedbackSink()
    collector = RetrievalFeedbackCollector(
        policy=RetrievalFeedbackPolicy(enabled=True),
        sink=sink,
    )
    docs = DocsApp.from_paths(
        REPO / "app" / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        retrieval_feedback=collector,
    )
    client = TestClient(docs.create_app())

    async def _search() -> None:
        response = await client.get("/search?q=installation")
        assert response.status == 200

    asyncio.run(_search())
    server = FuraMCPServer(
        docs,
        policy=MCPAccessPolicy(tenant="acme"),
        retrieval_feedback=collector,
    )
    result = server.call_tool("semantic_search", {"query": "installation"})

    assert result["isError"] is False
    browser = sink.events("default")
    mcp = sink.events("acme")
    assert any(event.kind == RetrievalFeedbackKind.QUERY for event in browser)
    assert {event.kind for event in mcp} == {
        RetrievalFeedbackKind.QUERY,
        RetrievalFeedbackKind.TOOL_OUTCOME,
    }
    assert all("query" not in event.data for event in (*browser, *mcp))


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ({"query_mode": "unknown"}, "query_mode"),
        ({"sample_rate": 1.1}, "sample_rate"),
        ({"retention_days": 0}, "retention_days"),
    ],
)
def test_invalid_privacy_policy_fails_loudly(
    policy: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RetrievalFeedbackPolicy(**policy)
