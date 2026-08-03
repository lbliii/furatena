"""Isolated Git changesets for commit and pull-request publication profiles."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from furatena.catalog.publication_contracts import (
    PublicationFailureDisposition,
    canonical_json_bytes,
    sha256_digest,
)
from furatena.catalog.publication_provider import (
    ProviderFailure,
    ProviderOperation,
    ProviderOutcome,
    ProviderPathChange,
    ProviderPathChangeKind,
    ProviderProtectionState,
    ProviderReconciliationState,
    ProviderReviewState,
    PublicationChangeRequest,
    PublicationChangeResult,
    PublicationProfile,
    RepositoryInspection,
)

type ChangesetLoader = Callable[[PublicationChangeRequest], str | bytes]
type Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class GitReviewCapabilities:
    """Review capabilities observed before a pull-request effect."""

    can_propose: bool
    can_observe: bool
    protection: ProviderProtectionState


@dataclass(frozen=True, slots=True)
class GitReview:
    """Provider-neutral pull-request observation returned by a review gateway."""

    review_id: str
    review_url: str
    state: ProviderReviewState
    head_revision: str
    base_ref: str
    protection: ProviderProtectionState
    merge_revision: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("review_id", "review_url", "head_revision", "base_ref"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(
                    f"Git review {field_name} must be non-empty; provide the provider value."
                )
        object.__setattr__(self, "state", ProviderReviewState(self.state))
        object.__setattr__(self, "protection", ProviderProtectionState(self.protection))
        if self.state == ProviderReviewState.MERGED and not self.merge_revision:
            raise ValueError("Merged Git review state requires a provider merge_revision value.")


class GitReviewGateway(Protocol):
    """Provider-specific review boundary; implementations must upsert by change ID."""

    def capabilities(self, request: PublicationChangeRequest) -> GitReviewCapabilities: ...

    def upsert(
        self,
        request: PublicationChangeRequest,
        *,
        head_revision: str,
    ) -> GitReview: ...

    def observe(self, request: PublicationChangeRequest) -> GitReview | None: ...


@dataclass(frozen=True, slots=True)
class _Command:
    stdout: str
    stderr: str


class _GitCommandError(RuntimeError):
    def __init__(self, command: Sequence[str], stderr: str) -> None:
        self.command = tuple(command)
        self.stderr = stderr
        super().__init__(stderr.strip() or f"Git command failed: {command[0]}")


class GitChangesetProvider:
    """Concrete Git provider that never mutates the user's working checkout.

    The provider persists only correlation metadata beneath ``state_root``. It
    creates a detached worktree at the approved Git revision, applies the bound
    unified diff there, stages only the approved paths, and updates a deterministic
    branch with compare-and-swap semantics. A review gateway owns provider-specific
    pull-request API calls.
    """

    id = "git"

    def __init__(
        self,
        repository: Path,
        state_root: Path,
        changeset_loader: ChangesetLoader,
        *,
        review_gateway: GitReviewGateway | None = None,
        remote: str = "origin",
        clock: Clock | None = None,
        command_timeout: float = 30.0,
    ) -> None:
        self.repository = repository.resolve()
        self.state_root = state_root.resolve()
        self.changeset_loader = changeset_loader
        self.review_gateway = review_gateway
        self.remote = _required(remote, "remote")
        self.clock = clock or (lambda: datetime.now(UTC))
        self.command_timeout = command_timeout
        if command_timeout <= 0:
            raise ValueError("Git command_timeout must be positive; configure seconds above zero.")
        if self.state_root == self.repository or self.repository in self.state_root.parents:
            raise ValueError(
                "Git provider state_root must be outside the source repository; choose a private path."
            )
        resolved = self._git("rev-parse", "--show-toplevel", cwd=self.repository).stdout.strip()
        if Path(resolved).resolve() != self.repository:
            raise ValueError(
                "Git provider repository must name the worktree root; pass its top-level path."
            )

    def inspect(self, request: PublicationChangeRequest) -> RepositoryInspection:
        self._require_request(request, ProviderOperation.INSPECT)
        base = self._resolve_commit(request.repository_base_revision)
        if base != request.repository_base_revision:
            raise ValueError(
                "Git repository_base_revision must be an exact full commit ID; refresh the request."
            )
        permissions = [
            ProviderOperation.INSPECT,
            ProviderOperation.PREPARE,
            ProviderOperation.COMMIT,
            ProviderOperation.RECONCILE,
        ]
        protection = ProviderProtectionState.UNPROTECTED
        if request.profile == PublicationProfile.PULL_REQUEST and self.review_gateway is not None:
            capabilities = self.review_gateway.capabilities(request)
            protection = capabilities.protection
            if capabilities.can_propose:
                permissions.append(ProviderOperation.PROPOSE_REVIEW)
            if capabilities.can_observe:
                permissions.append(ProviderOperation.OBSERVE_REVIEW)
        return RepositoryInspection.create(
            provider_id=request.provider_id,
            repository_id=request.target_repository_id,
            source_repository_id=request.repository_id,
            base_source_revision=request.base_source_revision,
            repository_revision=base,
            current_branch=None,
            detached=True,
            isolated=True,
            fork=request.repository_id != request.target_repository_id,
            protection=protection,
            permissions=tuple(permissions),
            observed_at=self._now(),
            extensions={"isolation": "git-worktree", "source_checkout_untouched": True},
        )

    def prepare(
        self,
        request: PublicationChangeRequest,
        inspection: RepositoryInspection,
    ) -> PublicationChangeResult:
        self._require_request(request, ProviderOperation.PREPARE)
        if (
            not inspection.isolated
            or inspection.repository_revision != request.repository_base_revision
        ):
            return self._failure(
                request,
                PublicationFailureDisposition.CONFLICT,
                "git.inspection_drift",
                "The repository inspection does not match the approved isolated base.",
                "Inspect the exact approved Git revision again.",
            )
        try:
            patch = self._changeset(request)
        except (TypeError, ValueError) as error:
            return self._failure(
                request,
                PublicationFailureDisposition.TERMINAL,
                "git.changeset_invalid",
                str(error),
                "Load the exact unified diff bound to the publication plan.",
            )
        with self._change_lock(request.change_id):
            state = self._load_state(request, required=False)
            if state is not None:
                replay = self._prepared_replay(request, state)
                if replay is not None:
                    return replay
                return self._diverged(request, "The isolated changeset changed after preparation.")
            workspace = self._workspace(request.change_id)
            if workspace.exists():
                return self._failure(
                    request,
                    PublicationFailureDisposition.RECONCILIATION_REQUIRED,
                    "git.workspace_unknown",
                    "An untracked isolated workspace already exists for this change.",
                    "Reconcile or explicitly clean up the isolated changeset before retrying.",
                    reconciliation=ProviderReconciliationState.MANUAL_ACTION_REQUIRED,
                    effect_may_have_occurred=True,
                )
            try:
                workspace.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._git(
                    "worktree",
                    "add",
                    "--detach",
                    str(workspace),
                    request.repository_base_revision,
                )
                self._git(
                    "apply",
                    "--index",
                    "--whitespace=nowarn",
                    "-",
                    cwd=workspace,
                    input_bytes=patch,
                )
                actual = self._staged_changes(workspace)
                if actual != request.path_changes:
                    self._remove_workspace(workspace)
                    return self._failure(
                        request,
                        PublicationFailureDisposition.TERMINAL,
                        "git.path_set_mismatch",
                        "The applied Git changes do not exactly match the approved path operations.",
                        "Create a fresh plan whose diff and approved path operations agree.",
                    )
                if (
                    self._git(
                        "status", "--porcelain=v1", "--untracked-files=all", cwd=workspace
                    ).stdout
                    == ""
                ):
                    self._remove_workspace(workspace)
                    return self._failure(
                        request,
                        PublicationFailureDisposition.TERMINAL,
                        "git.empty_changeset",
                        "The approved Git changeset produced no changes.",
                        "Create a fresh plan from the current source revision.",
                    )
                state = self._new_state(request, workspace)
                self._write_state(request.change_id, state)
            except subprocess.TimeoutExpired:
                return self._unknown(request, "git.prepare_timeout", ProviderOperation.PREPARE)
            except _GitCommandError as error:
                if workspace.exists():
                    self._remove_workspace(workspace)
                return self._failure(
                    request,
                    PublicationFailureDisposition.CONFLICT,
                    "git.prepare_failed",
                    "Git could not apply the approved changeset at its exact base revision.",
                    "Refresh the plan after resolving source conflicts or path moves.",
                    evidence=(f"git:{_safe_git_error(error)}",),
                )
            return self._result(
                request,
                ProviderOutcome.PREPARED,
                changed_paths=request.approved_paths,
                branch_name=request.branch_name,
                protection=inspection.protection,
                evidence=(f"git-worktree:{request.change_id}",),
            )

    def commit(
        self,
        request: PublicationChangeRequest,
        prepared: PublicationChangeResult,
    ) -> PublicationChangeResult:
        self._require_request(request, ProviderOperation.COMMIT)
        if prepared.change_id != request.change_id or prepared.outcome != ProviderOutcome.PREPARED:
            return self._failure(
                request,
                PublicationFailureDisposition.CONFLICT,
                "git.prepared_result_mismatch",
                "The prepared result does not belong to this Git change.",
                "Commit only the prepared result for the same deterministic change ID.",
            )
        with self._change_lock(request.change_id):
            state = self._load_state(request)
            assert state is not None
            workspace = Path(cast(str, state["workspace"]))
            recorded = cast(str | None, state.get("commit_id"))
            if recorded:
                return self._committed_replay(request, state, recorded)
            head = self._head(workspace)
            if head != request.repository_base_revision:
                recovered = self._recover_commit(request, state, workspace, head)
                if recovered is not None:
                    return recovered
                return self._diverged(request, "The isolated Git HEAD changed before commit.")
            try:
                if self._staged_changes(workspace) != request.path_changes:
                    return self._diverged(request, "The staged Git paths changed before commit.")
                if not self._workspace_has_only_staged_changes(workspace):
                    return self._diverged(
                        request, "The isolated Git workspace gained unstaged or untracked changes."
                    )
                message = _commit_message(request)
                env = {
                    "GIT_AUTHOR_NAME": request.attribution.author_name,
                    "GIT_AUTHOR_EMAIL": request.attribution.author_email,
                    "GIT_COMMITTER_NAME": request.attribution.committer_name,
                    "GIT_COMMITTER_EMAIL": request.attribution.committer_email,
                }
                self._git("commit", "--no-gpg-sign", "-m", message, cwd=workspace, env=env)
                commit_id = self._head(workspace)
                self._verify_commit(request, workspace, commit_id)
                if request.branch_name:
                    self._update_local_branch(request.branch_name, commit_id)
                state["commit_id"] = commit_id
                state["phase"] = "committed"
                self._write_state(request.change_id, state)
            except subprocess.TimeoutExpired:
                return self._unknown(request, "git.commit_timeout", ProviderOperation.COMMIT)
            except _GitCommandError as error:
                return self._failure(
                    request,
                    PublicationFailureDisposition.RETRYABLE,
                    "git.commit_failed",
                    "Git could not create the isolated publication commit.",
                    "Correct the local Git failure and retry the same deterministic change.",
                    retry=ProviderOperation.COMMIT,
                    evidence=(f"git:{_safe_git_error(error)}",),
                )
            return self._committed(request, commit_id)

    def propose_review(
        self,
        request: PublicationChangeRequest,
        committed: PublicationChangeResult,
    ) -> PublicationChangeResult:
        self._require_request(request, ProviderOperation.PROPOSE_REVIEW)
        if self.review_gateway is None:
            return self._authorization(request, "No pull-request review gateway is configured.")
        if committed.change_id != request.change_id or not committed.commit_id:
            return self._failure(
                request,
                PublicationFailureDisposition.CONFLICT,
                "git.commit_result_mismatch",
                "The committed result does not belong to this Git change.",
                "Propose review only for the matching deterministic commit.",
            )
        with self._change_lock(request.change_id):
            state = self._load_state(request)
            assert state is not None
            commit_id = cast(str | None, state.get("commit_id"))
            if commit_id is None or commit_id != committed.commit_id or not request.branch_name:
                return self._diverged(
                    request, "The deterministic branch no longer matches the commit."
                )
            try:
                remote_head = self._remote_head(request.branch_name)
                if remote_head not in {None, commit_id}:
                    return self._diverged(
                        request,
                        "The provider branch was changed externally before review creation.",
                        evidence=(f"provider-branch-revision:{remote_head}",),
                    )
                if remote_head is None:
                    self._git(
                        "push",
                        f"--force-with-lease=refs/heads/{request.branch_name}:",
                        self.remote,
                        f"{commit_id}:refs/heads/{request.branch_name}",
                    )
                review = self.review_gateway.upsert(request, head_revision=commit_id)
                self._validate_review(request, review, commit_id)
                state["phase"] = "review"
                state["review_id"] = review.review_id
                self._write_state(request.change_id, state)
            except TimeoutError, subprocess.TimeoutExpired:
                return self._unknown(
                    request, "git.review_timeout", ProviderOperation.PROPOSE_REVIEW
                )
            except PermissionError:
                return self._authorization(request, "The provider denied branch or review access.")
            except (_GitCommandError, ValueError) as error:
                return self._failure(
                    request,
                    PublicationFailureDisposition.AUTHORIZATION,
                    "git.review_failed",
                    "The provider rejected the branch or pull-request operation.",
                    "Verify push and pull-request permissions, then retry or reconcile.",
                    evidence=(f"provider:{_safe_error(error)}",),
                )
            return self._review_result(request, review)

    def observe_review(
        self,
        request: PublicationChangeRequest,
        proposed: PublicationChangeResult,
    ) -> PublicationChangeResult:
        self._require_request(request, ProviderOperation.OBSERVE_REVIEW)
        if self.review_gateway is None:
            return self._authorization(request, "No pull-request review gateway is configured.")
        with self._change_lock(request.change_id):
            state = self._load_state(request)
            assert state is not None
            commit_id = cast(str | None, state.get("commit_id"))
            if not commit_id or proposed.change_id != request.change_id:
                return self._diverged(request, "The reviewed Git change has no matching commit.")
            try:
                remote_head = self._remote_head(cast(str, request.branch_name))
                if remote_head != commit_id:
                    return self._diverged(
                        request,
                        "The provider branch changed externally.",
                        evidence=(
                            (f"provider-branch-revision:{remote_head}",) if remote_head else ()
                        ),
                    )
                review = self.review_gateway.observe(request)
                if review is None:
                    return self._diverged(request, "The pull request was removed externally.")
                self._validate_review(request, review, commit_id)
            except TimeoutError, subprocess.TimeoutExpired:
                return self._unknown(
                    request, "git.observe_timeout", ProviderOperation.OBSERVE_REVIEW
                )
            except PermissionError:
                return self._authorization(request, "The provider denied pull-request observation.")
            return self._review_result(request, review)

    def reconcile(
        self,
        request: PublicationChangeRequest,
        previous: PublicationChangeResult,
    ) -> PublicationChangeResult:
        self._require_request(request, ProviderOperation.RECONCILE)
        if previous.change_id != request.change_id:
            return self._diverged(request, "The reconciliation result belongs to another change.")
        with self._change_lock(request.change_id):
            state = self._load_state(request, required=False)
            if state is None:
                return self._failure(
                    request,
                    PublicationFailureDisposition.RETRYABLE,
                    "git.effect_absent",
                    "No isolated Git effect exists for this change.",
                    "Retry preparation for the same approved plan.",
                    retry=ProviderOperation.PREPARE,
                    reconciliation=ProviderReconciliationState.MATCHED,
                )
            workspace = Path(cast(str, state["workspace"]))
            commit_id = cast(str | None, state.get("commit_id"))
            if commit_id is None and workspace.exists():
                head = self._head(workspace)
                recovered = self._recover_commit(request, state, workspace, head)
                if recovered is not None:
                    commit_id = recovered.commit_id
            if commit_id is None:
                replay = self._prepared_replay(request, state)
                if replay is not None:
                    return self._result(
                        request,
                        ProviderOutcome.PREPARED,
                        changed_paths=request.approved_paths,
                        branch_name=request.branch_name,
                        reconciliation=ProviderReconciliationState.MATCHED,
                    )
                return self._diverged(request, "The isolated workspace cannot be reconciled.")
            if request.profile == PublicationProfile.COMMIT:
                return self._result(
                    request,
                    ProviderOutcome.COMMITTED,
                    changed_paths=request.approved_paths,
                    branch_name=request.branch_name,
                    commit_id=commit_id,
                    reconciliation=ProviderReconciliationState.MATCHED,
                    evidence=(f"git-commit:{commit_id}",),
                )
            branch = cast(str, request.branch_name)
            try:
                remote_head = self._remote_head(branch)
            except subprocess.TimeoutExpired:
                return self._unknown(request, "git.reconcile_timeout", ProviderOperation.RECONCILE)
            if remote_head not in {None, commit_id}:
                return self._diverged(
                    request,
                    "The provider branch diverged from the recorded commit.",
                    evidence=(f"provider-branch-revision:{remote_head}",),
                )
            if remote_head is None:
                return self._result(
                    request,
                    ProviderOutcome.COMMITTED,
                    changed_paths=request.approved_paths,
                    branch_name=branch,
                    commit_id=commit_id,
                    reconciliation=ProviderReconciliationState.MATCHED,
                    evidence=("provider-branch:absent",),
                )
            if self.review_gateway is None:
                return self._authorization(request, "No pull-request review gateway is configured.")
            try:
                review = self.review_gateway.observe(request)
            except TimeoutError, PermissionError:
                return self._unknown(
                    request, "git.reconcile_review_failed", ProviderOperation.RECONCILE
                )
            if review is None:
                return self._result(
                    request,
                    ProviderOutcome.COMMITTED,
                    changed_paths=request.approved_paths,
                    branch_name=branch,
                    commit_id=commit_id,
                    reconciliation=ProviderReconciliationState.MATCHED,
                    evidence=("provider-review:absent",),
                )
            try:
                self._validate_review(request, review, commit_id)
            except ValueError:
                return self._diverged(
                    request, "The pull request was edited to target another change."
                )
            return self._review_result(
                request, review, reconciliation=ProviderReconciliationState.MATCHED
            )

    def cleanup(self, request: PublicationChangeRequest) -> bool:
        """Remove resumable local state after proving no divergent provider branch exists."""
        with self._change_lock(request.change_id):
            state = self._load_state(request, required=False)
            if state is None:
                return False
            commit_id = cast(str | None, state.get("commit_id"))
            if request.branch_name and commit_id:
                remote_head = self._remote_head(request.branch_name)
                if remote_head not in {None, commit_id}:
                    raise RuntimeError(
                        "Cannot clean up after an external provider branch mutation; reconcile it first."
                    )
            workspace = Path(cast(str, state["workspace"]))
            if workspace.exists():
                self._remove_workspace(workspace)
            self._state_file(request.change_id).unlink(missing_ok=True)
            return True

    def _require_request(
        self, request: PublicationChangeRequest, operation: ProviderOperation
    ) -> None:
        if request.provider_id != self.id:
            raise ValueError(f"Git provider cannot execute provider_id {request.provider_id!r}")
        if request.operation != operation:
            raise ValueError(f"Git provider method requires {operation.value} request")
        if request.profile not in {PublicationProfile.COMMIT, PublicationProfile.PULL_REQUEST}:
            raise ValueError("GitChangesetProvider supports commit and pull-request profiles")
        if request.profile == PublicationProfile.PULL_REQUEST and not request.branch_name:
            raise ValueError("pull-request Git change requires branch_name")

    def _changeset(self, request: PublicationChangeRequest) -> bytes:
        value = self.changeset_loader(request)
        patch = value.encode("utf-8") if isinstance(value, str) else value
        if not isinstance(patch, bytes):
            raise TypeError("changeset loader must return str or bytes")
        if sha256_digest(patch) != request.changeset_digest:
            raise ValueError("loaded unified diff does not match the approved changeset digest")
        return patch

    def _new_state(self, request: PublicationChangeRequest, workspace: Path) -> dict[str, object]:
        return {
            "version": 1,
            "change_id": request.change_id,
            "plan_digest": request.plan_digest,
            "profile_digest": request.profile_digest,
            "changeset_digest": request.changeset_digest,
            "repository_base_revision": request.repository_base_revision,
            "approved_paths": list(request.approved_paths),
            "branch_name": request.branch_name,
            "workspace": str(workspace),
            "phase": "prepared",
            "commit_id": None,
            "review_id": None,
        }

    def _load_state(
        self, request: PublicationChangeRequest, *, required: bool = True
    ) -> dict[str, object] | None:
        path = self._state_file(request.change_id)
        if not path.exists():
            if required:
                raise RuntimeError("Git changeset has not been prepared")
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("Git changeset state is corrupt")
        expected = self._new_state(request, self._workspace(request.change_id))
        for key in (
            "version",
            "change_id",
            "plan_digest",
            "profile_digest",
            "changeset_digest",
            "repository_base_revision",
            "approved_paths",
            "branch_name",
            "workspace",
        ):
            if value.get(key) != expected[key]:
                raise RuntimeError(f"Git changeset state does not match request field {key}")
        return cast(dict[str, object], value)

    def _write_state(self, change_id: str, state: Mapping[str, object]) -> None:
        directory = self._change_dir(change_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = canonical_json_bytes(dict(state)) + b"\n"
        fd, temporary = tempfile.mkstemp(prefix="state-", dir=directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._state_file(change_id))
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextmanager
    def _change_lock(self, change_id: str):
        directory = self._change_dir(change_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = directory / "operation.lock"
        with lock_path.open("a+b") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _prepared_replay(
        self, request: PublicationChangeRequest, state: Mapping[str, object]
    ) -> PublicationChangeResult | None:
        workspace = Path(cast(str, state["workspace"]))
        if not workspace.exists():
            return None
        commit_id = cast(str | None, state.get("commit_id"))
        if commit_id:
            return self._result(
                request,
                ProviderOutcome.PREPARED,
                changed_paths=request.approved_paths,
                branch_name=request.branch_name,
                commit_id=commit_id,
                evidence=(f"git-commit:{commit_id}",),
            )
        if self._head(workspace) != request.repository_base_revision:
            return None
        if self._staged_changes(workspace) != request.path_changes:
            return None
        return self._result(
            request,
            ProviderOutcome.PREPARED,
            changed_paths=request.approved_paths,
            branch_name=request.branch_name,
            evidence=(f"git-worktree:{request.change_id}",),
        )

    def _committed_replay(
        self, request: PublicationChangeRequest, state: Mapping[str, object], commit_id: str
    ) -> PublicationChangeResult:
        workspace = Path(cast(str, state["workspace"]))
        try:
            self._verify_commit(request, workspace, commit_id)
            if request.branch_name:
                local = self._optional_ref(f"refs/heads/{request.branch_name}")
                if local != commit_id:
                    return self._diverged(request, "The deterministic local branch changed.")
        except _GitCommandError:
            return self._diverged(request, "The recorded Git commit is no longer available.")
        return self._committed(request, commit_id)

    def _recover_commit(
        self,
        request: PublicationChangeRequest,
        state: dict[str, object],
        workspace: Path,
        head: str,
    ) -> PublicationChangeResult | None:
        if head == request.repository_base_revision:
            return None
        try:
            self._verify_commit(request, workspace, head)
        except _GitCommandError:
            return None
        if request.branch_name:
            local = self._optional_ref(f"refs/heads/{request.branch_name}")
            if local not in {None, head}:
                return None
            self._update_local_branch(request.branch_name, head)
        state["commit_id"] = head
        state["phase"] = "committed"
        self._write_state(request.change_id, state)
        return self._result(
            request,
            ProviderOutcome.COMMITTED,
            changed_paths=request.approved_paths,
            branch_name=request.branch_name,
            commit_id=head,
            reconciliation=ProviderReconciliationState.MATCHED,
            evidence=(f"git-commit:{head}",),
        )

    def _committed(
        self, request: PublicationChangeRequest, commit_id: str
    ) -> PublicationChangeResult:
        return self._result(
            request,
            ProviderOutcome.COMMITTED,
            changed_paths=request.approved_paths,
            branch_name=request.branch_name,
            commit_id=commit_id,
            evidence=(
                f"git-commit:{commit_id}",
                f"source-author:{request.attribution.author_email}",
                f"workflow-committer:{request.attribution.committer_email}",
                f"workflow-actor:{request.attribution.workflow_actor}",
            ),
        )

    def _review_result(
        self,
        request: PublicationChangeRequest,
        review: GitReview,
        *,
        reconciliation: ProviderReconciliationState = ProviderReconciliationState.NOT_REQUIRED,
    ) -> PublicationChangeResult:
        outcomes = {
            ProviderReviewState.DRAFT: ProviderOutcome.REVIEW_OPEN,
            ProviderReviewState.OPEN: ProviderOutcome.REVIEW_OPEN,
            ProviderReviewState.CLOSED: ProviderOutcome.REVIEW_CLOSED,
            ProviderReviewState.REJECTED: ProviderOutcome.REVIEW_CLOSED,
            ProviderReviewState.MERGED: ProviderOutcome.MERGED,
        }
        outcome = outcomes.get(review.state, ProviderOutcome.FAILED)
        if outcome == ProviderOutcome.FAILED:
            return self._diverged(request, "The provider returned an invalid pull-request state.")
        return self._result(
            request,
            outcome,
            changed_paths=request.approved_paths,
            branch_name=request.branch_name,
            commit_id=review.head_revision,
            review_id=review.review_id,
            review_url=review.review_url,
            review_state=review.state,
            merge_revision=review.merge_revision,
            protection=review.protection,
            reconciliation=reconciliation,
            evidence=(f"provider-review:{review.review_id}",),
        )

    def _validate_review(
        self, request: PublicationChangeRequest, review: GitReview, commit_id: str
    ) -> None:
        if review.head_revision != commit_id:
            raise ValueError("review head does not match deterministic commit")
        if review.base_ref != request.review_target:
            raise ValueError("review base does not match approved target")

    def _verify_commit(
        self, request: PublicationChangeRequest, workspace: Path, commit_id: str
    ) -> None:
        parent = self._git("rev-parse", f"{commit_id}^", cwd=workspace).stdout.strip()
        if parent != request.repository_base_revision:
            raise _GitCommandError(("rev-parse",), "commit parent does not match approved base")
        actual = self._commit_changes(workspace, commit_id)
        if actual != request.path_changes:
            raise _GitCommandError(("diff-tree",), "commit paths do not match approved changes")
        body = self._git("show", "-s", "--format=%B", commit_id, cwd=workspace).stdout
        if f"Furatena-Change: {request.change_id}" not in body:
            raise _GitCommandError(("show",), "commit does not contain change correlation")
        attribution = (
            self._git("show", "-s", "--format=%an%x00%ae%x00%cn%x00%ce", commit_id, cwd=workspace)
            .stdout.rstrip("\n")
            .split("\0")
        )
        expected = [
            request.attribution.author_name,
            request.attribution.author_email,
            request.attribution.committer_name,
            request.attribution.committer_email,
        ]
        if attribution != expected:
            raise _GitCommandError(("show",), "commit attribution does not match the request")

    def _update_local_branch(self, branch: str, commit_id: str) -> None:
        ref = f"refs/heads/{branch}"
        current = self._optional_ref(ref)
        if current == commit_id:
            return
        if current is not None:
            raise _GitCommandError(("update-ref",), "deterministic branch already diverged")
        self._git("update-ref", ref, commit_id, "0" * 40)

    def _remote_head(self, branch: str) -> str | None:
        output = self._git(
            "ls-remote", "--refs", self.remote, f"refs/heads/{branch}"
        ).stdout.strip()
        if not output:
            return None
        return output.split()[0]

    def _optional_ref(self, ref: str) -> str | None:
        completed = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=self.command_timeout,
        )
        return completed.stdout.strip() if completed.returncode == 0 else None

    def _resolve_commit(self, revision: str) -> str:
        return self._git("rev-parse", "--verify", f"{revision}^{{commit}}").stdout.strip()

    def _head(self, workspace: Path) -> str:
        return self._git("rev-parse", "HEAD", cwd=workspace).stdout.strip()

    def _staged_changes(self, workspace: Path) -> tuple[ProviderPathChange, ...]:
        output = self._git(
            "diff", "--cached", "--name-status", "-z", "--find-renames", cwd=workspace
        ).stdout
        return _parse_name_status(output)

    def _commit_changes(self, workspace: Path, commit_id: str) -> tuple[ProviderPathChange, ...]:
        output = self._git(
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-z",
            "--find-renames",
            "-r",
            commit_id,
            cwd=workspace,
        ).stdout
        return _parse_name_status(output)

    def _workspace_has_only_staged_changes(self, workspace: Path) -> bool:
        unstaged = self._git("diff", "--name-only", cwd=workspace).stdout
        untracked = self._git("ls-files", "--others", "--exclude-standard", cwd=workspace).stdout
        conflicts = self._git("diff", "--name-only", "--diff-filter=U", cwd=workspace).stdout
        return not unstaged and not untracked and not conflicts

    def _remove_workspace(self, workspace: Path) -> None:
        self._git("worktree", "remove", "--force", str(workspace))
        self._git("worktree", "prune")

    def _git(
        self,
        *arguments: str,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
        env: Mapping[str, str] | None = None,
    ) -> _Command:
        command = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(cwd or self.repository),
            *arguments,
        ]
        process_env = os.environ.copy()
        process_env.update(env or {})
        process_env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        completed = subprocess.run(
            command,
            input=input_bytes,
            capture_output=True,
            text=input_bytes is None,
            check=False,
            timeout=self.command_timeout,
            env=process_env,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        if completed.returncode != 0:
            raise _GitCommandError(command, stderr)
        return _Command(stdout=stdout, stderr=stderr)

    def _result(
        self,
        request: PublicationChangeRequest,
        outcome: ProviderOutcome,
        *,
        changed_paths: tuple[str, ...] = (),
        branch_name: str | None = None,
        commit_id: str | None = None,
        review_id: str | None = None,
        review_url: str | None = None,
        review_state: ProviderReviewState = ProviderReviewState.NONE,
        merge_revision: str | None = None,
        protection: ProviderProtectionState = ProviderProtectionState.UNKNOWN,
        failure: ProviderFailure | None = None,
        reconciliation: ProviderReconciliationState = ProviderReconciliationState.NOT_REQUIRED,
        evidence: tuple[str, ...] = (),
    ) -> PublicationChangeResult:
        return PublicationChangeResult.create(
            request,
            outcome=outcome,
            observed_base_source_revision=request.base_source_revision,
            resulting_source_revision=(
                request.resulting_source_revision if outcome != ProviderOutcome.FAILED else None
            ),
            changed_paths=changed_paths,
            branch_name=branch_name,
            commit_id=commit_id,
            review_id=review_id,
            review_url=review_url,
            review_state=review_state,
            merge_revision=merge_revision,
            protection=protection,
            failure=failure,
            reconciliation=reconciliation,
            evidence_refs=evidence,
            observed_at=self._now(),
        )

    def _failure(
        self,
        request: PublicationChangeRequest,
        disposition: PublicationFailureDisposition,
        code: str,
        message: str,
        remediation: str,
        *,
        retry: ProviderOperation | None = None,
        reconciliation: ProviderReconciliationState = ProviderReconciliationState.NOT_REQUIRED,
        effect_may_have_occurred: bool = False,
        evidence: tuple[str, ...] = (),
    ) -> PublicationChangeResult:
        return self._result(
            request,
            ProviderOutcome.FAILED,
            branch_name=request.branch_name,
            failure=ProviderFailure(
                disposition=disposition,
                code=code,
                safe_message=message,
                remediation=remediation,
                retry_operation=retry,
                effect_may_have_occurred=effect_may_have_occurred,
            ),
            reconciliation=reconciliation,
            evidence=evidence,
        )

    def _unknown(
        self, request: PublicationChangeRequest, code: str, operation: ProviderOperation
    ) -> PublicationChangeResult:
        return self._failure(
            request,
            PublicationFailureDisposition.RECONCILIATION_REQUIRED,
            code,
            "The Git provider timed out after the effect may have occurred.",
            "Reconcile provider state before retrying the operation.",
            retry=ProviderOperation.RECONCILE,
            reconciliation=ProviderReconciliationState.PENDING,
            effect_may_have_occurred=True,
            evidence=(f"unknown-effect:{operation.value}",),
        )

    def _diverged(
        self,
        request: PublicationChangeRequest,
        message: str,
        *,
        evidence: tuple[str, ...] = (),
    ) -> PublicationChangeResult:
        return self._failure(
            request,
            PublicationFailureDisposition.CONFLICT,
            "git.external_divergence",
            message,
            "Review the external mutation and create a fresh publication plan if it is intentional.",
            retry=ProviderOperation.RECONCILE,
            reconciliation=ProviderReconciliationState.DIVERGED,
            evidence=evidence,
        )

    def _authorization(
        self, request: PublicationChangeRequest, message: str
    ) -> PublicationChangeResult:
        return self._failure(
            request,
            PublicationFailureDisposition.AUTHORIZATION,
            "git.permission_denied",
            message,
            "Grant the required branch and pull-request permissions without bypassing review.",
        )

    def _change_dir(self, change_id: str) -> Path:
        return self.state_root / change_id

    def _workspace(self, change_id: str) -> Path:
        return self._change_dir(change_id) / "worktree"

    def _state_file(self, change_id: str) -> Path:
        return self._change_dir(change_id) / "state.json"

    def _now(self) -> str:
        return self.clock().astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_name_status(value: str) -> tuple[ProviderPathChange, ...]:
    fields = value.rstrip("\0").split("\0") if value else []
    changes: list[ProviderPathChange] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if index >= len(fields):
            raise ValueError("incomplete Git name-status output")
        if status.startswith("R"):
            previous = fields[index]
            index += 1
            if index >= len(fields):
                raise ValueError("incomplete Git rename output")
            path = fields[index]
            index += 1
            changes.append(
                ProviderPathChange(ProviderPathChangeKind.MOVE, path, previous_path=previous)
            )
            continue
        path = fields[index]
        index += 1
        kinds = {
            "A": ProviderPathChangeKind.CREATE,
            "M": ProviderPathChangeKind.MODIFY,
            "D": ProviderPathChangeKind.DELETE,
        }
        kind = kinds.get(status[:1])
        if kind is None:
            raise ValueError(f"unsupported Git path status {status!r}")
        changes.append(ProviderPathChange(kind, path))
    return tuple(sorted(changes, key=lambda item: (item.path, item.kind)))


def _commit_message(request: PublicationChangeRequest) -> str:
    return (
        f"{request.attribution.message.rstrip()}\n\n"
        f"Furatena-Plan: {request.plan_id}\n"
        f"Furatena-Change: {request.change_id}\n"
        f"Furatena-Actor: {request.attribution.workflow_actor}"
    )


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _safe_git_error(error: _GitCommandError) -> str:
    return _safe_error(error).replace(str(Path.home()), "<home>")


def _safe_error(error: BaseException) -> str:
    return " ".join(str(error).split())[:240] or type(error).__name__
