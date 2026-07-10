"""Provider-neutral publication profiles and repository change contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol, cast, runtime_checkable

from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationFailureDisposition,
    PublicationPlan,
    canonical_json_bytes,
    normalize_rfc3339,
    sha256_digest,
)

PUBLICATION_PROVIDER_SCHEMA_VERSION = 1

type ProviderProjection = Literal["trusted", "audit", "public"]
type ProviderTransport = Literal["cli", "http", "mcp", "automation"]


class PublicationProfile(StrEnum):
    LOCAL_ONLY = "local_only"
    COMMIT = "commit"
    PULL_REQUEST = "pull_request"
    EXTERNAL = "external"


class ProviderOperation(StrEnum):
    INSPECT = "inspect"
    PREPARE = "prepare"
    COMMIT = "commit"
    PROPOSE_REVIEW = "propose_review"
    OBSERVE_REVIEW = "observe_review"
    RECONCILE = "reconcile"


class ProviderOutcome(StrEnum):
    PLANNED = "planned"
    PREPARED = "prepared"
    DIRTY_WORKING_TREE = "dirty_working_tree"
    COMMITTED = "committed"
    REVIEW_OPEN = "review_open"
    REVIEW_CLOSED = "review_closed"
    MERGED = "merged"
    HANDED_OFF = "handed_off"
    NO_CHANGE = "no_change"
    FAILED = "failed"


class ProviderReviewState(StrEnum):
    NONE = "none"
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"
    REJECTED = "rejected"


class ProviderProtectionState(StrEnum):
    UNKNOWN = "unknown"
    UNPROTECTED = "unprotected"
    PROTECTED = "protected"


class ProviderReconciliationState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    MATCHED = "matched"
    DIVERGED = "diverged"
    MANUAL_ACTION_REQUIRED = "manual_action_required"


class ProviderPathChangeKind(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    MOVE = "move"


class ProviderContractError(ValueError):
    """Typed fail-closed contract violation raised before the next effect."""

    def __init__(
        self,
        disposition: PublicationFailureDisposition,
        code: str,
        message: str,
        remediation: str,
    ) -> None:
        self.disposition = PublicationFailureDisposition(disposition)
        self.code = _required(code, "provider error code")
        self.remediation = _required(remediation, "provider remediation")
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {
            "disposition": self.disposition.value,
            "code": self.code,
            "message": str(self),
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class PublicationProfileConfig:
    profile: PublicationProfile
    provider_id: str
    repository_id: str
    target_repository_id: str | None = None
    base_ref: str | None = None
    branch_template: str | None = None
    review_target: str | None = None
    external_target: str | None = None
    allow_dirty_unrelated: bool = False
    profile_digest: str = ""
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", PublicationProfile(self.profile))
        for field_name in ("provider_id", "repository_id"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        for field_name in (
            "target_repository_id",
            "base_ref",
            "branch_template",
            "review_target",
            "external_target",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required(value, field_name))
        if self.profile == PublicationProfile.PULL_REQUEST:
            if self.branch_template is None or self.review_target is None:
                raise ValueError("pull-request profile requires branch_template and review_target")
        elif self.review_target is not None:
            raise ValueError("review_target is valid only for pull-request profile")
        if self.profile == PublicationProfile.EXTERNAL:
            if self.external_target is None:
                raise ValueError("external profile requires external_target")
        elif self.external_target is not None:
            raise ValueError("external_target is valid only for external profile")
        if self.profile != PublicationProfile.LOCAL_ONLY and self.allow_dirty_unrelated:
            raise ValueError("allow_dirty_unrelated is valid only for local-only profile")
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        expected = sha256_digest(canonical_json_bytes(self.digest_payload()))
        if self.profile_digest:
            if self.profile_digest != expected:
                raise ValueError("publication profile_digest does not match profile payload")
        else:
            object.__setattr__(self, "profile_digest", expected)

    def digest_payload(self) -> dict[str, object]:
        return {
            "schema_version": PUBLICATION_PROVIDER_SCHEMA_VERSION,
            "record_type": "furatena.publication-provider.profile",
            "profile": self.profile.value,
            "provider_id": self.provider_id,
            "repository_id": self.repository_id,
            "target_repository_id": self.target_repository_id,
            "base_ref": self.base_ref,
            "branch_template": self.branch_template,
            "review_target": self.review_target,
            "external_target": self.external_target,
            "allow_dirty_unrelated": self.allow_dirty_unrelated,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.digest_payload(),
            "profile_digest": self.profile_digest,
            "extensions": dict(self.extensions or {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationProfileConfig:
        _record_header(value, "furatena.publication-provider.profile")
        return cls(
            profile=PublicationProfile(str(value.get("profile") or "")),
            provider_id=str(value.get("provider_id") or ""),
            repository_id=str(value.get("repository_id") or ""),
            target_repository_id=_optional_string(value.get("target_repository_id")),
            base_ref=_optional_string(value.get("base_ref")),
            branch_template=_optional_string(value.get("branch_template")),
            review_target=_optional_string(value.get("review_target")),
            external_target=_optional_string(value.get("external_target")),
            allow_dirty_unrelated=bool(value.get("allow_dirty_unrelated", False)),
            profile_digest=str(value.get("profile_digest") or ""),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@dataclass(frozen=True, slots=True)
class ProviderCommitAttribution:
    author_name: str
    author_email: str
    committer_name: str
    committer_email: str
    workflow_actor: str
    message: str

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))

    def to_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderCommitAttribution:
        return cls(**{name: str(value.get(name) or "") for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class ProviderPathChange:
    kind: ProviderPathChangeKind
    path: str
    previous_path: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ProviderPathChangeKind(self.kind))
        object.__setattr__(self, "path", _logical_path(self.path))
        if self.previous_path is not None:
            object.__setattr__(self, "previous_path", _logical_path(self.previous_path))
        if self.kind == ProviderPathChangeKind.MOVE and self.previous_path is None:
            raise ValueError("move path change requires previous_path")
        if self.kind != ProviderPathChangeKind.MOVE and self.previous_path is not None:
            raise ValueError("previous_path is valid only for move path changes")
        if self.previous_path == self.path:
            raise ValueError("move path change must use distinct paths")

    @property
    def approved_paths(self) -> tuple[str, ...]:
        return _paths((self.path, self.previous_path) if self.previous_path else (self.path,))

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "path": self.path,
            "previous_path": self.previous_path,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderPathChange:
        return cls(
            kind=ProviderPathChangeKind(str(value.get("kind") or "")),
            path=str(value.get("path") or ""),
            previous_path=_optional_string(value.get("previous_path")),
        )


@dataclass(frozen=True, slots=True)
class RepositoryInspection:
    inspection_id: str
    inspection_digest: str
    provider_id: str
    repository_id: str
    source_repository_id: str
    base_source_revision: str
    repository_revision: str
    current_branch: str | None
    detached: bool
    isolated: bool
    fork: bool
    protection: ProviderProtectionState
    permissions: tuple[ProviderOperation, ...]
    staged_paths: tuple[str, ...] = ()
    unstaged_paths: tuple[str, ...] = ()
    untracked_paths: tuple[str, ...] = ()
    ignored_paths: tuple[str, ...] = ()
    conflicted_paths: tuple[str, ...] = ()
    observed_at: str = ""
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "provider_id",
            "repository_id",
            "source_repository_id",
            "base_source_revision",
            "repository_revision",
        ):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        if self.current_branch is not None:
            object.__setattr__(
                self, "current_branch", _required(self.current_branch, "current_branch")
            )
        object.__setattr__(self, "protection", ProviderProtectionState(self.protection))
        object.__setattr__(
            self,
            "permissions",
            tuple(sorted({ProviderOperation(item) for item in self.permissions}, key=str)),
        )
        for field_name in _INSPECTION_PATH_FIELDS:
            object.__setattr__(self, field_name, _paths(getattr(self, field_name)))
        object.__setattr__(self, "observed_at", normalize_rfc3339(self.observed_at))
        if self.detached and self.current_branch is not None:
            raise ValueError("detached repository inspection cannot declare current_branch")
        if not self.detached and self.current_branch is None:
            raise ValueError("attached repository inspection requires current_branch")
        if self.fork and self.source_repository_id == self.repository_id:
            raise ValueError("fork inspection requires distinct source and target repositories")
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        expected = sha256_digest(canonical_json_bytes(self.digest_payload()))
        if self.inspection_digest != expected:
            raise ValueError("repository inspection_digest does not match inspection payload")
        if self.inspection_id != _record_id("inspection", expected):
            raise ValueError("repository inspection_id does not match inspection_digest")

    @classmethod
    def create(
        cls,
        *,
        provider_id: str,
        repository_id: str,
        source_repository_id: str | None = None,
        base_source_revision: str,
        repository_revision: str,
        current_branch: str | None,
        detached: bool,
        isolated: bool,
        fork: bool,
        protection: ProviderProtectionState,
        permissions: tuple[ProviderOperation, ...],
        staged_paths: tuple[str, ...] = (),
        unstaged_paths: tuple[str, ...] = (),
        untracked_paths: tuple[str, ...] = (),
        ignored_paths: tuple[str, ...] = (),
        conflicted_paths: tuple[str, ...] = (),
        observed_at: str,
        extensions: Mapping[str, Any] | None = None,
    ) -> RepositoryInspection:
        normalized = {
            "provider_id": _required(provider_id, "provider_id"),
            "repository_id": _required(repository_id, "repository_id"),
            "source_repository_id": _required(
                source_repository_id or repository_id, "source_repository_id"
            ),
            "base_source_revision": _required(base_source_revision, "base_source_revision"),
            "repository_revision": _required(repository_revision, "repository_revision"),
            "current_branch": current_branch,
            "detached": bool(detached),
            "isolated": bool(isolated),
            "fork": bool(fork),
            "protection": ProviderProtectionState(protection),
            "permissions": tuple(
                sorted({ProviderOperation(item) for item in permissions}, key=str)
            ),
            "staged_paths": _paths(staged_paths),
            "unstaged_paths": _paths(unstaged_paths),
            "untracked_paths": _paths(untracked_paths),
            "ignored_paths": _paths(ignored_paths),
            "conflicted_paths": _paths(conflicted_paths),
            "observed_at": normalize_rfc3339(observed_at),
        }
        digest = sha256_digest(canonical_json_bytes(_inspection_payload(**normalized)))
        return cls(
            inspection_id=_record_id("inspection", digest),
            inspection_digest=digest,
            extensions=extensions,
            **normalized,
        )

    @property
    def dirty_paths(self) -> tuple[str, ...]:
        return _paths(
            item for field_name in _INSPECTION_PATH_FIELDS for item in getattr(self, field_name)
        )

    def digest_payload(self) -> dict[str, object]:
        return _inspection_payload(
            provider_id=self.provider_id,
            repository_id=self.repository_id,
            source_repository_id=self.source_repository_id,
            base_source_revision=self.base_source_revision,
            repository_revision=self.repository_revision,
            current_branch=self.current_branch,
            detached=self.detached,
            isolated=self.isolated,
            fork=self.fork,
            protection=self.protection,
            permissions=self.permissions,
            staged_paths=self.staged_paths,
            unstaged_paths=self.unstaged_paths,
            untracked_paths=self.untracked_paths,
            ignored_paths=self.ignored_paths,
            conflicted_paths=self.conflicted_paths,
            observed_at=self.observed_at,
        )

    def to_dict(self, projection: ProviderProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        payload: dict[str, object] = {
            "schema_version": PUBLICATION_PROVIDER_SCHEMA_VERSION,
            "record_type": "furatena.publication-provider.inspection",
            "inspection_id": self.inspection_id,
            "inspection_digest": self.inspection_digest,
            **self.digest_payload(),
        }
        if projection == "public":
            return {
                key: payload[key]
                for key in (
                    "schema_version",
                    "record_type",
                    "inspection_id",
                    "provider_id",
                    "protection",
                    "observed_at",
                )
            }
        if projection == "trusted":
            payload["extensions"] = dict(self.extensions or {})
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RepositoryInspection:
        _record_header(value, "furatena.publication-provider.inspection")
        return cls(
            inspection_id=str(value.get("inspection_id") or ""),
            inspection_digest=str(value.get("inspection_digest") or ""),
            provider_id=str(value.get("provider_id") or ""),
            repository_id=str(value.get("repository_id") or ""),
            source_repository_id=str(value.get("source_repository_id") or ""),
            base_source_revision=str(value.get("base_source_revision") or ""),
            repository_revision=str(value.get("repository_revision") or ""),
            current_branch=_optional_string(value.get("current_branch")),
            detached=bool(value.get("detached", False)),
            isolated=bool(value.get("isolated", False)),
            fork=bool(value.get("fork", False)),
            protection=ProviderProtectionState(str(value.get("protection") or "")),
            permissions=tuple(
                ProviderOperation(str(item)) for item in _sequence(value.get("permissions"))
            ),
            staged_paths=_string_tuple(value.get("staged_paths")),
            unstaged_paths=_string_tuple(value.get("unstaged_paths")),
            untracked_paths=_string_tuple(value.get("untracked_paths")),
            ignored_paths=_string_tuple(value.get("ignored_paths")),
            conflicted_paths=_string_tuple(value.get("conflicted_paths")),
            observed_at=str(value.get("observed_at") or ""),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@dataclass(frozen=True, slots=True)
class PublicationChangeRequest:
    request_id: str
    request_digest: str
    change_id: str
    profile: PublicationProfile
    profile_digest: str
    operation: ProviderOperation
    plan_id: str
    plan_digest: str
    changeset_digest: str
    approved_paths: tuple[str, ...]
    path_changes: tuple[ProviderPathChange, ...]
    base_source_revision: str
    resulting_source_revision: str
    provider_id: str
    repository_id: str
    target_repository_id: str
    repository_base_revision: str
    branch_name: str | None
    review_target: str | None
    external_target: str | None
    attribution: ProviderCommitAttribution
    actor: PublicationActor
    idempotency_key: str
    dry_run: bool
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", PublicationProfile(self.profile))
        object.__setattr__(self, "operation", ProviderOperation(self.operation))
        for field_name in (
            "change_id",
            "profile_digest",
            "plan_id",
            "plan_digest",
            "changeset_digest",
            "base_source_revision",
            "resulting_source_revision",
            "provider_id",
            "repository_id",
            "target_repository_id",
            "repository_base_revision",
            "idempotency_key",
        ):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        object.__setattr__(self, "approved_paths", _paths(self.approved_paths))
        if not self.approved_paths:
            raise ValueError("publication change request requires approved_paths")
        path_changes = tuple(sorted(self.path_changes, key=lambda item: (item.path, item.kind)))
        if not path_changes:
            raise ValueError("publication change request requires path_changes")
        object.__setattr__(self, "path_changes", path_changes)
        if _path_change_paths(path_changes) != self.approved_paths:
            raise ValueError("publication path_changes must cover exactly approved_paths")
        for field_name in ("branch_name", "review_target", "external_target"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required(value, field_name))
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        expected = sha256_digest(canonical_json_bytes(self.digest_payload()))
        if self.request_digest != expected:
            raise ValueError("publication request_digest does not match request payload")
        if self.request_id != _record_id("provider-request", expected):
            raise ValueError("publication request_id does not match request_digest")

    @classmethod
    def create(
        cls,
        plan: PublicationPlan,
        profile: PublicationProfileConfig,
        *,
        operation: ProviderOperation,
        repository_base_revision: str,
        attribution: ProviderCommitAttribution,
        actor: PublicationActor,
        idempotency_key: str,
        dry_run: bool = False,
        branch_name: str | None = None,
        path_changes: tuple[ProviderPathChange, ...] | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> PublicationChangeRequest:
        operation = ProviderOperation(operation)
        target_repository_id = profile.target_repository_id or profile.repository_id
        change_id = _change_id(plan, profile)
        normalized_branch = branch_name or _branch_name(profile, change_id)
        normalized_path_changes = path_changes or tuple(
            ProviderPathChange(ProviderPathChangeKind.MODIFY, path) for path in plan.changeset.paths
        )
        values = dict(
            change_id=change_id,
            profile=profile.profile,
            profile_digest=profile.profile_digest,
            operation=operation,
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            changeset_digest=plan.changeset.diff_sha256,
            approved_paths=plan.changeset.paths,
            path_changes=normalized_path_changes,
            base_source_revision=plan.changeset.previous_source_revision,
            resulting_source_revision=plan.changeset.resulting_source_revision,
            provider_id=profile.provider_id,
            repository_id=profile.repository_id,
            target_repository_id=target_repository_id,
            repository_base_revision=_required(
                repository_base_revision, "repository_base_revision"
            ),
            branch_name=normalized_branch,
            review_target=profile.review_target,
            external_target=profile.external_target,
            attribution=attribution,
            actor=actor,
            dry_run=bool(dry_run),
        )
        digest = sha256_digest(canonical_json_bytes(_request_payload(**values)))
        return cls(
            request_id=_record_id("provider-request", digest),
            request_digest=digest,
            idempotency_key=idempotency_key,
            extensions=extensions,
            **cast(Any, values),
        )

    def digest_payload(self) -> dict[str, object]:
        return _request_payload(
            change_id=self.change_id,
            profile=self.profile,
            profile_digest=self.profile_digest,
            operation=self.operation,
            plan_id=self.plan_id,
            plan_digest=self.plan_digest,
            changeset_digest=self.changeset_digest,
            approved_paths=self.approved_paths,
            path_changes=self.path_changes,
            base_source_revision=self.base_source_revision,
            resulting_source_revision=self.resulting_source_revision,
            provider_id=self.provider_id,
            repository_id=self.repository_id,
            target_repository_id=self.target_repository_id,
            repository_base_revision=self.repository_base_revision,
            branch_name=self.branch_name,
            review_target=self.review_target,
            external_target=self.external_target,
            attribution=self.attribution,
            actor=self.actor,
            dry_run=self.dry_run,
        )

    def to_dict(self, projection: ProviderProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        payload: dict[str, object] = {
            "schema_version": PUBLICATION_PROVIDER_SCHEMA_VERSION,
            "record_type": "furatena.publication-provider.request",
            "request_id": self.request_id,
            "request_digest": self.request_digest,
            **self.digest_payload(),
        }
        if projection == "public":
            return {
                key: payload[key]
                for key in (
                    "schema_version",
                    "record_type",
                    "request_id",
                    "profile",
                    "operation",
                    "plan_id",
                    "change_id",
                    "dry_run",
                )
            }
        if projection == "trusted":
            payload["idempotency_key"] = self.idempotency_key
            payload["extensions"] = dict(self.extensions or {})
        else:
            payload.pop("attribution", None)
            payload.pop("actor", None)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationChangeRequest:
        _record_header(value, "furatena.publication-provider.request")
        return cls(
            request_id=str(value.get("request_id") or ""),
            request_digest=str(value.get("request_digest") or ""),
            change_id=str(value.get("change_id") or ""),
            profile=PublicationProfile(str(value.get("profile") or "")),
            profile_digest=str(value.get("profile_digest") or ""),
            operation=ProviderOperation(str(value.get("operation") or "")),
            plan_id=str(value.get("plan_id") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            changeset_digest=str(value.get("changeset_digest") or ""),
            approved_paths=_string_tuple(value.get("approved_paths")),
            path_changes=tuple(
                ProviderPathChange.from_dict(_mapping(item, "path_changes item"))
                for item in _sequence(value.get("path_changes"))
            ),
            base_source_revision=str(value.get("base_source_revision") or ""),
            resulting_source_revision=str(value.get("resulting_source_revision") or ""),
            provider_id=str(value.get("provider_id") or ""),
            repository_id=str(value.get("repository_id") or ""),
            target_repository_id=str(value.get("target_repository_id") or ""),
            repository_base_revision=str(value.get("repository_base_revision") or ""),
            branch_name=_optional_string(value.get("branch_name")),
            review_target=_optional_string(value.get("review_target")),
            external_target=_optional_string(value.get("external_target")),
            attribution=ProviderCommitAttribution.from_dict(
                _mapping(value.get("attribution"), "attribution")
            ),
            actor=PublicationActor.from_dict(_mapping(value.get("actor"), "actor")),
            idempotency_key=str(value.get("idempotency_key") or ""),
            dry_run=bool(value.get("dry_run", False)),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )

    def dry_run_report(self) -> dict[str, object]:
        return {
            "profile": self.profile.value,
            "approved_paths": list(self.approved_paths),
            "base_source_revision": self.base_source_revision,
            "repository_base_revision": self.repository_base_revision,
            "branch_name": self.branch_name,
            "commit": self.attribution.to_dict(),
            "review_target": self.review_target,
            "external_target": self.external_target,
            "provider_operations": [
                operation.value for operation in provider_operations_for(self.profile)
            ],
        }


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    disposition: PublicationFailureDisposition
    code: str
    safe_message: str
    remediation: str
    retry_operation: ProviderOperation | None = None
    effect_may_have_occurred: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", PublicationFailureDisposition(self.disposition))
        for field_name in ("code", "safe_message", "remediation"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        if self.retry_operation is not None:
            object.__setattr__(self, "retry_operation", ProviderOperation(self.retry_operation))
        if self.effect_may_have_occurred and (
            self.disposition != PublicationFailureDisposition.RECONCILIATION_REQUIRED
        ):
            raise ValueError("unknown provider effects require reconciliation-required disposition")

    def to_dict(self) -> dict[str, object]:
        return {
            "disposition": self.disposition.value,
            "code": self.code,
            "safe_message": self.safe_message,
            "remediation": self.remediation,
            "retry_operation": self.retry_operation.value if self.retry_operation else None,
            "effect_may_have_occurred": self.effect_may_have_occurred,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderFailure:
        retry = value.get("retry_operation")
        return cls(
            disposition=PublicationFailureDisposition(str(value.get("disposition") or "")),
            code=str(value.get("code") or ""),
            safe_message=str(value.get("safe_message") or ""),
            remediation=str(value.get("remediation") or ""),
            retry_operation=ProviderOperation(str(retry)) if retry is not None else None,
            effect_may_have_occurred=bool(value.get("effect_may_have_occurred", False)),
        )


@dataclass(frozen=True, slots=True)
class PublicationChangeResult:
    result_id: str
    result_digest: str
    request_id: str
    request_digest: str
    plan_digest: str
    change_id: str
    profile: PublicationProfile
    operation: ProviderOperation
    outcome: ProviderOutcome
    provider_id: str
    repository_id: str
    observed_base_source_revision: str
    resulting_source_revision: str | None
    changed_paths: tuple[str, ...]
    preserved_paths: tuple[str, ...]
    branch_name: str | None
    commit_id: str | None
    review_id: str | None
    review_url: str | None
    review_state: ProviderReviewState
    merge_revision: str | None
    protection: ProviderProtectionState
    failure: ProviderFailure | None
    reconciliation: ProviderReconciliationState
    evidence_refs: tuple[str, ...]
    observed_at: str
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "request_id",
            "request_digest",
            "plan_digest",
            "change_id",
            "provider_id",
            "repository_id",
            "observed_base_source_revision",
        ):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        object.__setattr__(self, "profile", PublicationProfile(self.profile))
        object.__setattr__(self, "operation", ProviderOperation(self.operation))
        object.__setattr__(self, "outcome", ProviderOutcome(self.outcome))
        object.__setattr__(self, "review_state", ProviderReviewState(self.review_state))
        object.__setattr__(self, "protection", ProviderProtectionState(self.protection))
        object.__setattr__(self, "reconciliation", ProviderReconciliationState(self.reconciliation))
        object.__setattr__(self, "changed_paths", _paths(self.changed_paths))
        object.__setattr__(self, "preserved_paths", _paths(self.preserved_paths))
        object.__setattr__(self, "evidence_refs", _sorted_strings(self.evidence_refs))
        for field_name in (
            "resulting_source_revision",
            "branch_name",
            "commit_id",
            "review_id",
            "review_url",
            "merge_revision",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required(value, field_name))
        object.__setattr__(self, "observed_at", normalize_rfc3339(self.observed_at))
        if self.outcome == ProviderOutcome.FAILED and self.failure is None:
            raise ValueError("failed publication provider result requires failure details")
        if self.outcome != ProviderOutcome.FAILED and self.failure is not None:
            raise ValueError("publication provider failure requires failed outcome")
        if self.review_state == ProviderReviewState.MERGED and self.merge_revision is None:
            raise ValueError("merged review requires merge_revision")
        if self.outcome == ProviderOutcome.MERGED and (
            self.review_state != ProviderReviewState.MERGED or self.merge_revision is None
        ):
            raise ValueError("merged provider outcome requires merged review and revision")
        if self.outcome == ProviderOutcome.REVIEW_OPEN and (
            self.review_state not in {ProviderReviewState.DRAFT, ProviderReviewState.OPEN}
            or self.review_id is None
        ):
            raise ValueError("open review outcome requires draft/open review and review_id")
        if self.outcome == ProviderOutcome.COMMITTED and self.commit_id is None:
            raise ValueError("committed provider outcome requires commit_id")
        if (
            self.failure is not None
            and self.failure.effect_may_have_occurred
            and self.reconciliation == ProviderReconciliationState.NOT_REQUIRED
        ):
            raise ValueError("unknown provider effect requires active reconciliation state")
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        expected = sha256_digest(canonical_json_bytes(self.digest_payload()))
        if self.result_digest != expected:
            raise ValueError("publication result_digest does not match result payload")
        if self.result_id != _record_id("provider-result", expected):
            raise ValueError("publication result_id does not match result_digest")

    @classmethod
    def create(
        cls,
        request: PublicationChangeRequest,
        *,
        outcome: ProviderOutcome,
        observed_base_source_revision: str,
        resulting_source_revision: str | None = None,
        changed_paths: tuple[str, ...] = (),
        preserved_paths: tuple[str, ...] = (),
        branch_name: str | None = None,
        commit_id: str | None = None,
        review_id: str | None = None,
        review_url: str | None = None,
        review_state: ProviderReviewState = ProviderReviewState.NONE,
        merge_revision: str | None = None,
        protection: ProviderProtectionState = ProviderProtectionState.UNKNOWN,
        failure: ProviderFailure | None = None,
        reconciliation: ProviderReconciliationState = ProviderReconciliationState.NOT_REQUIRED,
        evidence_refs: tuple[str, ...] = (),
        observed_at: str,
        extensions: Mapping[str, Any] | None = None,
    ) -> PublicationChangeResult:
        values = dict(
            request_id=request.request_id,
            request_digest=request.request_digest,
            plan_digest=request.plan_digest,
            change_id=request.change_id,
            profile=request.profile,
            operation=request.operation,
            outcome=ProviderOutcome(outcome),
            provider_id=request.provider_id,
            repository_id=request.target_repository_id,
            observed_base_source_revision=_required(
                observed_base_source_revision, "observed_base_source_revision"
            ),
            resulting_source_revision=resulting_source_revision,
            changed_paths=_paths(changed_paths),
            preserved_paths=_paths(preserved_paths),
            branch_name=branch_name,
            commit_id=commit_id,
            review_id=review_id,
            review_url=review_url,
            review_state=ProviderReviewState(review_state),
            merge_revision=merge_revision,
            protection=ProviderProtectionState(protection),
            failure=failure,
            reconciliation=ProviderReconciliationState(reconciliation),
            evidence_refs=_sorted_strings(evidence_refs),
            observed_at=normalize_rfc3339(observed_at),
        )
        digest = sha256_digest(canonical_json_bytes(_result_payload(**values)))
        return cls(
            result_id=_record_id("provider-result", digest),
            result_digest=digest,
            extensions=extensions,
            **cast(Any, values),
        )

    def digest_payload(self) -> dict[str, object]:
        return _result_payload(
            request_id=self.request_id,
            request_digest=self.request_digest,
            plan_digest=self.plan_digest,
            change_id=self.change_id,
            profile=self.profile,
            operation=self.operation,
            outcome=self.outcome,
            provider_id=self.provider_id,
            repository_id=self.repository_id,
            observed_base_source_revision=self.observed_base_source_revision,
            resulting_source_revision=self.resulting_source_revision,
            changed_paths=self.changed_paths,
            preserved_paths=self.preserved_paths,
            branch_name=self.branch_name,
            commit_id=self.commit_id,
            review_id=self.review_id,
            review_url=self.review_url,
            review_state=self.review_state,
            merge_revision=self.merge_revision,
            protection=self.protection,
            failure=self.failure,
            reconciliation=self.reconciliation,
            evidence_refs=self.evidence_refs,
            observed_at=self.observed_at,
        )

    def to_dict(self, projection: ProviderProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        payload: dict[str, object] = {
            "schema_version": PUBLICATION_PROVIDER_SCHEMA_VERSION,
            "record_type": "furatena.publication-provider.result",
            "result_id": self.result_id,
            "result_digest": self.result_digest,
            **self.digest_payload(),
        }
        if projection == "public":
            return {
                key: payload[key]
                for key in (
                    "schema_version",
                    "record_type",
                    "result_id",
                    "profile",
                    "operation",
                    "outcome",
                    "review_state",
                    "protection",
                    "reconciliation",
                    "observed_at",
                )
            }
        if projection == "audit":
            payload.pop("review_url", None)
        else:
            payload["extensions"] = dict(self.extensions or {})
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationChangeResult:
        _record_header(value, "furatena.publication-provider.result")
        failure = value.get("failure")
        return cls(
            result_id=str(value.get("result_id") or ""),
            result_digest=str(value.get("result_digest") or ""),
            request_id=str(value.get("request_id") or ""),
            request_digest=str(value.get("request_digest") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            change_id=str(value.get("change_id") or ""),
            profile=PublicationProfile(str(value.get("profile") or "")),
            operation=ProviderOperation(str(value.get("operation") or "")),
            outcome=ProviderOutcome(str(value.get("outcome") or "")),
            provider_id=str(value.get("provider_id") or ""),
            repository_id=str(value.get("repository_id") or ""),
            observed_base_source_revision=str(value.get("observed_base_source_revision") or ""),
            resulting_source_revision=_optional_string(value.get("resulting_source_revision")),
            changed_paths=_string_tuple(value.get("changed_paths")),
            preserved_paths=_string_tuple(value.get("preserved_paths")),
            branch_name=_optional_string(value.get("branch_name")),
            commit_id=_optional_string(value.get("commit_id")),
            review_id=_optional_string(value.get("review_id")),
            review_url=_optional_string(value.get("review_url")),
            review_state=ProviderReviewState(str(value.get("review_state") or "")),
            merge_revision=_optional_string(value.get("merge_revision")),
            protection=ProviderProtectionState(str(value.get("protection") or "")),
            failure=(
                ProviderFailure.from_dict(_mapping(failure, "failure"))
                if failure is not None
                else None
            ),
            reconciliation=ProviderReconciliationState(str(value.get("reconciliation") or "")),
            evidence_refs=_string_tuple(value.get("evidence_refs")),
            observed_at=str(value.get("observed_at") or ""),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@dataclass(frozen=True, slots=True)
class ProviderBundleSignature:
    algorithm: str
    key_id: str
    signed_digest: str
    value: str

    def __post_init__(self) -> None:
        for field_name in ("algorithm", "key_id", "signed_digest", "value"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))

    def to_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ProviderBundleSignature:
        return cls(**{name: str(value.get(name) or "") for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class PublicationChangeBundle:
    bundle_id: str
    bundle_digest: str
    plan_id: str
    plan_digest: str
    profile: PublicationProfile
    provider_id: str
    target: str
    approved_paths: tuple[str, ...]
    diff_digest: str
    unified_diff: str
    base_source_revision: str
    resulting_source_revision: str
    created_at: str
    signature: ProviderBundleSignature | None = None
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "plan_id",
            "plan_digest",
            "provider_id",
            "target",
            "diff_digest",
            "base_source_revision",
            "resulting_source_revision",
        ):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        object.__setattr__(self, "profile", PublicationProfile(self.profile))
        object.__setattr__(self, "approved_paths", _paths(self.approved_paths))
        if not self.approved_paths:
            raise ValueError("publication change bundle requires approved_paths")
        if sha256_digest(self.unified_diff.encode("utf-8")) != self.diff_digest:
            raise ValueError("publication change bundle diff_digest does not match unified_diff")
        object.__setattr__(self, "created_at", normalize_rfc3339(self.created_at))
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        expected = sha256_digest(canonical_json_bytes(self.digest_payload()))
        if self.bundle_digest != expected:
            raise ValueError("publication bundle_digest does not match bundle payload")
        if self.bundle_id != _record_id("bundle", expected):
            raise ValueError("publication bundle_id does not match bundle_digest")
        if self.signature is not None and self.signature.signed_digest != self.bundle_digest:
            raise ValueError("publication bundle signature does not bind bundle_digest")

    @classmethod
    def create(
        cls,
        plan: PublicationPlan,
        profile: PublicationProfileConfig,
        *,
        created_at: str,
        extensions: Mapping[str, Any] | None = None,
    ) -> PublicationChangeBundle:
        target = profile.external_target or profile.target_repository_id or profile.repository_id
        payload = _bundle_payload(
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            profile=profile.profile,
            provider_id=profile.provider_id,
            target=target,
            approved_paths=plan.changeset.paths,
            diff_digest=plan.changeset.diff_sha256,
            unified_diff=plan.changeset.unified_diff,
            base_source_revision=plan.changeset.previous_source_revision,
            resulting_source_revision=plan.changeset.resulting_source_revision,
            created_at=normalize_rfc3339(created_at),
        )
        digest = sha256_digest(canonical_json_bytes(payload))
        return cls(
            bundle_id=_record_id("bundle", digest),
            bundle_digest=digest,
            signature=None,
            extensions=extensions,
            **cast(Any, payload),
        )

    def with_signature(self, signature: ProviderBundleSignature) -> PublicationChangeBundle:
        return replace(self, signature=signature)

    def digest_payload(self) -> dict[str, object]:
        return _bundle_payload(
            plan_id=self.plan_id,
            plan_digest=self.plan_digest,
            profile=self.profile,
            provider_id=self.provider_id,
            target=self.target,
            approved_paths=self.approved_paths,
            diff_digest=self.diff_digest,
            unified_diff=self.unified_diff,
            base_source_revision=self.base_source_revision,
            resulting_source_revision=self.resulting_source_revision,
            created_at=self.created_at,
        )

    def to_dict(self, projection: ProviderProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        payload: dict[str, object] = {
            "schema_version": PUBLICATION_PROVIDER_SCHEMA_VERSION,
            "record_type": "furatena.publication-provider.bundle",
            "bundle_id": self.bundle_id,
            "bundle_digest": self.bundle_digest,
            **self.digest_payload(),
            "signature": self.signature.to_dict() if self.signature else None,
        }
        if projection != "trusted":
            payload.pop("unified_diff", None)
        else:
            payload["extensions"] = dict(self.extensions or {})
        if projection == "public":
            return {
                key: payload[key]
                for key in (
                    "schema_version",
                    "record_type",
                    "bundle_id",
                    "bundle_digest",
                    "profile",
                    "provider_id",
                    "created_at",
                )
            }
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationChangeBundle:
        _record_header(value, "furatena.publication-provider.bundle")
        signature = value.get("signature")
        return cls(
            bundle_id=str(value.get("bundle_id") or ""),
            bundle_digest=str(value.get("bundle_digest") or ""),
            plan_id=str(value.get("plan_id") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            profile=PublicationProfile(str(value.get("profile") or "")),
            provider_id=str(value.get("provider_id") or ""),
            target=str(value.get("target") or ""),
            approved_paths=_string_tuple(value.get("approved_paths")),
            diff_digest=str(value.get("diff_digest") or ""),
            unified_diff=str(value.get("unified_diff") or ""),
            base_source_revision=str(value.get("base_source_revision") or ""),
            resulting_source_revision=str(value.get("resulting_source_revision") or ""),
            created_at=str(value.get("created_at") or ""),
            signature=(
                ProviderBundleSignature.from_dict(_mapping(signature, "signature"))
                if signature is not None
                else None
            ),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@runtime_checkable
class PublicationChangeProvider(Protocol):
    id: str

    def inspect(self, request: PublicationChangeRequest) -> RepositoryInspection: ...

    def prepare(
        self,
        request: PublicationChangeRequest,
        inspection: RepositoryInspection,
    ) -> PublicationChangeResult: ...

    def commit(
        self,
        request: PublicationChangeRequest,
        prepared: PublicationChangeResult,
    ) -> PublicationChangeResult: ...

    def propose_review(
        self,
        request: PublicationChangeRequest,
        committed: PublicationChangeResult,
    ) -> PublicationChangeResult: ...

    def observe_review(
        self,
        request: PublicationChangeRequest,
        proposed: PublicationChangeResult,
    ) -> PublicationChangeResult: ...

    def reconcile(
        self,
        request: PublicationChangeRequest,
        previous: PublicationChangeResult,
    ) -> PublicationChangeResult: ...


def provider_operations_for(profile: PublicationProfile) -> tuple[ProviderOperation, ...]:
    profile = PublicationProfile(profile)
    if profile == PublicationProfile.LOCAL_ONLY:
        return (
            ProviderOperation.INSPECT,
            ProviderOperation.PREPARE,
            ProviderOperation.RECONCILE,
        )
    if profile == PublicationProfile.COMMIT:
        return (
            ProviderOperation.INSPECT,
            ProviderOperation.PREPARE,
            ProviderOperation.COMMIT,
            ProviderOperation.RECONCILE,
        )
    if profile == PublicationProfile.PULL_REQUEST:
        return (
            ProviderOperation.INSPECT,
            ProviderOperation.PREPARE,
            ProviderOperation.COMMIT,
            ProviderOperation.PROPOSE_REVIEW,
            ProviderOperation.OBSERVE_REVIEW,
            ProviderOperation.RECONCILE,
        )
    return (ProviderOperation.PREPARE, ProviderOperation.RECONCILE)


def validate_change_request(
    plan: PublicationPlan,
    profile: PublicationProfileConfig,
    request: PublicationChangeRequest,
) -> None:
    checks = (
        (request.plan_id == plan.plan_id, "plan_id"),
        (request.plan_digest == plan.plan_digest, "plan_digest"),
        (request.profile == profile.profile, "profile"),
        (request.profile_digest == profile.profile_digest, "profile_digest"),
        (request.provider_id == profile.provider_id, "provider_id"),
        (request.repository_id == profile.repository_id, "repository_id"),
        (
            request.target_repository_id == (profile.target_repository_id or profile.repository_id),
            "target_repository_id",
        ),
        (request.review_target == profile.review_target, "review_target"),
        (request.external_target == profile.external_target, "external_target"),
        (request.change_id == _change_id(plan, profile), "change_id"),
        (request.approved_paths == plan.changeset.paths, "approved_paths"),
        (_path_change_paths(request.path_changes) == plan.changeset.paths, "path_changes"),
        (request.changeset_digest == plan.changeset.diff_sha256, "changeset_digest"),
        (
            request.base_source_revision == plan.changeset.previous_source_revision,
            "base_source_revision",
        ),
        (
            request.resulting_source_revision == plan.changeset.resulting_source_revision,
            "resulting_source_revision",
        ),
    )
    for valid, field_name in checks:
        if not valid:
            _contract_error(
                PublicationFailureDisposition.CONFLICT,
                f"request.{field_name}_mismatch",
                f"publication change request {field_name} does not match approved plan",
                "Create a fresh request from the immutable publication plan.",
            )
    allowed_operations = provider_operations_for(profile.profile)
    if request.operation not in allowed_operations:
        _contract_error(
            PublicationFailureDisposition.TERMINAL,
            "request.operation_not_in_profile",
            "publication provider operation is not valid for the configured profile",
            "Use an operation declared by the publication profile.",
        )
    if request.dry_run and request.operation not in {
        ProviderOperation.INSPECT,
        ProviderOperation.PREPARE,
    }:
        _contract_error(
            PublicationFailureDisposition.TERMINAL,
            "request.dry_run_effect",
            "dry-run publication request cannot perform provider effects",
            "Use inspect or prepare for dry-run planning.",
        )


def validate_repository_inspection(
    profile: PublicationProfileConfig,
    request: PublicationChangeRequest,
    inspection: RepositoryInspection,
) -> None:
    if inspection.provider_id != request.provider_id:
        _contract_error(
            PublicationFailureDisposition.CONFLICT,
            "inspection.provider_mismatch",
            "repository inspection came from a different provider",
            "Inspect through the provider selected by the publication profile.",
        )
    if inspection.repository_id != request.target_repository_id:
        _contract_error(
            PublicationFailureDisposition.CONFLICT,
            "inspection.repository_mismatch",
            "repository inspection targets a different repository",
            "Inspect the configured source/target repository pair.",
        )
    if inspection.base_source_revision != request.base_source_revision:
        _contract_error(
            PublicationFailureDisposition.CONFLICT,
            "inspection.base_revision_drift",
            "repository source revision drifted from the approved publication base",
            "Create a fresh publication plan from the current source revision.",
        )
    overlap = set(inspection.dirty_paths) & set(request.approved_paths)
    if overlap:
        _contract_error(
            PublicationFailureDisposition.CONFLICT,
            "inspection.approved_path_dirty",
            f"approved paths are already dirty: {', '.join(sorted(overlap))}",
            "Preserve the existing work and create an isolated changeset.",
        )
    if inspection.conflicted_paths:
        _contract_error(
            PublicationFailureDisposition.CONFLICT,
            "inspection.conflicted",
            "repository contains unresolved conflicts",
            "Resolve or isolate conflicts before publication.",
        )
    if profile.profile == PublicationProfile.LOCAL_ONLY:
        if inspection.dirty_paths and not profile.allow_dirty_unrelated:
            _contract_error(
                PublicationFailureDisposition.CONFLICT,
                "inspection.dirty_worktree",
                "local-only profile does not allow unrelated dirty paths",
                "Enable explicit dirty-path preservation or use a clean workspace.",
            )
    elif (
        profile.profile
        in {
            PublicationProfile.COMMIT,
            PublicationProfile.PULL_REQUEST,
        }
        and not inspection.isolated
    ):
        _contract_error(
            PublicationFailureDisposition.TERMINAL,
            "inspection.not_isolated",
            "commit and pull-request profiles require an isolated changeset",
            "Prepare an isolated worktree or temporary checkout at the approved base.",
        )
    if (
        profile.profile == PublicationProfile.COMMIT
        and inspection.protection == ProviderProtectionState.PROTECTED
    ):
        _contract_error(
            PublicationFailureDisposition.AUTHORIZATION,
            "inspection.protected_base",
            "direct commit profile cannot target a protected base",
            "Use the pull-request profile and satisfy required review.",
        )
    required = set(provider_operations_for(profile.profile)) - {ProviderOperation.RECONCILE}
    missing = required - set(inspection.permissions)
    if missing:
        _contract_error(
            PublicationFailureDisposition.AUTHORIZATION,
            "inspection.permission_missing",
            "provider permission is missing for: "
            + ", ".join(item.value for item in sorted(missing, key=str)),
            "Grant the required provider capability or select another profile.",
        )


def validate_provider_result(
    plan: PublicationPlan,
    request: PublicationChangeRequest,
    result: PublicationChangeResult,
) -> None:
    if (
        result.request_id != request.request_id
        or result.request_digest != request.request_digest
        or result.plan_digest != plan.plan_digest
        or result.change_id != request.change_id
        or result.profile != request.profile
        or result.operation != request.operation
        or result.provider_id != request.provider_id
        or result.repository_id != request.target_repository_id
    ):
        _contract_error(
            PublicationFailureDisposition.CONFLICT,
            "result.request_mismatch",
            "provider result does not belong to this publication request",
            "Reconcile using the exact provider request and plan.",
        )
    unexpected = set(result.changed_paths) - set(request.approved_paths)
    if unexpected:
        _contract_error(
            PublicationFailureDisposition.TERMINAL,
            "result.unapproved_paths",
            f"provider result includes unapproved paths: {', '.join(sorted(unexpected))}",
            "Discard the provider output and investigate the adapter.",
        )
    if result.observed_base_source_revision != request.base_source_revision:
        _contract_error(
            PublicationFailureDisposition.CONFLICT,
            "result.base_revision_drift",
            "provider result used a different source base revision",
            "Create a fresh plan and changeset from current source.",
        )
    if (
        result.outcome
        in {
            ProviderOutcome.PREPARED,
            ProviderOutcome.DIRTY_WORKING_TREE,
            ProviderOutcome.COMMITTED,
            ProviderOutcome.REVIEW_OPEN,
            ProviderOutcome.MERGED,
            ProviderOutcome.HANDED_OFF,
        }
        and result.resulting_source_revision != request.resulting_source_revision
    ):
        _contract_error(
            PublicationFailureDisposition.TERMINAL,
            "result.revision_mismatch",
            "provider result does not match approved resulting source revision",
            "Discard the provider output and reapply only the approved changeset.",
        )


def validate_change_bundle(
    plan: PublicationPlan,
    bundle: PublicationChangeBundle,
    *,
    require_signature: bool = True,
) -> None:
    checks = (
        bundle.plan_id == plan.plan_id,
        bundle.plan_digest == plan.plan_digest,
        bundle.approved_paths == plan.changeset.paths,
        bundle.diff_digest == plan.changeset.diff_sha256,
        bundle.base_source_revision == plan.changeset.previous_source_revision,
        bundle.resulting_source_revision == plan.changeset.resulting_source_revision,
    )
    if not all(checks):
        _contract_error(
            PublicationFailureDisposition.CONFLICT,
            "bundle.plan_mismatch",
            "publication change bundle does not match the approved plan",
            "Create a fresh bundle from the immutable publication plan.",
        )
    if require_signature and bundle.signature is None:
        _contract_error(
            PublicationFailureDisposition.AUTHORIZATION,
            "bundle.signature_missing",
            "external publication change bundle is not signed",
            "Sign the canonical bundle digest with an approved workflow key.",
        )


def provider_record_envelope(
    record: PublicationChangeRequest | PublicationChangeResult | PublicationChangeBundle,
    *,
    transport: ProviderTransport,
) -> dict[str, object]:
    if isinstance(record, PublicationChangeRequest):
        key = "publication_change_request"
    elif isinstance(record, PublicationChangeResult):
        key = "publication_change_result"
    else:
        key = "publication_change_bundle"
    payload = record.to_dict("trusted")
    if transport == "cli":
        return {"data": {key: payload}}
    if transport == "http":
        return {key: payload}
    if transport == "mcp":
        return {"structuredContent": {key: payload}}
    if transport == "automation":
        return {"payload": {key: payload}}
    raise ValueError(f"unsupported publication provider transport: {transport}")


def provider_record_from_envelope(
    envelope: Mapping[str, Any],
    *,
    transport: ProviderTransport,
) -> PublicationChangeRequest | PublicationChangeResult | PublicationChangeBundle:
    if transport == "cli":
        container = _mapping(envelope.get("data"), "data")
    elif transport == "http":
        container = envelope
    elif transport == "mcp":
        container = _mapping(envelope.get("structuredContent"), "structuredContent")
    elif transport == "automation":
        container = _mapping(envelope.get("payload"), "payload")
    else:
        raise ValueError(f"unsupported publication provider transport: {transport}")
    candidates = [
        (key, item) for key, item in container.items() if str(key).startswith("publication_change_")
    ]
    if len(candidates) != 1:
        raise ValueError("provider envelope must contain exactly one publication change record")
    key, raw = candidates[0]
    value = _mapping(raw, "publication change record")
    readers = {
        "publication_change_request": PublicationChangeRequest.from_dict,
        "publication_change_result": PublicationChangeResult.from_dict,
        "publication_change_bundle": PublicationChangeBundle.from_dict,
    }
    try:
        return readers[str(key)](value)
    except KeyError as exc:
        raise ValueError(f"unsupported publication provider envelope record: {key}") from exc


_INSPECTION_PATH_FIELDS = (
    "staged_paths",
    "unstaged_paths",
    "untracked_paths",
    "ignored_paths",
    "conflicted_paths",
)


def _inspection_payload(**values: Any) -> dict[str, object]:
    return {
        "provider_id": values["provider_id"],
        "repository_id": values["repository_id"],
        "source_repository_id": values["source_repository_id"],
        "base_source_revision": values["base_source_revision"],
        "repository_revision": values["repository_revision"],
        "current_branch": values["current_branch"],
        "detached": values["detached"],
        "isolated": values["isolated"],
        "fork": values["fork"],
        "protection": ProviderProtectionState(values["protection"]).value,
        "permissions": [ProviderOperation(item).value for item in values["permissions"]],
        "staged_paths": list(values["staged_paths"]),
        "unstaged_paths": list(values["unstaged_paths"]),
        "untracked_paths": list(values["untracked_paths"]),
        "ignored_paths": list(values["ignored_paths"]),
        "conflicted_paths": list(values["conflicted_paths"]),
        "observed_at": values["observed_at"],
    }


def _request_payload(**values: Any) -> dict[str, object]:
    return {
        "change_id": values["change_id"],
        "profile": PublicationProfile(values["profile"]).value,
        "profile_digest": values["profile_digest"],
        "operation": ProviderOperation(values["operation"]).value,
        "plan_id": values["plan_id"],
        "plan_digest": values["plan_digest"],
        "changeset_digest": values["changeset_digest"],
        "approved_paths": list(values["approved_paths"]),
        "path_changes": [item.to_dict() for item in values["path_changes"]],
        "base_source_revision": values["base_source_revision"],
        "resulting_source_revision": values["resulting_source_revision"],
        "provider_id": values["provider_id"],
        "repository_id": values["repository_id"],
        "target_repository_id": values["target_repository_id"],
        "repository_base_revision": values["repository_base_revision"],
        "branch_name": values["branch_name"],
        "review_target": values["review_target"],
        "external_target": values["external_target"],
        "attribution": values["attribution"].to_dict(),
        "actor": values["actor"].to_dict(),
        "dry_run": values["dry_run"],
    }


def _result_payload(**values: Any) -> dict[str, object]:
    failure = values["failure"]
    return {
        "request_id": values["request_id"],
        "request_digest": values["request_digest"],
        "plan_digest": values["plan_digest"],
        "change_id": values["change_id"],
        "profile": PublicationProfile(values["profile"]).value,
        "operation": ProviderOperation(values["operation"]).value,
        "outcome": ProviderOutcome(values["outcome"]).value,
        "provider_id": values["provider_id"],
        "repository_id": values["repository_id"],
        "observed_base_source_revision": values["observed_base_source_revision"],
        "resulting_source_revision": values["resulting_source_revision"],
        "changed_paths": list(values["changed_paths"]),
        "preserved_paths": list(values["preserved_paths"]),
        "branch_name": values["branch_name"],
        "commit_id": values["commit_id"],
        "review_id": values["review_id"],
        "review_url": values["review_url"],
        "review_state": ProviderReviewState(values["review_state"]).value,
        "merge_revision": values["merge_revision"],
        "protection": ProviderProtectionState(values["protection"]).value,
        "failure": failure.to_dict() if failure else None,
        "reconciliation": ProviderReconciliationState(values["reconciliation"]).value,
        "evidence_refs": list(values["evidence_refs"]),
        "observed_at": values["observed_at"],
    }


def _bundle_payload(**values: Any) -> dict[str, object]:
    return {
        "plan_id": values["plan_id"],
        "plan_digest": values["plan_digest"],
        "profile": PublicationProfile(values["profile"]).value,
        "provider_id": values["provider_id"],
        "target": values["target"],
        "approved_paths": list(values["approved_paths"]),
        "diff_digest": values["diff_digest"],
        "unified_diff": values["unified_diff"],
        "base_source_revision": values["base_source_revision"],
        "resulting_source_revision": values["resulting_source_revision"],
        "created_at": values["created_at"],
    }


def _change_id(plan: PublicationPlan, profile: PublicationProfileConfig) -> str:
    digest = sha256_digest(
        canonical_json_bytes(
            {
                "schema_version": PUBLICATION_PROVIDER_SCHEMA_VERSION,
                "plan_digest": plan.plan_digest,
                "profile_digest": profile.profile_digest,
                "provider_id": profile.provider_id,
                "repository_id": profile.repository_id,
                "target_repository_id": profile.target_repository_id or profile.repository_id,
            }
        )
    )
    return _record_id("change", digest)


def _path_change_paths(changes: tuple[ProviderPathChange, ...]) -> tuple[str, ...]:
    return _paths(path for change in changes for path in change.approved_paths)


def _branch_name(profile: PublicationProfileConfig, change_id: str) -> str | None:
    if profile.profile != PublicationProfile.PULL_REQUEST:
        return None
    assert profile.branch_template is not None
    return profile.branch_template.replace("{change_id}", change_id)


def _record_id(prefix: str, digest: str) -> str:
    return f"{prefix}-{digest.removeprefix('sha256:')[:24]}"


def _record_header(value: Mapping[str, Any], expected: str) -> None:
    if value.get("schema_version") != PUBLICATION_PROVIDER_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported publication provider schema_version: {value.get('schema_version')!r}"
        )
    if value.get("record_type") != expected:
        raise ValueError(f"expected publication provider record_type {expected!r}")


def _contract_error(
    disposition: PublicationFailureDisposition,
    code: str,
    message: str,
    remediation: str,
) -> None:
    raise ProviderContractError(disposition, code, message, remediation)


def _required(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"publication provider {label} is required")
    return text


def _optional_string(value: object) -> str | None:
    return _required(value, "optional value") if value is not None else None


def _logical_path(value: object) -> str:
    text = _required(value, "logical path").replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
        raise ValueError(f"invalid publication provider logical path: {value!r}")
    return path.as_posix()


def _paths(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(sorted({_logical_path(item) for item in values}))


def _sorted_strings(values: object) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in _sequence(values) if str(item).strip()}))


def _string_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(value))


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str | bytes | bytearray | Mapping):
        raise TypeError("publication provider sequence field must be an array")
    if not isinstance(value, Iterable):
        raise TypeError("publication provider sequence field must be an array")
    return tuple(value)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"publication provider {label} must be an object")
    return {str(key): item for key, item in value.items()}


def _projection(value: str) -> None:
    if value not in {"trusted", "audit", "public"}:
        raise ValueError(f"unsupported publication provider projection: {value}")
