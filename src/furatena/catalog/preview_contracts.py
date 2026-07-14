"""Versioned, provider-neutral contracts for ephemeral pull-request previews."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, cast, runtime_checkable

PREVIEW_SCHEMA_VERSION = 1

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class PreviewRecordType(StrEnum):
    REQUEST = "furatena.preview.request"
    MANIFEST = "furatena.preview.manifest"


class PreviewAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    STOP = "stop"


class PreviewState(StrEnum):
    REQUESTED = "requested"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


class PreviewCheckStatus(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class PreviewFailureDisposition(StrEnum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    CONFLICT = "conflict"
    AUTHORIZATION = "authorization"


class PreviewIndexingPolicy(StrEnum):
    NOINDEX_NOFOLLOW = "noindex,nofollow"


class PreviewTrustPolicy(StrEnum):
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    ALLOW = "allow"


_TRANSITIONS: dict[PreviewState, frozenset[PreviewState]] = {
    PreviewState.REQUESTED: frozenset(
        {PreviewState.BUILDING, PreviewState.FAILED, PreviewState.STOPPING}
    ),
    PreviewState.BUILDING: frozenset(
        {PreviewState.READY, PreviewState.FAILED, PreviewState.STOPPING}
    ),
    PreviewState.READY: frozenset(
        {PreviewState.BUILDING, PreviewState.FAILED, PreviewState.STOPPING}
    ),
    PreviewState.FAILED: frozenset(
        {PreviewState.REQUESTED, PreviewState.BUILDING, PreviewState.STOPPING}
    ),
    PreviewState.STOPPING: frozenset({PreviewState.STOPPED, PreviewState.FAILED}),
    PreviewState.STOPPED: frozenset({PreviewState.REQUESTED}),
}


def preview_transition_allowed(previous: PreviewState, resulting: PreviewState) -> bool:
    """Return whether the provider-neutral preview lifecycle permits a transition."""

    return PreviewState(resulting) in _TRANSITIONS[PreviewState(previous)]


def require_preview_transition(previous: PreviewState, resulting: PreviewState) -> None:
    """Reject an invalid preview lifecycle transition."""

    if not preview_transition_allowed(previous, resulting):
        raise ValueError(f"invalid preview transition: {previous.value} -> {resulting.value}")


@dataclass(frozen=True, slots=True)
class PreviewSource:
    repository_id: str
    repository_url: str
    pull_request_number: int
    head_ref: str
    head_sha: str
    review_url: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository_id", _required(self.repository_id, "repository_id"))
        object.__setattr__(self, "repository_url", _url(self.repository_url, "repository_url"))
        if self.pull_request_number < 1:
            raise ValueError("preview pull_request_number must be positive")
        object.__setattr__(self, "head_ref", _required(self.head_ref, "head_ref"))
        sha = str(self.head_sha).lower()
        if not _SHA_RE.fullmatch(sha):
            raise ValueError("preview head_sha must be a 40- or 64-character lowercase hex SHA")
        object.__setattr__(self, "head_sha", sha)
        if self.review_url is not None:
            object.__setattr__(self, "review_url", _url(self.review_url, "review_url"))

    def to_dict(self) -> dict[str, object]:
        return {
            "repository_id": self.repository_id,
            "repository_url": self.repository_url,
            "pull_request_number": self.pull_request_number,
            "head_ref": self.head_ref,
            "head_sha": self.head_sha,
            "review_url": self.review_url,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewSource:
        return cls(
            repository_id=str(value.get("repository_id") or ""),
            repository_url=str(value.get("repository_url") or ""),
            pull_request_number=int(value.get("pull_request_number") or 0),
            head_ref=str(value.get("head_ref") or ""),
            head_sha=str(value.get("head_sha") or ""),
            review_url=_optional_string(value.get("review_url")),
        )


@dataclass(frozen=True, slots=True)
class PreviewProviderIdentity:
    provider_id: str
    project_id: str
    environment_id: str | None = None
    deployment_id: str | None = None
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _required(self.provider_id, "provider_id"))
        object.__setattr__(self, "project_id", _required(self.project_id, "project_id"))
        object.__setattr__(
            self, "environment_id", _optional_required(self.environment_id, "environment_id")
        )
        object.__setattr__(
            self, "deployment_id", _optional_required(self.deployment_id, "deployment_id")
        )
        object.__setattr__(self, "capabilities", _string_set(self.capabilities, "capabilities"))

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "project_id": self.project_id,
            "environment_id": self.environment_id,
            "deployment_id": self.deployment_id,
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewProviderIdentity:
        return cls(
            provider_id=str(value.get("provider_id") or ""),
            project_id=str(value.get("project_id") or ""),
            environment_id=_optional_string(value.get("environment_id")),
            deployment_id=_optional_string(value.get("deployment_id")),
            capabilities=_string_tuple(value.get("capabilities")),
        )


@dataclass(frozen=True, slots=True)
class PreviewAccessPolicy:
    authentication_required: bool
    authentication_scheme: str | None
    indexing: PreviewIndexingPolicy = PreviewIndexingPolicy.NOINDEX_NOFOLLOW
    untrusted_forks: PreviewTrustPolicy = PreviewTrustPolicy.DENY
    bot_pull_requests: PreviewTrustPolicy = PreviewTrustPolicy.DENY

    def __post_init__(self) -> None:
        if self.authentication_required:
            object.__setattr__(
                self,
                "authentication_scheme",
                _required(self.authentication_scheme or "", "authentication_scheme"),
            )
        elif self.authentication_scheme is not None:
            raise ValueError("preview authentication_scheme requires authentication_required")
        object.__setattr__(self, "indexing", PreviewIndexingPolicy(self.indexing))
        object.__setattr__(self, "untrusted_forks", PreviewTrustPolicy(self.untrusted_forks))
        object.__setattr__(self, "bot_pull_requests", PreviewTrustPolicy(self.bot_pull_requests))

    def to_dict(self) -> dict[str, object]:
        return {
            "authentication_required": self.authentication_required,
            "authentication_scheme": self.authentication_scheme,
            "indexing": self.indexing.value,
            "untrusted_forks": self.untrusted_forks.value,
            "bot_pull_requests": self.bot_pull_requests.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewAccessPolicy:
        return cls(
            authentication_required=bool(value.get("authentication_required")),
            authentication_scheme=_optional_string(value.get("authentication_scheme")),
            indexing=PreviewIndexingPolicy(str(value.get("indexing") or "")),
            untrusted_forks=PreviewTrustPolicy(str(value.get("untrusted_forks") or "")),
            bot_pull_requests=PreviewTrustPolicy(str(value.get("bot_pull_requests") or "")),
        )


@dataclass(frozen=True, slots=True)
class PreviewSurfaces:
    human_url: str
    markdown_url_template: str
    llms_url: str
    catalog_url: str
    query_url: str
    search_url: str
    metadata_url: str
    health_url: str
    readiness_url: str
    extensions: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "human_url",
            "llms_url",
            "catalog_url",
            "query_url",
            "search_url",
            "metadata_url",
            "health_url",
            "readiness_url",
        ):
            object.__setattr__(self, field_name, _url(getattr(self, field_name), field_name))
        template = _required(self.markdown_url_template, "markdown_url_template")
        if "{path}" not in template:
            raise ValueError("preview markdown_url_template must contain {path}")
        _url(template.replace("{path}", "docs/example"), "markdown_url_template")
        object.__setattr__(self, "markdown_url_template", template)
        object.__setattr__(self, "extensions", _plain_mapping(self.extensions, "extensions"))

    def to_dict(self) -> dict[str, object]:
        return {
            "human_url": self.human_url,
            "markdown_url_template": self.markdown_url_template,
            "llms_url": self.llms_url,
            "catalog_url": self.catalog_url,
            "query_url": self.query_url,
            "search_url": self.search_url,
            "metadata_url": self.metadata_url,
            "health_url": self.health_url,
            "readiness_url": self.readiness_url,
            "extensions": dict(self.extensions),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewSurfaces:
        names = (
            "human_url",
            "markdown_url_template",
            "llms_url",
            "catalog_url",
            "query_url",
            "search_url",
            "metadata_url",
            "health_url",
            "readiness_url",
        )
        return cls(
            **{name: str(value.get(name) or "") for name in names},
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@dataclass(frozen=True, slots=True)
class PreviewArtifact:
    fingerprint: str
    build_id: str
    source_sha: str
    catalog_schema_version: int
    frozen_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", _digest(self.fingerprint, "fingerprint"))
        object.__setattr__(self, "build_id", _required(self.build_id, "build_id"))
        sha = str(self.source_sha).lower()
        if not _SHA_RE.fullmatch(sha):
            raise ValueError("preview artifact source_sha must be a 40- or 64-character SHA")
        object.__setattr__(self, "source_sha", sha)
        if self.catalog_schema_version < 1:
            raise ValueError("preview catalog_schema_version must be positive")
        object.__setattr__(self, "frozen_at", _timestamp(self.frozen_at, "frozen_at"))

    def to_dict(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "build_id": self.build_id,
            "source_sha": self.source_sha,
            "catalog_schema_version": self.catalog_schema_version,
            "frozen_at": self.frozen_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewArtifact:
        return cls(
            fingerprint=str(value.get("fingerprint") or ""),
            build_id=str(value.get("build_id") or ""),
            source_sha=str(value.get("source_sha") or ""),
            catalog_schema_version=int(value.get("catalog_schema_version") or 0),
            frozen_at=str(value.get("frozen_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class PreviewCheck:
    check_id: str
    status: PreviewCheckStatus
    summary: str
    observed_at: str
    details_url: str | None = None
    remediation: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_id", _required(self.check_id, "check_id"))
        object.__setattr__(self, "status", PreviewCheckStatus(self.status))
        object.__setattr__(self, "summary", _required(self.summary, "summary"))
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        if self.details_url is not None:
            object.__setattr__(self, "details_url", _url(self.details_url, "details_url"))
        object.__setattr__(self, "remediation", _optional_required(self.remediation, "remediation"))
        if self.status == PreviewCheckStatus.FAIL and self.remediation is None:
            raise ValueError("failed preview checks require remediation")

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "summary": self.summary,
            "observed_at": self.observed_at,
            "details_url": self.details_url,
            "remediation": self.remediation,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewCheck:
        return cls(
            check_id=str(value.get("check_id") or ""),
            status=PreviewCheckStatus(str(value.get("status") or "")),
            summary=str(value.get("summary") or ""),
            observed_at=str(value.get("observed_at") or ""),
            details_url=_optional_string(value.get("details_url")),
            remediation=_optional_string(value.get("remediation")),
        )


@dataclass(frozen=True, slots=True)
class PreviewFailure:
    disposition: PreviewFailureDisposition
    code: str
    safe_message: str
    remediation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", PreviewFailureDisposition(self.disposition))
        for field_name in ("code", "safe_message", "remediation"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))

    def to_dict(self) -> dict[str, str]:
        return {
            "disposition": self.disposition.value,
            "code": self.code,
            "safe_message": self.safe_message,
            "remediation": self.remediation,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewFailure:
        return cls(
            disposition=PreviewFailureDisposition(str(value.get("disposition") or "")),
            code=str(value.get("code") or ""),
            safe_message=str(value.get("safe_message") or ""),
            remediation=str(value.get("remediation") or ""),
        )


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    action: PreviewAction
    source: PreviewSource
    provider_id: str
    idempotency_key: str
    expected_state_version: int | None
    requested_at: str
    expires_at: str
    previous_preview_id: str | None = None
    correlation_id: str | None = None
    extensions: Mapping[str, object] = field(default_factory=dict)
    request_id: str = ""
    request_digest: str = ""
    schema_version: int = PREVIEW_SCHEMA_VERSION
    record_type: PreviewRecordType = PreviewRecordType.REQUEST

    def __post_init__(self) -> None:
        _record_header(self.schema_version, self.record_type, PreviewRecordType.REQUEST)
        object.__setattr__(self, "action", PreviewAction(self.action))
        object.__setattr__(self, "provider_id", _required(self.provider_id, "provider_id"))
        object.__setattr__(
            self, "idempotency_key", _required(self.idempotency_key, "idempotency_key")
        )
        if self.expected_state_version is not None and self.expected_state_version < 1:
            raise ValueError("preview expected_state_version must be positive")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(self, "expires_at", _timestamp(self.expires_at, "expires_at"))
        if _parse_timestamp(self.expires_at) <= _parse_timestamp(self.requested_at):
            raise ValueError("preview expires_at must be after requested_at")
        object.__setattr__(
            self,
            "previous_preview_id",
            _optional_required(self.previous_preview_id, "previous_preview_id"),
        )
        object.__setattr__(
            self, "correlation_id", _optional_required(self.correlation_id, "correlation_id")
        )
        if self.action == PreviewAction.CREATE and self.previous_preview_id is not None:
            raise ValueError("preview create request cannot bind previous_preview_id")
        if self.action != PreviewAction.CREATE and self.previous_preview_id is None:
            raise ValueError("preview update/stop request requires previous_preview_id")
        object.__setattr__(self, "extensions", _plain_mapping(self.extensions, "extensions"))
        digest = sha256_digest(canonical_json_bytes(self.semantic_payload()))
        if self.request_digest and self.request_digest != digest:
            raise ValueError("preview request_digest does not match request payload")
        object.__setattr__(self, "request_digest", digest)
        request_id = f"preview-request-{digest.removeprefix('sha256:')[:24]}"
        if self.request_id and self.request_id != request_id:
            raise ValueError("preview request_id does not match request payload")
        object.__setattr__(self, "request_id", request_id)

    def semantic_payload(self) -> dict[str, object]:
        """Return idempotency-comparison input without transport identity."""

        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type.value,
            "action": self.action.value,
            "source": self.source.to_dict(),
            "provider_id": self.provider_id,
            "expected_state_version": self.expected_state_version,
            "expires_at": self.expires_at,
            "previous_preview_id": self.previous_preview_id,
            "extensions": dict(self.extensions),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type.value,
            "request_id": self.request_id,
            "request_digest": self.request_digest,
            "action": self.action.value,
            "source": self.source.to_dict(),
            "provider_id": self.provider_id,
            "idempotency_key": self.idempotency_key,
            "expected_state_version": self.expected_state_version,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "previous_preview_id": self.previous_preview_id,
            "correlation_id": self.correlation_id,
            "extensions": dict(self.extensions),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewRequest:
        _record_mapping_header(value, PreviewRecordType.REQUEST)
        return cls(
            action=PreviewAction(str(value.get("action") or "")),
            source=PreviewSource.from_dict(_mapping(value.get("source"), "source")),
            provider_id=str(value.get("provider_id") or ""),
            idempotency_key=str(value.get("idempotency_key") or ""),
            expected_state_version=(
                int(value["expected_state_version"])
                if value.get("expected_state_version") is not None
                else None
            ),
            requested_at=str(value.get("requested_at") or ""),
            expires_at=str(value.get("expires_at") or ""),
            previous_preview_id=_optional_string(value.get("previous_preview_id")),
            correlation_id=_optional_string(value.get("correlation_id")),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
            request_id=str(value.get("request_id") or ""),
            request_digest=str(value.get("request_digest") or ""),
            schema_version=int(value.get("schema_version") or 0),
            record_type=PreviewRecordType(str(value.get("record_type") or "")),
        )


def preview_request_is_replay(existing: PreviewRequest, candidate: PreviewRequest) -> bool:
    """Classify a delivery replay and fail closed on idempotency-key reuse."""

    if existing.idempotency_key != candidate.idempotency_key:
        return False
    if existing.request_digest != candidate.request_digest:
        raise ValueError("preview idempotency key was reused for different request input")
    return True


@dataclass(frozen=True, slots=True)
class PreviewManifest:
    state: PreviewState
    state_version: int
    source: PreviewSource
    provider: PreviewProviderIdentity
    access: PreviewAccessPolicy
    created_at: str
    updated_at: str
    expires_at: str
    surfaces: PreviewSurfaces | None = None
    artifact: PreviewArtifact | None = None
    checks: tuple[PreviewCheck, ...] = ()
    failure: PreviewFailure | None = None
    stopped_at: str | None = None
    correlation_id: str | None = None
    extensions: Mapping[str, object] = field(default_factory=dict)
    preview_id: str = ""
    manifest_digest: str = ""
    schema_version: int = PREVIEW_SCHEMA_VERSION
    record_type: PreviewRecordType = PreviewRecordType.MANIFEST

    def __post_init__(self) -> None:
        _record_header(self.schema_version, self.record_type, PreviewRecordType.MANIFEST)
        object.__setattr__(self, "state", PreviewState(self.state))
        if self.state_version < 1:
            raise ValueError("preview state_version must be positive")
        expected_id = preview_id_for(self.source)
        if self.preview_id and self.preview_id != expected_id:
            raise ValueError("preview_id does not match repository and pull request")
        object.__setattr__(self, "preview_id", expected_id)
        for field_name in ("created_at", "updated_at", "expires_at"):
            object.__setattr__(self, field_name, _timestamp(getattr(self, field_name), field_name))
        if _parse_timestamp(self.updated_at) < _parse_timestamp(self.created_at):
            raise ValueError("preview updated_at cannot precede created_at")
        if _parse_timestamp(self.expires_at) <= _parse_timestamp(self.created_at):
            raise ValueError("preview expires_at must be after created_at")
        object.__setattr__(self, "stopped_at", _optional_timestamp(self.stopped_at, "stopped_at"))
        object.__setattr__(
            self, "correlation_id", _optional_required(self.correlation_id, "correlation_id")
        )
        checks = tuple(sorted(self.checks, key=lambda item: item.check_id))
        if len({item.check_id for item in checks}) != len(checks):
            raise ValueError("preview check IDs must be unique")
        object.__setattr__(self, "checks", checks)
        object.__setattr__(self, "extensions", _plain_mapping(self.extensions, "extensions"))
        self._validate_state_contract()
        digest = sha256_digest(canonical_json_bytes(self.semantic_payload()))
        if self.manifest_digest and self.manifest_digest != digest:
            raise ValueError("preview manifest_digest does not match manifest payload")
        object.__setattr__(self, "manifest_digest", digest)

    def _validate_state_contract(self) -> None:
        if self.artifact is not None and self.artifact.source_sha != self.source.head_sha:
            raise ValueError("preview artifact source_sha must equal reviewed head_sha")
        if self.state == PreviewState.READY:
            if self.surfaces is None or self.artifact is None:
                raise ValueError("ready preview requires surfaces and artifact identity")
            if self.provider.environment_id is None or self.provider.deployment_id is None:
                raise ValueError("ready preview requires provider environment and deployment IDs")
            readiness = next((item for item in self.checks if item.check_id == "readiness"), None)
            if readiness is None or readiness.status != PreviewCheckStatus.PASS:
                raise ValueError("ready preview requires a passing readiness check")
            if any(
                item.status in {PreviewCheckStatus.PENDING, PreviewCheckStatus.FAIL}
                for item in self.checks
            ):
                raise ValueError("ready preview cannot contain pending or failed checks")
        if self.state == PreviewState.FAILED and self.failure is None:
            raise ValueError("failed preview requires structured failure details")
        if self.state != PreviewState.FAILED and self.failure is not None:
            raise ValueError("preview failure details are only valid in failed state")
        if self.state == PreviewState.STOPPED:
            if self.stopped_at is None:
                raise ValueError("stopped preview requires stopped_at")
        elif self.stopped_at is not None:
            raise ValueError("stopped_at is only valid in stopped state")

    def semantic_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "record_type": self.record_type.value,
            "preview_id": self.preview_id,
            "state": self.state.value,
            "state_version": self.state_version,
            "source": self.source.to_dict(),
            "provider": self.provider.to_dict(),
            "access": self.access.to_dict(),
            "surfaces": self.surfaces.to_dict() if self.surfaces is not None else None,
            "artifact": self.artifact.to_dict() if self.artifact is not None else None,
            "checks": [item.to_dict() for item in self.checks],
            "failure": self.failure.to_dict() if self.failure is not None else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "stopped_at": self.stopped_at,
            "extensions": dict(self.extensions),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            **self.semantic_payload(),
            "manifest_digest": self.manifest_digest,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PreviewManifest:
        _record_mapping_header(value, PreviewRecordType.MANIFEST)
        surfaces = value.get("surfaces")
        artifact = value.get("artifact")
        failure = value.get("failure")
        return cls(
            state=PreviewState(str(value.get("state") or "")),
            state_version=int(value.get("state_version") or 0),
            source=PreviewSource.from_dict(_mapping(value.get("source"), "source")),
            provider=PreviewProviderIdentity.from_dict(_mapping(value.get("provider"), "provider")),
            access=PreviewAccessPolicy.from_dict(_mapping(value.get("access"), "access")),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            expires_at=str(value.get("expires_at") or ""),
            surfaces=(
                PreviewSurfaces.from_dict(_mapping(surfaces, "surfaces"))
                if surfaces is not None
                else None
            ),
            artifact=(
                PreviewArtifact.from_dict(_mapping(artifact, "artifact"))
                if artifact is not None
                else None
            ),
            checks=tuple(
                PreviewCheck.from_dict(_mapping(item, "checks item"))
                for item in _sequence(value.get("checks"))
            ),
            failure=(
                PreviewFailure.from_dict(_mapping(failure, "failure"))
                if failure is not None
                else None
            ),
            stopped_at=_optional_string(value.get("stopped_at")),
            correlation_id=_optional_string(value.get("correlation_id")),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
            preview_id=str(value.get("preview_id") or ""),
            manifest_digest=str(value.get("manifest_digest") or ""),
            schema_version=int(value.get("schema_version") or 0),
            record_type=PreviewRecordType(str(value.get("record_type") or "")),
        )


@runtime_checkable
class PreviewProvider(Protocol):
    """Provider adapter boundary; Railway and future providers implement this shape."""

    id: str

    def request(
        self,
        request: PreviewRequest,
        previous: PreviewManifest | None,
    ) -> PreviewManifest: ...

    def observe(self, manifest: PreviewManifest) -> PreviewManifest: ...

    def stop(self, request: PreviewRequest, manifest: PreviewManifest) -> PreviewManifest: ...


def preview_id_for(source: PreviewSource) -> str:
    """Return the stable PR-scoped identity that survives head-SHA updates."""

    payload = f"{source.repository_id}\0{source.pull_request_number}".encode()
    return f"preview-{hashlib.sha256(payload).hexdigest()[:24]}"


def canonical_json_bytes(value: object) -> bytes:
    """Encode a preview contract value deterministically for identity and replay checks."""

    return json.dumps(
        _plain(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _record_header(
    schema_version: int,
    record_type: PreviewRecordType,
    expected: PreviewRecordType,
) -> None:
    if schema_version != PREVIEW_SCHEMA_VERSION:
        raise ValueError(f"unsupported preview schema_version: {schema_version!r}")
    if PreviewRecordType(record_type) != expected:
        raise ValueError(f"expected preview record_type {expected.value!r}")


def _record_mapping_header(value: Mapping[str, Any], expected: PreviewRecordType) -> None:
    _record_header(
        int(value.get("schema_version") or 0),
        PreviewRecordType(str(value.get("record_type") or "")),
        expected,
    )


def _required(value: str, field_name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"preview {field_name} is required")
    return result


def _optional_required(value: str | None, field_name: str) -> str | None:
    return None if value is None else _required(value, field_name)


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _digest(value: str, field_name: str) -> str:
    result = str(value)
    if not _DIGEST_RE.fullmatch(result):
        raise ValueError(f"preview {field_name} must be a sha256 digest")
    return result


def _url(value: str, field_name: str) -> str:
    result = _required(value, field_name)
    if not result.startswith("https://"):
        raise ValueError(f"preview {field_name} must use https")
    return result


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _timestamp(value: str, field_name: str) -> str:
    result = _required(value, field_name)
    try:
        parsed = _parse_timestamp(result)
    except ValueError as exc:
        raise ValueError(f"preview {field_name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"preview {field_name} must include a timezone")
    return result


def _optional_timestamp(value: str | None, field_name: str) -> str | None:
    return None if value is None else _timestamp(value, field_name)


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("preview string collection must be an array")
    return tuple(str(item) for item in value)


def _string_set(value: Sequence[str], field_name: str) -> tuple[str, ...]:
    result = tuple(sorted(_required(item, field_name) for item in value))
    if len(result) != len(set(result)):
        raise ValueError(f"preview {field_name} must contain unique values")
    return result


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"preview {field_name} must be an object")
    return cast("Mapping[str, Any]", value)


def _plain_mapping(value: Mapping[str, object] | object, field_name: str) -> dict[str, object]:
    mapping = _mapping(value, field_name)
    result = _plain(mapping)
    if not isinstance(result, dict):
        raise TypeError(f"preview {field_name} must be an object")
    return cast("dict[str, object]", result)


def _sequence(value: object) -> Sequence[object]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("preview value must be an array")
    return value


def _plain(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    serializer = getattr(value, "to_dict", None)
    if callable(serializer):
        return _plain(serializer())
    raise TypeError(f"preview contract value is not JSON-serializable: {type(value).__name__}")
