"""Operational publication workflow store and receipt contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from furatena.catalog.publication_contracts import PublicationState, PublicationStateSnapshot
from furatena.catalog.publication_state import transition_publication_state
from furatena.catalog.publication_workflow_store import (
    InMemoryPublicationWorkflowStore,
    JsonDirectoryPublicationWorkflowStore,
    PublicationOperationReceipt,
    PublicationOperationStatus,
    PublicationWorkflowStore,
    PublicationWorkflowStoreError,
    PublicationWorkflowStoreErrorCode,
)
from tests.publication_support import sample_actor, sample_plan

StoreFactory = Callable[[Path], PublicationWorkflowStore]


@pytest.fixture(params=["memory", "json"])
def store_factory(request: pytest.FixtureRequest) -> StoreFactory:
    if request.param == "memory":
        return lambda _path: InMemoryPublicationWorkflowStore()
    return JsonDirectoryPublicationWorkflowStore


def test_plan_creation_is_idempotent_and_state_updates_use_cas(
    tmp_path: Path, store_factory: StoreFactory
) -> None:
    store = store_factory(tmp_path / "workflow")
    plan = sample_plan(required_count=0)
    initial = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")
    store.create_plan(plan, initial)
    store.create_plan(plan, initial)

    transition = transition_publication_state(
        plan,
        initial,
        PublicationState.VALIDATING,
        expected_state_version=1,
        actor=sample_actor(),
        timestamp="2030-01-01T00:00:01Z",
    )
    store.compare_and_swap(plan.plan_id, 1, transition.snapshot, transition.event)

    assert store.get_plan(plan.plan_id) == plan
    assert store.get_snapshot(plan.plan_id) == transition.snapshot
    assert store.get_events(plan.plan_id) == (transition.event,)
    assert store.get_events(plan.plan_id, after_state_version=2) == ()
    with pytest.raises(PublicationWorkflowStoreError) as caught:
        store.compare_and_swap(plan.plan_id, 1, transition.snapshot, transition.event)
    assert caught.value.code == PublicationWorkflowStoreErrorCode.STALE


def test_receipts_replay_exact_input_and_reject_key_reuse(
    tmp_path: Path, store_factory: StoreFactory
) -> None:
    store = store_factory(tmp_path / "workflow")
    receipt = _receipt()

    assert store.begin_operation(receipt) == receipt
    assert store.begin_operation(receipt) == receipt
    conflict = PublicationOperationReceipt.start(
        idempotency_key=receipt.idempotency_key,
        command="execute",
        plan_id=receipt.plan_id,
        plan_digest=receipt.plan_digest,
        expected_state_version=receipt.expected_state_version,
        input_payload={"approval": "different"},
        started_at=receipt.started_at,
    )
    with pytest.raises(PublicationWorkflowStoreError) as caught:
        store.begin_operation(conflict)
    assert caught.value.code == PublicationWorkflowStoreErrorCode.IDEMPOTENCY_CONFLICT

    completed = receipt.complete(
        status=PublicationOperationStatus.SUCCEEDED,
        completed_at="2030-01-01T00:01:00Z",
        response={"state": "applied", "state_version": 4},
    )
    assert store.complete_operation(completed) == completed
    assert store.complete_operation(completed) == completed
    assert store.get_operation(receipt.idempotency_key) == completed


def test_json_store_recovers_after_restart_and_uses_private_files(tmp_path: Path) -> None:
    root = tmp_path / "workflow"
    first = JsonDirectoryPublicationWorkflowStore(root)
    plan = sample_plan()
    initial = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")
    first.create_plan(plan, initial)
    first.begin_operation(_receipt(plan_id=plan.plan_id, plan_digest=plan.plan_digest))

    restarted = JsonDirectoryPublicationWorkflowStore(root)
    assert restarted.get_plan(plan.plan_id) == plan
    assert restarted.get_snapshot(plan.plan_id) == initial
    assert restarted.get_operation("operation-1") is not None
    assert (root.stat().st_mode & 0o777) == 0o700
    assert ((root / "plans" / f"{plan.plan_id}.json").stat().st_mode & 0o777) == 0o600


def test_json_store_fails_closed_for_corrupt_or_mismatched_records(tmp_path: Path) -> None:
    store = JsonDirectoryPublicationWorkflowStore(tmp_path / "workflow")
    plan = sample_plan()
    initial = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")
    store.create_plan(plan, initial)
    path = store.snapshots / f"{plan.plan_id}.json"
    path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(PublicationWorkflowStoreError) as caught:
        store.get_snapshot(plan.plan_id)
    assert caught.value.code == PublicationWorkflowStoreErrorCode.CORRUPT


def test_json_store_recovers_identical_event_written_before_snapshot(tmp_path: Path) -> None:
    store = JsonDirectoryPublicationWorkflowStore(tmp_path / "workflow")
    plan = sample_plan(required_count=0)
    initial = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")
    store.create_plan(plan, initial)
    transition = transition_publication_state(
        plan,
        initial,
        PublicationState.VALIDATING,
        expected_state_version=1,
        actor=sample_actor(),
        timestamp="2030-01-01T00:00:01Z",
    )
    store.compare_and_swap(plan.plan_id, 1, transition.snapshot, transition.event)
    snapshot_path = store.snapshots / f"{plan.plan_id}.json"
    snapshot_path.write_text(json.dumps(initial.to_dict()) + "\n", encoding="utf-8")

    store.compare_and_swap(plan.plan_id, 1, transition.snapshot, transition.event)

    assert store.get_snapshot(plan.plan_id) == transition.snapshot
    assert store.get_events(plan.plan_id) == (transition.event,)


def _receipt(
    *,
    plan_id: str = "plan-example",
    plan_digest: str = "sha256:" + "1" * 64,
) -> PublicationOperationReceipt:
    return PublicationOperationReceipt.start(
        idempotency_key="operation-1",
        command="execute",
        plan_id=plan_id,
        plan_digest=plan_digest,
        expected_state_version=3,
        input_payload={"approval": "current"},
        started_at="2030-01-01T00:00:00Z",
    )
