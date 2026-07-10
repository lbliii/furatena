"""Provider-neutral publication profile and change safety contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from furatena.catalog.publication_contracts import (
    PublicationFailureDisposition,
    PublicationPlan,
)
from furatena.catalog.publication_provider import (
    ProviderBundleSignature,
    ProviderContractError,
    ProviderFailure,
    ProviderOperation,
    ProviderOutcome,
    ProviderPathChange,
    ProviderPathChangeKind,
    ProviderProtectionState,
    ProviderReconciliationState,
    ProviderReviewState,
    PublicationChangeBundle,
    PublicationChangeProvider,
    PublicationChangeRequest,
    PublicationChangeResult,
    PublicationProfile,
    PublicationProfileConfig,
    provider_operations_for,
    provider_record_envelope,
    provider_record_from_envelope,
    validate_change_bundle,
    validate_change_request,
    validate_provider_result,
    validate_repository_inspection,
)
from tests.provider_support import (
    sample_attribution,
    sample_inspection,
    sample_profile,
    sample_provider_request,
)
from tests.publication_support import sample_actor, sample_plan

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "publication-provider" / "v1"


def test_profiles_are_explicit_deterministic_and_round_trip() -> None:
    for profile in PublicationProfile:
        config = sample_profile(profile)
        assert config.profile == profile
        assert config.profile_digest.startswith("sha256:")
        assert PublicationProfileConfig.from_dict(config.to_dict()) == config
        assert provider_operations_for(profile)

    with pytest.raises(ValueError, match="branch_template"):
        PublicationProfileConfig(
            profile=PublicationProfile.PULL_REQUEST,
            provider_id="git",
            repository_id="repo",
        )
    with pytest.raises(ValueError, match="external_target"):
        PublicationProfileConfig(
            profile=PublicationProfile.EXTERNAL,
            provider_id="external",
            repository_id="repo",
        )


def test_request_binds_plan_paths_revisions_and_idempotent_change_identity() -> None:
    plan = sample_plan()
    profile = sample_profile(PublicationProfile.PULL_REQUEST)
    first = sample_provider_request()
    replay = sample_provider_request(idempotency_key="different-retry-key")

    validate_change_request(plan, profile, first)
    assert first.request_digest == replay.request_digest
    assert first.change_id == replay.change_id
    assert first.idempotency_key != replay.idempotency_key
    assert first.approved_paths == plan.changeset.paths
    assert PublicationChangeRequest.from_dict(first.to_dict()) == first
    assert "publisher@example.com" not in str(first.to_dict("audit"))

    with pytest.raises(ValueError, match="request_digest"):
        replace(first, request_digest="sha256:" + "0" * 64)


def test_dry_run_reports_operations_without_effectful_request() -> None:
    request = sample_provider_request(dry_run=True)
    report = request.dry_run_report()

    assert report["approved_paths"] == list(request.approved_paths)
    assert report["base_source_revision"] == request.base_source_revision
    assert report["branch_name"] == request.branch_name
    assert report["commit"] == request.attribution.to_dict()
    assert report["provider_operations"] == [
        operation.value for operation in provider_operations_for(request.profile)
    ]

    effectful = sample_provider_request(
        operation=ProviderOperation.COMMIT,
        dry_run=True,
    )
    with pytest.raises(ProviderContractError) as error:
        validate_change_request(sample_plan(), sample_profile(request.profile), effectful)
    assert error.value.code == "request.dry_run_effect"


def test_local_only_preserves_unrelated_dirty_paths_but_rejects_overlap() -> None:
    profile = sample_profile(PublicationProfile.LOCAL_ONLY)
    request = sample_provider_request(PublicationProfile.LOCAL_ONLY)
    unrelated = sample_inspection(
        PublicationProfile.LOCAL_ONLY,
        isolated=False,
        protection=ProviderProtectionState.UNPROTECTED,
        untracked_paths=("notes/private.txt",),
    )
    validate_repository_inspection(profile, request, unrelated)

    overlap = sample_inspection(
        PublicationProfile.LOCAL_ONLY,
        isolated=False,
        protection=ProviderProtectionState.UNPROTECTED,
        unstaged_paths=request.approved_paths,
    )
    with pytest.raises(ProviderContractError) as error:
        validate_repository_inspection(profile, request, overlap)
    assert error.value.disposition == PublicationFailureDisposition.CONFLICT
    assert error.value.code == "inspection.approved_path_dirty"


@pytest.mark.parametrize(
    "field_name",
    [
        "staged_paths",
        "unstaged_paths",
        "untracked_paths",
        "ignored_paths",
        "conflicted_paths",
    ],
)
def test_every_dirty_path_class_isolated_from_approved_changes(field_name: str) -> None:
    profile = sample_profile(PublicationProfile.LOCAL_ONLY)
    request = sample_provider_request(PublicationProfile.LOCAL_ONLY)
    inspection = sample_inspection(
        PublicationProfile.LOCAL_ONLY,
        isolated=False,
        protection=ProviderProtectionState.UNPROTECTED,
        **cast(Any, {field_name: request.approved_paths}),
    )

    with pytest.raises(ProviderContractError) as error:
        validate_repository_inspection(profile, request, inspection)
    assert error.value.disposition == PublicationFailureDisposition.CONFLICT


def test_commit_and_pr_profiles_require_isolation_permissions_and_protection() -> None:
    commit_profile = sample_profile(PublicationProfile.COMMIT)
    commit_request = sample_provider_request(PublicationProfile.COMMIT)

    with pytest.raises(ProviderContractError) as not_isolated:
        validate_repository_inspection(
            commit_profile,
            commit_request,
            sample_inspection(
                PublicationProfile.COMMIT,
                isolated=False,
                protection=ProviderProtectionState.UNPROTECTED,
            ),
        )
    assert not_isolated.value.code == "inspection.not_isolated"

    with pytest.raises(ProviderContractError) as protected:
        validate_repository_inspection(
            commit_profile,
            commit_request,
            sample_inspection(PublicationProfile.COMMIT),
        )
    assert protected.value.disposition == PublicationFailureDisposition.AUTHORIZATION

    pr_profile = sample_profile(PublicationProfile.PULL_REQUEST)
    pr_request = sample_provider_request(PublicationProfile.PULL_REQUEST)
    pr_inspection = sample_inspection(PublicationProfile.PULL_REQUEST)
    validate_repository_inspection(pr_profile, pr_request, pr_inspection)
    assert pr_inspection.fork is True
    assert pr_inspection.source_repository_id != pr_inspection.repository_id

    with pytest.raises(ProviderContractError) as permission:
        validate_repository_inspection(
            pr_profile,
            pr_request,
            sample_inspection(
                PublicationProfile.PULL_REQUEST,
                permissions=(ProviderOperation.INSPECT, ProviderOperation.PREPARE),
            ),
        )
    assert permission.value.code == "inspection.permission_missing"


def test_path_change_contract_models_modify_delete_and_move_exactly() -> None:
    base = sample_plan()
    changeset = replace(
        base.changeset,
        paths=("docs/old.md", "docs/new.md"),
    )
    plan = PublicationPlan.create(
        correlation_id=base.correlation_id,
        idempotency_key=base.idempotency_key,
        creator=base.creator,
        expires_at=base.expires_at,
        identity=base.identity,
        request=base.request,
        bindings=base.bindings,
        changeset=changeset,
        validation=base.validation,
        impact=base.impact,
        approval_requirements=base.approval_requirements,
        intended_outputs=base.intended_outputs,
    )
    profile = sample_profile(PublicationProfile.COMMIT)
    request = PublicationChangeRequest.create(
        plan,
        profile,
        operation=ProviderOperation.PREPARE,
        repository_base_revision="git-base",
        attribution=sample_attribution(),
        actor=sample_actor(),
        idempotency_key="move-idem",
        path_changes=(
            ProviderPathChange(
                ProviderPathChangeKind.MOVE,
                "docs/new.md",
                previous_path="docs/old.md",
            ),
        ),
    )
    validate_change_request(plan, profile, request)

    with pytest.raises(ValueError, match="exactly approved_paths"):
        replace(
            request,
            path_changes=(ProviderPathChange(ProviderPathChangeKind.DELETE, "docs/old.md"),),
        )


def test_provider_result_rejects_unapproved_paths_and_revision_drift() -> None:
    plan = sample_plan()
    request = sample_provider_request()
    result = PublicationChangeResult.create(
        request,
        outcome=ProviderOutcome.PREPARED,
        observed_base_source_revision=request.base_source_revision,
        resulting_source_revision=request.resulting_source_revision,
        changed_paths=request.approved_paths,
        branch_name=request.branch_name,
        protection=ProviderProtectionState.PROTECTED,
        observed_at="2030-01-01T00:01:00Z",
    )
    validate_provider_result(plan, request, result)
    assert PublicationChangeResult.from_dict(result.to_dict()) == result

    extra = PublicationChangeResult.create(
        request,
        outcome=ProviderOutcome.PREPARED,
        observed_base_source_revision=request.base_source_revision,
        resulting_source_revision=request.resulting_source_revision,
        changed_paths=(*request.approved_paths, "unrelated.txt"),
        observed_at="2030-01-01T00:01:00Z",
    )
    with pytest.raises(ProviderContractError) as unapproved:
        validate_provider_result(plan, request, extra)
    assert unapproved.value.code == "result.unapproved_paths"

    drift = PublicationChangeResult.create(
        request,
        outcome=ProviderOutcome.PREPARED,
        observed_base_source_revision=request.base_source_revision,
        resulting_source_revision="different-result",
        changed_paths=request.approved_paths,
        observed_at="2030-01-01T00:01:00Z",
    )
    with pytest.raises(ProviderContractError) as revision:
        validate_provider_result(plan, request, drift)
    assert revision.value.code == "result.revision_mismatch"


def test_review_state_and_projection_do_not_imply_deployment() -> None:
    request = sample_provider_request(operation=ProviderOperation.PROPOSE_REVIEW)
    result = PublicationChangeResult.create(
        request,
        outcome=ProviderOutcome.REVIEW_OPEN,
        observed_base_source_revision=request.base_source_revision,
        resulting_source_revision=request.resulting_source_revision,
        changed_paths=request.approved_paths,
        branch_name=request.branch_name,
        commit_id="commit-abc",
        review_id="review-17",
        review_url="https://provider.invalid/private/reviews/17",
        review_state=ProviderReviewState.OPEN,
        protection=ProviderProtectionState.PROTECTED,
        observed_at="2030-01-01T00:02:00Z",
    )

    assert result.outcome == ProviderOutcome.REVIEW_OPEN
    assert result.review_state == ProviderReviewState.OPEN
    assert "private/reviews" not in str(result.to_dict("audit"))
    assert "commit-abc" in str(result.to_dict("audit"))


def test_provider_outcomes_require_commit_and_review_evidence() -> None:
    commit_request = sample_provider_request(
        PublicationProfile.COMMIT,
        operation=ProviderOperation.COMMIT,
    )
    with pytest.raises(ValueError, match="commit_id"):
        PublicationChangeResult.create(
            commit_request,
            outcome=ProviderOutcome.COMMITTED,
            observed_base_source_revision=commit_request.base_source_revision,
            resulting_source_revision=commit_request.resulting_source_revision,
            changed_paths=commit_request.approved_paths,
            observed_at="2030-01-01T00:02:00Z",
        )

    review_request = sample_provider_request(operation=ProviderOperation.PROPOSE_REVIEW)
    with pytest.raises(ValueError, match="review_id"):
        PublicationChangeResult.create(
            review_request,
            outcome=ProviderOutcome.REVIEW_OPEN,
            observed_base_source_revision=review_request.base_source_revision,
            resulting_source_revision=review_request.resulting_source_revision,
            changed_paths=review_request.approved_paths,
            review_state=ProviderReviewState.OPEN,
            observed_at="2030-01-01T00:02:00Z",
        )

    with pytest.raises(ValueError, match="merged provider outcome"):
        PublicationChangeResult.create(
            review_request,
            outcome=ProviderOutcome.MERGED,
            observed_base_source_revision=review_request.base_source_revision,
            resulting_source_revision=review_request.resulting_source_revision,
            changed_paths=review_request.approved_paths,
            review_state=ProviderReviewState.CLOSED,
            merge_revision="merge-abc",
            observed_at="2030-01-01T00:02:00Z",
        )


def test_typed_failures_require_reconciliation_for_unknown_effects() -> None:
    with pytest.raises(ValueError, match="reconciliation-required"):
        ProviderFailure(
            disposition=PublicationFailureDisposition.RETRYABLE,
            code="provider.timeout",
            safe_message="Provider timed out.",
            remediation="Retry.",
            retry_operation=ProviderOperation.COMMIT,
            effect_may_have_occurred=True,
        )

    failure = ProviderFailure(
        disposition=PublicationFailureDisposition.RECONCILIATION_REQUIRED,
        code="provider.commit_unknown",
        safe_message="Commit outcome is unknown.",
        remediation="Observe provider state before retrying.",
        retry_operation=ProviderOperation.RECONCILE,
        effect_may_have_occurred=True,
    )
    request = sample_provider_request(operation=ProviderOperation.COMMIT)
    result = PublicationChangeResult.create(
        request,
        outcome=ProviderOutcome.FAILED,
        observed_base_source_revision=request.base_source_revision,
        failure=failure,
        reconciliation=ProviderReconciliationState.PENDING,
        observed_at="2030-01-01T00:02:00Z",
    )
    assert result.failure == failure
    assert result.reconciliation == ProviderReconciliationState.PENDING


@pytest.mark.parametrize("disposition", list(PublicationFailureDisposition))
def test_all_provider_failure_dispositions_round_trip(
    disposition: PublicationFailureDisposition,
) -> None:
    failure = ProviderFailure(
        disposition=disposition,
        code=f"provider.{disposition.value}",
        safe_message="Provider operation did not complete.",
        remediation="Follow the disposition-specific recovery path.",
        retry_operation=ProviderOperation.RECONCILE,
        effect_may_have_occurred=(
            disposition == PublicationFailureDisposition.RECONCILIATION_REQUIRED
        ),
    )
    assert ProviderFailure.from_dict(failure.to_dict()) == failure


def test_external_branch_divergence_is_explicit_reconciliation_state() -> None:
    request = sample_provider_request(
        PublicationProfile.PULL_REQUEST,
        operation=ProviderOperation.RECONCILE,
    )
    failure = ProviderFailure(
        disposition=PublicationFailureDisposition.CONFLICT,
        code="provider.branch_diverged",
        safe_message="The provider branch changed externally.",
        remediation="Review external commits and create a fresh publication plan.",
        retry_operation=ProviderOperation.RECONCILE,
    )
    result = PublicationChangeResult.create(
        request,
        outcome=ProviderOutcome.FAILED,
        observed_base_source_revision=request.base_source_revision,
        branch_name=request.branch_name,
        failure=failure,
        reconciliation=ProviderReconciliationState.DIVERGED,
        evidence_refs=("provider-branch-revision:external-456",),
        observed_at="2030-01-01T00:03:00Z",
    )

    assert result.reconciliation == ProviderReconciliationState.DIVERGED
    assert result.evidence_refs == ("provider-branch-revision:external-456",)


def test_external_bundle_requires_signature_and_binds_exact_plan() -> None:
    plan = sample_plan()
    profile = sample_profile(PublicationProfile.EXTERNAL)
    bundle = PublicationChangeBundle.create(
        plan,
        profile,
        created_at="2030-01-01T00:00:00Z",
    )

    with pytest.raises(ProviderContractError) as unsigned:
        validate_change_bundle(plan, bundle)
    assert unsigned.value.code == "bundle.signature_missing"

    signed = bundle.with_signature(
        ProviderBundleSignature(
            algorithm="test-signature-v1",
            key_id="workflow-key-1",
            signed_digest=bundle.bundle_digest,
            value="test-signature-value",
        )
    )
    validate_change_bundle(plan, signed)
    assert PublicationChangeBundle.from_dict(signed.to_dict()) == signed
    assert "visibility: public" not in str(signed.to_dict("audit"))


@pytest.mark.parametrize("transport", ["cli", "http", "mcp", "automation"])
def test_transport_envelopes_preserve_provider_records(
    transport: Literal["cli", "http", "mcp", "automation"],
) -> None:
    request = sample_provider_request()
    result = PublicationChangeResult.create(
        request,
        outcome=ProviderOutcome.PREPARED,
        observed_base_source_revision=request.base_source_revision,
        resulting_source_revision=request.resulting_source_revision,
        changed_paths=request.approved_paths,
        observed_at="2030-01-01T00:01:00Z",
    )
    bundle = PublicationChangeBundle.create(
        sample_plan(),
        sample_profile(PublicationProfile.EXTERNAL),
        created_at="2030-01-01T00:00:00Z",
    )

    for record in (request, result, bundle):
        envelope = provider_record_envelope(record, transport=transport)
        loaded = provider_record_from_envelope(envelope, transport=transport)
        assert loaded == record


def test_fixture_inventory_covers_required_provider_scenarios() -> None:
    fixtures = [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(FIXTURE_ROOT.glob("*.json"))
    ]
    assert {fixture["scenario"] for fixture in fixtures} == {
        "external-provider",
        "github-pr",
        "remote-mount-handoff",
        "solo-local",
    }
    for fixture in fixtures:
        profile = PublicationProfile(fixture["profile"])
        assert fixture["provider_operations"] == [
            operation.value for operation in provider_operations_for(profile)
        ]


def test_provider_protocol_is_runtime_checkable() -> None:
    class CompleteProvider:
        id = "complete"

        def inspect(self, request): ...
        def prepare(self, request, inspection): ...
        def commit(self, request, prepared): ...
        def propose_review(self, request, committed): ...
        def observe_review(self, request, proposed): ...
        def reconcile(self, request, previous): ...

    assert isinstance(CompleteProvider(), PublicationChangeProvider)


def test_concurrent_contract_validation_is_deterministic() -> None:
    plan = sample_plan()
    profile = sample_profile(PublicationProfile.PULL_REQUEST)
    request = sample_provider_request()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _: validate_change_request(plan, profile, request),
                range(200),
            )
        )
    assert results == [None] * 200
