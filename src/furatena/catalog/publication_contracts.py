"""Versioned, provider-neutral publication workflow records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

PUBLICATION_SCHEMA_VERSION = 1

type PublicationProjection = Literal["trusted", "audit", "public"]

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class PublicationRecordType(StrEnum):
    PLAN = "furatena.publication.plan"
    DECISION = "furatena.publication.decision"
    EVENT = "furatena.publication.event"
    STATE = "furatena.publication.state"


class PublicationOperation(StrEnum):
    DRAFT = "draft"
    PUBLISH = "publish"
    UNPUBLISH = "unpublish"
    ARCHIVE = "archive"
    ROLLBACK = "rollback"


class PublicationState(StrEnum):
    PROPOSED = "proposed"
    VALIDATING = "validating"
    REVIEWABLE = "reviewable"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    APPLIED = "applied"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class PublicationDecisionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"
    REVOKE = "revoke"
    WAIVE_WARNING = "waive_warning"
    EMERGENCY_OVERRIDE = "emergency_override"
    EXPIRE = "expire"
    SUPERSEDE = "supersede"


class PublicationFailureDisposition(StrEnum):
    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    CONFLICT = "conflict"
    AUTHORIZATION = "authorization"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class PublicationRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class PublicationActor:
    actor: str
    identity_source: str
    roles: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "actor", _required(self.actor, "actor"))
        object.__setattr__(
            self,
            "identity_source",
            _required(self.identity_source, "identity_source"),
        )
        object.__setattr__(self, "roles", _sorted_strings(self.roles))
        object.__setattr__(self, "teams", _sorted_strings(self.teams))

    def to_dict(self) -> dict[str, object]:
        return {
            "actor": self.actor,
            "identity_source": self.identity_source,
            "roles": list(self.roles),
            "teams": list(self.teams),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationActor:
        return cls(
            actor=str(value.get("actor") or ""),
            identity_source=str(value.get("identity_source") or ""),
            roles=_string_tuple(value.get("roles")),
            teams=_string_tuple(value.get("teams")),
        )


@dataclass(frozen=True, slots=True)
class PublicationIdentity:
    tenant: str
    workspace: str
    site: str
    mount: str
    node_id: str
    source_path: str

    def __post_init__(self) -> None:
        for field_name in ("tenant", "workspace", "site", "mount", "node_id"):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name),
            )
        object.__setattr__(self, "source_path", _logical_path(self.source_path))

    def to_dict(self) -> dict[str, str]:
        return {
            "tenant": self.tenant,
            "workspace": self.workspace,
            "site": self.site,
            "mount": self.mount,
            "node_id": self.node_id,
            "source_path": self.source_path,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationIdentity:
        return cls(**{name: str(value.get(name) or "") for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class PublicationRequest:
    operation: PublicationOperation
    previous_visibility: str
    resulting_visibility: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", PublicationOperation(self.operation))
        object.__setattr__(
            self,
            "previous_visibility",
            _required(self.previous_visibility, "previous_visibility"),
        )
        object.__setattr__(
            self,
            "resulting_visibility",
            _required(self.resulting_visibility, "resulting_visibility"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "operation": self.operation.value,
            "previous_visibility": self.previous_visibility,
            "resulting_visibility": self.resulting_visibility,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationRequest:
        return cls(
            operation=PublicationOperation(str(value.get("operation") or "")),
            previous_visibility=str(value.get("previous_visibility") or ""),
            resulting_visibility=str(value.get("resulting_visibility") or ""),
        )


@dataclass(frozen=True, slots=True)
class PublicationBindings:
    source_revision: str
    catalog_generation: str
    config_digest: str
    policy_version: str
    policy_digest: str
    validation_snapshot_id: str
    validation_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_revision", _digest(self.source_revision))
        object.__setattr__(
            self, "catalog_generation", _required(self.catalog_generation, "catalog_generation")
        )
        object.__setattr__(self, "config_digest", _digest(self.config_digest))
        object.__setattr__(self, "policy_version", _required(self.policy_version, "policy_version"))
        object.__setattr__(self, "policy_digest", _digest(self.policy_digest))
        object.__setattr__(
            self,
            "validation_snapshot_id",
            _required(self.validation_snapshot_id, "validation_snapshot_id"),
        )
        object.__setattr__(self, "validation_digest", _digest(self.validation_digest))

    def to_dict(self) -> dict[str, str]:
        return {name: str(getattr(self, name)) for name in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationBindings:
        return cls(**{name: str(value.get(name) or "") for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class PublicationFieldChange:
    field: str
    previous: Any
    resulting: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", _required(self.field, "field"))
        canonical_json_bytes(self.previous)
        canonical_json_bytes(self.resulting)

    def to_dict(self) -> dict[str, object]:
        return {
            "field": self.field,
            "previous": _plain(self.previous),
            "resulting": _plain(self.resulting),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationFieldChange:
        return cls(
            field=str(value.get("field") or ""),
            previous=value.get("previous"),
            resulting=value.get("resulting"),
        )


@dataclass(frozen=True, slots=True)
class PublicationChangeset:
    paths: tuple[str, ...]
    unified_diff: str
    diff_sha256: str
    previous_source_revision: str
    resulting_source_revision: str
    front_matter_changes: tuple[PublicationFieldChange, ...] = ()

    def __post_init__(self) -> None:
        paths = tuple(sorted(_logical_path(path) for path in self.paths))
        if not paths or len(paths) != len(set(paths)):
            raise ValueError("publication changeset paths must be non-empty and unique")
        object.__setattr__(self, "paths", paths)
        if not isinstance(self.unified_diff, str):
            raise TypeError("publication unified_diff must be text")
        observed = sha256_digest(self.unified_diff.encode("utf-8"))
        if self.diff_sha256 != observed:
            raise ValueError("publication diff_sha256 does not match unified_diff")
        object.__setattr__(self, "diff_sha256", _digest(self.diff_sha256))
        object.__setattr__(
            self,
            "previous_source_revision",
            _digest(self.previous_source_revision),
        )
        object.__setattr__(
            self,
            "resulting_source_revision",
            _digest(self.resulting_source_revision),
        )
        fields = tuple(sorted(self.front_matter_changes, key=lambda item: item.field))
        if len({item.field for item in fields}) != len(fields):
            raise ValueError("publication front-matter change fields must be unique")
        object.__setattr__(self, "front_matter_changes", fields)

    def to_dict(self) -> dict[str, object]:
        return {
            "paths": list(self.paths),
            "unified_diff": self.unified_diff,
            "diff_sha256": self.diff_sha256,
            "previous_source_revision": self.previous_source_revision,
            "resulting_source_revision": self.resulting_source_revision,
            "front_matter_changes": [item.to_dict() for item in self.front_matter_changes],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationChangeset:
        return cls(
            paths=_string_tuple(value.get("paths")),
            unified_diff=str(value.get("unified_diff") or ""),
            diff_sha256=str(value.get("diff_sha256") or ""),
            previous_source_revision=str(value.get("previous_source_revision") or ""),
            resulting_source_revision=str(value.get("resulting_source_revision") or ""),
            front_matter_changes=tuple(
                PublicationFieldChange.from_dict(_mapping(item, "front_matter_changes item"))
                for item in _sequence(value.get("front_matter_changes"))
            ),
        )


@dataclass(frozen=True, slots=True)
class PublicationValidation:
    run_id: str
    snapshot_id: str
    catalog_generation: str
    error_count: int
    warning_count: int
    info_count: int
    diagnostic_ids: tuple[str, ...]
    waivable_warning_ids: tuple[str, ...]
    diagnostics_digest: str

    def __post_init__(self) -> None:
        for field_name in ("run_id", "snapshot_id", "catalog_generation"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        for field_name in ("error_count", "warning_count", "info_count"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"publication {field_name} must be non-negative")
        object.__setattr__(self, "diagnostic_ids", _sorted_strings(self.diagnostic_ids))
        object.__setattr__(
            self,
            "waivable_warning_ids",
            _sorted_strings(self.waivable_warning_ids),
        )
        if not set(self.waivable_warning_ids) <= set(self.diagnostic_ids):
            raise ValueError("waivable warning IDs must be present in diagnostic IDs")
        object.__setattr__(self, "diagnostics_digest", _digest(self.diagnostics_digest))

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "catalog_generation": self.catalog_generation,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "diagnostic_ids": list(self.diagnostic_ids),
            "waivable_warning_ids": list(self.waivable_warning_ids),
            "diagnostics_digest": self.diagnostics_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationValidation:
        return cls(
            run_id=str(value.get("run_id") or ""),
            snapshot_id=str(value.get("snapshot_id") or ""),
            catalog_generation=str(value.get("catalog_generation") or ""),
            error_count=int(value.get("error_count") or 0),
            warning_count=int(value.get("warning_count") or 0),
            info_count=int(value.get("info_count") or 0),
            diagnostic_ids=_string_tuple(value.get("diagnostic_ids")),
            waivable_warning_ids=_string_tuple(value.get("waivable_warning_ids")),
            diagnostics_digest=str(value.get("diagnostics_digest") or ""),
        )


@dataclass(frozen=True, slots=True)
class PublicationImpact:
    affected_projections: tuple[str, ...]
    risk: PublicationRisk
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "affected_projections", _sorted_strings(self.affected_projections))
        object.__setattr__(self, "risk", PublicationRisk(self.risk))
        object.__setattr__(self, "reasons", _sorted_strings(self.reasons))

    def to_dict(self) -> dict[str, object]:
        return {
            "affected_projections": list(self.affected_projections),
            "risk": self.risk.value,
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationImpact:
        return cls(
            affected_projections=_string_tuple(value.get("affected_projections")),
            risk=PublicationRisk(str(value.get("risk") or "")),
            reasons=_string_tuple(value.get("reasons")),
        )


@dataclass(frozen=True, slots=True)
class PublicationApprovalRequirements:
    policy_version: str
    policy_digest: str
    required_count: int = 0
    eligible_roles: tuple[str, ...] = ()
    eligible_teams: tuple[str, ...] = ()
    allow_self_approval: bool = True
    separation_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _required(self.policy_version, "policy_version"))
        object.__setattr__(self, "policy_digest", _digest(self.policy_digest))
        if self.required_count < 0:
            raise ValueError("publication required_count must be non-negative")
        object.__setattr__(self, "eligible_roles", _sorted_strings(self.eligible_roles))
        object.__setattr__(self, "eligible_teams", _sorted_strings(self.eligible_teams))
        object.__setattr__(self, "separation_rules", _sorted_strings(self.separation_rules))

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "required_count": self.required_count,
            "eligible_roles": list(self.eligible_roles),
            "eligible_teams": list(self.eligible_teams),
            "allow_self_approval": self.allow_self_approval,
            "separation_rules": list(self.separation_rules),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationApprovalRequirements:
        return cls(
            policy_version=str(value.get("policy_version") or ""),
            policy_digest=str(value.get("policy_digest") or ""),
            required_count=int(value.get("required_count") or 0),
            eligible_roles=_string_tuple(value.get("eligible_roles")),
            eligible_teams=_string_tuple(value.get("eligible_teams")),
            allow_self_approval=bool(value.get("allow_self_approval", True)),
            separation_rules=_string_tuple(value.get("separation_rules")),
        )


@dataclass(frozen=True, slots=True)
class PublicationOutputIntent:
    kind: str
    target: str
    environment: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _required(self.kind, "output intent kind"))
        object.__setattr__(self, "target", _required(self.target, "output intent target"))
        if self.environment is not None:
            object.__setattr__(self, "environment", _required(self.environment, "environment"))

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "target": self.target,
            "environment": self.environment,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationOutputIntent:
        environment = value.get("environment")
        return cls(
            kind=str(value.get("kind") or ""),
            target=str(value.get("target") or ""),
            environment=str(environment) if environment is not None else None,
        )


@dataclass(frozen=True, slots=True)
class PublicationFailure:
    disposition: PublicationFailureDisposition
    code: str
    safe_message: str
    remediation: str
    retry_target: PublicationState | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", PublicationFailureDisposition(self.disposition))
        object.__setattr__(self, "code", _required(self.code, "failure code"))
        object.__setattr__(self, "safe_message", _required(self.safe_message, "safe_message"))
        object.__setattr__(self, "remediation", _required(self.remediation, "remediation"))
        if self.retry_target is not None:
            object.__setattr__(self, "retry_target", PublicationState(self.retry_target))

    def to_dict(self) -> dict[str, object]:
        return {
            "disposition": self.disposition.value,
            "code": self.code,
            "safe_message": self.safe_message,
            "remediation": self.remediation,
            "retry_target": self.retry_target.value if self.retry_target else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationFailure:
        retry_target = value.get("retry_target")
        return cls(
            disposition=PublicationFailureDisposition(str(value.get("disposition") or "")),
            code=str(value.get("code") or ""),
            safe_message=str(value.get("safe_message") or ""),
            remediation=str(value.get("remediation") or ""),
            retry_target=PublicationState(str(retry_target)) if retry_target else None,
        )


@dataclass(frozen=True, slots=True)
class PublicationOutputReference:
    kind: str
    identifier: str
    status: str
    revision: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("kind", "identifier", "status"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        if self.revision is not None:
            object.__setattr__(self, "revision", _required(self.revision, "revision"))
        if self.url is not None:
            object.__setattr__(self, "url", _required(self.url, "url"))

    def to_dict(self, *, include_private: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "identifier": self.identifier,
            "status": self.status,
            "revision": self.revision,
        }
        if include_private:
            payload["url"] = self.url
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationOutputReference:
        return cls(
            kind=str(value.get("kind") or ""),
            identifier=str(value.get("identifier") or ""),
            status=str(value.get("status") or ""),
            revision=str(value["revision"]) if value.get("revision") is not None else None,
            url=str(value["url"]) if value.get("url") is not None else None,
        )


@dataclass(frozen=True, slots=True)
class PublicationRecordReference:
    record_id: str
    record_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", _required(self.record_id, "record_id"))
        object.__setattr__(self, "record_digest", _digest(self.record_digest))

    def to_dict(self) -> dict[str, str]:
        return {"record_id": self.record_id, "record_digest": self.record_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationRecordReference:
        return cls(
            record_id=str(value.get("record_id") or ""),
            record_digest=str(value.get("record_digest") or ""),
        )


@dataclass(frozen=True, slots=True)
class PublicationPlan:
    plan_id: str
    plan_digest: str
    correlation_id: str
    idempotency_key: str
    creator: PublicationActor
    expires_at: str
    identity: PublicationIdentity
    request: PublicationRequest
    bindings: PublicationBindings
    changeset: PublicationChangeset
    validation: PublicationValidation
    impact: PublicationImpact
    approval_requirements: PublicationApprovalRequirements
    intended_outputs: tuple[PublicationOutputIntent, ...] = ()
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _required(self.plan_id, "plan_id"))
        object.__setattr__(self, "plan_digest", _digest(self.plan_digest))
        object.__setattr__(self, "correlation_id", _required(self.correlation_id, "correlation_id"))
        object.__setattr__(
            self, "idempotency_key", _required(self.idempotency_key, "idempotency_key")
        )
        object.__setattr__(self, "expires_at", normalize_rfc3339(self.expires_at))
        outputs = tuple(
            sorted(
                self.intended_outputs,
                key=lambda item: (item.kind, item.target, item.environment or ""),
            )
        )
        object.__setattr__(self, "intended_outputs", outputs)
        if (
            self.bindings.policy_version != self.approval_requirements.policy_version
            or self.bindings.policy_digest != self.approval_requirements.policy_digest
        ):
            raise ValueError("publication approval policy does not match bound policy")
        if (
            self.bindings.catalog_generation != self.validation.catalog_generation
            or self.bindings.validation_snapshot_id != self.validation.snapshot_id
        ):
            raise ValueError("publication validation does not match bound snapshot")
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        expected = sha256_digest(canonical_json_bytes(self.approval_payload()))
        if self.plan_digest != expected:
            raise ValueError("publication plan_digest does not match approval payload")
        if self.plan_id != _record_id("plan", self.plan_digest):
            raise ValueError("publication plan_id does not match plan_digest")

    @classmethod
    def create(
        cls,
        *,
        correlation_id: str,
        idempotency_key: str,
        creator: PublicationActor,
        expires_at: str,
        identity: PublicationIdentity,
        request: PublicationRequest,
        bindings: PublicationBindings,
        changeset: PublicationChangeset,
        validation: PublicationValidation,
        impact: PublicationImpact,
        approval_requirements: PublicationApprovalRequirements,
        intended_outputs: tuple[PublicationOutputIntent, ...] = (),
        extensions: Mapping[str, Any] | None = None,
    ) -> PublicationPlan:
        normalized_expiry = normalize_rfc3339(expires_at)
        outputs = tuple(
            sorted(
                intended_outputs,
                key=lambda item: (item.kind, item.target, item.environment or ""),
            )
        )
        approval_payload = _plan_approval_payload(
            creator=creator,
            expires_at=normalized_expiry,
            identity=identity,
            request=request,
            bindings=bindings,
            changeset=changeset,
            validation=validation,
            impact=impact,
            approval_requirements=approval_requirements,
            intended_outputs=outputs,
        )
        digest = sha256_digest(canonical_json_bytes(approval_payload))
        return cls(
            plan_id=_record_id("plan", digest),
            plan_digest=digest,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            creator=creator,
            expires_at=normalized_expiry,
            identity=identity,
            request=request,
            bindings=bindings,
            changeset=changeset,
            validation=validation,
            impact=impact,
            approval_requirements=approval_requirements,
            intended_outputs=outputs,
            extensions=extensions,
        )

    def approval_payload(self) -> dict[str, object]:
        return _plan_approval_payload(
            creator=self.creator,
            expires_at=self.expires_at,
            identity=self.identity,
            request=self.request,
            bindings=self.bindings,
            changeset=self.changeset,
            validation=self.validation,
            impact=self.impact,
            approval_requirements=self.approval_requirements,
            intended_outputs=self.intended_outputs,
        )

    def to_dict(self, projection: PublicationProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        base: dict[str, object] = {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "record_type": PublicationRecordType.PLAN.value,
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "expires_at": self.expires_at,
            "request": self.request.to_dict(),
            "impact": self.impact.to_dict(),
        }
        if projection == "public":
            return base
        base.update(
            {
                "correlation_id": self.correlation_id,
                "creator": (
                    self.creator.to_dict()
                    if projection == "trusted"
                    else {
                        "actor": self.creator.actor,
                        "identity_source": self.creator.identity_source,
                    }
                ),
                "validation": (
                    self.validation.to_dict()
                    if projection == "trusted"
                    else {
                        "run_id": self.validation.run_id,
                        "snapshot_id": self.validation.snapshot_id,
                        "error_count": self.validation.error_count,
                        "warning_count": self.validation.warning_count,
                        "info_count": self.validation.info_count,
                        "diagnostics_digest": self.validation.diagnostics_digest,
                    }
                ),
                "approval_requirements": self.approval_requirements.to_dict(),
                "intended_outputs": [item.to_dict() for item in self.intended_outputs],
            }
        )
        if projection == "trusted":
            base.update(
                {
                    "idempotency_key": self.idempotency_key,
                    "identity": self.identity.to_dict(),
                    "bindings": self.bindings.to_dict(),
                    "changeset": self.changeset.to_dict(),
                    "extensions": _plain(self.extensions),
                }
            )
        else:
            base["bindings"] = {
                "source_revision": self.bindings.source_revision,
                "config_digest": self.bindings.config_digest,
                "policy_digest": self.bindings.policy_digest,
                "validation_digest": self.bindings.validation_digest,
            }
            base["changeset"] = {
                "diff_sha256": self.changeset.diff_sha256,
                "path_count": len(self.changeset.paths),
            }
        return base

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationPlan:
        _record_header(value, PublicationRecordType.PLAN)
        return cls(
            plan_id=str(value.get("plan_id") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            correlation_id=str(value.get("correlation_id") or ""),
            idempotency_key=str(value.get("idempotency_key") or ""),
            creator=PublicationActor.from_dict(_mapping(value.get("creator"), "creator")),
            expires_at=str(value.get("expires_at") or ""),
            identity=PublicationIdentity.from_dict(_mapping(value.get("identity"), "identity")),
            request=PublicationRequest.from_dict(_mapping(value.get("request"), "request")),
            bindings=PublicationBindings.from_dict(_mapping(value.get("bindings"), "bindings")),
            changeset=PublicationChangeset.from_dict(_mapping(value.get("changeset"), "changeset")),
            validation=PublicationValidation.from_dict(
                _mapping(value.get("validation"), "validation")
            ),
            impact=PublicationImpact.from_dict(_mapping(value.get("impact"), "impact")),
            approval_requirements=PublicationApprovalRequirements.from_dict(
                _mapping(value.get("approval_requirements"), "approval_requirements")
            ),
            intended_outputs=tuple(
                PublicationOutputIntent.from_dict(_mapping(item, "intended_outputs item"))
                for item in _sequence(value.get("intended_outputs"))
            ),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    decision_id: str
    decision_digest: str
    plan_digest: str
    policy_digest: str
    actor: PublicationActor
    decision: PublicationDecisionKind
    reason: str
    diagnostic_ids: tuple[str, ...]
    timestamp: str
    expires_at: str | None = None
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _required(self.decision_id, "decision_id"))
        object.__setattr__(self, "decision_digest", _digest(self.decision_digest))
        object.__setattr__(self, "plan_digest", _digest(self.plan_digest))
        object.__setattr__(self, "policy_digest", _digest(self.policy_digest))
        object.__setattr__(self, "decision", PublicationDecisionKind(self.decision))
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "diagnostic_ids", _sorted_strings(self.diagnostic_ids))
        object.__setattr__(self, "timestamp", normalize_rfc3339(self.timestamp))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", normalize_rfc3339(self.expires_at))
        if (
            self.decision
            in {
                PublicationDecisionKind.REJECT,
                PublicationDecisionKind.REQUEST_CHANGES,
                PublicationDecisionKind.REVOKE,
                PublicationDecisionKind.WAIVE_WARNING,
                PublicationDecisionKind.EMERGENCY_OVERRIDE,
            }
            and not self.reason
        ):
            raise ValueError(f"publication {self.decision.value} decision requires a reason")
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        expected = sha256_digest(canonical_json_bytes(self.digest_payload()))
        if self.decision_digest != expected:
            raise ValueError("publication decision_digest does not match decision payload")
        if self.decision_id != _record_id("decision", self.decision_digest):
            raise ValueError("publication decision_id does not match decision_digest")

    @classmethod
    def create(
        cls,
        *,
        plan_digest: str,
        policy_digest: str,
        actor: PublicationActor,
        decision: PublicationDecisionKind,
        reason: str = "",
        diagnostic_ids: tuple[str, ...] = (),
        timestamp: str,
        expires_at: str | None = None,
        extensions: Mapping[str, Any] | None = None,
    ) -> PublicationDecision:
        normalized_diagnostics = _sorted_strings(diagnostic_ids)
        normalized_timestamp = normalize_rfc3339(timestamp)
        normalized_expiry = normalize_rfc3339(expires_at) if expires_at else None
        payload = _decision_digest_payload(
            plan_digest=_digest(plan_digest),
            policy_digest=_digest(policy_digest),
            actor=actor,
            decision=PublicationDecisionKind(decision),
            reason=str(reason or "").strip(),
            diagnostic_ids=normalized_diagnostics,
            timestamp=normalized_timestamp,
            expires_at=normalized_expiry,
        )
        digest = sha256_digest(canonical_json_bytes(payload))
        return cls(
            decision_id=_record_id("decision", digest),
            decision_digest=digest,
            plan_digest=plan_digest,
            policy_digest=policy_digest,
            actor=actor,
            decision=decision,
            reason=reason,
            diagnostic_ids=normalized_diagnostics,
            timestamp=normalized_timestamp,
            expires_at=normalized_expiry,
            extensions=extensions,
        )

    def digest_payload(self) -> dict[str, object]:
        return _decision_digest_payload(
            plan_digest=self.plan_digest,
            policy_digest=self.policy_digest,
            actor=self.actor,
            decision=self.decision,
            reason=self.reason,
            diagnostic_ids=self.diagnostic_ids,
            timestamp=self.timestamp,
            expires_at=self.expires_at,
        )

    def to_dict(self, projection: PublicationProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        payload: dict[str, object] = {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "record_type": PublicationRecordType.DECISION.value,
            "decision_id": self.decision_id,
            "decision_digest": self.decision_digest,
            "plan_digest": self.plan_digest,
            "policy_digest": self.policy_digest,
            "decision": self.decision.value,
            "timestamp": self.timestamp,
            "expires_at": self.expires_at,
        }
        if projection != "public":
            payload["actor"] = (
                self.actor.to_dict()
                if projection == "trusted"
                else {"actor": self.actor.actor, "identity_source": self.actor.identity_source}
            )
            payload["diagnostic_ids"] = list(self.diagnostic_ids)
        if projection == "trusted":
            payload["reason"] = self.reason
            payload["extensions"] = _plain(self.extensions)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationDecision:
        _record_header(value, PublicationRecordType.DECISION)
        expiry = value.get("expires_at")
        return cls(
            decision_id=str(value.get("decision_id") or ""),
            decision_digest=str(value.get("decision_digest") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            policy_digest=str(value.get("policy_digest") or ""),
            actor=PublicationActor.from_dict(_mapping(value.get("actor"), "actor")),
            decision=PublicationDecisionKind(str(value.get("decision") or "")),
            reason=str(value.get("reason") or ""),
            diagnostic_ids=_string_tuple(value.get("diagnostic_ids")),
            timestamp=str(value.get("timestamp") or ""),
            expires_at=str(expiry) if expiry is not None else None,
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@dataclass(frozen=True, slots=True)
class PublicationEvent:
    event_id: str
    event_digest: str
    plan_digest: str
    state_version: int
    from_state: PublicationState
    to_state: PublicationState
    event_type: str
    actor: PublicationActor
    correlation_id: str
    timestamp: str
    failure: PublicationFailure | None = None
    outputs: tuple[PublicationOutputReference, ...] = ()
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _required(self.event_id, "event_id"))
        object.__setattr__(self, "event_digest", _digest(self.event_digest))
        object.__setattr__(self, "plan_digest", _digest(self.plan_digest))
        if self.state_version < 1:
            raise ValueError("publication event state_version must be positive")
        object.__setattr__(self, "from_state", PublicationState(self.from_state))
        object.__setattr__(self, "to_state", PublicationState(self.to_state))
        object.__setattr__(self, "event_type", _required(self.event_type, "event_type"))
        object.__setattr__(self, "correlation_id", _required(self.correlation_id, "correlation_id"))
        object.__setattr__(self, "timestamp", normalize_rfc3339(self.timestamp))
        outputs = tuple(sorted(self.outputs, key=lambda item: (item.kind, item.identifier)))
        object.__setattr__(self, "outputs", outputs)
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        expected = sha256_digest(canonical_json_bytes(self.digest_payload()))
        if self.event_digest != expected:
            raise ValueError("publication event_digest does not match event payload")
        if self.event_id != _record_id("event", self.event_digest):
            raise ValueError("publication event_id does not match event_digest")

    @classmethod
    def create(
        cls,
        *,
        plan_digest: str,
        state_version: int,
        from_state: PublicationState,
        to_state: PublicationState,
        event_type: str,
        actor: PublicationActor,
        correlation_id: str,
        timestamp: str,
        failure: PublicationFailure | None = None,
        outputs: tuple[PublicationOutputReference, ...] = (),
        extensions: Mapping[str, Any] | None = None,
    ) -> PublicationEvent:
        normalized_timestamp = normalize_rfc3339(timestamp)
        normalized_outputs = tuple(sorted(outputs, key=lambda item: (item.kind, item.identifier)))
        payload = _event_digest_payload(
            plan_digest=_digest(plan_digest),
            state_version=state_version,
            from_state=PublicationState(from_state),
            to_state=PublicationState(to_state),
            event_type=event_type,
            actor=actor,
            correlation_id=correlation_id,
            timestamp=normalized_timestamp,
            failure=failure,
            outputs=normalized_outputs,
        )
        digest = sha256_digest(canonical_json_bytes(payload))
        return cls(
            event_id=_record_id("event", digest),
            event_digest=digest,
            plan_digest=plan_digest,
            state_version=state_version,
            from_state=from_state,
            to_state=to_state,
            event_type=event_type,
            actor=actor,
            correlation_id=correlation_id,
            timestamp=normalized_timestamp,
            failure=failure,
            outputs=normalized_outputs,
            extensions=extensions,
        )

    def digest_payload(self) -> dict[str, object]:
        return _event_digest_payload(
            plan_digest=self.plan_digest,
            state_version=self.state_version,
            from_state=self.from_state,
            to_state=self.to_state,
            event_type=self.event_type,
            actor=self.actor,
            correlation_id=self.correlation_id,
            timestamp=self.timestamp,
            failure=self.failure,
            outputs=self.outputs,
        )

    def to_dict(self, projection: PublicationProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        payload: dict[str, object] = {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "record_type": PublicationRecordType.EVENT.value,
            "event_id": self.event_id,
            "event_digest": self.event_digest,
            "plan_digest": self.plan_digest,
            "state_version": self.state_version,
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
        }
        if projection != "public":
            payload["correlation_id"] = self.correlation_id
            payload["actor"] = (
                self.actor.to_dict()
                if projection == "trusted"
                else {"actor": self.actor.actor, "identity_source": self.actor.identity_source}
            )
            payload["failure"] = self.failure.to_dict() if self.failure else None
            payload["outputs"] = [
                item.to_dict(include_private=projection == "trusted") for item in self.outputs
            ]
        if projection == "trusted":
            payload["extensions"] = _plain(self.extensions)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationEvent:
        _record_header(value, PublicationRecordType.EVENT)
        failure = value.get("failure")
        return cls(
            event_id=str(value.get("event_id") or ""),
            event_digest=str(value.get("event_digest") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            state_version=int(value.get("state_version") or 0),
            from_state=PublicationState(str(value.get("from_state") or "")),
            to_state=PublicationState(str(value.get("to_state") or "")),
            event_type=str(value.get("event_type") or ""),
            actor=PublicationActor.from_dict(_mapping(value.get("actor"), "actor")),
            correlation_id=str(value.get("correlation_id") or ""),
            timestamp=str(value.get("timestamp") or ""),
            failure=(
                PublicationFailure.from_dict(_mapping(failure, "failure"))
                if failure is not None
                else None
            ),
            outputs=tuple(
                PublicationOutputReference.from_dict(_mapping(item, "outputs item"))
                for item in _sequence(value.get("outputs"))
            ),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@dataclass(frozen=True, slots=True)
class PublicationStateSnapshot:
    plan_id: str
    plan_digest: str
    state_version: int
    state: PublicationState
    decision_refs: tuple[PublicationRecordReference, ...]
    failure: PublicationFailure | None
    outputs: tuple[PublicationOutputReference, ...]
    created_at: str
    updated_at: str
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _required(self.plan_id, "plan_id"))
        object.__setattr__(self, "plan_digest", _digest(self.plan_digest))
        if self.state_version < 1:
            raise ValueError("publication state_version must be positive")
        object.__setattr__(self, "state", PublicationState(self.state))
        object.__setattr__(
            self,
            "decision_refs",
            tuple(sorted(self.decision_refs, key=lambda item: item.record_id)),
        )
        object.__setattr__(
            self,
            "outputs",
            tuple(sorted(self.outputs, key=lambda item: (item.kind, item.identifier))),
        )
        object.__setattr__(self, "created_at", normalize_rfc3339(self.created_at))
        object.__setattr__(self, "updated_at", normalize_rfc3339(self.updated_at))
        if _timestamp(self.updated_at) < _timestamp(self.created_at):
            raise ValueError("publication updated_at cannot precede created_at")
        if self.state == PublicationState.FAILED and self.failure is None:
            raise ValueError("failed publication state requires failure details")
        if self.state != PublicationState.FAILED and self.failure is not None:
            raise ValueError("publication failure details require failed state")
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)

    @classmethod
    def initial(cls, plan: PublicationPlan, *, timestamp: str) -> PublicationStateSnapshot:
        normalized = normalize_rfc3339(timestamp)
        return cls(
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            state_version=1,
            state=PublicationState.PROPOSED,
            decision_refs=(),
            failure=None,
            outputs=(),
            created_at=normalized,
            updated_at=normalized,
        )

    @property
    def terminal(self) -> bool:
        if self.state in {
            PublicationState.APPLIED,
            PublicationState.EXPIRED,
            PublicationState.CANCELLED,
            PublicationState.SUPERSEDED,
        }:
            return True
        return bool(
            self.state == PublicationState.FAILED
            and self.failure is not None
            and (
                self.failure.disposition
                in {
                    PublicationFailureDisposition.TERMINAL,
                    PublicationFailureDisposition.AUTHORIZATION,
                }
                or self.failure.retry_target is None
            )
        )

    def to_dict(self, projection: PublicationProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        payload: dict[str, object] = {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "record_type": PublicationRecordType.STATE.value,
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "state_version": self.state_version,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "terminal": self.terminal,
        }
        if projection != "public":
            payload["decision_refs"] = [item.to_dict() for item in self.decision_refs]
            payload["failure"] = self.failure.to_dict() if self.failure else None
            payload["outputs"] = [
                item.to_dict(include_private=projection == "trusted") for item in self.outputs
            ]
        if projection == "trusted":
            payload["extensions"] = _plain(self.extensions)
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PublicationStateSnapshot:
        _record_header(value, PublicationRecordType.STATE)
        failure = value.get("failure")
        snapshot = cls(
            plan_id=str(value.get("plan_id") or ""),
            plan_digest=str(value.get("plan_digest") or ""),
            state_version=int(value.get("state_version") or 0),
            state=PublicationState(str(value.get("state") or "")),
            decision_refs=tuple(
                PublicationRecordReference.from_dict(_mapping(item, "decision_refs item"))
                for item in _sequence(value.get("decision_refs"))
            ),
            failure=(
                PublicationFailure.from_dict(_mapping(failure, "failure"))
                if failure is not None
                else None
            ),
            outputs=tuple(
                PublicationOutputReference.from_dict(_mapping(item, "outputs item"))
                for item in _sequence(value.get("outputs"))
            ),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )
        claimed_terminal = value.get("terminal")
        if claimed_terminal is not None and bool(claimed_terminal) is not snapshot.terminal:
            raise ValueError("publication terminal flag does not match state")
        return snapshot


type PublicationRecord = (
    PublicationPlan | PublicationDecision | PublicationEvent | PublicationStateSnapshot
)


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes for publication digests."""
    return json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def normalize_rfc3339(value: str) -> str:
    text = _required(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid RFC 3339 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("publication timestamps must include a timezone")
    utc = parsed.astimezone(UTC)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def publication_record_envelope(
    record: PublicationRecord,
    *,
    transport: Literal["cli", "http", "mcp"],
) -> dict[str, object]:
    key = _record_key(record)
    payload = record.to_dict("trusted")
    if transport == "cli":
        return {"data": {key: payload}}
    if transport == "http":
        return {key: payload}
    if transport == "mcp":
        return {"structuredContent": {key: payload}}
    raise ValueError(f"unsupported publication transport: {transport}")


def publication_record_from_envelope(
    envelope: Mapping[str, Any],
    *,
    transport: Literal["cli", "http", "mcp"],
) -> PublicationRecord:
    container: Mapping[str, Any]
    if transport == "cli":
        container = _mapping(envelope.get("data"), "data")
    elif transport == "http":
        container = envelope
    elif transport == "mcp":
        container = _mapping(envelope.get("structuredContent"), "structuredContent")
    else:
        raise ValueError(f"unsupported publication transport: {transport}")
    candidates = [value for key, value in container.items() if str(key).startswith("publication_")]
    if len(candidates) != 1:
        raise ValueError("publication envelope must contain exactly one publication record")
    payload = _mapping(candidates[0], "publication record")
    record_type = PublicationRecordType(str(payload.get("record_type") or ""))
    readers = {
        PublicationRecordType.PLAN: PublicationPlan.from_dict,
        PublicationRecordType.DECISION: PublicationDecision.from_dict,
        PublicationRecordType.EVENT: PublicationEvent.from_dict,
        PublicationRecordType.STATE: PublicationStateSnapshot.from_dict,
    }
    return readers[record_type](payload)


def _plan_approval_payload(
    *,
    creator: PublicationActor,
    expires_at: str,
    identity: PublicationIdentity,
    request: PublicationRequest,
    bindings: PublicationBindings,
    changeset: PublicationChangeset,
    validation: PublicationValidation,
    impact: PublicationImpact,
    approval_requirements: PublicationApprovalRequirements,
    intended_outputs: tuple[PublicationOutputIntent, ...],
) -> dict[str, object]:
    return {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "record_type": PublicationRecordType.PLAN.value,
        "creator": creator.to_dict(),
        "expires_at": expires_at,
        "identity": identity.to_dict(),
        "request": request.to_dict(),
        "bindings": bindings.to_dict(),
        "changeset": changeset.to_dict(),
        "validation": validation.to_dict(),
        "impact": impact.to_dict(),
        "approval_requirements": approval_requirements.to_dict(),
        "intended_outputs": [item.to_dict() for item in intended_outputs],
    }


def _decision_digest_payload(
    *,
    plan_digest: str,
    policy_digest: str,
    actor: PublicationActor,
    decision: PublicationDecisionKind,
    reason: str,
    diagnostic_ids: tuple[str, ...],
    timestamp: str,
    expires_at: str | None,
) -> dict[str, object]:
    return {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "record_type": PublicationRecordType.DECISION.value,
        "plan_digest": plan_digest,
        "policy_digest": policy_digest,
        "actor": actor.to_dict(),
        "decision": decision.value,
        "reason": reason,
        "diagnostic_ids": list(diagnostic_ids),
        "timestamp": timestamp,
        "expires_at": expires_at,
    }


def _event_digest_payload(
    *,
    plan_digest: str,
    state_version: int,
    from_state: PublicationState,
    to_state: PublicationState,
    event_type: str,
    actor: PublicationActor,
    correlation_id: str,
    timestamp: str,
    failure: PublicationFailure | None,
    outputs: tuple[PublicationOutputReference, ...],
) -> dict[str, object]:
    return {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "record_type": PublicationRecordType.EVENT.value,
        "plan_digest": plan_digest,
        "state_version": state_version,
        "from_state": from_state.value,
        "to_state": to_state.value,
        "event_type": _required(event_type, "event_type"),
        "actor": actor.to_dict(),
        "correlation_id": _required(correlation_id, "correlation_id"),
        "timestamp": timestamp,
        "failure": failure.to_dict() if failure else None,
        "outputs": [item.to_dict() for item in outputs],
    }


def _plain(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("canonical datetimes must include a timezone")
        return normalize_rfc3339(value.isoformat())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if isinstance(value, set | frozenset):
        items = [_plain(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    return value


def _record_id(prefix: str, digest: str) -> str:
    return f"{prefix}-{_digest(digest).removeprefix('sha256:')[:24]}"


def _record_key(record: PublicationRecord) -> str:
    if isinstance(record, PublicationPlan):
        return "publication_plan"
    if isinstance(record, PublicationDecision):
        return "publication_decision"
    if isinstance(record, PublicationEvent):
        return "publication_event"
    return "publication_state"


def _record_header(value: Mapping[str, Any], expected: PublicationRecordType) -> None:
    if value.get("schema_version") != PUBLICATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported publication schema_version: {value.get('schema_version')!r}")
    if value.get("record_type") != expected.value:
        raise ValueError(f"expected publication record_type {expected.value!r}")


def _required(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"publication {label} is required")
    return text


def _digest(value: object) -> str:
    text = str(value or "")
    if not _DIGEST_RE.fullmatch(text):
        raise ValueError(f"invalid publication SHA-256 digest: {value!r}")
    return text


def _logical_path(value: object) -> str:
    text = _required(value, "logical path").replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
        raise ValueError(f"invalid publication logical path: {value!r}")
    return path.as_posix()


def _sorted_strings(values: object) -> tuple[str, ...]:
    normalized = tuple(
        sorted({str(value).strip() for value in _sequence(values) if str(value).strip()})
    )
    return normalized


def _string_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(value))


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str | bytes | bytearray | Mapping):
        raise TypeError("publication sequence field must be an array")
    if not isinstance(value, Iterable):
        raise TypeError("publication sequence field must be an array")
    return tuple(value)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"publication {label} must be an object")
    return {str(key): item for key, item in value.items()}


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(normalize_rfc3339(value).replace("Z", "+00:00"))


def _projection(value: str) -> None:
    if value not in {"trusted", "audit", "public"}:
        raise ValueError(f"unsupported publication projection: {value}")
