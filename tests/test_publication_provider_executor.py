"""End-to-end workflow coverage for concrete publication providers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from furatena.catalog.audit_store import InMemoryAuditStore
from furatena.catalog.publication_contracts import PublicationPlan, PublicationState, sha256_digest
from furatena.catalog.publication_git_provider import GitChangesetProvider
from furatena.catalog.publication_provider import (
    ProviderCommitAttribution,
    ProviderOperation,
    ProviderProtectionState,
    ProviderReconciliationState,
    ProviderReviewState,
    PublicationChangeResult,
    PublicationProfile,
    PublicationProfileConfig,
    RepositoryInspection,
)
from furatena.catalog.publication_provider_executor import (
    InMemoryPublicationProviderExecutionStore,
    JsonDirectoryPublicationProviderExecutionStore,
    PublicationProviderExecutor,
    PublicationProviderSelection,
)
from furatena.catalog.publication_workflow import (
    FilesystemPublicationLeaseFactory,
    PublicationWorkflowService,
)
from furatena.catalog.publication_workflow_store import (
    InMemoryPublicationWorkflowStore,
    PublicationOperationStatus,
)
from tests.publication_support import sample_actor, sample_plan
from tests.test_publication_git_provider import (
    FakeReviewGateway,
    _git,
    _repository,
)


class LocalOnlyProvider:
    id = "local"

    def inspect(self, request):
        return RepositoryInspection.create(
            provider_id=self.id,
            repository_id=request.target_repository_id,
            base_source_revision=request.base_source_revision,
            repository_revision=request.repository_base_revision,
            current_branch="main",
            detached=False,
            isolated=False,
            fork=False,
            protection=ProviderProtectionState.UNPROTECTED,
            permissions=(
                request.operation,
                ProviderOperation.PREPARE,
            ),
            observed_at="2030-01-01T00:00:00Z",
        )

    def prepare(self, request, inspection):
        return PublicationChangeResult.create(
            request,
            outcome="dirty_working_tree",
            observed_base_source_revision=request.base_source_revision,
            resulting_source_revision=request.resulting_source_revision,
            changed_paths=request.approved_paths,
            protection=inspection.protection,
            observed_at="2030-01-01T00:00:01Z",
        )

    def commit(self, request, prepared):
        raise AssertionError("local-only profile cannot commit")

    def propose_review(self, request, committed):
        raise AssertionError("local-only profile cannot propose review")

    def observe_review(self, request, proposed):
        raise AssertionError("local-only profile cannot observe review")

    def reconcile(self, request, previous):
        return previous


def test_local_only_profile_is_explicit_and_never_enters_git_or_review_steps() -> None:
    plan = sample_plan(required_count=0)
    profile = PublicationProfileConfig(
        profile=PublicationProfile.LOCAL_ONLY,
        provider_id="local",
        repository_id="workspace",
        base_ref="main",
        allow_dirty_unrelated=True,
    )
    executor = PublicationProviderExecutor(
        selector=lambda _plan: PublicationProviderSelection(
            profile=profile,
            repository_base_revision="workspace-revision",
            attribution=ProviderCommitAttribution(
                author_name="Docs Author",
                author_email="author@example.com",
                committer_name="Furatena Workflow",
                committer_email="workflow@example.com",
                workflow_actor=sample_actor().actor,
                message="Apply docs guide locally",
            ),
        ),
        providers={"local": LocalOnlyProvider()},
        store=InMemoryPublicationProviderExecutionStore(),
    )

    result = executor.execute_with_context(
        plan,
        actor=sample_actor(),
        idempotency_key="local-only",
    )

    assert result.disposition.value == "applied"
    assert any(
        output.kind == "publication_provider_profile" and output.status == "local_only"
        for output in result.outputs
    )
    assert any(
        output.kind == "publication_provider_result" and output.status == "dirty_working_tree"
        for output in result.outputs
    )


def test_commit_profile_applies_once_and_replays_workflow_receipt(tmp_path: Path) -> None:
    repository, base_revision, patch = _repository(tmp_path)
    plan = _plan(patch)
    service, records = _service(
        tmp_path,
        plan,
        repository,
        base_revision,
        patch,
        PublicationProfile.COMMIT,
    )
    approved = service.validate(plan.plan_id, actor=sample_actor()).snapshot

    applied = service.execute(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="commit-workflow",
        expected_state_version=approved.state_version,
    )
    replay = service.execute(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="commit-workflow",
        expected_state_version=approved.state_version,
    )

    assert applied.snapshot.state == PublicationState.APPLIED
    assert replay.replayed is True
    assert replay.snapshot == applied.snapshot
    assert applied.receipt is not None
    assert applied.receipt.status == PublicationOperationStatus.SUCCEEDED
    assert [result.outcome.value for result in records.results(plan.plan_id)] == [
        "prepared",
        "committed",
    ]
    restarted_records = JsonDirectoryPublicationProviderExecutionStore(
        tmp_path / "provider-execution"
    )
    assert restarted_records.results(plan.plan_id) == records.results(plan.plan_id)
    restarted_latest = restarted_records.latest_result(plan.plan_id)
    assert restarted_latest is not None
    assert restarted_latest.outcome.value == "committed"
    assert _git(repository, "status", "--porcelain=v1") == ""
    assert {output.kind for output in applied.snapshot.outputs} >= {
        "publication_provider_profile",
        "publication_provider_result",
        "publication_provider_protection",
        "publication_provider_reconciliation",
    }


def test_pull_request_stays_pending_until_protected_review_is_merged(
    tmp_path: Path,
) -> None:
    repository, base_revision, patch = _repository(tmp_path, remote=True)
    gateway = FakeReviewGateway()
    plan = _plan(patch)
    service, records = _service(
        tmp_path,
        plan,
        repository,
        base_revision,
        patch,
        PublicationProfile.PULL_REQUEST,
        gateway=gateway,
    )
    approved = service.validate(plan.plan_id, actor=sample_actor()).snapshot

    pending = service.execute(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="pr-workflow",
        expected_state_version=approved.state_version,
    )

    assert pending.snapshot.state == PublicationState.REVIEWABLE
    assert pending.snapshot.terminal is False
    assert any(
        output.kind == "publication_provider_review" and output.status == "open"
        for output in pending.snapshot.outputs
    )
    assert any(
        output.kind == "publication_provider_protection"
        and output.status == ProviderProtectionState.PROTECTED.value
        for output in pending.snapshot.outputs
    )

    gateway.state = ProviderReviewState.MERGED
    applied = service.reconcile(plan.plan_id, actor=sample_actor())

    assert applied.snapshot.state == PublicationState.APPLIED
    assert any(
        output.kind == "publication_provider_review"
        and output.status == ProviderReviewState.MERGED.value
        and output.revision == "merge-123"
        for output in applied.snapshot.outputs
    )
    assert records.latest_result(plan.plan_id) is not None
    assert records.latest_result(plan.plan_id).reconciliation == (
        ProviderReconciliationState.MATCHED
    )


def test_partial_review_failure_is_truthful_and_reconciles_without_duplicate_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository, base_revision, patch = _repository(tmp_path, remote=True)
    gateway = FakeReviewGateway()
    original_upsert = gateway.upsert
    timed_out = False

    def timeout_after_review(request, *, head_revision: str):
        nonlocal timed_out
        review = original_upsert(request, head_revision=head_revision)
        if not timed_out:
            timed_out = True
            raise TimeoutError("provider response lost")
        return review

    monkeypatch.setattr(gateway, "upsert", timeout_after_review)
    plan = _plan(patch)
    service, records = _service(
        tmp_path,
        plan,
        repository,
        base_revision,
        patch,
        PublicationProfile.PULL_REQUEST,
        gateway=gateway,
    )
    approved = service.validate(plan.plan_id, actor=sample_actor()).snapshot

    failed = service.execute(
        plan.plan_id,
        actor=sample_actor(),
        idempotency_key="partial-review",
        expected_state_version=approved.state_version,
    )

    assert failed.snapshot.state == PublicationState.FAILED
    assert failed.snapshot.failure is not None
    assert failed.snapshot.failure.code == "git.review_timeout"
    assert failed.snapshot.failure.retry_target == PublicationState.EXECUTING
    assert failed.receipt is not None
    assert failed.receipt.status == PublicationOperationStatus.RECONCILIATION_REQUIRED
    assert records.latest_result(plan.plan_id) is not None
    assert records.latest_result(plan.plan_id).reconciliation == ProviderReconciliationState.PENDING

    recovered = service.reconcile(plan.plan_id, actor=sample_actor())

    assert recovered.snapshot.state == PublicationState.REVIEWABLE
    assert gateway.upsert_calls == 1
    assert records.latest_result(plan.plan_id) is not None
    assert records.latest_result(plan.plan_id).reconciliation == ProviderReconciliationState.MATCHED


def _service(
    tmp_path: Path,
    plan: PublicationPlan,
    repository: Path,
    base_revision: str,
    patch: str,
    profile: PublicationProfile,
    *,
    gateway: FakeReviewGateway | None = None,
) -> tuple[PublicationWorkflowService, JsonDirectoryPublicationProviderExecutionStore]:
    config = (
        PublicationProfileConfig(
            profile=profile,
            provider_id="git",
            repository_id="docs/repository",
            base_ref="main",
        )
        if profile == PublicationProfile.COMMIT
        else PublicationProfileConfig(
            profile=profile,
            provider_id="git",
            repository_id="docs/repository",
            base_ref="main",
            branch_template="fura/{change_id}",
            review_target="main",
        )
    )
    provider = GitChangesetProvider(
        repository,
        tmp_path / "git-provider-state",
        lambda _request: patch,
        review_gateway=gateway,
    )
    records = JsonDirectoryPublicationProviderExecutionStore(tmp_path / "provider-execution")
    selection = PublicationProviderSelection(
        profile=config,
        repository_base_revision=base_revision,
        attribution=ProviderCommitAttribution(
            author_name="Docs Author",
            author_email="author@example.com",
            committer_name="Furatena Workflow",
            committer_email="workflow@example.com",
            workflow_actor=sample_actor().actor,
            message="Publish docs guide",
        ),
    )
    executor = PublicationProviderExecutor(
        selector=lambda _plan: selection,
        providers={"git": provider},
        store=records,
    )
    service = PublicationWorkflowService(
        store=InMemoryPublicationWorkflowStore(),
        binding_checker=lambda _plan: True,
        approval_evaluator=lambda _plan: True,
        authorizer=lambda _plan, _actor, _command: True,
        executor=executor,
        audit_store=InMemoryAuditStore(clock=lambda: 1_893_456_000.0),
        lease_factory=FilesystemPublicationLeaseFactory(tmp_path / "workflow-leases"),
        clock=lambda: "2030-01-01T00:00:00Z",
    )
    service.create_plan(plan)
    return service, records


def _plan(patch: str) -> PublicationPlan:
    base = sample_plan(required_count=0)
    changeset = replace(
        base.changeset,
        unified_diff=patch,
        diff_sha256=sha256_digest(patch.encode("utf-8")),
    )
    return PublicationPlan.create(
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
