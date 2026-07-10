"""Provider-neutral durable audit storage with redaction and retention."""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, Protocol

_SECRET_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "privileged_token",
    "secret",
    "token",
)
_CONTENT_KEYS = frozenset(
    {
        "body",
        "content",
        "diff",
        "new_text",
        "old_text",
        "patch",
        "prompt",
        "query",
        "source",
        "source_text",
    }
)


class AuditStore(Protocol):
    """Persistence boundary for security-relevant audit events."""

    retention_days: int

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]: ...

    def query(
        self,
        *,
        tenant: str | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    def purge(self, *, now: float | None = None) -> int: ...

    def export(self) -> dict[str, Any]: ...

    def write_export(self, path: Path) -> None: ...


class InMemoryAuditStore:
    """Thread-safe test and local-session audit store."""

    def __init__(
        self,
        *,
        retention_days: int = 90,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.retention_days = _retention_days(retention_days)
        self._clock = clock
        self._events: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_audit_event(event, clock=self._clock)
        with self._lock:
            self._purge_locked(self._clock())
            self._events.append(deepcopy(normalized))
        return deepcopy(normalized)

    def query(
        self,
        *,
        tenant: str | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._purge_locked(self._clock())
            return _filter_events(self._events, tenant=tenant, since=since, limit=limit)

    def purge(self, *, now: float | None = None) -> int:
        with self._lock:
            return self._purge_locked(self._clock() if now is None else now)

    def export(self) -> dict[str, Any]:
        events = self.query()
        return _export_payload("memory", self.retention_days, events)

    def write_export(self, path: Path) -> None:
        _write_json(path, self.export())

    def _purge_locked(self, now: float) -> int:
        cutoff = now - self.retention_days * 86_400
        retained = [event for event in self._events if float(event["timestamp"]) >= cutoff]
        removed = len(self._events) - len(retained)
        self._events = retained
        return removed


class JsonLinesAuditStore:
    """Restart-safe append-only JSONL audit store with atomic retention rewrites."""

    def __init__(
        self,
        path: Path,
        *,
        retention_days: int = 90,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path.expanduser().resolve()
        self.retention_days = _retention_days(retention_days)
        self._clock = clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_audit_event(event, clock=self._clock)
        payload = (json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        with self._lock:
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(descriptor, remaining)
                    if written <= 0:
                        raise OSError("audit JSONL write made no progress")
                    remaining = remaining[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return dict(normalized)

    def query(
        self,
        *,
        tenant: str | None = None,
        since: float | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._purge_locked(self._clock())
            return _filter_events(
                self._read_locked(),
                tenant=tenant,
                since=since,
                limit=limit,
            )

    def purge(self, *, now: float | None = None) -> int:
        with self._lock:
            return self._purge_locked(self._clock() if now is None else now)

    def export(self) -> dict[str, Any]:
        events = self.query()
        return _export_payload("jsonl", self.retention_days, events, path=self.path)

    def write_export(self, path: Path) -> None:
        _write_json(path, self.export())

    def _read_locked(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid audit JSONL record at {self.path}:{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(event, dict):
                raise ValueError(f"audit JSONL record must be an object: {self.path}:{line_number}")
            _validate_persisted_event(event, path=self.path, line_number=line_number)
            events.append(event)
        return events

    def _purge_locked(self, now: float) -> int:
        events = self._read_locked()
        cutoff = now - self.retention_days * 86_400
        retained = [event for event in events if float(event["timestamp"]) >= cutoff]
        removed = len(events) - len(retained)
        if removed:
            _write_jsonl_atomic(self.path, retained)
        return removed


def normalize_audit_event(
    event: Mapping[str, Any],
    *,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Validate required identity fields and redact before persistence."""
    sanitized = redact_audit_value(dict(event))
    timestamp = _finite_timestamp(sanitized.get("timestamp"), default=clock())
    event_id = _identifier(sanitized.get("event_id")) or uuid.uuid4().hex
    correlation_id = (
        _identifier(sanitized.get("correlation_id"))
        or _identifier(sanitized.get("operation_id"))
        or event_id
    )
    actor = _required_text(sanitized.get("actor"), "actor")
    action = _required_text(sanitized.get("action") or sanitized.get("tool"), "action")
    outcome = _required_text(
        sanitized.get("outcome") or sanitized.get("status"),
        "outcome",
    )
    normalized = {
        "schema_version": 1,
        "event_id": event_id,
        "timestamp": round(timestamp, 6),
        "correlation_id": correlation_id,
        "actor": actor,
        "tenant": _optional_text(sanitized.get("tenant")),
        "site": _optional_text(sanitized.get("site")),
        "action": action,
        "target": _optional_text(sanitized.get("target")),
        "outcome": outcome,
    }
    reserved = set(normalized)
    normalized.update(
        (str(key), value) for key, value in sanitized.items() if str(key) not in reserved
    )
    return normalized


def redact_audit_value(value: Any) -> Any:
    """Recursively remove secrets and authored/query content before storage."""
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            lowered = text_key.lower()
            if lowered in _CONTENT_KEYS:
                redacted[text_key] = "<redacted:content>"
            elif any(part in lowered for part in _SECRET_KEY_PARTS):
                redacted[text_key] = "<redacted>"
            else:
                redacted[text_key] = redact_audit_value(item)
        return redacted
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact_audit_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _filter_events(
    events: list[dict[str, Any]],
    *,
    tenant: str | None,
    since: float | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    selected = [
        event
        for event in events
        if (tenant is None or event.get("tenant") == tenant)
        and (since is None or float(event["timestamp"]) >= since)
    ]
    if limit is not None:
        normalized_limit = max(int(limit), 0)
        selected = selected[-normalized_limit:] if normalized_limit else []
    return deepcopy(selected)


def _export_payload(
    backend: str,
    retention_days: int,
    events: list[dict[str, Any]],
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "backend": backend,
        "retention_days": retention_days,
        "path": str(path) if path is not None else None,
        "count": len(events),
        "entries": events,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_jsonl_atomic(path: Path, events: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            "".join(
                json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events
            ),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_persisted_event(event: dict[str, Any], *, path: Path, line_number: int) -> None:
    required = {
        "event_id",
        "timestamp",
        "correlation_id",
        "actor",
        "action",
        "outcome",
    }
    missing = sorted(required - set(event))
    if missing:
        raise ValueError(f"audit JSONL record is missing {missing}: {path}:{line_number}")
    _finite_timestamp(event["timestamp"], default=0.0)


def _retention_days(value: int) -> int:
    normalized = int(value)
    if normalized < 1:
        raise ValueError("audit retention_days must be at least 1")
    return normalized


def _finite_timestamp(value: Any, *, default: float) -> float:
    normalized = default if value is None else float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("audit timestamp must be a finite non-negative number")
    return normalized


def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"audit {label} is required")
    return normalized


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _identifier(value: Any) -> str | None:
    normalized = _optional_text(value)
    if normalized is None:
        return None
    if len(normalized) > 200:
        raise ValueError("audit identifiers must be at most 200 characters")
    return normalized
