"""Guarded legal transitions for publication workflow state snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Never

from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationEvent,
    PublicationFailure,
    PublicationFailureDisposition,
    PublicationOutputReference,
    PublicationPlan,
    PublicationRecordReference,
    PublicationState,
    PublicationStateSnapshot,
    normalize_rfc3339,
)

_TRANSITIONS: dict[PublicationState, frozenset[PublicationState]] = {
    PublicationState.PROPOSED: frozenset(
        {
            PublicationState.VALIDATING,
            PublicationState.CANCELLED,
            PublicationState.SUPERSEDED,
            PublicationState.EXPIRED,
        }
    ),
    PublicationState.VALIDATING: frozenset(
        {
            PublicationState.REVIEWABLE,
            PublicationState.FAILED,
            PublicationState.CANCELLED,
            PublicationState.SUPERSEDED,
            PublicationState.EXPIRED,
        }
    ),
    PublicationState.REVIEWABLE: frozenset(
        {
            PublicationState.VALIDATING,
            PublicationState.AWAITING_APPROVAL,
            PublicationState.APPROVED,
            PublicationState.CANCELLED,
            PublicationState.SUPERSEDED,
            PublicationState.EXPIRED,
        }
    ),
    PublicationState.AWAITING_APPROVAL: frozenset(
        {
            PublicationState.REVIEWABLE,
            PublicationState.APPROVED,
            PublicationState.CANCELLED,
            PublicationState.SUPERSEDED,
            PublicationState.EXPIRED,
        }
    ),
    PublicationState.APPROVED: frozenset(
        {
            PublicationState.EXECUTING,
            PublicationState.AWAITING_APPROVAL,
            PublicationState.FAILED,
            PublicationState.CANCELLED,
            PublicationState.SUPERSEDED,
            PublicationState.EXPIRED,
        }
    ),
    PublicationState.EXECUTING: frozenset(
        {
            PublicationState.REVIEWABLE,
            PublicationState.APPLIED,
            PublicationState.FAILED,
        }
    ),
    PublicationState.FAILED: frozenset(
        {
            PublicationState.VALIDATING,
            PublicationState.APPROVED,
            PublicationState.EXECUTING,
            PublicationState.CANCELLED,
            PublicationState.SUPERSEDED,
            PublicationState.EXPIRED,
        }
    ),
    PublicationState.APPLIED: frozenset(),
    PublicationState.EXPIRED: frozenset(),
    PublicationState.CANCELLED: frozenset(),
    PublicationState.SUPERSEDED: frozenset(),
}


class PublicationTransitionErrorCode(StrEnum):
    ILLEGAL = "illegal_transition"
    TERMINAL = "terminal_state"
    STALE = "stale_state"
    EXPIRED = "expired_plan"
    GUARD = "guard_failed"


class PublicationTransitionError(ValueError):
    """Typed failure returned before state or source can be mutated."""

    def __init__(
        self,
        code: PublicationTransitionErrorCode,
        current: PublicationState,
        target: PublicationState,
        message: str,
        remediation: str,
    ) -> None:
        self.code = code
        self.current = current
        self.target = target
        self.remediation = remediation
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "current_state": self.current.value,
            "target_state": self.target.value,
            "message": str(self),
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class PublicationTransitionGuards:
    """External facts rechecked by the workflow service before transition."""

    bindings_current: bool = True
    approvals_satisfied: bool = False
    decision_invalidated: bool = False
    reconciliation_recorded: bool = False


@dataclass(frozen=True, slots=True)
class PublicationTransition:
    snapshot: PublicationStateSnapshot
    event: PublicationEvent


def allowed_publication_transitions(state: PublicationState) -> frozenset[PublicationState]:
    return _TRANSITIONS[PublicationState(state)]


def transition_publication_state(
    plan: PublicationPlan,
    snapshot: PublicationStateSnapshot,
    target: PublicationState,
    *,
    expected_state_version: int,
    actor: PublicationActor,
    timestamp: str,
    guards: PublicationTransitionGuards | None = None,
    decision_refs: tuple[PublicationRecordReference, ...] | None = None,
    failure: PublicationFailure | None = None,
    outputs: tuple[PublicationOutputReference, ...] | None = None,
    event_type: str | None = None,
) -> PublicationTransition:
    """Apply one legal CAS transition and emit its immutable history event."""
    target = PublicationState(target)
    guards = guards or PublicationTransitionGuards()
    now = normalize_rfc3339(timestamp)
    _same_plan(plan, snapshot, target)
    if expected_state_version != snapshot.state_version:
        _raise(
            PublicationTransitionErrorCode.STALE,
            snapshot,
            target,
            "publication state version changed before transition",
            "Reload the workflow snapshot and retry against its current state_version.",
        )
    if snapshot.terminal:
        _raise(
            PublicationTransitionErrorCode.TERMINAL,
            snapshot,
            target,
            f"publication state {snapshot.state.value!r} is terminal",
            "Create a new publication plan for further work.",
        )
    if _timestamp(now) < _timestamp(snapshot.updated_at):
        _raise(
            PublicationTransitionErrorCode.STALE,
            snapshot,
            target,
            "publication transition timestamp precedes the current snapshot",
            "Retry with a timestamp at or after the snapshot updated_at value.",
        )

    expired = _timestamp(now) >= _timestamp(plan.expires_at)
    if expired and target != PublicationState.EXPIRED:
        _raise(
            PublicationTransitionErrorCode.EXPIRED,
            snapshot,
            target,
            "publication plan expired before transition",
            "Create a fresh plan from current source, validation, and policy inputs.",
        )
    if target == PublicationState.EXPIRED and not expired:
        _raise(
            PublicationTransitionErrorCode.GUARD,
            snapshot,
            target,
            "publication plan has not reached its expiry",
            "Wait until expires_at or cancel/supersede the plan explicitly.",
        )
    if target not in _TRANSITIONS[snapshot.state]:
        _raise(
            PublicationTransitionErrorCode.ILLEGAL,
            snapshot,
            target,
            f"illegal publication transition {snapshot.state.value} -> {target.value}",
            "Use a state allowed by the publication transition table.",
        )

    _check_guards(plan, snapshot, target, guards, failure)
    if target == PublicationState.FAILED and failure is None:
        _raise(
            PublicationTransitionErrorCode.GUARD,
            snapshot,
            target,
            "failed publication transition requires failure details",
            "Provide a typed failure disposition, code, safe message, and remediation.",
        )
    if target != PublicationState.FAILED and failure is not None:
        _raise(
            PublicationTransitionErrorCode.GUARD,
            snapshot,
            target,
            "failure details are only valid for the failed state",
            "Remove failure details or transition to failed.",
        )

    next_snapshot = PublicationStateSnapshot(
        plan_id=snapshot.plan_id,
        plan_digest=snapshot.plan_digest,
        state_version=snapshot.state_version + 1,
        state=target,
        decision_refs=decision_refs if decision_refs is not None else snapshot.decision_refs,
        failure=failure,
        outputs=outputs if outputs is not None else snapshot.outputs,
        created_at=snapshot.created_at,
        updated_at=now,
        extensions=snapshot.extensions,
    )
    event = PublicationEvent.create(
        plan_digest=plan.plan_digest,
        state_version=next_snapshot.state_version,
        from_state=snapshot.state,
        to_state=target,
        event_type=event_type or f"state.{target.value}",
        actor=actor,
        correlation_id=plan.correlation_id,
        timestamp=now,
        failure=failure,
        outputs=next_snapshot.outputs,
    )
    return PublicationTransition(snapshot=next_snapshot, event=event)


def _check_guards(
    plan: PublicationPlan,
    snapshot: PublicationStateSnapshot,
    target: PublicationState,
    guards: PublicationTransitionGuards,
    failure: PublicationFailure | None,
) -> None:
    approvals_required = plan.approval_requirements.required_count > 0
    if (
        target
        in {
            PublicationState.REVIEWABLE,
            PublicationState.APPROVED,
            PublicationState.EXECUTING,
        }
        and plan.validation.error_count
    ):
        _raise(
            PublicationTransitionErrorCode.GUARD,
            snapshot,
            target,
            "publication validation contains blocking errors",
            "Resolve validation errors and create a fresh bound plan.",
        )
    if (
        target == PublicationState.APPROVED
        and approvals_required
        and not guards.approvals_satisfied
    ):
        _raise(
            PublicationTransitionErrorCode.GUARD,
            snapshot,
            target,
            "publication approval requirements are not satisfied",
            "Collect eligible decisions bound to this plan and policy digest.",
        )
    if target == PublicationState.EXECUTING:
        if not guards.bindings_current:
            _raise(
                PublicationTransitionErrorCode.STALE,
                snapshot,
                target,
                "publication source, validation, configuration, or policy binding is stale",
                "Create or validate a fresh plan before execution.",
            )
        if approvals_required and not guards.approvals_satisfied:
            _raise(
                PublicationTransitionErrorCode.GUARD,
                snapshot,
                target,
                "publication approvals are not valid at execution",
                "Reauthorize current decisions against the bound policy.",
            )
    if (
        snapshot.state == PublicationState.APPROVED
        and target == PublicationState.AWAITING_APPROVAL
        and not guards.decision_invalidated
    ):
        _raise(
            PublicationTransitionErrorCode.GUARD,
            snapshot,
            target,
            "approved publication can await approval only after decision invalidation",
            "Record the revoked or expired decision before changing state.",
        )
    if snapshot.state != PublicationState.FAILED:
        return
    current_failure = snapshot.failure
    if current_failure is None:
        _raise(
            PublicationTransitionErrorCode.GUARD,
            snapshot,
            target,
            "failed publication snapshot has no failure disposition",
            "Repair the persisted snapshot before retry.",
        )
    if target == PublicationState.VALIDATING and not (
        current_failure.disposition == PublicationFailureDisposition.RETRYABLE
        and current_failure.retry_target == PublicationState.VALIDATING
    ):
        _retry_guard(snapshot, target, "validation")
    if target == PublicationState.APPROVED and not (
        current_failure.disposition == PublicationFailureDisposition.RETRYABLE
        and current_failure.retry_target == PublicationState.APPROVED
        and guards.bindings_current
    ):
        _retry_guard(snapshot, target, "approval")
    if target == PublicationState.EXECUTING and not (
        current_failure.disposition == PublicationFailureDisposition.RECONCILIATION_REQUIRED
        and current_failure.retry_target == PublicationState.EXECUTING
        and guards.reconciliation_recorded
        and guards.bindings_current
    ):
        _retry_guard(snapshot, target, "execution reconciliation")
    if failure is not None:
        _raise(
            PublicationTransitionErrorCode.GUARD,
            snapshot,
            target,
            "retry transitions cannot carry a replacement failure",
            "Clear the resolved failure when leaving failed state.",
        )


def _same_plan(
    plan: PublicationPlan,
    snapshot: PublicationStateSnapshot,
    target: PublicationState,
) -> None:
    if snapshot.plan_id != plan.plan_id or snapshot.plan_digest != plan.plan_digest:
        _raise(
            PublicationTransitionErrorCode.STALE,
            snapshot,
            target,
            "publication snapshot does not belong to the supplied plan",
            "Load the plan identified by the snapshot before transitioning.",
        )


def _retry_guard(
    snapshot: PublicationStateSnapshot,
    target: PublicationState,
    label: str,
) -> None:
    _raise(
        PublicationTransitionErrorCode.GUARD,
        snapshot,
        target,
        f"failed publication is not eligible for {label} retry",
        "Follow the failure remediation or supersede the plan.",
    )


def _raise(
    code: PublicationTransitionErrorCode,
    snapshot: PublicationStateSnapshot,
    target: PublicationState,
    message: str,
    remediation: str,
) -> Never:
    raise PublicationTransitionError(code, snapshot.state, target, message, remediation)


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(normalize_rfc3339(value).replace("Z", "+00:00"))
