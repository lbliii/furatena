"""Immutable publication records, digest, redaction, and envelope contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

import pytest

from furatena.catalog.publication_contracts import (
    PublicationDecision,
    PublicationDecisionKind,
    PublicationEvent,
    PublicationFailure,
    PublicationFailureDisposition,
    PublicationOutputIntent,
    PublicationOutputReference,
    PublicationPlan,
    PublicationState,
    PublicationStateSnapshot,
    canonical_json_bytes,
    publication_record_envelope,
    publication_record_from_envelope,
)
from tests.publication_support import digest, sample_actor, sample_plan


def test_plan_digest_is_deterministic_and_excludes_transport_metadata() -> None:
    outputs = (
        PublicationOutputIntent("deployment", "developer-docs", "production"),
        PublicationOutputIntent("repository_change", "product-docs", "review"),
    )
    first = sample_plan(outputs=outputs)
    replay = sample_plan(
        correlation_id="different-correlation",
        idempotency_key="different-idempotency-key",
        outputs=tuple(reversed(outputs)),
    )

    assert first.plan_digest == replay.plan_digest
    assert first.plan_id == replay.plan_id
    assert first.correlation_id != replay.correlation_id
    assert canonical_json_bytes(first.approval_payload()) == canonical_json_bytes(
        replay.approval_payload()
    )

    changed = sample_plan(required_count=2)
    assert changed.plan_digest != first.plan_digest


def test_plan_round_trip_rejects_tampering_and_unknown_versions() -> None:
    plan = sample_plan()
    payload = plan.to_dict()

    assert PublicationPlan.from_dict(payload) == plan

    tampered = {**payload, "expires_at": "2036-01-01T00:00:00Z"}
    with pytest.raises(ValueError, match="plan_digest"):
        PublicationPlan.from_dict(tampered)

    unsupported = {**payload, "schema_version": 2}
    with pytest.raises(ValueError, match="unsupported publication schema_version"):
        PublicationPlan.from_dict(unsupported)


def test_plan_canonicalizes_paths_and_rejects_inconsistent_bindings() -> None:
    plan = sample_plan()
    second_path = "docs/appendix.md"
    changeset = replace(
        plan.changeset,
        paths=(plan.changeset.paths[0], second_path),
    )
    reversed_changeset = replace(changeset, paths=tuple(reversed(changeset.paths)))

    assert changeset.paths == reversed_changeset.paths

    with pytest.raises(ValueError, match="approval policy"):
        replace(
            plan,
            approval_requirements=replace(
                plan.approval_requirements,
                policy_digest=digest("different-policy"),
            ),
        )

    with pytest.raises(ValueError, match="bound snapshot"):
        replace(
            plan,
            validation=replace(plan.validation, snapshot_id="different-snapshot"),
        )


def test_public_and_audit_plan_projections_redact_private_content() -> None:
    plan = sample_plan()

    trusted = canonical_json_bytes(plan.to_dict("trusted"))
    audit = canonical_json_bytes(plan.to_dict("audit"))
    public = canonical_json_bytes(plan.to_dict("public"))

    assert b"visibility: public" in trusted
    assert b"docs/guide.md" in trusted
    for projection in (audit, public):
        assert b"visibility: public" not in projection
        assert b"docs/guide.md" not in projection
    assert b"publisher@example.com" in audit
    assert b"publisher@example.com" not in public
    assert b"acme" not in public


def test_decision_event_and_snapshot_are_deterministic_and_round_trip() -> None:
    plan = sample_plan()
    actor = sample_actor("reviewer@example.com")
    decision = PublicationDecision.create(
        plan_digest=plan.plan_digest,
        policy_digest=plan.bindings.policy_digest,
        actor=actor,
        decision=PublicationDecisionKind.APPROVE,
        timestamp="2030-01-01T00:00:00Z",
    )
    failure = PublicationFailure(
        disposition=PublicationFailureDisposition.RETRYABLE,
        code="provider.timeout",
        safe_message="Repository provider timed out.",
        remediation="Retry after checking provider status.",
        retry_target=PublicationState.APPROVED,
    )
    output = PublicationOutputReference(
        kind="review",
        identifier="review-17",
        status="open",
        revision="abc123",
        url="https://provider.invalid/private/review/17",
    )
    event = PublicationEvent.create(
        plan_digest=plan.plan_digest,
        state_version=4,
        from_state=PublicationState.EXECUTING,
        to_state=PublicationState.FAILED,
        event_type="execution.failed",
        actor=actor,
        correlation_id=plan.correlation_id,
        timestamp="2030-01-01T00:01:00Z",
        failure=failure,
        outputs=(output,),
    )
    snapshot = PublicationStateSnapshot(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        state_version=4,
        state=PublicationState.FAILED,
        decision_refs=(),
        failure=failure,
        outputs=(output,),
        created_at="2030-01-01T00:00:00Z",
        updated_at="2030-01-01T00:01:00Z",
    )

    assert PublicationDecision.from_dict(decision.to_dict()) == decision
    assert PublicationEvent.from_dict(event.to_dict()) == event
    assert PublicationStateSnapshot.from_dict(snapshot.to_dict()) == snapshot
    assert decision.decision_id.startswith("decision-")
    assert event.event_id.startswith("event-")
    assert snapshot.terminal is False
    assert "private/review" not in str(event.to_dict("audit"))


@pytest.mark.parametrize("transport", ["cli", "http", "mcp"])
def test_transport_envelopes_preserve_record_bytes(
    transport: Literal["cli", "http", "mcp"],
) -> None:
    plan = sample_plan()
    snapshot = PublicationStateSnapshot.initial(plan, timestamp="2030-01-01T00:00:00Z")

    for record in (plan, snapshot):
        envelope = publication_record_envelope(record, transport=transport)
        loaded = publication_record_from_envelope(envelope, transport=transport)
        assert loaded == record
        assert canonical_json_bytes(loaded.to_dict()) == canonical_json_bytes(record.to_dict())


def test_diff_digest_covers_exact_utf8_bytes() -> None:
    plan = sample_plan()
    unicode_diff = plan.changeset.unified_diff.replace("public", "públic")
    with pytest.raises(ValueError, match="diff_sha256"):
        replace(plan.changeset, unified_diff=unicode_diff)
