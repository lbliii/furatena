"""Idempotent publication workflow orchestration tests."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from furatena.catalog.audit_store import InMemoryAuditStore
from furatena.catalog.publication_contracts import (
    PublicationOutputReference,
    PublicationPlan,
    PublicationState,
)
from furatena.catalog.publication_state import (
    PublicationTransitionGuards,
    transition_publication_state,
)
from furatena.catalog.publication_workflow import (
    FilesystemPublicationLeaseFactory,
    PublicationWorkflowService,
    PublicationWorkflowTransportAdapter,
)
from furatena.catalog.publication_workflow_store import (
    InMemoryPublicationWorkflowStore,
    JsonDirectoryPublicationWorkflowStore,
    PublicationOperationReceipt,
    PublicationOperationStatus,
)
from tests.publication_support import sample_actor, sample_plan


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self.outputs = (
            PublicationOutputReference(
                kind="repository_change",
                identifier="change-42",
                status="applied",
                revision="revision-new",
            ),
        )

    def execute(self, _plan: object) -> tuple[PublicationOutputReference, ...]:
        with self._lock:
            self.calls += 1
        time.sleep(0.01)
        return self.outputs

    def reconcile(self, _plan: object) -> tuple[PublicationOutputReference, ...] | None:
        return self.outputs


def test_validation_uses_bound_snapshot_and_execution_rechecks_all_preconditions(
    tmp_path: Path,
) -> None:
    executor = RecordingExecutor()
    audit = InMemoryAuditStore(clock=lambda: 1_893_456_000.0)
    service = _service(tmp_path, executor=executor, audit=audit)
    plan = sample_plan(required_count=0)

    created = service.create_plan(plan)
    assert created.snapshot.state == PublicationState.PROPOSED
    validated = service.validate(plan.plan_id, actor=sample_actor())
    assert validated.snapshot.state == PublicationState.APPROVED
    assert validated.snapshot.state_version == 4

    result = service.execute(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="execute-1",
        expected_state_version=4,
    )
    assert result.snapshot.state == PublicationState.APPLIED
    assert result.receipt is not None
    assert result.receipt.status == PublicationOperationStatus.SUCCEEDED
    assert executor.calls == 1
    assert audit.query()[0]["action"] == "publication.execute"

    replay = service.execute(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="execute-1",
        expected_state_version=4,
    )
    assert replay.replayed is True
    assert replay.snapshot == result.snapshot
    assert executor.calls == 1


def test_stale_binding_fails_before_executor_mutation(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service = _service(tmp_path, executor=executor, binding_checker=lambda _plan: False)
    plan = sample_plan(required_count=0)
    service.create_plan(plan)
    approved = service.validate(plan.plan_id, actor=sample_actor()).snapshot

    result = service.execute(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="stale-1",
        expected_state_version=approved.state_version,
    )

    assert result.snapshot.state == PublicationState.FAILED
    assert result.snapshot.failure is not None
    assert result.snapshot.failure.code == "binding.stale"
    assert executor.calls == 0


def test_stale_state_command_records_failure_without_changing_workflow(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service = _service(tmp_path, executor=executor)
    plan = sample_plan(required_count=0)
    service.create_plan(plan)
    approved = service.validate(plan.plan_id, actor=sample_actor()).snapshot

    result = service.execute(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="stale-state-1",
        expected_state_version=approved.state_version - 1,
    )

    assert result.snapshot == approved
    assert result.receipt is not None
    assert result.receipt.status == PublicationOperationStatus.FAILED
    assert result.receipt.response is not None
    assert result.receipt.response["failure"]["code"] == "binding.stale"
    assert executor.calls == 0


def test_retry_follows_typed_validation_target(tmp_path: Path) -> None:
    service = _service(tmp_path)
    plan = sample_plan(required_count=0, validation_error_count=1)
    service.create_plan(plan)
    failed = service.validate(plan.plan_id, actor=sample_actor()).snapshot

    retried = service.retry(
        plan.plan_id,
        actor=sample_actor(),
        expected_state_version=failed.state_version,
    )

    assert failed.state == PublicationState.FAILED
    assert failed.failure is not None
    assert failed.failure.retry_target == PublicationState.VALIDATING
    assert retried.snapshot.state == PublicationState.VALIDATING


def test_expired_plan_becomes_terminal_without_executor_mutation(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service = _service(tmp_path, executor=executor)
    plan = sample_plan(expires_at="2029-01-01T00:00:00Z")
    service.create_plan(plan)

    result = service.validate(plan.plan_id, actor=sample_actor())

    assert result.snapshot.state == PublicationState.EXPIRED
    assert result.snapshot.terminal is True
    assert executor.calls == 0


def test_two_service_instances_execute_same_key_exactly_once(tmp_path: Path) -> None:
    root = tmp_path / "workflow"
    store_one = JsonDirectoryPublicationWorkflowStore(root)
    store_two = JsonDirectoryPublicationWorkflowStore(root)
    executor = RecordingExecutor()
    lease_factory = FilesystemPublicationLeaseFactory(
        tmp_path / "leases", timeout_seconds=2, lease_seconds=2
    )
    first = _service(tmp_path, store=store_one, executor=executor, lease_factory=lease_factory)
    second = _service(tmp_path, store=store_two, executor=executor, lease_factory=lease_factory)
    plan = sample_plan(required_count=0)
    first.create_plan(plan)
    approved = first.validate(plan.plan_id, actor=sample_actor()).snapshot
    responses = []

    def run(service: PublicationWorkflowService) -> None:
        responses.append(
            service.execute(
                plan.plan_id,
                actor=sample_actor(),
                idempotency_key="concurrent-1",
                expected_state_version=approved.state_version,
            )
        )

    threads = [
        threading.Thread(target=run, args=(first,)),
        threading.Thread(target=run, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert executor.calls == 1
    assert len(responses) == 2
    assert all(item.snapshot.state == PublicationState.APPLIED for item in responses)
    assert sum(item.replayed for item in responses) == 1


def test_started_receipt_after_restart_requires_reconciliation(tmp_path: Path) -> None:
    root = tmp_path / "workflow"
    store = JsonDirectoryPublicationWorkflowStore(root)
    service = _service(tmp_path, store=store)
    plan = sample_plan(required_count=0)
    service.create_plan(plan)
    approved = service.validate(plan.plan_id, actor=sample_actor()).snapshot
    actor = sample_actor()
    receipt = PublicationOperationReceipt.start(
        idempotency_key="crashed-1",
        command="execute",
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        expected_state_version=approved.state_version,
        input_payload={
            "command": "execute",
            "plan_id": plan.plan_id,
            "actor": actor.to_dict(),
            "expected_state_version": approved.state_version,
        },
        started_at="2030-01-01T00:00:00Z",
    )
    store.begin_operation(receipt)
    executing = transition_publication_state(
        plan,
        approved,
        PublicationState.EXECUTING,
        expected_state_version=approved.state_version,
        actor=actor,
        timestamp="2030-01-01T00:00:00Z",
        guards=PublicationTransitionGuards(bindings_current=True, approvals_satisfied=True),
    )
    store.compare_and_swap(
        plan.plan_id,
        approved.state_version,
        executing.snapshot,
        executing.event,
    )

    restarted = _service(
        tmp_path,
        store=JsonDirectoryPublicationWorkflowStore(root),
        executor=RecordingExecutor(),
    )
    result = restarted.execute(
        plan.plan_id,
        actor=actor,
        idempotency_key="crashed-1",
        expected_state_version=approved.state_version,
    )

    assert result.snapshot.state == PublicationState.FAILED
    assert result.snapshot.failure is not None
    assert result.snapshot.failure.code == "execution.outcome_unknown"
    assert result.receipt is not None
    assert result.receipt.status == PublicationOperationStatus.RECONCILIATION_REQUIRED


def test_cancel_is_idempotent_and_never_calls_executor(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service = _service(tmp_path, executor=executor)
    plan = sample_plan()
    initial = service.create_plan(plan).snapshot

    cancelled = service.cancel(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="cancel-1",
        expected_state_version=initial.state_version,
    )
    replay = service.cancel(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="cancel-1",
        expected_state_version=initial.state_version,
    )

    assert cancelled.snapshot.state == PublicationState.CANCELLED
    assert replay.snapshot == cancelled.snapshot
    assert replay.replayed is True
    assert executor.calls == 0


def test_transport_adapters_submit_identical_inner_command(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service = _service(tmp_path, executor=executor)
    plan = sample_plan(required_count=0)
    service.create_plan(plan)
    approved = service.validate(plan.plan_id, actor=sample_actor()).snapshot
    command = {
        "plan_id": plan.plan_id,
        "idempotency_key": "transport-1",
        "expected_state_version": approved.state_version,
    }

    responses = [
        PublicationWorkflowTransportAdapter(name, service).execute(command, actor=sample_actor())
        for name in ("browser", "cli", "mcp", "automation")
    ]

    assert executor.calls == 1
    assert {str(item["snapshot"]) for item in responses} == {str(responses[0]["snapshot"])}


def _service(
    tmp_path: Path,
    *,
    store: InMemoryPublicationWorkflowStore | JsonDirectoryPublicationWorkflowStore | None = None,
    executor: RecordingExecutor | None = None,
    audit: InMemoryAuditStore | None = None,
    binding_checker: Callable[[PublicationPlan], bool] | None = None,
    lease_factory: FilesystemPublicationLeaseFactory | None = None,
) -> PublicationWorkflowService:
    return PublicationWorkflowService(
        store=store or InMemoryPublicationWorkflowStore(),
        binding_checker=binding_checker or (lambda _plan: True),
        approval_evaluator=lambda _plan: True,
        authorizer=lambda _plan, _actor, _command: True,
        executor=executor or RecordingExecutor(),
        audit_store=audit or InMemoryAuditStore(clock=lambda: 1_893_456_000.0),
        lease_factory=lease_factory
        or FilesystemPublicationLeaseFactory(
            tmp_path / "leases", timeout_seconds=2, lease_seconds=2
        ),
        clock=lambda: "2030-01-01T00:00:00Z",
    )
