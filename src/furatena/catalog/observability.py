"""Vendor-neutral structured events with an optional OpenTelemetry adapter."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Protocol

from furatena.catalog.audit_store import redact_audit_value

OPERATIONAL_EVENT_NAMES = frozenset(
    {
        "furatena.service.health",
        "furatena.service.readiness",
        "furatena.content.freshness",
        "furatena.artifact.status",
        "furatena.source.sync",
        "furatena.index.status",
        "furatena.freeze.completed",
        "furatena.export.completed",
        "furatena.incident.recovery",
    }
)


class TelemetrySink(Protocol):
    """Optional metrics/trace boundary for structured operational events."""

    def emit(self, event: Mapping[str, Any]) -> None: ...


class NullTelemetrySink:
    """Dependency-free no-op telemetry implementation."""

    def emit(self, event: Mapping[str, Any]) -> None:
        return None


class OpenTelemetrySink:
    """Map canonical events to OpenTelemetry spans and an event counter."""

    def __init__(self, *, tracer: Any, meter: Any) -> None:
        self._tracer = tracer
        self._counter = meter.create_counter(
            "furatena.operational.events",
            unit="{event}",
            description="Furatena operational events by stable name and status",
        )

    @classmethod
    def from_installed_api(cls) -> OpenTelemetrySink:
        """Load the optional OpenTelemetry API only when explicitly configured."""
        try:
            metrics = import_module("opentelemetry.metrics")
            trace = import_module("opentelemetry.trace")
        except ImportError as exc:
            raise RuntimeError(
                "FURA_TELEMETRY=opentelemetry requires the opentelemetry-api package"
            ) from exc
        return cls(
            tracer=trace.get_tracer("furatena.operational", "1"),
            meter=metrics.get_meter("furatena.operational", "1"),
        )

    def emit(self, event: Mapping[str, Any]) -> None:
        attributes = _otel_attributes(event)
        span = self._tracer.start_span(str(event["event_name"]), attributes=attributes)
        try:
            span.set_attribute("furatena.event_id", str(event["event_id"]))
            span.set_attribute("furatena.correlation_id", str(event["correlation_id"]))
        finally:
            span.end()
        self._counter.add(
            1,
            {
                "event.name": str(event["event_name"]),
                "event.status": str(event["status"]),
                "event.severity": str(event["severity"]),
            },
        )


@dataclass(frozen=True, slots=True)
class OperationalEvent:
    """Stable event envelope shared by logs, metrics, and traces."""

    event_name: str
    event_id: str
    correlation_id: str
    timestamp: str
    severity: str
    status: str
    attributes: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_name": self.event_name,
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "severity": self.severity,
            "status": self.status,
            "attributes": dict(self.attributes),
        }


class OperationalEventEmitter:
    """Emit privacy-safe canonical events to JSON logs and optional telemetry."""

    def __init__(
        self,
        *,
        structured_logs: bool = False,
        telemetry: TelemetrySink | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.structured_logs = structured_logs
        self.telemetry = telemetry or NullTelemetrySink()
        self.logger = logger or logging.getLogger("furatena.operational")
        self.enabled = structured_logs or not isinstance(self.telemetry, NullTelemetrySink)

    @classmethod
    def disabled(cls) -> OperationalEventEmitter:
        return cls()

    @classmethod
    def from_environment(cls) -> OperationalEventEmitter:
        structured_logs = os.environ.get("FURA_STRUCTURED_LOGS", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        telemetry_name = os.environ.get("FURA_TELEMETRY", "none").strip().lower()
        if telemetry_name in {"", "none", "off"}:
            telemetry: TelemetrySink = NullTelemetrySink()
        elif telemetry_name == "opentelemetry":
            telemetry = OpenTelemetrySink.from_installed_api()
        else:
            raise ValueError("FURA_TELEMETRY must be none or opentelemetry")
        return cls(structured_logs=structured_logs, telemetry=telemetry)

    def emit(
        self,
        event_name: str,
        *,
        status: str,
        severity: str = "info",
        correlation_id: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        if event_name not in OPERATIONAL_EVENT_NAMES:
            raise ValueError(f"unknown operational event name: {event_name}")
        event = OperationalEvent(
            event_name=event_name,
            event_id=uuid.uuid4().hex,
            correlation_id=_identifier(correlation_id) or uuid.uuid4().hex,
            timestamp=_iso(time.time() if timestamp is None else timestamp),
            severity=_severity(severity),
            status=str(status).strip() or "unknown",
            attributes=redact_audit_value(dict(attributes or {})),
        ).to_dict()
        if self.structured_logs:
            self.logger.log(_logging_level(event["severity"]), json.dumps(event, sort_keys=True))
        self.telemetry.emit(event)
        return event


def emit_operational_status(emitter: OperationalEventEmitter, report: Mapping[str, Any]) -> None:
    """Emit the four #193 status contracts under one correlation id."""
    if not emitter.enabled:
        return
    correlation_id = uuid.uuid4().hex
    mappings = (
        ("furatena.service.health", report.get("health") or {}),
        ("furatena.service.readiness", report.get("readiness") or {}),
        ("furatena.content.freshness", report.get("freshness") or {}),
        ("furatena.artifact.status", report.get("artifacts") or {}),
    )
    for event_name, payload in mappings:
        status = str(payload.get("status") or "unknown")
        emitter.emit(
            event_name,
            status=status,
            severity="info" if bool(payload.get("ok")) else "warning",
            correlation_id=correlation_id,
            attributes={
                "serve_mode": report.get("serve_mode"),
                "http_status": payload.get("http_status"),
                "remediation": payload.get("remediation", []),
            },
        )


def _otel_attributes(event: Mapping[str, Any]) -> dict[str, Any]:
    attributes = {
        "furatena.event.name": str(event["event_name"]),
        "furatena.event.id": str(event["event_id"]),
        "furatena.correlation.id": str(event["correlation_id"]),
        "furatena.event.status": str(event["status"]),
        "furatena.event.severity": str(event["severity"]),
    }
    for key, value in (event.get("attributes") or {}).items():
        name = f"furatena.{key}"
        attributes[name] = (
            value if isinstance(value, bool | int | float | str) else json.dumps(value)
        )
    return attributes


def _identifier(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) > 200:
        raise ValueError("operational correlation identifiers must be at most 200 characters")
    return normalized


def _severity(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"debug", "info", "warning", "error", "critical"}:
        raise ValueError(f"unknown operational event severity: {value}")
    return normalized


def _logging_level(severity: str) -> int:
    return {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }[severity]


def _iso(value: float) -> str:
    normalized = float(value)
    if not (0 <= normalized < float("inf")):
        raise ValueError("operational event timestamp must be finite and non-negative")
    return datetime.fromtimestamp(normalized, tz=UTC).isoformat().replace("+00:00", "Z")
