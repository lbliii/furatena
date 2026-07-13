"""Revision-bound publication approval, waiver, and audit behavior."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from furatena.catalog.audit_store import InMemoryAuditStore
from furatena.catalog.publication_approvals import (
    InMemoryPublicationApprovalStore,
    JsonDirectoryPublicationApprovalStore,
    PublicationApprovalError,
    PublicationApprovalErrorCode,
    PublicationApprovalRecord,
    PublicationApprovalScope,
    PublicationApprovalService,
    PublicationApprovalTransportAdapter,
)
from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationDecisionKind,
    PublicationPlan,
)
from tests.publication_support import sample_actor, sample_plan


def test_record_binds_policy_scope_identity_and_round_trips() -> None:
    plan = sample_plan()
    service = _service()
    record = service.decide(
        plan,
        actor=_reviewer("one@example.com"),
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="approve-1",
    )

    assert PublicationApprovalRecord.from_dict(record.to_dict()) == record
    assert record.decision.plan_digest == plan.plan_digest
    assert record.decision.policy_digest == plan.bindings.policy_digest
    assert record.policy_version == plan.bindings.policy_version
    assert record.decision.actor.identity_source == "test-oidc"
    assert record.scope == PublicationApprovalScope.from_plan(plan)
    assert record.scope.paths == plan.changeset.paths
    assert record.scope.environments == ("production", "review")
    public = str(record.to_dict("public"))
    assert plan.identity.source_path not in public
    assert plan.identity.tenant not in public
    assert record.decision.actor.actor not in public


def test_no_self_approval_and_distinct_actor_separation() -> None:
    plan = sample_plan(required_count=2)
    service = _service()

    with pytest.raises(PublicationApprovalError) as caught:
        service.decide(
            plan,
            actor=plan.creator,
            decision=PublicationDecisionKind.APPROVE,
            idempotency_key="self-1",
        )
    assert caught.value.code == PublicationApprovalErrorCode.SELF_APPROVAL

    first = _reviewer("one@example.com")
    service.decide(
        plan,
        actor=first,
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="approve-1",
    )
    service.decide(
        plan,
        actor=first,
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="approve-duplicate-actor",
    )
    service.decide(
        plan,
        actor=_reviewer("waiver@example.com"),
        decision=PublicationDecisionKind.WAIVE_WARNING,
        idempotency_key="waive-1",
        reason="Known link migration warning.",
        diagnostic_ids=plan.validation.waivable_warning_ids,
        expires_at="2031-01-01T00:00:00Z",
    )

    denied = service.evaluate(plan, now="2030-01-01T00:00:00Z")
    assert denied.satisfied is False
    assert len(denied.approval_record_ids) == 1
    assert "insufficient_approvals" in denied.reason_codes

    service.decide(
        plan,
        actor=_reviewer("two@example.com"),
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="approve-2",
    )
    allowed = service.evaluate(plan, now="2030-01-01T00:00:00Z")
    assert allowed.satisfied is True
    assert len(allowed.approval_record_ids) == 2


def test_team_owned_policy_rejects_non_member_and_accepts_member() -> None:
    plan = _plan_with_requirements(
        sample_plan(required_count=1),
        eligible_roles=(),
        eligible_teams=("security",),
    )
    service = _service()
    with pytest.raises(PublicationApprovalError) as caught:
        service.decide(
            plan,
            actor=_reviewer("docs@example.com"),
            decision=PublicationDecisionKind.APPROVE,
            idempotency_key="team-denied",
        )
    assert caught.value.code == PublicationApprovalErrorCode.INELIGIBLE

    security = PublicationActor(
        actor="security@example.com",
        identity_source="test-oidc",
        roles=(),
        teams=("security",),
    )
    accepted = service.decide(
        plan,
        actor=security,
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="team-approved",
    )
    assert accepted.decision.actor == security


def test_waivers_are_narrow_scoped_and_expire() -> None:
    plan = sample_plan(required_count=0)
    service = _service()
    actor = _reviewer("waiver@example.com")

    with pytest.raises(PublicationApprovalError) as caught:
        service.decide(
            plan,
            actor=actor,
            decision=PublicationDecisionKind.WAIVE_WARNING,
            idempotency_key="waive-invalid",
            reason="Too broad.",
            diagnostic_ids=("warning:not-listed",),
            expires_at="2031-01-01T00:00:00Z",
        )
    assert caught.value.code == PublicationApprovalErrorCode.INVALID_WAIVER

    waiver = service.decide(
        plan,
        actor=actor,
        decision=PublicationDecisionKind.WAIVE_WARNING,
        idempotency_key="waive-valid",
        reason="Accepted migration warning.",
        diagnostic_ids=plan.validation.waivable_warning_ids,
        expires_at="2031-01-01T00:00:00Z",
    )
    active = service.evaluate(plan, now="2030-06-01T00:00:00Z")
    expired = service.evaluate(plan, now="2032-01-01T00:00:00Z")

    assert active.satisfied is True
    assert active.waiver_record_ids == (waiver.record_id,)
    assert expired.satisfied is False
    assert waiver.record_id in expired.invalidated_record_ids
    assert expired.unwaived_warning_ids == plan.validation.waivable_warning_ids


def test_revoke_and_blocking_decisions_fold_deterministically() -> None:
    plan = sample_plan(required_count=1)
    service = _service()
    approval = service.decide(
        plan,
        actor=_reviewer("one@example.com"),
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="approve-1",
    )
    waiver = service.decide(
        plan,
        actor=_reviewer("waiver@example.com"),
        decision=PublicationDecisionKind.WAIVE_WARNING,
        idempotency_key="waive-1",
        reason="Known warning.",
        diagnostic_ids=plan.validation.waivable_warning_ids,
        expires_at="2031-01-01T00:00:00Z",
    )
    rejection = service.decide(
        plan,
        actor=_reviewer("two@example.com"),
        decision=PublicationDecisionKind.REJECT,
        idempotency_key="reject-1",
        reason="Content is not ready.",
    )
    blocked = service.evaluate(plan, now="2030-01-01T00:00:00Z")
    assert blocked.satisfied is False
    assert blocked.blocking_record_ids == (rejection.record_id,)

    service.decide(
        plan,
        actor=_reviewer("admin@example.com"),
        decision=PublicationDecisionKind.REVOKE,
        idempotency_key="revoke-rejection",
        reason="Concerns resolved.",
        target_record_ids=(rejection.record_id,),
    )
    allowed = service.evaluate(plan, now="2030-01-01T00:00:00Z")
    assert allowed.satisfied is True
    assert rejection.record_id in allowed.invalidated_record_ids
    assert approval.record_id in allowed.approval_record_ids
    assert waiver.record_id in allowed.waiver_record_ids


def test_drift_invalidates_prior_records_for_same_resource() -> None:
    store = InMemoryPublicationApprovalStore()
    service = _service(store=store)
    original = sample_plan(required_count=1)
    record = service.decide(
        original,
        actor=_reviewer("one@example.com"),
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="approve-original",
    )
    drifted = sample_plan(
        required_count=1,
        policy_version="policy-v3",
        policy_digest_value=None,
    )

    evaluation = service.evaluate(drifted, now="2030-01-01T00:00:00Z")

    assert evaluation.satisfied is False
    assert record.record_id in evaluation.invalidated_record_ids
    assert "invalidated_records" in evaluation.reason_codes


def test_emergency_override_requires_current_elevated_authorization() -> None:
    plan = sample_plan(required_count=2)
    denied_service = _service(
        authorizer=lambda _plan, _actor, kind: kind != PublicationDecisionKind.EMERGENCY_OVERRIDE
    )
    with pytest.raises(PublicationApprovalError) as caught:
        denied_service.decide(
            plan,
            actor=_reviewer("admin@example.com"),
            decision=PublicationDecisionKind.EMERGENCY_OVERRIDE,
            idempotency_key="override-denied",
            reason="Incident mitigation.",
        )
    assert caught.value.code == PublicationApprovalErrorCode.AUTHORIZATION

    service = _service()
    override = service.decide(
        plan,
        actor=_reviewer("admin@example.com"),
        decision=PublicationDecisionKind.EMERGENCY_OVERRIDE,
        idempotency_key="override-1",
        reason="Incident mitigation approved by on-call commander.",
    )
    evaluation = service.evaluate(plan, now="2030-01-01T00:00:00Z")
    assert evaluation.satisfied is True
    assert evaluation.override_applied is True
    assert evaluation.reason_codes == ("emergency_override",)
    assert override.record_id not in evaluation.invalidated_record_ids


def test_evaluation_reauthorizes_approver_against_current_policy() -> None:
    permitted = {"one@example.com"}
    service = _service(authorizer=lambda _plan, actor, _kind: actor.actor in permitted)
    plan = sample_plan(required_count=1)
    approval = service.decide(
        plan,
        actor=_reviewer("one@example.com"),
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="approve-current",
    )
    permitted.clear()

    evaluation = service.evaluate(plan, now="2030-01-01T00:00:00Z")

    assert evaluation.satisfied is False
    assert approval.record_id in evaluation.invalidated_record_ids


def test_transport_adapters_ignore_untrusted_actor_fields() -> None:
    plan = sample_plan()
    service = _service()
    trusted = _reviewer("trusted@example.com")
    command = {
        "decision": "approve",
        "idempotency_key": "transport-1",
        "actor": sample_actor("attacker@example.com").to_dict(),
    }
    responses = [
        PublicationApprovalTransportAdapter(transport, service).decide(
            plan, command, trusted_actor=trusted
        )
        for transport in ("browser", "cli", "mcp")
    ]
    assert responses[0] == responses[1] == responses[2]
    decision_payload = responses[0]["decision"]
    assert isinstance(decision_payload, Mapping)
    typed_decision = cast(Mapping[str, object], decision_payload)
    actor_payload = typed_decision["actor"]
    assert isinstance(actor_payload, Mapping)
    assert cast(Mapping[str, object], actor_payload)["actor"] == trusted.actor


def test_audit_events_are_correlated_and_redact_reason_and_source() -> None:
    audit = InMemoryAuditStore(clock=lambda: 1_893_456_000.0)
    service = _service(audit=audit)
    plan = sample_plan()
    service.decide(
        plan,
        actor=_reviewer("one@example.com"),
        decision=PublicationDecisionKind.REJECT,
        idempotency_key="reject-secret",
        reason="Private source contains secret-token-value.",
    )
    service.evaluate(plan, now="2030-01-01T00:00:00Z")

    exported = str(audit.export())
    assert "publication.decision.reject" in exported
    assert "publication.approval.evaluate" in exported
    assert plan.correlation_id in exported
    assert "secret-token-value" not in exported
    assert plan.changeset.unified_diff not in exported


@pytest.mark.parametrize("kind", ["memory", "json"])
def test_idempotency_survives_restart_and_rejects_changed_input(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "approvals"
    store = (
        InMemoryPublicationApprovalStore()
        if kind == "memory"
        else JsonDirectoryPublicationApprovalStore(root)
    )
    plan = sample_plan()
    first_service = _service(store=store)
    first = first_service.decide(
        plan,
        actor=_reviewer("one@example.com"),
        decision=PublicationDecisionKind.APPROVE,
        idempotency_key="approve-1",
    )
    restarted_store = store if kind == "memory" else JsonDirectoryPublicationApprovalStore(root)
    restarted = _service(store=restarted_store)
    assert (
        restarted.decide(
            plan,
            actor=_reviewer("one@example.com"),
            decision=PublicationDecisionKind.APPROVE,
            idempotency_key="approve-1",
        )
        == first
    )
    with pytest.raises(PublicationApprovalError) as caught:
        restarted.decide(
            plan,
            actor=_reviewer("two@example.com"),
            decision=PublicationDecisionKind.APPROVE,
            idempotency_key="approve-1",
        )
    assert caught.value.code == PublicationApprovalErrorCode.IDEMPOTENCY_CONFLICT
    if kind == "json":
        assert (root.stat().st_mode & 0o777) == 0o700
        assert ((root / "records" / f"{first.record_id}.json").stat().st_mode & 0o777) == 0o600


def test_concurrent_different_input_same_key_persists_exactly_one_record(
    tmp_path: Path,
) -> None:
    root = tmp_path / "approvals"
    services = [
        _service(store=JsonDirectoryPublicationApprovalStore(root)),
        _service(store=JsonDirectoryPublicationApprovalStore(root)),
    ]
    plan = sample_plan()
    barrier = threading.Barrier(2)
    accepted: list[PublicationApprovalRecord] = []
    errors: list[PublicationApprovalError] = []

    def decide(index: int) -> None:
        barrier.wait()
        try:
            accepted.append(
                services[index].decide(
                    plan,
                    actor=_reviewer(f"reviewer-{index}@example.com"),
                    decision=PublicationDecisionKind.APPROVE,
                    idempotency_key="shared-key",
                )
            )
        except PublicationApprovalError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=decide, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    restarted = JsonDirectoryPublicationApprovalStore(root)
    assert len(accepted) == 1
    assert len(errors) == 1
    assert errors[0].code == PublicationApprovalErrorCode.IDEMPOTENCY_CONFLICT
    assert restarted.list_records() == (accepted[0],)


def _reviewer(actor: str) -> PublicationActor:
    return PublicationActor(
        actor=actor,
        identity_source="test-oidc",
        roles=("publisher",),
        teams=("docs",),
    )


def _plan_with_requirements(
    plan: PublicationPlan,
    *,
    eligible_roles: tuple[str, ...],
    eligible_teams: tuple[str, ...],
) -> PublicationPlan:
    requirements = replace(
        plan.approval_requirements,
        eligible_roles=eligible_roles,
        eligible_teams=eligible_teams,
    )
    return PublicationPlan.create(
        correlation_id=plan.correlation_id,
        idempotency_key=plan.idempotency_key,
        creator=plan.creator,
        expires_at=plan.expires_at,
        identity=plan.identity,
        request=plan.request,
        bindings=plan.bindings,
        changeset=plan.changeset,
        validation=plan.validation,
        impact=plan.impact,
        approval_requirements=requirements,
        intended_outputs=plan.intended_outputs,
        extensions=plan.extensions,
    )


def _service(
    *,
    store: InMemoryPublicationApprovalStore | JsonDirectoryPublicationApprovalStore | None = None,
    audit: InMemoryAuditStore | None = None,
    authorizer: Callable[[PublicationPlan, PublicationActor, PublicationDecisionKind], bool]
    | None = None,
) -> PublicationApprovalService:
    return PublicationApprovalService(
        store=store or InMemoryPublicationApprovalStore(),
        audit_store=audit or InMemoryAuditStore(clock=lambda: 1_893_456_000.0),
        authorizer=authorizer or (lambda _plan, _actor, _decision: True),
        clock=lambda: "2030-01-01T00:00:00Z",
    )
