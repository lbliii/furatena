"""Cross-process leases, idempotency, and crash-recovery contracts."""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from furatena.catalog.atomic_directory import AtomicDirectoryTransaction
from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.deployment_manifest import (
    DeploymentArtifact,
    DeploymentManifest,
    read_deployment_manifest,
    write_deployment_manifest,
)
from furatena.catalog.operation_lease import OperationLease, OperationLeaseTimeout


def _increment_process(root: str, counter_path: str, iterations: int) -> None:
    root_path = Path(root)
    counter = Path(counter_path)
    for _ in range(iterations):
        with OperationLease(
            root_path,
            "shared",
            timeout_seconds=10.0,
            lease_seconds=1.0,
            poll_seconds=0.005,
        ):
            value = int(counter.read_text(encoding="utf-8"))
            counter.write_text(str(value + 1), encoding="utf-8")


def _crash_with_lease(root: str) -> None:
    OperationLease(
        Path(root),
        "crash",
        timeout_seconds=1.0,
        lease_seconds=0.2,
        poll_seconds=0.005,
    ).acquire()
    os._exit(17)


def test_lease_serializes_free_threaded_writers(tmp_path: Path) -> None:
    assert_free_threading()
    counter = tmp_path / "counter.txt"
    counter.write_text("0", encoding="utf-8")

    def increment(_index: int) -> None:
        with OperationLease(
            tmp_path / "leases",
            "threads",
            timeout_seconds=10.0,
            lease_seconds=1.0,
            poll_seconds=0.002,
        ):
            value = int(counter.read_text(encoding="utf-8"))
            counter.write_text(str(value + 1), encoding="utf-8")

    with ThreadPoolExecutor(max_workers=64) as pool:
        list(pool.map(increment, range(256)))

    assert counter.read_text(encoding="utf-8") == "256"


def test_lease_retries_when_owner_bootstrap_directory_is_reclaimed(tmp_path: Path) -> None:
    assert_free_threading()
    root = tmp_path / "leases"
    bootstrap_started = threading.Event()
    resume_bootstrap = threading.Event()
    delayed_entered = threading.Event()

    class DelayedOwnerLease(OperationLease):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._delay_owner_once = True

        def _claim_owner(self) -> None:
            if self._delay_owner_once:
                self._delay_owner_once = False
                bootstrap_started.set()
                assert resume_bootstrap.wait(timeout=5)
            super()._claim_owner()

    def acquire_delayed() -> None:
        with DelayedOwnerLease(
            root,
            "bootstrap",
            timeout_seconds=5.0,
            lease_seconds=0.05,
            poll_seconds=0.002,
        ):
            delayed_entered.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(acquire_delayed)
        assert bootstrap_started.wait(timeout=5)
        time.sleep(0.08)

        with OperationLease(
            root,
            "bootstrap",
            timeout_seconds=5.0,
            lease_seconds=0.2,
            poll_seconds=0.002,
        ) as newer_owner:
            assert newer_owner.owner()["token"] == newer_owner.token
            resume_bootstrap.set()
            time.sleep(0.03)
            assert newer_owner.owner()["token"] == newer_owner.token
            assert not delayed_entered.is_set()

        future.result(timeout=5)

    assert delayed_entered.is_set()


def test_lease_serializes_multiple_processes(tmp_path: Path) -> None:
    counter = tmp_path / "counter.txt"
    counter.write_text("0", encoding="utf-8")
    context = multiprocessing.get_context("fork")
    workers = [
        context.Process(
            target=_increment_process,
            args=(str(tmp_path / "leases"), str(counter), 20),
        )
        for _ in range(4)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)

    assert [worker.exitcode for worker in workers] == [0, 0, 0, 0]
    assert counter.read_text(encoding="utf-8") == "80"


def test_heartbeat_prevents_steal_and_crashed_worker_expires(tmp_path: Path) -> None:
    root = tmp_path / "leases"
    with OperationLease(
        root,
        "live",
        timeout_seconds=1.0,
        lease_seconds=0.3,
        poll_seconds=0.01,
    ):
        time.sleep(0.75)
        with pytest.raises(OperationLeaseTimeout, match="timed out"):
            OperationLease(
                root,
                "live",
                timeout_seconds=0.1,
                lease_seconds=0.3,
                poll_seconds=0.01,
            ).acquire()

    context = multiprocessing.get_context("fork")
    crashed = context.Process(target=_crash_with_lease, args=(str(root),))
    crashed.start()
    crashed.join(timeout=10)
    assert crashed.exitcode == 17

    with pytest.raises(OperationLeaseTimeout):
        OperationLease(
            root,
            "crash",
            timeout_seconds=0.03,
            lease_seconds=0.2,
            poll_seconds=0.005,
        ).acquire()
    time.sleep(0.22)
    with OperationLease(
        root,
        "crash",
        timeout_seconds=1.0,
        lease_seconds=0.2,
        poll_seconds=0.005,
    ) as recovered:
        assert recovered.owner()["pid"] == os.getpid()


def test_renewal_invalidates_an_expired_reclaim_snapshot(tmp_path: Path) -> None:
    assert_free_threading()
    root = tmp_path / "leases"
    reclaim_started = threading.Event()
    resume_reclaim = threading.Event()

    class PausedReclaimLease(OperationLease):
        def _reclaim_expired(self, expected_identity) -> None:
            reclaim_started.set()
            assert resume_reclaim.wait(timeout=5)
            super()._reclaim_expired(expected_identity)

    owner = OperationLease(
        root,
        "renew-race",
        timeout_seconds=1.0,
        lease_seconds=0.2,
        poll_seconds=0.002,
    ).acquire()
    owner._stop.set()
    assert owner._heartbeat is not None
    owner._heartbeat.join(timeout=1)
    time.sleep(0.22)

    def contend() -> None:
        PausedReclaimLease(
            root,
            "renew-race",
            timeout_seconds=0.08,
            lease_seconds=0.2,
            poll_seconds=0.002,
        ).acquire()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(contend)
            assert reclaim_started.wait(timeout=5)
            owner.renew()
            resume_reclaim.set()
            with pytest.raises(OperationLeaseTimeout):
                future.result(timeout=5)
        assert owner.owner()["token"] == owner.token
    finally:
        resume_reclaim.set()
        owner.release()


def test_duplicate_delivery_is_serial_and_leaves_one_consistent_tree(tmp_path: Path) -> None:
    target = tmp_path / "public"
    invocations: list[str] = []
    invocations_lock = threading.Lock()

    def deliver(_index: int) -> None:
        with OperationLease(tmp_path / "leases", "deployment", timeout_seconds=5.0):
            transaction = AtomicDirectoryTransaction(target, operation="export")
            staging = transaction.prepare()
            try:
                (staging / "operation.json").write_text(
                    json.dumps({"operation_id": "delivery-42", "status": "complete"}),
                    encoding="utf-8",
                )
                with invocations_lock:
                    invocations.append("delivery-42")
                transaction.commit()
            finally:
                transaction.cleanup()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(deliver, range(2)))

    assert invocations == ["delivery-42", "delivery-42"]
    assert json.loads((target / "operation.json").read_text(encoding="utf-8")) == {
        "operation_id": "delivery-42",
        "status": "complete",
    }
    assert not list(tmp_path.glob(".public.export.*"))


def test_partial_write_and_interrupted_promotion_restore_last_known_good(tmp_path: Path) -> None:
    target = tmp_path / "frozen"
    target.mkdir()
    (target / "catalog.json").write_text('{"version":"good"}', encoding="utf-8")

    transaction = AtomicDirectoryTransaction(target, operation="freeze")
    staging = transaction.prepare()
    (staging / "catalog.json").write_text('{"version":', encoding="utf-8")
    transaction.cleanup()
    assert (target / "catalog.json").read_text(encoding="utf-8") == '{"version":"good"}'

    backup = tmp_path / ".frozen.freeze.backup.crashed"
    pending = tmp_path / ".frozen.freeze.pending.crashed"
    os.replace(target, backup)
    pending.mkdir()
    (pending / "catalog.json").write_text("partial", encoding="utf-8")

    assert AtomicDirectoryTransaction.reconcile(target, operation="freeze") is True
    assert (target / "catalog.json").read_text(encoding="utf-8") == '{"version":"good"}'
    assert not backup.exists()
    assert not pending.exists()


def test_manifest_write_failure_preserves_previous_manifest(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "export.manifest.json"
    first = DeploymentManifest(
        target="static",
        mode="static",
        page_count=1,
        artifacts=(DeploymentArtifact("index.html"),),
    )
    write_deployment_manifest(path, first)
    original = path.read_bytes()

    from furatena.catalog import deployment_manifest as module

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated worker failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated worker failure"):
        write_deployment_manifest(
            path,
            DeploymentManifest(target="static", mode="static", page_count=2),
        )

    assert path.read_bytes() == original
    assert read_deployment_manifest(path).page_count == 1
    assert not list(tmp_path.glob(".export.manifest.json.*.tmp"))


def test_recovery_runbook_documents_lease_timeout_and_partial_write_repair() -> None:
    repo = Path(__file__).resolve().parents[1]
    runbook = (repo / "content/furatena/docs/operations/observability-and-recovery.md").read_text(
        encoding="utf-8"
    )

    for contract in (
        "FURA_OPERATION_LOCK_TIMEOUT",
        "FURA_OPERATION_LEASE_SECONDS",
        "heartbeat",
        "orphaned backup",
        "partial pending",
        "Duplicate deliveries",
    ):
        assert contract in runbook
