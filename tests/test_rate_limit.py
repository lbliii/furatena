"""Shared, restart-safe MCP abuse-control contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.rate_limit import (
    InMemoryRateLimitStore,
    RateLimitBackendError,
    RateLimitRequest,
    RateLimitRule,
    ResilientRateLimitStore,
    SQLiteRateLimitStore,
    mcp_rate_limit_rules,
)


def _request(
    actor: str = "agent-1",
    *,
    tenant: str = "acme",
    action: str = "semantic_search",
    sensitive: bool = False,
) -> RateLimitRequest:
    return RateLimitRequest(tenant, actor, action, sensitive)


def test_burst_and_sustained_actor_policies_are_independent() -> None:
    burst_store = InMemoryRateLimitStore()
    burst_rules = mcp_rate_limit_rules(
        burst=2,
        actor_per_minute=100,
        tenant_per_minute=100,
        sensitive_per_minute=100,
        sensitive=False,
    )

    assert burst_store.consume(_request(), burst_rules, now=100.1).allowed
    assert burst_store.consume(_request(), burst_rules, now=100.2).allowed
    burst_denied = burst_store.consume(_request(), burst_rules, now=100.3)
    assert burst_denied.allowed is False
    assert burst_denied.rule == "actor-burst"

    sustained_store = InMemoryRateLimitStore()
    sustained_rules = mcp_rate_limit_rules(
        burst=100,
        actor_per_minute=2,
        tenant_per_minute=100,
        sensitive_per_minute=100,
        sensitive=False,
    )
    assert sustained_store.consume(_request(), sustained_rules, now=120.1).allowed
    assert sustained_store.consume(_request(), sustained_rules, now=121.1).allowed
    sustained_denied = sustained_store.consume(_request(), sustained_rules, now=122.1)
    assert sustained_denied.allowed is False
    assert sustained_denied.rule == "actor-sustained"


def test_tenant_and_sensitive_tool_policies_aggregate_the_expected_calls() -> None:
    tenant_store = InMemoryRateLimitStore()
    tenant_rules = mcp_rate_limit_rules(
        burst=100,
        actor_per_minute=100,
        tenant_per_minute=2,
        sensitive_per_minute=100,
        sensitive=False,
    )
    assert tenant_store.consume(_request("agent-1"), tenant_rules, now=180.1).allowed
    assert tenant_store.consume(_request("agent-2"), tenant_rules, now=180.2).allowed
    tenant_denied = tenant_store.consume(_request("agent-3"), tenant_rules, now=180.3)
    assert tenant_denied.allowed is False
    assert tenant_denied.rule == "tenant-sustained"

    sensitive_store = InMemoryRateLimitStore()
    regular_rules = mcp_rate_limit_rules(
        burst=100,
        actor_per_minute=100,
        tenant_per_minute=100,
        sensitive_per_minute=1,
        sensitive=False,
    )
    sensitive_rules = mcp_rate_limit_rules(
        burst=100,
        actor_per_minute=100,
        tenant_per_minute=100,
        sensitive_per_minute=1,
        sensitive=True,
    )
    assert sensitive_store.consume(_request(), regular_rules, now=240.1).allowed
    assert sensitive_store.consume(
        _request(action="author_read_source", sensitive=True),
        sensitive_rules,
        now=240.2,
    ).allowed
    sensitive_denied = sensitive_store.consume(
        _request(action="author_apply_edit", sensitive=True),
        sensitive_rules,
        now=240.3,
    )
    assert sensitive_denied.allowed is False
    assert sensitive_denied.rule == "sensitive-tools"


def test_sqlite_limits_survive_restart_and_hash_identity(tmp_path: Path) -> None:
    path = tmp_path / "limits.sqlite3"
    rules = (RateLimitRule("restart", "actor", 2, 60.0),)
    request = _request(actor="private-actor@example.com")
    first_worker = SQLiteRateLimitStore(path)

    assert first_worker.consume(request, rules, now=300.1).allowed
    assert first_worker.consume(request, rules, now=300.2).allowed

    restarted_worker = SQLiteRateLimitStore(path)
    denied = restarted_worker.consume(request, rules, now=300.3)
    assert denied.allowed is False
    assert denied.rule == "restart"
    assert path.stat().st_mode & 0o777 == 0o600
    assert b"private-actor@example.com" not in path.read_bytes()


def test_sqlite_limits_are_atomic_across_free_threaded_workers(tmp_path: Path) -> None:
    assert_free_threading()
    path = tmp_path / "limits.sqlite3"
    stores = [SQLiteRateLimitStore(path) for _ in range(8)]
    rules = (RateLimitRule("workers", "tenant", 50, 60.0),)
    request = _request()

    def consume(index: int) -> bool:
        return stores[index % len(stores)].consume(request, rules, now=360.1).allowed

    with ThreadPoolExecutor(max_workers=64) as pool:
        decisions = list(pool.map(consume, range(100)))

    assert decisions.count(True) == 50
    assert decisions.count(False) == 50


class _UnavailableStore:
    def consume(self, request, rules, *, now=None):
        raise RateLimitBackendError("unavailable")

    def describe(self):
        return {"backend": "unavailable", "shared": True, "restart_safe": True}


def test_backend_failure_defaults_to_deny_with_explicit_memory_fallback() -> None:
    rules = (RateLimitRule("fallback", "actor", 1, 60.0),)
    fail_closed = ResilientRateLimitStore(_UnavailableStore(), fallback_mode="deny")
    available = ResilientRateLimitStore(_UnavailableStore(), fallback_mode="memory")

    denied = fail_closed.consume(_request(), rules, now=420.1)
    first = available.consume(_request(), rules, now=420.1)
    second = available.consume(_request(), rules, now=420.2)

    assert denied.allowed is False
    assert denied.rule == "backend-unavailable"
    assert denied.reason == "shared_backend_unavailable"
    assert first.allowed is True and first.backend == "memory-fallback"
    assert second.allowed is False and second.rule == "fallback"
    assert first.fallback is second.fallback is True


def test_sqlite_startup_failure_uses_the_configured_fallback(tmp_path: Path) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("blocked", encoding="utf-8")
    path = blocked_parent / "limits.sqlite3"
    rules = (RateLimitRule("startup", "actor", 1, 60.0),)

    fail_closed = ResilientRateLimitStore.from_sqlite(path, fallback_mode="deny")
    available = ResilientRateLimitStore.from_sqlite(path, fallback_mode="memory")

    assert fail_closed.describe()["available"] is False
    assert fail_closed.consume(_request(), rules, now=480.1).allowed is False
    assert available.consume(_request(), rules, now=480.1).allowed is True
