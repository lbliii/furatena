"""Provider-neutral, idempotent publication workflow orchestration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from furatena.catalog.audit_store import AuditStore
from furatena.catalog.operation_lease import OperationLease
from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationFailure,
    PublicationFailureDisposition,
    PublicationOutputReference,
    PublicationPlan,
    PublicationProjection,
    PublicationState,
    PublicationStateSnapshot,
    canonical_json_bytes,
    normalize_rfc3339,
)
from furatena.catalog.publication_state import (
    PublicationTransitionGuards,
    transition_publication_state,
)
from furatena.catalog.publication_workflow_store import (
    PublicationOperationReceipt,
    PublicationOperationStatus,
    PublicationWorkflowStore,
)


class PublicationBindingChecker(Protocol):
    def __call__(self, plan: PublicationPlan, /) -> bool: ...


class PublicationApprovalEvaluator(Protocol):
    def __call__(self, plan: PublicationPlan, /) -> bool: ...


class PublicationCapabilityAuthorizer(Protocol):
    def __call__(self, plan: PublicationPlan, actor: PublicationActor, command: str, /) -> bool: ...


class PublicationExecutor(Protocol):
    """Internal boundary implemented by local authoring or publication providers."""

    def execute(self, plan: PublicationPlan, /) -> tuple[PublicationOutputReference, ...]: ...

    def reconcile(
        self, plan: PublicationPlan, /
    ) -> tuple[PublicationOutputReference, ...] | None: ...


class PublicationLeaseFactory(Protocol):
    def __call__(self, plan_id: str) -> AbstractContextManager[Any]: ...


class PublicationExecutionError(RuntimeError):
    """Known executor failure safe to persist and return."""

    def __init__(self, failure: PublicationFailure) -> None:
        self.failure = failure
        super().__init__(failure.safe_message)


@dataclass(frozen=True, slots=True)
class PublicationWorkflowResponse:
    plan: PublicationPlan
    snapshot: PublicationStateSnapshot
    receipt: PublicationOperationReceipt | None = None
    replayed: bool = False

    def to_dict(self, projection: PublicationProjection = "trusted") -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "record_type": "furatena.publication.workflow-response",
            "plan": self.plan.to_dict(projection),
            "snapshot": self.snapshot.to_dict(projection),
            "replayed": self.replayed,
        }
        if projection == "trusted":
            payload["receipt"] = self.receipt.to_dict() if self.receipt else None
        elif self.receipt is not None:
            payload["operation"] = {
                "command": self.receipt.command,
                "status": self.receipt.status.value,
            }
        return payload


class FilesystemPublicationLeaseFactory:
    """Cross-process plan lease factory backed by :class:`OperationLease`."""

    def __init__(
        self,
        root: Path,
        *,
        timeout_seconds: float = 30.0,
        lease_seconds: float = 300.0,
    ) -> None:
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.lease_seconds = lease_seconds

    def __call__(self, plan_id: str) -> OperationLease:
        return OperationLease(
            self.root,
            f"publication-{plan_id}",
            resource=plan_id,
            timeout_seconds=self.timeout_seconds,
            lease_seconds=self.lease_seconds,
        )


class PublicationWorkflowService:
    """One orchestration boundary shared by browser, CLI, MCP, and automation."""

    def __init__(
        self,
        *,
        store: PublicationWorkflowStore,
        binding_checker: PublicationBindingChecker,
        approval_evaluator: PublicationApprovalEvaluator,
        authorizer: PublicationCapabilityAuthorizer,
        executor: PublicationExecutor,
        audit_store: AuditStore,
        lease_factory: PublicationLeaseFactory,
        clock: Callable[[], str],
    ) -> None:
        self.store = store
        self.binding_checker = binding_checker
        self.approval_evaluator = approval_evaluator
        self.authorizer = authorizer
        self.executor = executor
        self.audit_store = audit_store
        self.lease_factory = lease_factory
        self.clock = clock

    def create_plan(self, plan: PublicationPlan) -> PublicationWorkflowResponse:
        """Persist a deterministically created plan without touching its source."""
        with self.lease_factory(plan.plan_id):
            snapshot = PublicationStateSnapshot.initial(plan, timestamp=self.clock())
            self.store.create_plan(plan, snapshot)
            return PublicationWorkflowResponse(plan, self.store.get_snapshot(plan.plan_id))

    def get(
        self, plan_id: str, *, projection: PublicationProjection = "trusted"
    ) -> dict[str, object]:
        return PublicationWorkflowResponse(
            self.store.get_plan(plan_id), self.store.get_snapshot(plan_id)
        ).to_dict(projection)

    def history(self, plan_id: str, *, after_state_version: int = 0) -> tuple[object, ...]:
        return tuple(
            event.to_dict("audit")
            for event in self.store.get_events(plan_id, after_state_version=after_state_version)
        )

    def validate(self, plan_id: str, *, actor: PublicationActor) -> PublicationWorkflowResponse:
        """Advance a proposed plan using only its bound validation snapshot."""
        with self.lease_factory(plan_id):
            plan = self.store.get_plan(plan_id)
            snapshot = self.store.get_snapshot(plan_id)
            if _expired(plan, self.clock()):
                if snapshot.terminal:
                    return PublicationWorkflowResponse(plan, snapshot, replayed=True)
                snapshot = self._transition(plan, snapshot, PublicationState.EXPIRED, actor)
                return PublicationWorkflowResponse(plan, snapshot)
            if snapshot.state == PublicationState.PROPOSED:
                snapshot = self._transition(plan, snapshot, PublicationState.VALIDATING, actor)
            if snapshot.state != PublicationState.VALIDATING:
                return PublicationWorkflowResponse(plan, snapshot, replayed=True)
            if plan.validation.error_count:
                failure = PublicationFailure(
                    disposition=PublicationFailureDisposition.RETRYABLE,
                    code="validation.blocked",
                    safe_message="Publication validation contains blocking errors.",
                    remediation="Resolve the bound diagnostics and create or validate a fresh plan.",
                    retry_target=PublicationState.VALIDATING,
                )
                snapshot = self._transition(
                    plan, snapshot, PublicationState.FAILED, actor, failure=failure
                )
                return PublicationWorkflowResponse(plan, snapshot)
            snapshot = self._transition(plan, snapshot, PublicationState.REVIEWABLE, actor)
            target = (
                PublicationState.AWAITING_APPROVAL
                if plan.approval_requirements.required_count
                else PublicationState.APPROVED
            )
            snapshot = self._transition(
                plan,
                snapshot,
                target,
                actor,
                guards=PublicationTransitionGuards(
                    approvals_satisfied=target == PublicationState.APPROVED
                ),
            )
            return PublicationWorkflowResponse(plan, snapshot)

    def execute(
        self,
        plan_id: str,
        *,
        actor: PublicationActor,
        idempotency_key: str,
        expected_state_version: int,
    ) -> PublicationWorkflowResponse:
        input_payload = _command_payload("execute", plan_id, actor, expected_state_version)
        with self.lease_factory(plan_id):
            plan = self.store.get_plan(plan_id)
            existing = self.store.get_operation(idempotency_key)
            if existing is not None:
                requested = self._started_receipt(
                    plan,
                    "execute",
                    idempotency_key,
                    expected_state_version,
                    input_payload,
                )
                if not existing.same_operation(requested):
                    self.store.begin_operation(requested)
                return self._replay_or_reconcile(plan, existing, actor)

            snapshot = self.store.get_snapshot(plan_id)
            failure = self._execution_precondition_failure(plan, snapshot, actor)
            receipt = self._started_receipt(
                plan,
                "execute",
                idempotency_key,
                expected_state_version,
                input_payload,
            )
            self.store.begin_operation(receipt)
            if snapshot.state_version != expected_state_version:
                return self._finish(
                    plan,
                    snapshot,
                    receipt,
                    PublicationOperationStatus.FAILED,
                    actor,
                    operation_failure=_stale_failure(),
                )
            if _expired(plan, self.clock()):
                expired = self._transition(plan, snapshot, PublicationState.EXPIRED, actor)
                return self._finish(
                    plan,
                    expired,
                    receipt,
                    PublicationOperationStatus.FAILED,
                    actor,
                )
            if failure is not None:
                return self._complete_denial(plan, snapshot, actor, receipt, failure)

            executing = self._transition(
                plan,
                snapshot,
                PublicationState.EXECUTING,
                actor,
                guards=PublicationTransitionGuards(bindings_current=True, approvals_satisfied=True),
            )
            try:
                outputs = self.executor.execute(plan)
            except PublicationExecutionError as exc:
                failed = self._transition(
                    plan, executing, PublicationState.FAILED, actor, failure=exc.failure
                )
                return self._finish(plan, failed, receipt, PublicationOperationStatus.FAILED, actor)
            except Exception:
                failure = _reconciliation_failure()
                failed = self._transition(
                    plan, executing, PublicationState.FAILED, actor, failure=failure
                )
                return self._finish(
                    plan,
                    failed,
                    receipt,
                    PublicationOperationStatus.RECONCILIATION_REQUIRED,
                    actor,
                )

            applied = self._transition(
                plan,
                executing,
                PublicationState.APPLIED,
                actor,
                outputs=outputs,
            )
            return self._finish(plan, applied, receipt, PublicationOperationStatus.SUCCEEDED, actor)

    def cancel(
        self,
        plan_id: str,
        *,
        actor: PublicationActor,
        idempotency_key: str,
        expected_state_version: int,
    ) -> PublicationWorkflowResponse:
        return self._terminal_command(
            "cancel",
            PublicationState.CANCELLED,
            plan_id,
            actor,
            idempotency_key,
            expected_state_version,
        )

    def supersede(
        self,
        plan_id: str,
        *,
        actor: PublicationActor,
        idempotency_key: str,
        expected_state_version: int,
    ) -> PublicationWorkflowResponse:
        return self._terminal_command(
            "supersede",
            PublicationState.SUPERSEDED,
            plan_id,
            actor,
            idempotency_key,
            expected_state_version,
        )

    def expire(
        self,
        plan_id: str,
        *,
        actor: PublicationActor,
        idempotency_key: str,
        expected_state_version: int,
    ) -> PublicationWorkflowResponse:
        return self._terminal_command(
            "expire",
            PublicationState.EXPIRED,
            plan_id,
            actor,
            idempotency_key,
            expected_state_version,
        )

    def reconcile(
        self,
        plan_id: str,
        *,
        actor: PublicationActor,
    ) -> PublicationWorkflowResponse:
        with self.lease_factory(plan_id):
            plan = self.store.get_plan(plan_id)
            snapshot = self.store.get_snapshot(plan_id)
            if (
                snapshot.state != PublicationState.FAILED
                or snapshot.failure is None
                or snapshot.failure.disposition
                != PublicationFailureDisposition.RECONCILIATION_REQUIRED
            ):
                return PublicationWorkflowResponse(plan, snapshot, replayed=True)
            outputs = self.executor.reconcile(plan)
            if outputs is None:
                return PublicationWorkflowResponse(plan, snapshot, replayed=True)
            executing = self._transition(
                plan,
                snapshot,
                PublicationState.EXECUTING,
                actor,
                guards=PublicationTransitionGuards(
                    bindings_current=self.binding_checker(plan),
                    approvals_satisfied=self.approval_evaluator(plan),
                    reconciliation_recorded=True,
                ),
                event_type="execution.reconciled",
            )
            applied = self._transition(
                plan, executing, PublicationState.APPLIED, actor, outputs=outputs
            )
            return PublicationWorkflowResponse(plan, applied)

    def retry(
        self,
        plan_id: str,
        *,
        actor: PublicationActor,
        expected_state_version: int,
    ) -> PublicationWorkflowResponse:
        """Follow the exact retry target encoded by a typed workflow failure."""
        with self.lease_factory(plan_id):
            plan = self.store.get_plan(plan_id)
            snapshot = self.store.get_snapshot(plan_id)
            failure = snapshot.failure
            if (
                snapshot.state != PublicationState.FAILED
                or failure is None
                or failure.retry_target is None
                or failure.retry_target == PublicationState.EXECUTING
            ):
                return PublicationWorkflowResponse(plan, snapshot, replayed=True)
            retried = self._transition(
                plan,
                snapshot,
                failure.retry_target,
                actor,
                expected_state_version=expected_state_version,
                guards=PublicationTransitionGuards(
                    bindings_current=self.binding_checker(plan),
                    approvals_satisfied=self.approval_evaluator(plan),
                ),
                event_type=f"retry.{failure.retry_target.value}",
            )
            return PublicationWorkflowResponse(plan, retried)

    def _terminal_command(
        self,
        command: str,
        target: PublicationState,
        plan_id: str,
        actor: PublicationActor,
        idempotency_key: str,
        expected_state_version: int,
    ) -> PublicationWorkflowResponse:
        payload = _command_payload(command, plan_id, actor, expected_state_version)
        with self.lease_factory(plan_id):
            plan = self.store.get_plan(plan_id)
            requested = self._started_receipt(
                plan, command, idempotency_key, expected_state_version, payload
            )
            existing = self.store.get_operation(idempotency_key)
            if existing is not None:
                if not existing.same_operation(requested):
                    self.store.begin_operation(requested)
                return PublicationWorkflowResponse(
                    plan, self.store.get_snapshot(plan_id), existing, replayed=True
                )
            receipt = self.store.begin_operation(requested)
            snapshot = self.store.get_snapshot(plan_id)
            transitioned = self._transition(
                plan,
                snapshot,
                target,
                actor,
                expected_state_version=expected_state_version,
            )
            return self._finish(
                plan,
                transitioned,
                receipt,
                PublicationOperationStatus.SUCCEEDED,
                actor,
            )

    def _execution_precondition_failure(
        self,
        plan: PublicationPlan,
        snapshot: PublicationStateSnapshot,
        actor: PublicationActor,
    ) -> PublicationFailure | None:
        if snapshot.state != PublicationState.APPROVED:
            return PublicationFailure(
                PublicationFailureDisposition.CONFLICT,
                "workflow.not_approved",
                "Publication plan is not approved for execution.",
                "Complete validation and approval before executing.",
            )
        if not self.binding_checker(plan):
            return _stale_failure()
        if not self.authorizer(plan, actor, "execute"):
            return PublicationFailure(
                PublicationFailureDisposition.AUTHORIZATION,
                "authorization.denied",
                "Current policy does not permit this publication.",
                "Request access or use an eligible trusted identity.",
            )
        if not self.approval_evaluator(plan):
            return PublicationFailure(
                PublicationFailureDisposition.CONFLICT,
                "approval.stale",
                "Publication approvals are missing, expired, or no longer eligible.",
                "Collect current approvals bound to this plan and policy.",
            )
        return None

    def _complete_denial(
        self,
        plan: PublicationPlan,
        snapshot: PublicationStateSnapshot,
        actor: PublicationActor,
        receipt: PublicationOperationReceipt,
        failure: PublicationFailure,
    ) -> PublicationWorkflowResponse:
        if snapshot.state in {PublicationState.APPROVED, PublicationState.EXECUTING}:
            snapshot = self._transition(
                plan, snapshot, PublicationState.FAILED, actor, failure=failure
            )
        return self._finish(
            plan,
            snapshot,
            receipt,
            PublicationOperationStatus.FAILED,
            actor,
            operation_failure=failure,
        )

    def _replay_or_reconcile(
        self,
        plan: PublicationPlan,
        receipt: PublicationOperationReceipt,
        actor: PublicationActor,
    ) -> PublicationWorkflowResponse:
        snapshot = self.store.get_snapshot(plan.plan_id)
        if receipt.status != PublicationOperationStatus.STARTED:
            return PublicationWorkflowResponse(plan, snapshot, receipt, replayed=True)
        if snapshot.state == PublicationState.EXECUTING:
            failed = self._transition(
                plan,
                snapshot,
                PublicationState.FAILED,
                actor,
                failure=_reconciliation_failure(),
            )
            return self._finish(
                plan,
                failed,
                receipt,
                PublicationOperationStatus.RECONCILIATION_REQUIRED,
                actor,
                replayed=True,
            )
        completed = receipt.complete(
            status=PublicationOperationStatus.RECONCILIATION_REQUIRED,
            completed_at=self.clock(),
            response={"state": snapshot.state.value, "state_version": snapshot.state_version},
        )
        self.store.complete_operation(completed)
        return PublicationWorkflowResponse(plan, snapshot, completed, replayed=True)

    def _started_receipt(
        self,
        plan: PublicationPlan,
        command: str,
        idempotency_key: str,
        expected_state_version: int,
        payload: object,
    ) -> PublicationOperationReceipt:
        return PublicationOperationReceipt.start(
            idempotency_key=idempotency_key,
            command=command,
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            expected_state_version=expected_state_version,
            input_payload=payload,
            started_at=self.clock(),
        )

    def _finish(
        self,
        plan: PublicationPlan,
        snapshot: PublicationStateSnapshot,
        receipt: PublicationOperationReceipt,
        status: PublicationOperationStatus,
        actor: PublicationActor,
        *,
        replayed: bool = False,
        operation_failure: PublicationFailure | None = None,
    ) -> PublicationWorkflowResponse:
        reported_failure = operation_failure or snapshot.failure
        response_payload = {
            "plan_id": plan.plan_id,
            "state": snapshot.state.value,
            "state_version": snapshot.state_version,
            "failure": reported_failure.to_dict() if reported_failure else None,
            "outputs": [item.to_dict() for item in snapshot.outputs],
        }
        completed = receipt.complete(
            status=status, completed_at=self.clock(), response=response_payload
        )
        self.store.complete_operation(completed)
        self.audit_store.append(
            {
                "event_id": f"{receipt.command}-{receipt.idempotency_key}",
                "correlation_id": plan.correlation_id,
                "actor": actor.actor,
                "tenant": plan.identity.tenant,
                "site": plan.identity.site,
                "action": f"publication.{receipt.command}",
                "target": plan.plan_id,
                "outcome": status.value,
                "state": snapshot.state.value,
                "failure": reported_failure.to_dict() if reported_failure else None,
            }
        )
        return PublicationWorkflowResponse(plan, snapshot, completed, replayed)

    def _transition(
        self,
        plan: PublicationPlan,
        snapshot: PublicationStateSnapshot,
        target: PublicationState,
        actor: PublicationActor,
        *,
        expected_state_version: int | None = None,
        guards: PublicationTransitionGuards | None = None,
        failure: PublicationFailure | None = None,
        outputs: tuple[PublicationOutputReference, ...] | None = None,
        event_type: str | None = None,
    ) -> PublicationStateSnapshot:
        expected = (
            snapshot.state_version if expected_state_version is None else expected_state_version
        )
        transition = transition_publication_state(
            plan,
            snapshot,
            target,
            expected_state_version=expected,
            actor=actor,
            timestamp=self.clock(),
            guards=guards,
            failure=failure,
            outputs=outputs,
            event_type=event_type,
        )
        self.store.compare_and_swap(plan.plan_id, expected, transition.snapshot, transition.event)
        return transition.snapshot


@dataclass(frozen=True, slots=True)
class PublicationWorkflowTransportAdapter:
    """Transport label plus the same service boundary for parity tests and adapters."""

    transport: str
    service: PublicationWorkflowService

    def execute(self, command: Mapping[str, Any], *, actor: PublicationActor) -> dict[str, object]:
        response = self.service.execute(
            str(command["plan_id"]),
            actor=actor,
            idempotency_key=str(command["idempotency_key"]),
            expected_state_version=int(command["expected_state_version"]),
        )
        return response.to_dict("trusted")


def _command_payload(
    command: str,
    plan_id: str,
    actor: PublicationActor,
    expected_state_version: int,
) -> dict[str, object]:
    payload = {
        "command": command,
        "plan_id": plan_id,
        "actor": actor.to_dict(),
        "expected_state_version": expected_state_version,
    }
    canonical_json_bytes(payload)
    return payload


def _stale_failure() -> PublicationFailure:
    return PublicationFailure(
        PublicationFailureDisposition.CONFLICT,
        "binding.stale",
        "Publication source, catalog, configuration, policy, or validation changed.",
        "Create or validate a fresh plan against current bindings.",
    )


def _reconciliation_failure() -> PublicationFailure:
    return PublicationFailure(
        PublicationFailureDisposition.RECONCILIATION_REQUIRED,
        "execution.outcome_unknown",
        "Publication execution outcome is unknown.",
        "Reconcile provider or source state before any execution retry.",
        retry_target=PublicationState.EXECUTING,
    )


def _expired(plan: PublicationPlan, timestamp: str) -> bool:
    now = datetime.fromisoformat(normalize_rfc3339(timestamp).replace("Z", "+00:00"))
    expiry = datetime.fromisoformat(normalize_rfc3339(plan.expires_at).replace("Z", "+00:00"))
    return now >= expiry
