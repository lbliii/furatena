"""Durable managed-content refresh operation contracts."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any, cast

import pytest
from chirp.testing import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.build_identity import _content_identity
from furatena.catalog.content_deployment import (
    ContentDeploymentConfig,
    ContentDeploymentConflict,
    ContentDeploymentStore,
)
from furatena.catalog.content_refresh import (
    ContentRefreshActor,
    ContentRefreshConflict,
    ContentRefreshReceipt,
    ContentRefreshRequest,
    ContentRefreshService,
    ContentRefreshState,
    authenticate_content_actor,
)
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.operation_lease import OperationLease
from furatena.catalog.runtime import ServeConfig, ServeMode
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

SCHEMAS = Path("src/furatena/catalog/schemas/content-refresh/v1")
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
COMMIT_C = "c" * 40


@dataclass
class _Config:
    repository: str = "https://github.com/example/docs.git"
    ref: str = "main"
    subdirectory: str = "app"


class _SubmissionExitFailure:
    def __init__(self, lease: OperationLease) -> None:
        self.lease = lease

    def __enter__(self) -> OperationLease:
        return self.lease.acquire()

    def __exit__(self, *_args: object) -> None:
        self.lease.release()
        raise RuntimeError("injected submission release failure")


class _Store:
    def __init__(self, root: Path, *, active_commit: str | None = None) -> None:
        self.root = root
        self.leases = root / "leases"
        self.config = _Config()
        self.active_commit = active_commit
        self.active_generation = "generation-a" if active_commit else None
        self.image_digest = f"sha256:{'c' * 64}"
        self.build_commit = "d" * 40
        self.refresh_calls = 0
        self.unreachable = False

    def status(self) -> dict[str, Any]:
        receipt = None
        if self.active_commit is not None:
            receipt = {
                "resolved_ref": self.active_commit,
                "image_digest": self.image_digest,
                "build_commit": self.build_commit,
            }
        return {
            "active_generation": self.active_generation,
            "receipt": receipt,
        }

    def _status_owned(self) -> dict[str, Any]:
        return self.status()

    def _content_refresh_lease(self) -> OperationLease:
        return OperationLease(
            self.leases,
            "content-refresh",
            resource=self.config.repository,
            timeout_seconds=5,
        )

    def resolve_requested_commit(self, requested_commit: str) -> str:
        if self.unreachable:
            raise ContentDeploymentConflict(
                "unreachable_commit",
                "The exact requested commit is not reachable under the configured ref policy.",
            )
        return requested_commit

    def refresh(self, **values: Any) -> dict[str, Any]:
        self.refresh_calls += 1
        completion = values.pop("completion")
        assert values.pop("_lease_owned") is True
        expected = values.get("expected_active_commit")
        if expected != self.active_commit:
            raise ContentDeploymentConflict("stale_active_commit", "stale active commit")
        requested = cast(str, values.get("requested_commit") or COMMIT_B)
        self.active_commit = requested
        self.active_generation = "generation-b"
        result = {
            "operation": "refresh",
            "resolved_ref": requested,
            "generation": self.active_generation,
        }
        completion(result)
        return result


def _actor() -> ContentRefreshActor:
    return ContentRefreshActor("operator", "test-credential", "test")


def _request(
    *, expected: str | None = None, requested: str = COMMIT_B, key: str = "refresh-key-0001"
) -> ContentRefreshRequest:
    return ContentRefreshRequest(expected, requested, key)


def _service(store: _Store, **values: Any) -> ContentRefreshService:
    return ContentRefreshService(cast(Any, store), **values)


def _managed_store(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ContentDeploymentStore:
    config = ContentDeploymentConfig(
        repository="https://github.com/example/docs.git",
        ref="main",
        subdirectory="app",
        allowed_hosts=frozenset({"github.com"}),
        state_root=root,
        max_bytes=10_000,
        max_files=100,
    )

    def freezer(app_root: Path, output: Path) -> dict[str, Any]:
        assert (app_root / "docs.yaml").is_file()
        output.mkdir(parents=True)
        for name, content in {
            "catalog.json": '{"pages": [{"id": "index"}]}',
            "search.json": "[]",
            "semantic.json": "[]",
            "llms-full.txt": "# Docs\n",
        }.items():
            (output / name).write_text(content, encoding="utf-8")
        return {"page_count": 1, "frozen_root": output}

    store = ContentDeploymentStore(config, freezer=freezer, clock=lambda: 1_700_000_000)

    def checkout(target: Path, *, requested_commit: str | None = None) -> str:
        commit = requested_commit or COMMIT_A
        app = target / "app"
        app.mkdir(parents=True)
        (app / "docs.yaml").write_text("site:\n  title: Docs\n", encoding="utf-8")
        (app / "index.md").write_text(f"# {commit[:1]}\n", encoding="utf-8")
        return commit

    monkeypatch.setattr(store, "_checkout", checkout)
    return store


def test_request_is_versioned_exact_and_cannot_override_configured_scope() -> None:
    request = _request()
    assert ContentRefreshRequest.from_dict(request.to_dict()) == request

    for field in ("actor", "repository", "ref", "subdirectory"):
        with pytest.raises(ValueError, match="unsupported fields"):
            ContentRefreshRequest.from_dict({**request.to_dict(), field: "override"})
    with pytest.raises(ValueError, match="40-character"):
        ContentRefreshRequest(None, "main", "refresh-key-0001")
    with pytest.raises(ValueError, match="unsupported schema version"):
        ContentRefreshRequest.from_dict({**request.to_dict(), "schema_version": True})


def test_submit_persists_transitions_replays_and_detects_key_collision(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    service = _service(store, restart_scheduler=lambda: True)

    receipt = service.submit(_request(), actor=_actor(), asynchronous=False)

    assert receipt.state == ContentRefreshState.RESTART_SCHEDULED
    assert receipt.restart_required and receipt.restart_scheduled
    assert store.refresh_calls == 1
    assert [event.state for event in receipt.history] == [
        ContentRefreshState.QUEUED,
        ContentRefreshState.STAGING,
        ContentRefreshState.PROMOTED,
        ContentRefreshState.ACTIVATION_PENDING_RESTART,
        ContentRefreshState.RESTART_SCHEDULED,
    ]
    assert service.get(receipt.operation_id) == receipt
    assert "refresh-key-0001" not in json.dumps(receipt.to_dict())

    replay = service.submit(_request(), actor=_actor(), asynchronous=False)
    assert replay.operation_id == receipt.operation_id
    assert replay.replayed
    assert store.refresh_calls == 1

    with pytest.raises(ContentRefreshConflict) as collision:
        service.submit(
            _request(requested=COMMIT_A),
            actor=_actor(),
            asynchronous=False,
        )
    assert collision.value.code == "idempotency_key_collision"

    with pytest.raises(ContentRefreshConflict) as pending:
        service.submit(
            _request(expected=COMMIT_B, requested=COMMIT_A, key="refresh-key-0007"),
            actor=_actor(),
            asynchronous=False,
        )
    assert pending.value.code == "refresh_in_progress"


@pytest.mark.parametrize("submission_kind", ["exact", "compatibility"])
def test_submission_exit_failure_releases_transferred_refresh_lease_without_starting_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    submission_kind: str,
) -> None:
    assert_free_threading()
    store = _Store(tmp_path)
    service = _service(store)
    original_submission_lease = service._submission_lease
    original_refresh_lease = store._content_refresh_lease
    transferred: list[OperationLease] = []
    worker_started = threading.Event()

    monkeypatch.setattr(
        service,
        "_submission_lease",
        lambda: _SubmissionExitFailure(original_submission_lease()),
    )

    def capture_refresh_lease() -> OperationLease:
        lease = original_refresh_lease()
        release = lease.release

        def noisy_release() -> None:
            release()
            raise OSError("injected transferred lease cleanup failure")

        monkeypatch.setattr(lease, "release", noisy_release)
        transferred.append(lease)
        return lease

    monkeypatch.setattr(store, "_content_refresh_lease", capture_refresh_lease)
    monkeypatch.setattr(service, "_start", lambda _target: worker_started.set())

    with pytest.raises(RuntimeError, match="injected submission release failure") as failure:
        if submission_kind == "exact":
            service.submit(_request(), actor=_actor(), asynchronous=True)
        else:
            service.submit_compatibility(actor=_actor(), asynchronous=True)

    assert str(failure.value) == "injected submission release failure"
    assert failure.value.__notes__ == [
        "The provisional content-refresh lease also failed to release cleanly (OSError)."
    ]
    assert len(transferred) == 1
    lease = transferred[0]
    assert not worker_started.is_set()
    assert not lease._acquired
    assert lease._stop.is_set()
    assert lease._heartbeat is not None
    assert not lease._heartbeat.is_alive()
    assert lease.owner() == {}
    assert not lease.path.exists()

    failed = service.latest()
    assert failed is not None
    assert failed.state == ContentRefreshState.FAILED
    assert failed.failure == {
        "code": "submission_failed",
        "message": "The content refresh could not start; retry with a new idempotency key.",
    }
    assert [event.state for event in failed.history] == [
        ContentRefreshState.QUEUED,
        ContentRefreshState.FAILED,
    ]
    assert "injected submission release failure" not in json.dumps(failed.to_dict())
    assert not service._pending_receipts()

    monkeypatch.setattr(service, "_submission_lease", original_submission_lease)
    monkeypatch.setattr(store, "_content_refresh_lease", original_refresh_lease)
    if submission_kind == "exact":
        replay = service.submit(_request(), actor=_actor(), asynchronous=False)
        assert replay.operation_id == failed.operation_id
        assert replay.state == ContentRefreshState.FAILED
        assert replay.replayed
        recovered = service.submit(
            _request(key="refresh-key-after-exit-failure"),
            actor=_actor(),
            asynchronous=False,
        )
    else:
        recovered = service.submit_compatibility(actor=_actor(), asynchronous=False)
    assert recovered.state == ContentRefreshState.ACTIVATION_PENDING_RESTART
    assert store.refresh_calls == 1


@pytest.mark.parametrize("submission_kind", ["exact", "compatibility"])
def test_worker_start_failure_terminalizes_queue_and_allows_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    submission_kind: str,
) -> None:
    assert_free_threading()
    store = _Store(tmp_path)
    service = _service(store)
    original_refresh_lease = store._content_refresh_lease
    transferred: list[OperationLease] = []

    def capture_refresh_lease() -> OperationLease:
        lease = original_refresh_lease()
        transferred.append(lease)
        return lease

    monkeypatch.setattr(store, "_content_refresh_lease", capture_refresh_lease)
    original_thread_start = threading.Thread.start

    def fail_refresh_worker_start(thread: threading.Thread) -> None:
        if thread.name == "furatena-content-refresh":
            raise RuntimeError("injected worker start secret")
        original_thread_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_refresh_worker_start)

    with pytest.raises(RuntimeError, match="injected worker start secret") as failure:
        if submission_kind == "exact":
            service.submit(_request(), actor=_actor(), asynchronous=True)
        else:
            service.submit_compatibility(actor=_actor(), asynchronous=True)

    assert str(failure.value) == "injected worker start secret"
    assert not getattr(failure.value, "__notes__", ())
    assert len(transferred) == 1
    lease = transferred[0]
    assert not lease._acquired
    assert lease._stop.is_set()
    assert lease._heartbeat is not None
    assert not lease._heartbeat.is_alive()
    assert lease.owner() == {}
    assert not lease.path.exists()
    assert service._threads == set()
    assert store.refresh_calls == 0

    failed = service.latest()
    assert failed is not None
    assert failed.state == ContentRefreshState.FAILED
    assert failed.failure == {
        "code": "submission_failed",
        "message": "The content refresh could not start; retry with a new idempotency key.",
    }
    assert "injected worker start secret" not in json.dumps(failed.to_dict())
    assert not service._pending_receipts()

    monkeypatch.setattr(threading.Thread, "start", original_thread_start)
    monkeypatch.setattr(store, "_content_refresh_lease", original_refresh_lease)
    if submission_kind == "exact":
        replay = service.submit(_request(), actor=_actor(), asynchronous=False)
        assert replay.operation_id == failed.operation_id
        assert replay.state == ContentRefreshState.FAILED
        assert replay.replayed
        recovered = service.submit(
            _request(key="refresh-key-after-start-failure"),
            actor=_actor(),
            asynchronous=False,
        )
    else:
        recovered = service.submit_compatibility(actor=_actor(), asynchronous=False)
    assert recovered.state == ContentRefreshState.ACTIVATION_PENDING_RESTART
    assert store.refresh_calls == 1


def test_conflicts_are_deterministic_before_promotion(tmp_path: Path) -> None:
    store = _Store(tmp_path, active_commit=COMMIT_A)
    service = _service(store)

    with pytest.raises(ContentRefreshConflict) as stale:
        service.submit(_request(expected=None), actor=_actor(), asynchronous=False)
    assert stale.value.code == "stale_active_commit"
    assert store.refresh_calls == 0

    store.unreachable = True
    with pytest.raises(ContentRefreshConflict) as unreachable:
        service.submit(
            _request(expected=COMMIT_A, key="refresh-key-0002"),
            actor=_actor(),
            asynchronous=False,
        )
    assert unreachable.value.code == "unreachable_commit"
    assert store.refresh_calls == 0


def test_concurrent_workers_replay_one_delivery_and_reject_distinct_pending_work(
    tmp_path: Path,
) -> None:
    assert_free_threading()
    entered = threading.Event()
    release = threading.Event()

    class BlockingStore(_Store):
        def refresh(self, **values: Any) -> dict[str, Any]:
            entered.set()
            assert release.wait(timeout=5)
            return super().refresh(**values)

    store = BlockingStore(tmp_path)
    first_service = _service(store)
    second_service = _service(store)
    request = _request()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(first_service.submit, request, actor=_actor(), asynchronous=False)
        assert entered.wait(timeout=5)

        replay = second_service.submit(request, actor=_actor(), asynchronous=False)
        assert replay.replayed
        assert replay.operation_id.startswith("refresh-")
        assert store.refresh_calls == 0

        with pytest.raises(ContentRefreshConflict) as pending:
            second_service.submit(
                _request(key="refresh-key-0008"),
                actor=_actor(),
                asynchronous=False,
            )
        assert pending.value.code == "refresh_in_progress"

        release.set()
        completed = first.result(timeout=5)

    assert completed.state == ContentRefreshState.ACTIVATION_PENDING_RESTART
    assert store.refresh_calls == 1


def test_startup_reconciliation_is_local_and_binds_generation_image_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(tmp_path)
    receipt = _service(store).submit(_request(), actor=_actor(), asynchronous=False)
    assert receipt.state == ContentRefreshState.ACTIVATION_PENDING_RESTART

    monkeypatch.setenv("FURA_IMAGE_DIGEST", store.image_digest)
    monkeypatch.setenv("FURA_BUILD_GIT_SHA", store.build_commit)
    reconciled = _service(store).reconcile_startup()
    assert reconciled[-1].state == ContentRefreshState.READY
    assert reconciled[-1].readiness == {
        "ready": True,
        "active_generation": "generation-b",
        "resolved_commit": COMMIT_B,
        "image_digest": store.image_digest,
        "build_commit": store.build_commit,
    }

    other = _Store(tmp_path / "interrupted")
    queued = _service(other)._initial_receipt(
        "refresh-" + "0" * 24,
        "sha256:" + "1" * 64,
        _request(key="refresh-key-0003"),
        _actor(),
        active_commit=None,
        compatibility_mode=False,
    )
    _service(other)._write(queued)
    interrupted = _service(other).reconcile_startup()
    assert interrupted[-1].state == ContentRefreshState.FAILED
    assert interrupted[-1].failure["code"] == "interrupted_before_promotion"
    public_failure = interrupted[-1].public_dict(
        status_url=f"/_fura/content/operations/{interrupted[-1].operation_id}",
        include_failure_message=False,
    )["failure"]
    assert public_failure == {"code": "interrupted_before_promotion"}

    superseded_store = _Store(tmp_path / "superseded", active_commit=COMMIT_A)
    superseded_service = _service(superseded_store)
    pending = superseded_service.submit(
        _request(expected=COMMIT_A, key="refresh-key-0009"),
        actor=_actor(),
        asynchronous=False,
    )
    assert pending.state == ContentRefreshState.ACTIVATION_PENDING_RESTART
    superseded_store.active_commit = COMMIT_A
    superseded_store.active_generation = "generation-a"

    reconciled_superseded = superseded_service.reconcile_startup()

    assert reconciled_superseded[-1].state == ContentRefreshState.FAILED
    assert reconciled_superseded[-1].failure is not None
    assert reconciled_superseded[-1].failure["code"] == "superseded_generation"
    assert not superseded_service._pending_receipts()


def test_rollback_waits_for_refresh_bookkeeping_and_terminalizes_the_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_free_threading()
    store = _managed_store(tmp_path, monkeypatch)
    store.refresh(requested_commit=COMMIT_A)
    store.refresh(requested_commit=COMMIT_B)

    scheduler_entered = threading.Event()
    scheduler_release = threading.Event()
    rollback_started = threading.Event()
    rollback_lease_attempted = threading.Event()
    rollback_done = threading.Event()
    original_lease_factory = store._content_refresh_lease

    def tracked_lease(*, timeout_seconds: float | None = None) -> OperationLease:
        lease = original_lease_factory(timeout_seconds=timeout_seconds)
        acquire = lease.acquire

        def tracked_acquire() -> OperationLease:
            if threading.current_thread().name == "test-rollback":
                rollback_lease_attempted.set()
            return acquire()

        monkeypatch.setattr(lease, "acquire", tracked_acquire)
        return lease

    monkeypatch.setattr(store, "_content_refresh_lease", tracked_lease)

    def schedule_restart() -> bool:
        scheduler_entered.set()
        assert scheduler_release.wait(timeout=5)
        return False

    service = ContentRefreshService(store, restart_scheduler=schedule_restart)
    request = _request(expected=COMMIT_B, requested=COMMIT_C, key="refresh-key-race")

    def roll_back() -> dict[str, Any]:
        rollback_started.set()
        result = ContentRefreshService(store).rollback(actor="operator", reason="test race")
        rollback_done.set()
        return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        refresh_future = pool.submit(service.submit, request, actor=_actor(), asynchronous=False)
        assert scheduler_entered.wait(timeout=5)
        rollback_result: Queue[dict[str, Any]] = Queue()
        rollback_worker = threading.Thread(
            target=lambda: rollback_result.put(roll_back()),
            name="test-rollback",
        )
        rollback_worker.start()
        assert rollback_started.wait(timeout=5)
        assert rollback_lease_attempted.wait(timeout=5)
        assert not rollback_done.is_set()
        assert store.active.resolve().name.endswith("-cccccccccccc")

        scheduler_release.set()
        refresh_receipt = refresh_future.result(timeout=5)
        rollback_worker.join(timeout=5)
        assert not rollback_worker.is_alive()
        rollback_receipt = rollback_result.get_nowait()

    assert refresh_receipt.state == ContentRefreshState.ACTIVATION_PENDING_RESTART
    assert rollback_receipt["active_generation"].endswith("-bbbbbbbbbbbb")
    assert store.active.resolve().name.endswith("-bbbbbbbbbbbb")
    persisted = service.get(refresh_receipt.operation_id)
    assert persisted is not None
    assert persisted.state == ContentRefreshState.FAILED
    assert persisted.failure is not None
    assert persisted.failure["code"] == "superseded_by_rollback"
    assert not service._pending_receipts()


def test_startup_snapshot_and_rollback_receipts_share_one_ownership_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_free_threading()
    store = _managed_store(tmp_path, monkeypatch)
    store.refresh(requested_commit=COMMIT_A)
    store.refresh(requested_commit=COMMIT_B)
    service = ContentRefreshService(store)
    promoted = service.submit(
        _request(expected=COMMIT_B, requested=COMMIT_C, key="refresh-startup-rollback"),
        actor=_actor(),
        asynchronous=False,
    )
    assert promoted.state == ContentRefreshState.ACTIVATION_PENDING_RESTART

    snapshot_taken = threading.Event()
    snapshot_release = threading.Event()
    rollback_lease_attempted = threading.Event()
    rollback_done = threading.Event()
    original_status_owned = store._status_owned

    def paused_status_owned(*, full_verification: bool = False) -> dict[str, Any]:
        status = original_status_owned(full_verification=full_verification)
        snapshot_taken.set()
        assert snapshot_release.wait(timeout=5)
        return status

    monkeypatch.setattr(store, "_status_owned", paused_status_owned)
    startup_results: Queue[tuple[ContentRefreshReceipt, ...]] = Queue()
    startup_worker = threading.Thread(
        target=lambda: startup_results.put(service.reconcile_startup()),
        name="test-startup",
    )
    startup_worker.start()
    assert snapshot_taken.wait(timeout=5)

    rollback_service = ContentRefreshService(store)
    original_submission_factory = rollback_service._submission_lease

    def tracked_submission_lease() -> OperationLease:
        lease = original_submission_factory()
        acquire = lease.acquire

        def tracked_acquire() -> OperationLease:
            rollback_lease_attempted.set()
            return acquire()

        monkeypatch.setattr(lease, "acquire", tracked_acquire)
        return lease

    monkeypatch.setattr(rollback_service, "_submission_lease", tracked_submission_lease)
    rollback_results: Queue[dict[str, Any]] = Queue()

    def roll_back() -> None:
        rollback_results.put(rollback_service.rollback(actor="operator", reason="startup race"))
        rollback_done.set()

    rollback_worker = threading.Thread(target=roll_back, name="test-rollback")
    rollback_worker.start()
    assert rollback_lease_attempted.wait(timeout=5)
    assert not rollback_done.is_set()

    snapshot_release.set()
    startup_worker.join(timeout=5)
    rollback_worker.join(timeout=5)
    assert not startup_worker.is_alive()
    assert not rollback_worker.is_alive()
    assert startup_results.get_nowait()[-1].state == ContentRefreshState.READY
    assert rollback_results.get_nowait()["active_generation"].endswith("-bbbbbbbbbbbb")

    persisted = service.get(promoted.operation_id)
    assert persisted is not None
    assert persisted.state == ContentRefreshState.FAILED
    assert persisted.failure is not None
    assert persisted.failure["code"] == "superseded_by_rollback"
    assert store.active.resolve().name.endswith("-bbbbbbbbbbbb")


def test_startup_waits_for_live_queued_and_staging_refresh_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert_free_threading()
    store = _managed_store(tmp_path, monkeypatch)
    store.refresh(requested_commit=COMMIT_A)
    checkout = store._checkout
    checkout_entered = threading.Event()
    checkout_release = threading.Event()

    def blocking_checkout(target: Path, *, requested_commit: str | None = None) -> str:
        if target.name == "source":
            checkout_entered.set()
            assert checkout_release.wait(timeout=5)
        return checkout(target, requested_commit=requested_commit)

    monkeypatch.setattr(store, "_checkout", blocking_checkout)
    startup_lease_attempted = threading.Event()
    original_lease_factory = store._content_refresh_lease

    def tracked_lease(*, timeout_seconds: float | None = None) -> OperationLease:
        lease = original_lease_factory(timeout_seconds=timeout_seconds)
        acquire = lease.acquire

        def tracked_acquire() -> OperationLease:
            if threading.current_thread().name == "test-startup":
                startup_lease_attempted.set()
            return acquire()

        monkeypatch.setattr(lease, "acquire", tracked_acquire)
        return lease

    monkeypatch.setattr(store, "_content_refresh_lease", tracked_lease)
    service = ContentRefreshService(store)
    captured: list[Callable[[], object]] = []
    monkeypatch.setattr(service, "_start", captured.append)

    queued = service.submit(
        _request(expected=COMMIT_A, requested=COMMIT_B, key="refresh-startup-live"),
        actor=_actor(),
        asynchronous=True,
    )
    assert len(captured) == 1
    assert service.get(queued.operation_id).state == ContentRefreshState.QUEUED

    startup_results: Queue[tuple[ContentRefreshReceipt, ...]] = Queue()
    startup_done = threading.Event()

    def reconcile() -> None:
        startup_results.put(service.reconcile_startup())
        startup_done.set()

    startup_worker = threading.Thread(target=reconcile, name="test-startup")
    startup_worker.start()
    assert startup_lease_attempted.wait(timeout=5)
    assert not startup_done.is_set()
    persisted_queued = service.get(queued.operation_id)
    assert persisted_queued is not None
    assert persisted_queued.state == ContentRefreshState.QUEUED

    refresh_worker = threading.Thread(target=captured[0], name="test-refresh")
    refresh_worker.start()
    assert checkout_entered.wait(timeout=5)
    persisted_staging = service.get(queued.operation_id)
    assert persisted_staging is not None
    assert persisted_staging.state == ContentRefreshState.STAGING
    assert not startup_done.is_set()

    checkout_release.set()
    refresh_worker.join(timeout=5)
    startup_worker.join(timeout=5)
    assert not refresh_worker.is_alive()
    assert not startup_worker.is_alive()
    assert startup_results.get_nowait()[-1].state == ContentRefreshState.READY

    persisted = service.get(queued.operation_id)
    assert persisted is not None
    assert persisted.state == ContentRefreshState.READY
    assert persisted.generation == store.active.resolve().name


def test_actor_comes_from_bearer_transport_not_request_body() -> None:
    token = "t" * 32
    actor = authenticate_content_actor(
        f"Bearer {token}", transport="http", environ={"FURA_CONTENT_REFRESH_TOKEN": token}
    )
    assert actor == ContentRefreshActor(
        "configured-content-operator", "content-refresh-bearer", "http"
    )
    assert (
        authenticate_content_actor(
            "Bearer wrong", transport="http", environ={"FURA_CONTENT_REFRESH_TOKEN": token}
        )
        is None
    )
    assert (
        authenticate_content_actor(
            token, transport="http", environ={"FURA_CONTENT_REFRESH_TOKEN": token}
        )
        is None
    )


def test_json_schemas_validate_request_and_durable_receipt(tmp_path: Path) -> None:
    request = _request()
    receipt = _service(_Store(tmp_path)).submit(request, actor=_actor(), asynchronous=False)
    checker = FormatChecker()
    request_validator = Draft202012Validator(
        json.loads((SCHEMAS / "request.schema.json").read_text(encoding="utf-8")),
        format_checker=checker,
    )
    receipt_validator = Draft202012Validator(
        json.loads((SCHEMAS / "receipt.schema.json").read_text(encoding="utf-8")),
        format_checker=checker,
    )
    request_validator.validate(request.to_dict())
    receipt_validator.validate(receipt.to_dict())

    with pytest.raises(ValueError, match="unsupported schema version"):
        type(receipt).from_dict({**receipt.to_dict(), "schema_version": True})
    with pytest.raises(ValidationError):
        request_validator.validate({**request.to_dict(), "schema_version": True})
    with pytest.raises(ValidationError):
        receipt_validator.validate({**receipt.to_dict(), "schema_version": True})

    with pytest.raises(ValidationError):
        request_validator.validate({**request.to_dict(), "actor": "body-actor"})

    fixture_root = Path("tests/fixtures/content-refresh/v1")
    request_validator.validate(
        json.loads((fixture_root / "request.json").read_text(encoding="utf-8"))
    )
    receipt_validator.validate(
        json.loads((fixture_root / "receipt.json").read_text(encoding="utf-8"))
    )


def test_http_refresh_returns_202_and_deterministic_409_without_scope_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from furatena.catalog import route_registrars

    app_root = tmp_path / "app"
    content_root = tmp_path / "content"
    app_root.mkdir()
    content_root.mkdir()
    copy_app_theme(app_root, Path(__file__).resolve().parents[1] / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(app_root / "mounts.yaml", content_root)
    (content_root / "_index.md").write_text("# Home\n", encoding="utf-8")
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, False, False),
    )
    token = "t" * 32
    monkeypatch.setenv("FURA_CONTENT_REFRESH_TOKEN", token)
    monkeypatch.setattr(route_registrars, "_schedule_content_restart", lambda: False)
    store = _Store(tmp_path / "managed")
    monkeypatch.setattr(route_registrars, "_content_store", lambda: cast(Any, store))
    client = TestClient(docs.create_app())

    async def exercise() -> None:
        headers = {"authorization": f"Bearer {token}"}
        accepted = await client.post(
            "/_fura/content/refresh",
            headers=headers,
            json=_request().to_dict(),
        )
        assert accepted.status == 202
        payload = json.loads(accepted.text)
        assert payload["status_url"] == f"/_fura/content/operations/{payload['operation_id']}"
        assert "repository" not in payload

        public_status = await client.get("/_fura/content/status")
        assert public_status.status == 200
        status_payload = json.loads(public_status.text)
        assert "repository" not in status_payload
        assert "generation_selection" not in status_payload
        assert status_payload["lifecycle_state"] in {"staging", "active", "stale"}

        unreachable_store = _Store(tmp_path / "unreachable")
        unreachable_store.unreachable = True
        monkeypatch.setattr(
            route_registrars, "_content_store", lambda: cast(Any, unreachable_store)
        )
        conflict = await client.post(
            "/_fura/content/refresh",
            headers=headers,
            json=_request(key="refresh-key-0004").to_dict(),
        )
        assert conflict.status == 409
        assert json.loads(conflict.text)["error"]["code"] == "unreachable_commit"

        invalid = await client.post(
            "/_fura/content/refresh",
            headers=headers,
            json={**_request(key="refresh-key-0005").to_dict(), "actor": "body"},
        )
        assert invalid.status == 400
        assert json.loads(invalid.text)["error"]["code"] == "invalid_refresh_request"

        unauthorized = await client.post(
            "/_fura/content/refresh",
            json=_request(key="refresh-key-0006").to_dict(),
        )
        assert unauthorized.status == 401

    asyncio.run(exercise())


def test_build_identity_reports_running_generation_until_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generations = tmp_path / "generations"
    running = generations / "generation-running"
    selected = generations / "generation-selected"
    for generation, commit in ((running, COMMIT_A), (selected, COMMIT_B)):
        generation.mkdir(parents=True)
        (generation / "receipt.json").write_text(
            json.dumps(
                {
                    "status": "active",
                    "generation": generation.name,
                    "resolved_ref": commit,
                    "image_digest": f"sha256:{'c' * 64}",
                    "build_commit": "d" * 40,
                }
            ),
            encoding="utf-8",
        )
    (tmp_path / "active").symlink_to(selected)
    monkeypatch.setenv("FURA_CONTENT_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FURA_ACTIVE_CONTENT_GENERATION", running.name)

    identity = _content_identity()

    assert identity["generation"] == running.name
    assert identity["resolved_ref"] == COMMIT_A
    assert identity["selected_generation"] == selected.name
    assert identity["activation_pending_restart"] is True


def test_promoted_generation_restarts_into_browser_search_catalog_and_agent_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    state_root = tmp_path / "state"
    config = ContentDeploymentConfig(
        repository="https://github.com/example/docs.git",
        ref="main",
        subdirectory="app",
        allowed_hosts=frozenset({"github.com"}),
        state_root=state_root,
    )
    store = ContentDeploymentStore(config)
    canary = "RUNTIME_REFRESH_CANARY_436"

    def checkout(target: Path, *, requested_commit: str | None = None) -> str:
        assert requested_commit == COMMIT_B
        app_root = target / "app"
        content_root = app_root / "content"
        content_root.mkdir(parents=True)
        copy_app_theme(app_root, repository_root / "app")
        write_minimal_docs_yaml(app_root / "docs.yaml")
        write_mounts_yaml(app_root / "mounts.yaml", content_root)
        (content_root / "_index.md").write_text(
            f"---\ntitle: Refreshed home\n---\n\n# Refreshed home\n\n{canary}\n",
            encoding="utf-8",
        )
        return COMMIT_B

    monkeypatch.setattr(store, "_checkout", checkout)
    monkeypatch.setenv("FURA_CONTENT_REPOSITORY", config.repository)
    monkeypatch.setenv("FURA_CONTENT_REF", config.ref)
    monkeypatch.setenv("FURA_CONTENT_SUBDIRECTORY", config.subdirectory)
    monkeypatch.setenv("FURA_CONTENT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("FURA_PLATFORM_ROOT", str(repository_root / "app"))
    monkeypatch.setenv("FURA_RUNTIME_STATE_ROOT", str(tmp_path / "runtime-state"))
    monkeypatch.setenv("FURA_OUTPUT_ROOT", str(tmp_path / "runtime-output"))
    monkeypatch.setenv("FURA_IMAGE_DIGEST", "sha256:" + "c" * 64)
    monkeypatch.setenv("FURA_BUILD_GIT_SHA", "d" * 40)
    service = ContentRefreshService(store)

    promoted = service.submit(_request(), actor=_actor(), asynchronous=False)
    assert promoted.state == ContentRefreshState.ACTIVATION_PENDING_RESTART

    selection = store.active_selection()
    monkeypatch.setenv("FURA_ACTIVE_CONTENT_GENERATION", selection.generation)
    reconciled = service.reconcile_startup()
    assert reconciled[-1].state == ContentRefreshState.READY

    docs = DocsApp.from_paths(
        selection.site_root / "docs.yaml",
        repo_root=selection.checkout_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, selection.frozen_root, True, False),
    )
    client = TestClient(docs.create_app())

    async def assert_surfaces() -> None:
        async with client:
            browser, search, catalog, llms = await asyncio.gather(
                client.get("/"),
                client.get("/search.json"),
                client.get("/catalog.json"),
                client.get("/llms-full.txt"),
            )
        for response in (browser, search, catalog, llms):
            assert response.status == 200
        assert "Refreshed home" in browser.text
        for response in (search, catalog, llms):
            assert canary in response.text

    asyncio.run(assert_surfaces())
