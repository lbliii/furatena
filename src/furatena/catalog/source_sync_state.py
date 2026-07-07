"""Durable retry, quarantine, and last-known-good source-sync state."""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from furatena.catalog.audit_store import redact_audit_value


class SourceSyncStateStore:
    """Persist source-sync transitions with atomic per-mount state files."""

    def __init__(
        self,
        root: Path,
        *,
        max_failures: int = 3,
        base_backoff_seconds: float = 5.0,
        max_backoff_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.max_failures = max(int(max_failures), 1)
        self.base_backoff_seconds = _positive(base_backoff_seconds, "base_backoff_seconds")
        self.max_backoff_seconds = _positive(max_backoff_seconds, "max_backoff_seconds")
        self._clock = clock
        self._lock = threading.RLock()

    def load(self, mount: str) -> dict[str, Any] | None:
        with self._lock:
            return self._load_locked(mount)

    def can_attempt(self, mount: str, *, now: float | None = None) -> tuple[bool, str | None]:
        observed_at = _timestamp(self._clock() if now is None else now)
        with self._lock:
            state = self._load_locked(mount)
            if state is None:
                return True, None
            if bool((state.get("quarantine") or {}).get("active")):
                return False, "quarantined"
            next_retry = (state.get("retry") or {}).get("next_at_epoch")
            if next_retry is not None and observed_at < float(next_retry):
                return False, "backoff"
            return True, None

    def begin(
        self,
        mount: str,
        *,
        provider: str,
        source_repo: str | None,
        requested_ref: str | None,
        now: float | None = None,
    ) -> dict[str, Any]:
        observed_at = _timestamp(self._clock() if now is None else now)
        with self._lock:
            previous = self._load_locked(mount) or {}
            attempt_id = uuid.uuid4().hex
            state = {
                **_base_state(previous, mount, provider),
                "state": "attempt",
                "attempt": {
                    "id": attempt_id,
                    "started_at": _iso(observed_at),
                    "started_at_epoch": observed_at,
                    "source_repo": source_repo,
                    "requested_ref": requested_ref,
                },
                "updated_at": _iso(observed_at),
                "updated_at_epoch": observed_at,
                "repair_actions": [],
            }
            state["history"] = _history(
                previous,
                "attempt",
                observed_at,
                attempt_id=attempt_id,
            )
            self._write_locked(mount, state)
            return _copy(state)

    def record_failure(
        self,
        mount: str,
        error: Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        observed_at = _timestamp(self._clock() if now is None else now)
        with self._lock:
            previous = self._load_locked(mount) or {}
            failures = int(previous.get("consecutive_failures") or 0) + 1
            sanitized_error = redact_audit_value(dict(error))
            history = _history(
                previous,
                "error",
                observed_at,
                error=sanitized_error,
            )
            if failures >= self.max_failures:
                state_name = "quarantine"
                retry = {"attempt": failures, "next_at": None, "next_at_epoch": None}
                quarantine = {
                    "active": True,
                    "since": _iso(observed_at),
                    "since_epoch": observed_at,
                    "reason": "consecutive sync failures exceeded the quarantine threshold",
                }
                repair = [
                    "Repair source access or configuration, then clear quarantine and retry sync.",
                    "Continue serving the recorded last-known-good snapshot until reconciliation succeeds.",
                ]
            else:
                state_name = "retry"
                backoff = min(
                    self.base_backoff_seconds * (2 ** (failures - 1)),
                    self.max_backoff_seconds,
                )
                next_at = observed_at + backoff
                retry = {
                    "attempt": failures,
                    "backoff_seconds": backoff,
                    "next_at": _iso(next_at),
                    "next_at_epoch": next_at,
                }
                quarantine = {"active": False, "since": None, "reason": None}
                repair = [
                    f"Retry source sync after {retry['next_at']}.",
                    "Continue serving the recorded last-known-good snapshot while backoff is active.",
                ]
            state = {
                **_base_state(previous, mount, str(previous.get("provider") or "unknown")),
                "state": state_name,
                "consecutive_failures": failures,
                "last_error": sanitized_error,
                "retry": retry,
                "quarantine": quarantine,
                "updated_at": _iso(observed_at),
                "updated_at_epoch": observed_at,
                "repair_actions": repair,
                "history": [
                    *history,
                    {
                        "state": state_name,
                        "at": _iso(observed_at),
                        "at_epoch": observed_at,
                    },
                ][-100:],
            }
            self._write_locked(mount, state)
            return _copy(state)

    def reconcile(
        self,
        mount: str,
        *,
        provider: str,
        resolved_ref: str | None,
        content_root: Path,
        source_url: str | None,
        now: float | None = None,
    ) -> dict[str, Any]:
        observed_at = _timestamp(self._clock() if now is None else now)
        with self._lock:
            previous = self._load_locked(mount) or {}
            last_known_good = {
                "resolved_ref": resolved_ref,
                "content_root": str(content_root.resolve()),
                "source_url": source_url,
                "reconciled_at": _iso(observed_at),
                "reconciled_at_epoch": observed_at,
            }
            state = {
                **_base_state(previous, mount, provider),
                "state": "reconciled",
                "consecutive_failures": 0,
                "last_error": None,
                "retry": {"attempt": 0, "next_at": None, "next_at_epoch": None},
                "quarantine": {"active": False, "since": None, "reason": None},
                "last_known_good": last_known_good,
                "updated_at": _iso(observed_at),
                "updated_at_epoch": observed_at,
                "repair_actions": [],
                "history": _history(
                    previous,
                    "reconciled",
                    observed_at,
                    resolved_ref=resolved_ref,
                ),
            }
            self._write_locked(mount, state)
            return _copy(state)

    def clear_quarantine(self, mount: str, *, now: float | None = None) -> dict[str, Any]:
        observed_at = _timestamp(self._clock() if now is None else now)
        with self._lock:
            previous = self._load_locked(mount)
            if previous is None:
                raise KeyError(f"unknown source-sync mount: {mount}")
            state = {
                **previous,
                "state": "retry",
                "retry": {
                    "attempt": int(previous.get("consecutive_failures") or 0),
                    "backoff_seconds": 0.0,
                    "next_at": _iso(observed_at),
                    "next_at_epoch": observed_at,
                },
                "quarantine": {"active": False, "since": None, "reason": None},
                "updated_at": _iso(observed_at),
                "updated_at_epoch": observed_at,
                "repair_actions": ["Retry source sync and verify reconciliation."],
                "history": _history(previous, "retry", observed_at, cleared_quarantine=True),
            }
            self._write_locked(mount, state)
            return _copy(state)

    def _load_locked(self, mount: str) -> dict[str, Any] | None:
        path = self._path(mount)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid source-sync state: {path}") from exc
        if not isinstance(payload, dict) or payload.get("mount") != mount:
            raise ValueError(f"source-sync state identity mismatch: {path}")
        return payload

    def _write_locked(self, mount: str, payload: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(mount)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _path(self, mount: str) -> Path:
        normalized = "".join(char if char.isalnum() or char in "-_" else "-" for char in mount)
        if not normalized:
            raise ValueError("source-sync mount id is required")
        return self.root / f"{normalized}.json"


def _base_state(previous: Mapping[str, Any], mount: str, provider: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mount": mount,
        "provider": provider,
        "consecutive_failures": int(previous.get("consecutive_failures") or 0),
        "last_error": previous.get("last_error"),
        "retry": previous.get("retry") or {"attempt": 0, "next_at": None},
        "quarantine": previous.get("quarantine")
        or {"active": False, "since": None, "reason": None},
        "last_known_good": previous.get("last_known_good"),
        "history": list(previous.get("history") or []),
    }


def _history(
    previous: Mapping[str, Any],
    state: str,
    at: float,
    **details: Any,
) -> list[dict[str, Any]]:
    return [
        *list(previous.get("history") or []),
        {
            "state": state,
            "at": _iso(at),
            "at_epoch": at,
            **details,
        },
    ][-100:]


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))


def _positive(value: float, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"source-sync {label} must be finite and positive")
    return normalized


def _timestamp(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("source-sync timestamp must be finite and non-negative")
    return normalized


def _iso(value: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
