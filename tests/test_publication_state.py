"""Publication workflow transition table and fail-closed guards."""

from __future__ import annotations

from dataclasses import replace

import pytest

from furatena.catalog.publication_contracts import (
    PublicationFailure,
    PublicationFailureDisposition,
    PublicationState,
    PublicationStateSnapshot,
)
from furatena.catalog.publication_state import (
    PublicationTransitionError,
    PublicationTransitionErrorCode,
    PublicationTransitionGuards,
    allowed_publication_transitions,
    transition_publication_state,
)
from tests.publication_support import sample_actor, sample_plan


def _snapshot(
    state: PublicationState,
    *,
    version: int = 2,
    failure: PublicationFailure | None = None,
) -> PublicationStateSnapshot:
    plan = sample_plan()
    return PublicationStateSnapshot(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        state_version=version,
        state=state,
        decision_refs=(),
        failure=failure,
        outputs=(),
        created_at="2030-01-01T00:00:00Z",
        updated_at="2030-01-01T00:01:00Z",
    )


def test_transition_table_has_exact_required_states() -> None:
    assert allowed_publication_transitions(PublicationState.PROPOSED) == {
        PublicationState.VALIDATING,
        PublicationState.CANCELLED,
        PublicationState.SUPERSEDED,
        PublicationState.EXPIRED,
    }
    assert allowed_publication_transitions(PublicationState.EXECUTING) == {
        PublicationState.APPLIED,
        PublicationState.FAILED,
    }
    for state in (
        PublicationState.APPLIED,
        PublicationState.EXPIRED,
        PublicationState.CANCELLED,
        PublicationState.SUPERSEDED,
    ):
        assert allowed_publication_transitions(state) == frozenset()


def test_happy_path_emits_monotonic_snapshots_and_events() -> None:
    plan = sample_plan()
    actor = sample_actor()
    snapshot = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")
    path = (
        PublicationState.VALIDATING,
        PublicationState.REVIEWABLE,
        PublicationState.AWAITING_APPROVAL,
        PublicationState.APPROVED,
        PublicationState.EXECUTING,
        PublicationState.APPLIED,
    )

    for target in path:
        transition = transition_publication_state(
            plan,
            snapshot,
            target,
            expected_state_version=snapshot.state_version,
            actor=actor,
            timestamp=f"2030-01-01T00:0{snapshot.state_version}:00Z",
            guards=PublicationTransitionGuards(
                approvals_satisfied=target
                in {
                    PublicationState.APPROVED,
                    PublicationState.EXECUTING,
                }
            ),
        )
        assert transition.snapshot.state_version == snapshot.state_version + 1
        assert transition.event.state_version == transition.snapshot.state_version
        assert transition.event.from_state == snapshot.state
        assert transition.event.to_state == target
        snapshot = transition.snapshot

    assert snapshot.state == PublicationState.APPLIED
    assert snapshot.terminal is True


def test_approval_staleness_expiry_and_terminal_guards_fail_closed() -> None:
    plan = sample_plan()
    actor = sample_actor()
    reviewable = _snapshot(PublicationState.REVIEWABLE)

    with pytest.raises(PublicationTransitionError) as missing_approval:
        transition_publication_state(
            plan,
            reviewable,
            PublicationState.APPROVED,
            expected_state_version=reviewable.state_version,
            actor=actor,
            timestamp="2030-01-01T00:02:00Z",
        )
    assert missing_approval.value.code == PublicationTransitionErrorCode.GUARD

    approved = _snapshot(PublicationState.APPROVED)
    with pytest.raises(PublicationTransitionError) as stale:
        transition_publication_state(
            plan,
            approved,
            PublicationState.EXECUTING,
            expected_state_version=approved.state_version,
            actor=actor,
            timestamp="2030-01-01T00:02:00Z",
            guards=PublicationTransitionGuards(
                bindings_current=False,
                approvals_satisfied=True,
            ),
        )
    assert stale.value.code == PublicationTransitionErrorCode.STALE

    with pytest.raises(PublicationTransitionError) as cas:
        transition_publication_state(
            plan,
            approved,
            PublicationState.EXECUTING,
            expected_state_version=approved.state_version - 1,
            actor=actor,
            timestamp="2030-01-01T00:02:00Z",
            guards=PublicationTransitionGuards(approvals_satisfied=True),
        )
    assert cas.value.code == PublicationTransitionErrorCode.STALE

    expired_plan = sample_plan(expires_at="2030-01-01T00:01:00Z")
    expired_snapshot = replace(
        approved,
        plan_id=expired_plan.plan_id,
        plan_digest=expired_plan.plan_digest,
    )
    with pytest.raises(PublicationTransitionError) as expired:
        transition_publication_state(
            expired_plan,
            expired_snapshot,
            PublicationState.EXECUTING,
            expected_state_version=expired_snapshot.state_version,
            actor=actor,
            timestamp="2030-01-01T00:02:00Z",
            guards=PublicationTransitionGuards(approvals_satisfied=True),
        )
    assert expired.value.code == PublicationTransitionErrorCode.EXPIRED

    terminal = _snapshot(PublicationState.APPLIED)
    with pytest.raises(PublicationTransitionError) as terminal_error:
        transition_publication_state(
            plan,
            terminal,
            PublicationState.FAILED,
            expected_state_version=terminal.state_version,
            actor=actor,
            timestamp="2030-01-01T00:02:00Z",
        )
    assert terminal_error.value.code == PublicationTransitionErrorCode.TERMINAL

    with pytest.raises(PublicationTransitionError) as old_timestamp:
        transition_publication_state(
            plan,
            approved,
            PublicationState.EXECUTING,
            expected_state_version=approved.state_version,
            actor=actor,
            timestamp="2029-12-31T23:59:00Z",
            guards=PublicationTransitionGuards(approvals_satisfied=True),
        )
    assert old_timestamp.value.code == PublicationTransitionErrorCode.STALE


def test_blocking_validation_errors_cannot_reach_review_or_execution() -> None:
    invalid_plan = sample_plan(validation_error_count=1)
    actor = sample_actor()
    validating = replace(
        _snapshot(PublicationState.VALIDATING),
        plan_id=invalid_plan.plan_id,
        plan_digest=invalid_plan.plan_digest,
    )

    with pytest.raises(PublicationTransitionError) as blocked:
        transition_publication_state(
            invalid_plan,
            validating,
            PublicationState.REVIEWABLE,
            expected_state_version=validating.state_version,
            actor=actor,
            timestamp="2030-01-01T00:02:00Z",
        )
    assert blocked.value.code == PublicationTransitionErrorCode.GUARD


def test_failed_retry_and_reconciliation_require_exact_disposition() -> None:
    plan = sample_plan()
    actor = sample_actor()
    retryable = PublicationFailure(
        disposition=PublicationFailureDisposition.RETRYABLE,
        code="validation.unavailable",
        safe_message="Validation service unavailable.",
        remediation="Retry validation.",
        retry_target=PublicationState.VALIDATING,
    )
    failed = _snapshot(PublicationState.FAILED, failure=retryable)

    retried = transition_publication_state(
        plan,
        failed,
        PublicationState.VALIDATING,
        expected_state_version=failed.state_version,
        actor=actor,
        timestamp="2030-01-01T00:02:00Z",
    )
    assert retried.snapshot.failure is None

    reconciliation = replace(
        retryable,
        disposition=PublicationFailureDisposition.RECONCILIATION_REQUIRED,
        retry_target=PublicationState.EXECUTING,
    )
    reconcile_failed = _snapshot(PublicationState.FAILED, failure=reconciliation)
    with pytest.raises(PublicationTransitionError):
        transition_publication_state(
            plan,
            reconcile_failed,
            PublicationState.EXECUTING,
            expected_state_version=reconcile_failed.state_version,
            actor=actor,
            timestamp="2030-01-01T00:02:00Z",
        )

    reconciled = transition_publication_state(
        plan,
        reconcile_failed,
        PublicationState.EXECUTING,
        expected_state_version=reconcile_failed.state_version,
        actor=actor,
        timestamp="2030-01-01T00:02:00Z",
        guards=PublicationTransitionGuards(
            approvals_satisfied=True,
            reconciliation_recorded=True,
        ),
    )
    assert reconciled.snapshot.state == PublicationState.EXECUTING
