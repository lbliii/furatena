"""Real-Git coverage for isolated publication changesets."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

import pytest

from furatena.catalog.publication_contracts import (
    PublicationFailureDisposition,
    PublicationPlan,
    sha256_digest,
)
from furatena.catalog.publication_git_provider import (
    GitChangesetProvider,
    GitReview,
    GitReviewCapabilities,
)
from furatena.catalog.publication_provider import (
    ProviderCommitAttribution,
    ProviderOperation,
    ProviderOutcome,
    ProviderProtectionState,
    ProviderReconciliationState,
    ProviderReviewState,
    PublicationChangeRequest,
    PublicationProfile,
    PublicationProfileConfig,
    validate_provider_result,
    validate_repository_inspection,
)
from tests.publication_support import sample_actor, sample_plan

FIXED_NOW = datetime(2030, 1, 1, tzinfo=UTC)


class FakeReviewGateway:
    def __init__(
        self,
        *,
        allowed: bool = True,
        state: ProviderReviewState = ProviderReviewState.OPEN,
    ) -> None:
        self.allowed = allowed
        self.state = state
        self.head_revision: str | None = None
        self.upsert_calls = 0
        self._lock = Lock()

    def capabilities(self, request: PublicationChangeRequest) -> GitReviewCapabilities:
        return GitReviewCapabilities(
            can_propose=self.allowed,
            can_observe=self.allowed,
            protection=ProviderProtectionState.PROTECTED,
        )

    def upsert(
        self,
        request: PublicationChangeRequest,
        *,
        head_revision: str,
    ) -> GitReview:
        if not self.allowed:
            raise PermissionError("review denied")
        with self._lock:
            self.upsert_calls += 1
            self.head_revision = head_revision
        return self._review(request)

    def observe(self, request: PublicationChangeRequest) -> GitReview | None:
        if not self.allowed:
            raise PermissionError("review denied")
        if self.head_revision is None:
            return None
        return self._review(request)

    def _review(self, request: PublicationChangeRequest) -> GitReview:
        assert self.head_revision is not None
        return GitReview(
            review_id=f"review-{request.change_id}",
            review_url="https://provider.invalid/reviews/1",
            state=self.state,
            head_revision=self.head_revision,
            base_ref=request.review_target or "main",
            protection=ProviderProtectionState.PROTECTED,
            merge_revision=("merge-123" if self.state == ProviderReviewState.MERGED else None),
        )


def test_commit_profile_isolates_every_user_worktree_class_and_records_attribution(
    tmp_path: Path,
) -> None:
    repository, base, patch = _repository(tmp_path)
    _write(repository / ".gitignore", "ignored.txt\n")
    _write(repository / "conflicted.txt", "base\n")
    _git(repository, "add", ".gitignore", "conflicted.txt")
    _git(repository, "commit", "-m", "ignore fixture")
    base = _git(repository, "rev-parse", "HEAD")
    patch = _patch(repository, "docs/guide.md", "visibility: public\n")

    _git(repository, "switch", "-c", "conflicting-change")
    _write(repository / "conflicted.txt", "theirs\n")
    _git(repository, "commit", "-am", "theirs")
    _git(repository, "switch", "main")
    _write(repository / "conflicted.txt", "ours\n")
    _git(repository, "commit", "-am", "ours")
    merge = subprocess.run(
        ["git", "-C", str(repository), "merge", "conflicting-change"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0

    _write(repository / "staged.txt", "staged user work\n")
    _git(repository, "add", "staged.txt")
    _write(repository / "notes.txt", "unstaged user work\n")
    _write(repository / "untracked.txt", "untracked user work\n")
    _write(repository / "ignored.txt", "ignored user work\n")
    status_before = _git(repository, "status", "--porcelain=v1", "--ignored")

    plan = _plan(patch)
    provider = _provider(repository, tmp_path / "state", patch)
    inspect_request = _request(plan, base, ProviderOperation.INSPECT, PublicationProfile.COMMIT)
    inspection = provider.inspect(inspect_request)
    validate_repository_inspection(_profile(PublicationProfile.COMMIT), inspect_request, inspection)

    prepare_request = _request(plan, base, ProviderOperation.PREPARE, PublicationProfile.COMMIT)
    prepared = provider.prepare(prepare_request, inspection)
    validate_provider_result(plan, prepare_request, prepared)
    commit_request = _request(plan, base, ProviderOperation.COMMIT, PublicationProfile.COMMIT)
    committed = provider.commit(commit_request, prepared)
    validate_provider_result(plan, commit_request, committed)

    assert committed.outcome == ProviderOutcome.COMMITTED
    assert committed.commit_id
    assert _git(repository, "status", "--porcelain=v1", "--ignored") == status_before
    assert (
        _git(repository, "show", "--format=", "--name-only", committed.commit_id) == "docs/guide.md"
    )
    assert _git(repository, "show", "-s", "--format=%an <%ae>", committed.commit_id) == (
        "Docs Author <author@example.com>"
    )
    assert _git(repository, "show", "-s", "--format=%cn <%ce>", committed.commit_id) == (
        "Furatena Workflow <workflow@example.com>"
    )
    assert "Furatena-Actor: workflow@example.com" in _git(
        repository, "show", "-s", "--format=%B", committed.commit_id
    )
    assert provider.cleanup(commit_request) is True
    assert provider.cleanup(commit_request) is False


def test_prepare_rejects_conflicts_and_unapproved_paths_without_touching_checkout(
    tmp_path: Path,
) -> None:
    repository, base, _ = _repository(tmp_path)
    _write(repository / "extra.md", "old\n")
    _git(repository, "add", "extra.md")
    _git(repository, "commit", "-m", "add extra")
    base = _git(repository, "rev-parse", "HEAD")
    _write(repository / "docs/guide.md", "visibility: public\n")
    _write(repository / "extra.md", "new\n")
    patch = _git(repository, "diff", "--", "docs/guide.md", "extra.md") + "\n"
    _git(repository, "restore", "docs/guide.md", "extra.md")
    plan = _plan(patch, paths=("docs/guide.md",))
    provider = _provider(repository, tmp_path / "state", patch)
    inspection = provider.inspect(
        _request(plan, base, ProviderOperation.INSPECT, PublicationProfile.COMMIT)
    )
    request = _request(plan, base, ProviderOperation.PREPARE, PublicationProfile.COMMIT)

    result = provider.prepare(request, inspection)

    assert result.outcome == ProviderOutcome.FAILED
    assert result.failure is not None
    assert result.failure.code == "git.path_set_mismatch"
    assert _git(repository, "status", "--porcelain=v1") == ""
    assert not (tmp_path / "state" / request.change_id / "worktree").exists()

    conflicting_patch = patch.replace("-visibility: draft", "-visibility: missing")
    conflict_plan = _plan(conflicting_patch, paths=("docs/guide.md",))
    conflict_provider = _provider(repository, tmp_path / "conflict-state", conflicting_patch)
    conflict_inspection = conflict_provider.inspect(
        _request(
            conflict_plan,
            base,
            ProviderOperation.INSPECT,
            PublicationProfile.COMMIT,
        )
    )
    conflict_request = _request(
        conflict_plan,
        base,
        ProviderOperation.PREPARE,
        PublicationProfile.COMMIT,
    )
    conflict = conflict_provider.prepare(conflict_request, conflict_inspection)
    assert conflict.failure is not None
    assert conflict.failure.code == "git.prepare_failed"
    assert conflict.failure.disposition == PublicationFailureDisposition.CONFLICT


def test_concurrent_replay_creates_one_commit_branch_and_review(tmp_path: Path) -> None:
    repository, base, patch = _repository(tmp_path, remote=True)
    plan = _plan(patch)
    gateway = FakeReviewGateway()
    provider = _provider(repository, tmp_path / "state", patch, gateway=gateway)
    inspection = provider.inspect(
        _request(plan, base, ProviderOperation.INSPECT, PublicationProfile.PULL_REQUEST)
    )
    prepare_request = _request(
        plan, base, ProviderOperation.PREPARE, PublicationProfile.PULL_REQUEST
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        prepared_results = list(
            executor.map(lambda _: provider.prepare(prepare_request, inspection), range(16))
        )
    assert {result.resulting_source_revision for result in prepared_results} == {
        plan.changeset.resulting_source_revision
    }
    assert {result.outcome for result in prepared_results} == {ProviderOutcome.PREPARED}

    commit_request = _request(plan, base, ProviderOperation.COMMIT, PublicationProfile.PULL_REQUEST)
    with ThreadPoolExecutor(max_workers=8) as executor:
        committed_results = list(
            executor.map(
                lambda prepared: provider.commit(commit_request, prepared),
                prepared_results,
            )
        )
    commit_ids = {result.commit_id for result in committed_results}
    assert len(commit_ids) == 1
    assert None not in commit_ids

    review_request = _request(
        plan,
        base,
        ProviderOperation.PROPOSE_REVIEW,
        PublicationProfile.PULL_REQUEST,
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        review_results = list(
            executor.map(
                lambda committed: provider.propose_review(review_request, committed),
                committed_results,
            )
        )
    assert {result.outcome for result in review_results} == {ProviderOutcome.REVIEW_OPEN}
    assert {result.review_id for result in review_results} == {f"review-{review_request.change_id}"}
    assert gateway.upsert_calls == len(review_results)
    assert (
        _git(
            repository,
            "ls-remote",
            "--refs",
            "origin",
            f"refs/heads/{review_request.branch_name}",
        ).split()[0]
        in commit_ids
    )


def test_commit_timeout_reconciles_without_duplicate_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base, patch = _repository(tmp_path)
    plan = _plan(patch)
    provider = _provider(repository, tmp_path / "state", patch)
    inspection = provider.inspect(
        _request(plan, base, ProviderOperation.INSPECT, PublicationProfile.COMMIT)
    )
    prepare_request = _request(plan, base, ProviderOperation.PREPARE, PublicationProfile.COMMIT)
    prepared = provider.prepare(prepare_request, inspection)
    commit_request = _request(plan, base, ProviderOperation.COMMIT, PublicationProfile.COMMIT)
    original_git = provider._git
    timed_out = False

    def time_out_after_commit(*arguments: str, **kwargs):
        nonlocal timed_out
        result = original_git(*arguments, **kwargs)
        if arguments[0] == "commit" and not timed_out:
            timed_out = True
            raise subprocess.TimeoutExpired(["git", "commit"], 30)
        return result

    monkeypatch.setattr(provider, "_git", time_out_after_commit)
    unknown = provider.commit(commit_request, prepared)
    assert unknown.failure is not None
    assert unknown.failure.effect_may_have_occurred is True
    assert unknown.reconciliation == ProviderReconciliationState.PENDING

    reconcile_request = _request(plan, base, ProviderOperation.RECONCILE, PublicationProfile.COMMIT)
    reconciled = provider.reconcile(reconcile_request, unknown)
    assert reconciled.outcome == ProviderOutcome.COMMITTED
    assert reconciled.commit_id
    assert reconciled.reconciliation == ProviderReconciliationState.MATCHED
    assert _git(repository, "rev-list", "--count", f"{base}..{reconciled.commit_id}") == "1"


def test_external_branch_mutation_and_permission_failures_stop_safely(tmp_path: Path) -> None:
    repository, base, patch = _repository(tmp_path, remote=True)
    plan = _plan(patch)
    gateway = FakeReviewGateway()
    provider = _provider(repository, tmp_path / "state", patch, gateway=gateway)
    inspection = provider.inspect(
        _request(plan, base, ProviderOperation.INSPECT, PublicationProfile.PULL_REQUEST)
    )
    prepared = provider.prepare(
        _request(plan, base, ProviderOperation.PREPARE, PublicationProfile.PULL_REQUEST),
        inspection,
    )
    committed = provider.commit(
        _request(plan, base, ProviderOperation.COMMIT, PublicationProfile.PULL_REQUEST),
        prepared,
    )
    review_request = _request(
        plan,
        base,
        ProviderOperation.PROPOSE_REVIEW,
        PublicationProfile.PULL_REQUEST,
    )
    proposed = provider.propose_review(review_request, committed)
    assert proposed.outcome == ProviderOutcome.REVIEW_OPEN
    _git(
        repository,
        "push",
        "--force",
        "origin",
        f"{base}:refs/heads/{review_request.branch_name}",
    )
    observed = provider.observe_review(
        _request(
            plan,
            base,
            ProviderOperation.OBSERVE_REVIEW,
            PublicationProfile.PULL_REQUEST,
        ),
        proposed,
    )
    assert observed.failure is not None
    assert observed.failure.disposition == PublicationFailureDisposition.CONFLICT
    assert observed.reconciliation == ProviderReconciliationState.DIVERGED
    with pytest.raises(RuntimeError, match="external provider branch mutation"):
        provider.cleanup(review_request)

    denied_gateway = FakeReviewGateway(allowed=False)
    denied_provider = _provider(
        repository, tmp_path / "denied-state", patch, gateway=denied_gateway
    )
    denied_inspection = denied_provider.inspect(
        _request(plan, base, ProviderOperation.INSPECT, PublicationProfile.PULL_REQUEST)
    )
    with pytest.raises(Exception) as error:
        validate_repository_inspection(
            _profile(PublicationProfile.PULL_REQUEST),
            _request(
                plan,
                base,
                ProviderOperation.INSPECT,
                PublicationProfile.PULL_REQUEST,
            ),
            denied_inspection,
        )
    assert getattr(error.value, "disposition", None) == PublicationFailureDisposition.AUTHORIZATION


@pytest.mark.parametrize(
    ("review_state", "outcome"),
    [
        (ProviderReviewState.CLOSED, ProviderOutcome.REVIEW_CLOSED),
        (ProviderReviewState.REJECTED, ProviderOutcome.REVIEW_CLOSED),
        (ProviderReviewState.MERGED, ProviderOutcome.MERGED),
    ],
)
def test_review_state_reconciles_external_close_reject_and_merge(
    tmp_path: Path,
    review_state: ProviderReviewState,
    outcome: ProviderOutcome,
) -> None:
    repository, base, patch = _repository(tmp_path, remote=True)
    plan = _plan(patch)
    gateway = FakeReviewGateway()
    provider = _provider(repository, tmp_path / "state", patch, gateway=gateway)
    inspection = provider.inspect(
        _request(plan, base, ProviderOperation.INSPECT, PublicationProfile.PULL_REQUEST)
    )
    prepared = provider.prepare(
        _request(plan, base, ProviderOperation.PREPARE, PublicationProfile.PULL_REQUEST),
        inspection,
    )
    committed = provider.commit(
        _request(plan, base, ProviderOperation.COMMIT, PublicationProfile.PULL_REQUEST), prepared
    )
    proposed = provider.propose_review(
        _request(
            plan,
            base,
            ProviderOperation.PROPOSE_REVIEW,
            PublicationProfile.PULL_REQUEST,
        ),
        committed,
    )
    gateway.state = review_state
    observed = provider.observe_review(
        _request(
            plan,
            base,
            ProviderOperation.OBSERVE_REVIEW,
            PublicationProfile.PULL_REQUEST,
        ),
        proposed,
    )
    assert observed.outcome == outcome
    assert observed.review_state == review_state


def _repository(tmp_path: Path, *, remote: bool = False) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Fixture User")
    _git(repository, "config", "user.email", "fixture@example.com")
    _git(repository, "config", "commit.gpgsign", "false")
    _write(repository / "docs/guide.md", "visibility: draft\n")
    _git(repository, "add", "docs/guide.md")
    _git(repository, "commit", "-m", "seed")
    base = _git(repository, "rev-parse", "HEAD")
    patch = _patch(repository, "docs/guide.md", "visibility: public\n")
    if remote:
        bare = tmp_path / "remote.git"
        _git(tmp_path, "init", "--bare", str(bare))
        _git(repository, "remote", "add", "origin", str(bare))
        _git(repository, "push", "-u", "origin", "main")
    return repository, base, patch


def _patch(repository: Path, path: str, contents: str) -> str:
    target = repository / path
    previous = target.read_text(encoding="utf-8")
    _write(target, contents)
    patch = _git(repository, "diff", "--", path) + "\n"
    _write(target, previous)
    return patch


def _plan(patch: str, *, paths: tuple[str, ...] = ("docs/guide.md",)) -> PublicationPlan:
    base = sample_plan()
    changeset = replace(
        base.changeset,
        paths=paths,
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


def _profile(profile: PublicationProfile) -> PublicationProfileConfig:
    if profile == PublicationProfile.COMMIT:
        return PublicationProfileConfig(
            profile=profile,
            provider_id="git",
            repository_id="docs/repository",
            base_ref="main",
        )
    return PublicationProfileConfig(
        profile=profile,
        provider_id="git",
        repository_id="docs/repository",
        base_ref="main",
        branch_template="fura/{change_id}",
        review_target="main",
    )


def _request(
    plan: PublicationPlan,
    base: str,
    operation: ProviderOperation,
    profile: PublicationProfile,
) -> PublicationChangeRequest:
    return PublicationChangeRequest.create(
        plan,
        _profile(profile),
        operation=operation,
        repository_base_revision=base,
        attribution=ProviderCommitAttribution(
            author_name="Docs Author",
            author_email="author@example.com",
            committer_name="Furatena Workflow",
            committer_email="workflow@example.com",
            workflow_actor="workflow@example.com",
            message="Publish docs guide",
        ),
        actor=sample_actor(),
        idempotency_key=f"git-{operation.value}",
    )


def _provider(
    repository: Path,
    state_root: Path,
    patch: str,
    *,
    gateway: FakeReviewGateway | None = None,
) -> GitChangesetProvider:
    return GitChangesetProvider(
        repository,
        state_root,
        lambda _: patch,
        review_gateway=gateway,
        clock=lambda: FIXED_NOW,
    )


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
