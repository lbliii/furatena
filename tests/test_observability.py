"""Vendor-neutral structured logging and OpenTelemetry compatibility."""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.observability import (
    OPERATIONAL_EVENT_NAMES,
    OpenTelemetrySink,
    OperationalEventEmitter,
    emit_operational_status,
)

REPO = Path(__file__).resolve().parents[1]


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.lock = threading.Lock()

    def emit(self, event) -> None:
        with self.lock:
            self.events.append(dict(event))


def test_structured_event_envelope_redacts_and_correlates(caplog) -> None:
    sink = _CollectingSink()
    logger = logging.getLogger("test.furatena.operational")
    emitter = OperationalEventEmitter(
        structured_logs=True,
        telemetry=sink,
        logger=logger,
    )

    with caplog.at_level(logging.INFO, logger=logger.name):
        event = emitter.emit(
            "furatena.source.sync",
            status="failed",
            severity="warning",
            correlation_id="deployment-42",
            attributes={
                "tenant": "acme",
                "privileged_token": "secret-value",
                "query": "private query",
            },
            timestamp=100.0,
        )

    assert event["schema_version"] == 1
    assert event["correlation_id"] == "deployment-42"
    assert event["event_id"]
    assert event["timestamp"] == "1970-01-01T00:01:40Z"
    assert event["attributes"] == {
        "tenant": "acme",
        "privileged_token": "<redacted>",
        "query": "<redacted:content>",
    }
    assert sink.events == [event]
    logged = json.loads(caplog.records[-1].message)
    assert logged == event
    assert "secret-value" not in caplog.text
    assert "private query" not in caplog.text


class _Span:
    def __init__(self, name: str, attributes: dict[str, object]) -> None:
        self.name = name
        self.attributes = attributes
        self.ended = False

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended = True


class _Tracer:
    def __init__(self) -> None:
        self.spans: list[_Span] = []

    def start_span(self, name: str, *, attributes: dict[str, object]) -> _Span:
        span = _Span(name, dict(attributes))
        self.spans.append(span)
        return span


class _Counter:
    def __init__(self) -> None:
        self.additions: list[tuple[int, dict[str, str]]] = []

    def add(self, value: int, attributes: dict[str, str]) -> None:
        self.additions.append((value, attributes))


class _Meter:
    def __init__(self) -> None:
        self.counter = _Counter()
        self.instrument: tuple[str, str, str] | None = None

    def create_counter(self, name: str, *, unit: str, description: str) -> _Counter:
        self.instrument = (name, unit, description)
        return self.counter


def test_opentelemetry_adapter_emits_span_and_counter() -> None:
    tracer = _Tracer()
    meter = _Meter()
    emitter = OperationalEventEmitter(
        telemetry=OpenTelemetrySink(tracer=tracer, meter=meter)
    )

    event = emitter.emit(
        "furatena.export.completed",
        status="ok",
        correlation_id="release-7",
        attributes={"page_count": 42, "paths": ["index.html"]},
        timestamp=200.0,
    )

    assert meter.instrument[0] == "furatena.operational.events"
    assert tracer.spans[0].name == "furatena.export.completed"
    assert tracer.spans[0].attributes["furatena.correlation.id"] == "release-7"
    assert tracer.spans[0].attributes["furatena.paths"] == '["index.html"]'
    assert tracer.spans[0].ended is True
    assert meter.counter.additions == [
        (
            1,
            {
                "event.name": "furatena.export.completed",
                "event.status": "ok",
                "event.severity": "info",
            },
        )
    ]
    assert event["correlation_id"] == "release-7"


def test_operational_status_events_share_one_correlation_id() -> None:
    sink = _CollectingSink()
    emitter = OperationalEventEmitter(telemetry=sink)
    report = {
        "serve_mode": "preview",
        "health": {"ok": True, "status": "healthy", "http_status": 200},
        "readiness": {"ok": False, "status": "not_ready", "http_status": 503},
        "freshness": {"ok": False, "status": "stale", "http_status": 200},
        "artifacts": {"ok": False, "status": "degraded", "http_status": 200},
    }

    emit_operational_status(emitter, report)

    assert {event["event_name"] for event in sink.events} == {
        "furatena.service.health",
        "furatena.service.readiness",
        "furatena.content.freshness",
        "furatena.artifact.status",
    }
    assert len({event["correlation_id"] for event in sink.events}) == 1
    assert [event["severity"] for event in sink.events] == [
        "info",
        "warning",
        "warning",
        "warning",
    ]


def test_event_emitter_is_free_threading_safe() -> None:
    assert_free_threading()
    sink = _CollectingSink()
    emitter = OperationalEventEmitter(telemetry=sink)

    with ThreadPoolExecutor(max_workers=64) as pool:
        events = list(
            pool.map(
                lambda index: emitter.emit(
                    "furatena.service.health",
                    status="healthy",
                    correlation_id=f"probe-{index}",
                ),
                range(1000),
            )
        )

    assert len(sink.events) == len(events) == 1000
    assert len({event["event_id"] for event in events}) == 1000
    assert {event["correlation_id"] for event in events} == {
        f"probe-{index}" for index in range(1000)
    }


def test_unknown_event_names_and_telemetry_modes_fail_loudly(monkeypatch) -> None:
    emitter = OperationalEventEmitter(structured_logs=True)
    with pytest.raises(ValueError, match="unknown operational event name"):
        emitter.emit("furatena.unknown", status="bad")

    monkeypatch.setenv("FURA_TELEMETRY", "vendor-x")
    with pytest.raises(ValueError, match="none or opentelemetry"):
        OperationalEventEmitter.from_environment()

    assert "furatena.incident.recovery" in OPERATIONAL_EVENT_NAMES


def test_operations_runbook_covers_rollout_rollback_backup_and_recovery() -> None:
    runbook = (
        REPO / "content/furatena/docs/operations/observability-and-recovery.md"
    ).read_text(encoding="utf-8")

    for heading in ("## Rollout", "## Rollback", "## Backup and restore", "## Incident recovery"):
        assert heading in runbook
    for contract in (
        "FURA_STRUCTURED_LOGS",
        "FURA_TELEMETRY=opentelemetry",
        "correlation_id",
        "furatena.incident.recovery",
        "recovery-point objective",
        "recovery-time objective",
        "SQLite's online backup",
    ):
        assert contract in runbook
