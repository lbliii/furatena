"""Restart-safe shared rate limits for MCP abuse controls."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

RateLimitScope = Literal["actor", "tenant"]
FallbackMode = Literal["deny", "memory"]


class RateLimitBackendError(RuntimeError):
    """Raised when a shared counter backend cannot make an atomic decision."""


@dataclass(frozen=True, slots=True)
class RateLimitRequest:
    """Stable identity and action used to select rate-limit buckets."""

    tenant: str
    actor: str
    action: str
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """One fixed-window abuse-control policy."""

    name: str
    scope: RateLimitScope
    limit: int
    window_seconds: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("rate-limit rule name is required")
        if self.scope not in {"actor", "tenant"}:
            raise ValueError(f"unknown rate-limit scope: {self.scope}")
        if self.limit < 1:
            raise ValueError("rate-limit rule limit must be at least 1")
        if not math.isfinite(self.window_seconds) or self.window_seconds <= 0:
            raise ValueError("rate-limit window_seconds must be finite and positive")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Atomic decision returned by every rate-limit backend."""

    allowed: bool
    backend: str
    rule: str | None = None
    retry_after_seconds: float = 0.0
    fallback: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "backend": self.backend,
            "rule": self.rule,
            "retry_after_seconds": self.retry_after_seconds,
            "fallback": self.fallback,
            "reason": self.reason,
        }


class RateLimitStore(Protocol):
    """Atomic storage boundary shared by MCP workers."""

    def consume(
        self,
        request: RateLimitRequest,
        rules: Sequence[RateLimitRule],
        *,
        now: float | None = None,
    ) -> RateLimitDecision: ...

    def describe(self) -> dict[str, Any]: ...


class InMemoryRateLimitStore:
    """Free-threading-safe process-local backend for tests and local sessions."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._counters: dict[tuple[str, str, int], tuple[int, float]] = {}
        self._lock = threading.RLock()

    def consume(
        self,
        request: RateLimitRequest,
        rules: Sequence[RateLimitRule],
        *,
        now: float | None = None,
    ) -> RateLimitDecision:
        observed_at = _timestamp(self._clock() if now is None else now)
        with self._lock:
            self._counters = {
                key: value for key, value in self._counters.items() if value[1] > observed_at
            }
            violations: list[tuple[RateLimitRule, float]] = []
            for rule in rules:
                bucket = math.floor(observed_at / rule.window_seconds)
                expires_at = (bucket + 1) * rule.window_seconds
                key = (rule.name, _identity_key(rule.scope, request), bucket)
                count = self._counters.get(key, (0, expires_at))[0] + 1
                self._counters[key] = (count, expires_at)
                if count > rule.limit:
                    violations.append((rule, expires_at))
        return _decision("memory", observed_at, violations)

    def describe(self) -> dict[str, Any]:
        return {"backend": "memory", "shared": False, "restart_safe": False}


class SQLiteRateLimitStore:
    """Shared fixed-window counters serialized by SQLite transactions."""

    def __init__(self, path: Path, *, timeout_seconds: float = 5.0) -> None:
        self.path = path.expanduser().resolve()
        self.timeout_seconds = float(timeout_seconds)
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("rate-limit SQLite timeout_seconds must be positive")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rate_limit_counters (
                        rule_name TEXT NOT NULL,
                        identity_key TEXT NOT NULL,
                        bucket INTEGER NOT NULL,
                        count INTEGER NOT NULL,
                        expires_at REAL NOT NULL,
                        PRIMARY KEY (rule_name, identity_key, bucket)
                    )
                    """
                )
            self.path.chmod(0o600)
        except (OSError, sqlite3.Error) as exc:
            raise RateLimitBackendError(f"cannot initialize rate-limit store: {exc}") from exc

    def consume(
        self,
        request: RateLimitRequest,
        rules: Sequence[RateLimitRule],
        *,
        now: float | None = None,
    ) -> RateLimitDecision:
        observed_at = _timestamp(time.time() if now is None else now)
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM rate_limit_counters WHERE expires_at <= ?",
                (observed_at,),
            )
            violations: list[tuple[RateLimitRule, float]] = []
            for rule in rules:
                bucket = math.floor(observed_at / rule.window_seconds)
                expires_at = (bucket + 1) * rule.window_seconds
                identity = _identity_key(rule.scope, request)
                connection.execute(
                    """
                    INSERT INTO rate_limit_counters (
                        rule_name, identity_key, bucket, count, expires_at
                    ) VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(rule_name, identity_key, bucket)
                    DO UPDATE SET count = count + 1, expires_at = excluded.expires_at
                    """,
                    (rule.name, identity, bucket, expires_at),
                )
                row = connection.execute(
                    """
                    SELECT count FROM rate_limit_counters
                    WHERE rule_name = ? AND identity_key = ? AND bucket = ?
                    """,
                    (rule.name, identity, bucket),
                ).fetchone()
                if row is None:
                    raise RateLimitBackendError("rate-limit counter update was not observable")
                if int(row[0]) > rule.limit:
                    violations.append((rule, expires_at))
            connection.commit()
            return _decision("sqlite", observed_at, violations)
        except RateLimitBackendError:
            if connection is not None:
                connection.rollback()
            raise
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.rollback()
            raise RateLimitBackendError(f"shared rate-limit decision failed: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def describe(self) -> dict[str, Any]:
        return {
            "backend": "sqlite",
            "shared": True,
            "restart_safe": True,
            "available": True,
            "path": str(self.path),
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout_seconds,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {max(round(self.timeout_seconds * 1000), 1)}")
        return connection


class ResilientRateLimitStore:
    """Apply an explicit fail-closed or process-local backend failure policy."""

    def __init__(
        self,
        primary: RateLimitStore,
        *,
        fallback_mode: FallbackMode = "deny",
        fallback: RateLimitStore | None = None,
    ) -> None:
        if fallback_mode not in {"deny", "memory"}:
            raise ValueError(f"unknown rate-limit fallback mode: {fallback_mode}")
        self.primary = primary
        self.fallback_mode = fallback_mode
        self.fallback = fallback or InMemoryRateLimitStore()

    @classmethod
    def from_sqlite(
        cls,
        path: Path,
        *,
        fallback_mode: FallbackMode = "deny",
    ) -> ResilientRateLimitStore:
        """Construct a shared store while applying fallback to startup failures too."""
        try:
            primary: RateLimitStore = SQLiteRateLimitStore(path)
        except RateLimitBackendError as exc:
            primary = _UnavailableSharedRateLimitStore(path, reason=str(exc))
        return cls(primary, fallback_mode=fallback_mode)

    def consume(
        self,
        request: RateLimitRequest,
        rules: Sequence[RateLimitRule],
        *,
        now: float | None = None,
    ) -> RateLimitDecision:
        try:
            return self.primary.consume(request, rules, now=now)
        except RateLimitBackendError:
            if self.fallback_mode == "memory":
                decision = self.fallback.consume(request, rules, now=now)
                return RateLimitDecision(
                    allowed=decision.allowed,
                    backend=f"{decision.backend}-fallback",
                    rule=decision.rule,
                    retry_after_seconds=decision.retry_after_seconds,
                    fallback=True,
                    reason="shared_backend_unavailable",
                )
            return RateLimitDecision(
                allowed=False,
                backend="unavailable",
                rule="backend-unavailable",
                retry_after_seconds=1.0,
                fallback=True,
                reason="shared_backend_unavailable",
            )

    def describe(self) -> dict[str, Any]:
        return {
            **self.primary.describe(),
            "fallback_mode": self.fallback_mode,
        }


class _UnavailableSharedRateLimitStore:
    def __init__(self, path: Path, *, reason: str) -> None:
        self.path = path.expanduser().resolve()
        self.reason = reason

    def consume(
        self,
        request: RateLimitRequest,
        rules: Sequence[RateLimitRule],
        *,
        now: float | None = None,
    ) -> RateLimitDecision:
        raise RateLimitBackendError(self.reason)

    def describe(self) -> dict[str, Any]:
        return {
            "backend": "sqlite",
            "shared": True,
            "restart_safe": True,
            "available": False,
            "path": str(self.path),
        }


def mcp_rate_limit_rules(
    *,
    burst: int,
    actor_per_minute: int,
    tenant_per_minute: int,
    sensitive_per_minute: int,
    sensitive: bool,
) -> tuple[RateLimitRule, ...]:
    """Build the ordered burst, sustained, tenant, and sensitive-tool policy."""
    rules = [
        RateLimitRule("actor-burst", "actor", max(int(burst), 1), 1.0),
        RateLimitRule("actor-sustained", "actor", max(int(actor_per_minute), 1), 60.0),
        RateLimitRule("tenant-sustained", "tenant", max(int(tenant_per_minute), 1), 60.0),
    ]
    if sensitive:
        rules.append(
            RateLimitRule(
                "sensitive-tools",
                "actor",
                max(int(sensitive_per_minute), 1),
                60.0,
            )
        )
    return tuple(rules)


def _decision(
    backend: str,
    observed_at: float,
    violations: list[tuple[RateLimitRule, float]],
) -> RateLimitDecision:
    if not violations:
        return RateLimitDecision(allowed=True, backend=backend)
    rule, expires_at = max(violations, key=lambda item: item[1] - observed_at)
    return RateLimitDecision(
        allowed=False,
        backend=backend,
        rule=rule.name,
        retry_after_seconds=round(max(expires_at - observed_at, 0.001), 3),
        reason="limit_exceeded",
    )


def _identity_key(scope: RateLimitScope, request: RateLimitRequest) -> str:
    tenant = request.tenant.strip() or "default"
    actor = request.actor.strip() or "anonymous"
    identity = tenant if scope == "tenant" else f"{tenant}\x00{actor}"
    return hashlib.sha256(f"{scope}\x00{identity}".encode()).hexdigest()


def _timestamp(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("rate-limit timestamp must be finite and non-negative")
    return normalized
