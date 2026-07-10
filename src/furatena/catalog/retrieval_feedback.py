"""Opt-in, privacy-safe feedback events for retrieval surfaces."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "password",
        "privileged_token",
        "secret",
        "token",
    }
)


class RetrievalFeedbackKind(StrEnum):
    """Supported feedback event families."""

    QUERY = "query"
    ZERO_RESULT = "zero_result"
    SELECTION = "selection"
    TOOL_OUTCOME = "tool_outcome"


@dataclass(frozen=True, slots=True)
class RetrievalFeedbackPolicy:
    """Collection, redaction, sampling, retention, and tenant policy."""

    enabled: bool = False
    query_mode: str = "digest"
    sample_rate: float = 1.0
    retention_days: int = 30
    digest_salt: str = ""

    def __post_init__(self) -> None:
        if self.query_mode not in {"digest", "drop", "raw"}:
            raise ValueError("query_mode must be digest, drop, or raw")
        if not 0.0 <= self.sample_rate <= 1.0:
            raise ValueError("sample_rate must be between 0 and 1")
        if self.retention_days < 1:
            raise ValueError("retention_days must be positive")


@dataclass(frozen=True, slots=True)
class RetrievalFeedbackEvent:
    """One sanitized feedback event scoped to a single tenant."""

    event_id: str
    kind: RetrievalFeedbackKind
    timestamp: str
    tenant: str
    surface: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "kind": self.kind.value,
            "timestamp": self.timestamp,
            "tenant": self.tenant,
            "surface": self.surface,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RetrievalFeedbackEvent:
        return cls(
            event_id=str(raw["event_id"]),
            kind=RetrievalFeedbackKind(str(raw["kind"])),
            timestamp=str(raw["timestamp"]),
            tenant=str(raw["tenant"]),
            surface=str(raw["surface"]),
            data=dict(raw.get("data") or {}),
        )


class RetrievalFeedbackSink(Protocol):
    """Pluggable destination for sanitized retrieval events."""

    def emit(self, event: RetrievalFeedbackEvent) -> None: ...


class NullRetrievalFeedbackSink:
    """No-op sink used by the disabled default."""

    def emit(self, event: RetrievalFeedbackEvent) -> None:
        del event


class MemoryRetrievalFeedbackSink:
    """Thread-safe test sink with tenant-isolated reads."""

    def __init__(self) -> None:
        self._events: dict[str, list[RetrievalFeedbackEvent]] = {}
        self._lock = threading.RLock()

    def emit(self, event: RetrievalFeedbackEvent) -> None:
        with self._lock:
            self._events.setdefault(event.tenant, []).append(event)

    def events(self, tenant: str) -> tuple[RetrievalFeedbackEvent, ...]:
        with self._lock:
            return tuple(self._events.get(_tenant(tenant), ()))


class JsonlRetrievalFeedbackSink:
    """Thread-safe local JSONL sink with per-tenant files and retention pruning."""

    def __init__(
        self,
        root: Path,
        *,
        retention_days: int = 30,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self.root = root
        self.retention_days = retention_days
        self._now = now or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()

    def emit(self, event: RetrievalFeedbackEvent) -> None:
        path = self._path(event.tenant)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            retained = self._read(path, tenant=event.tenant)
            retained.append(event)
            self._write(path, retained)

    def events(self, tenant: str) -> tuple[RetrievalFeedbackEvent, ...]:
        normalized = _tenant(tenant)
        path = self._path(normalized)
        with self._lock:
            retained = self._read(path, tenant=normalized)
            if path.exists():
                self._write(path, retained)
            return tuple(retained)

    def _path(self, tenant: str) -> Path:
        normalized = _tenant(tenant)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        return self.root / f"tenant-{digest}.jsonl"

    def _read(self, path: Path, *, tenant: str) -> list[RetrievalFeedbackEvent]:
        if not path.exists():
            return []
        cutoff = self._now() - timedelta(days=self.retention_days)
        retained: list[RetrievalFeedbackEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = RetrievalFeedbackEvent.from_dict(json.loads(line))
            if event.tenant != tenant:
                continue
            if _parse_timestamp(event.timestamp) >= cutoff:
                retained.append(event)
        return retained

    @staticmethod
    def _write(path: Path, events: list[RetrievalFeedbackEvent]) -> None:
        payload = "".join(
            json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
        path.chmod(0o600)


class RetrievalFeedbackCollector:
    """Sanitize and emit optional query, selection, and tool feedback."""

    def __init__(
        self,
        *,
        policy: RetrievalFeedbackPolicy | None = None,
        sink: RetrievalFeedbackSink | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.policy = policy or RetrievalFeedbackPolicy()
        self.sink = sink or NullRetrievalFeedbackSink()
        self._now = now or (lambda: datetime.now(UTC))
        if isinstance(self.sink, JsonlRetrievalFeedbackSink):
            self.sink.retention_days = self.policy.retention_days

    @classmethod
    def disabled(cls) -> RetrievalFeedbackCollector:
        return cls()

    def record_query(
        self,
        query: str,
        *,
        tenant: str,
        surface: str,
        result_count: int,
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[RetrievalFeedbackEvent, ...]:
        if not self.policy.enabled:
            return ()
        data = {
            **self._query_data(query, tenant=tenant),
            "result_count": max(int(result_count), 0),
            "metadata": _sanitize(metadata or {}),
        }
        events: list[RetrievalFeedbackEvent] = []
        event = self._emit(
            RetrievalFeedbackKind.QUERY,
            tenant=tenant,
            surface=surface,
            sample_key=query,
            data=data,
        )
        if event is not None:
            events.append(event)
        if result_count == 0:
            zero = self._emit(
                RetrievalFeedbackKind.ZERO_RESULT,
                tenant=tenant,
                surface=surface,
                sample_key=query,
                data=data,
            )
            if zero is not None:
                events.append(zero)
        return tuple(events)

    def record_selection(
        self,
        *,
        tenant: str,
        surface: str,
        node_id: str,
        chunk_id: str | None = None,
        rank: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RetrievalFeedbackEvent | None:
        if not self.policy.enabled:
            return None
        return self._emit(
            RetrievalFeedbackKind.SELECTION,
            tenant=tenant,
            surface=surface,
            sample_key=f"{node_id}:{chunk_id or ''}",
            data={
                "node_id": node_id,
                "chunk_id": chunk_id,
                "rank": rank,
                "metadata": _sanitize(metadata or {}),
            },
        )

    def record_tool_outcome(
        self,
        *,
        tenant: str,
        surface: str,
        tool: str,
        status: str,
        duration_ms: float | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RetrievalFeedbackEvent | None:
        if not self.policy.enabled:
            return None
        return self._emit(
            RetrievalFeedbackKind.TOOL_OUTCOME,
            tenant=tenant,
            surface=surface,
            sample_key=f"{tool}:{status}",
            data={
                "tool": tool,
                "status": status,
                "duration_ms": duration_ms,
                "metadata": _sanitize(metadata or {}),
            },
        )

    def _query_data(self, query: str, *, tenant: str) -> dict[str, Any]:
        normalized = query.strip()
        data: dict[str, Any] = {"query_length": len(normalized)}
        if self.policy.query_mode == "raw":
            data["query"] = normalized
        elif self.policy.query_mode == "digest":
            value = f"{self.policy.digest_salt}\0{tenant.strip()}\0{normalized}"
            data["query_sha256"] = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return data

    def _emit(
        self,
        kind: RetrievalFeedbackKind,
        *,
        tenant: str,
        surface: str,
        sample_key: str,
        data: dict[str, Any],
    ) -> RetrievalFeedbackEvent | None:
        if not self.policy.enabled:
            return None
        normalized_tenant = _tenant(tenant)
        if not _sampled(
            self.policy.sample_rate,
            f"{normalized_tenant}:{surface}:{kind.value}:{sample_key}",
        ):
            return None
        now = self._now().astimezone(UTC)
        timestamp = now.isoformat().replace("+00:00", "Z")
        identity = f"{normalized_tenant}:{surface}:{kind.value}:{sample_key}:{time.time_ns()}"
        event = RetrievalFeedbackEvent(
            event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
            kind=kind,
            timestamp=timestamp,
            tenant=normalized_tenant,
            surface=surface.strip() or "unknown",
            data=data,
        )
        self.sink.emit(event)
        return event


def _tenant(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("feedback events require a tenant")
    return normalized


def _sampled(rate: float, key: str) -> bool:
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    bucket = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")
    return bucket / float(2**64) < rate


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if str(key).lower() in _SENSITIVE_KEYS else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
