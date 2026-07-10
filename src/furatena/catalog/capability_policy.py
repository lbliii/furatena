"""Versioned, fail-closed capability decisions for governed operations."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any, Literal

from furatena.catalog.publication_contracts import (
    PublicationApprovalRequirements,
    PublicationPlan,
    canonical_json_bytes,
    normalize_rfc3339,
    sha256_digest,
)

CAPABILITY_SCHEMA_VERSION = 1

type CapabilityProjection = Literal["trusted", "display"]

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RULE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class Capability(StrEnum):
    READ = "read"
    EDIT = "edit"
    VALIDATE = "validate"
    REQUEST_REVIEW = "request_review"
    REVIEW = "review"
    APPROVE = "approve"
    PUBLISH = "publish"
    UNPUBLISH = "unpublish"
    ARCHIVE = "archive"
    CREATE_CHANGE = "create_change"
    OPEN_PR = "open_pr"
    MERGE_OBSERVE = "merge_observe"
    BUILD = "build"
    PROMOTE = "promote"
    DEPLOY = "deploy"
    VERIFY = "verify"
    ROLL_BACK = "roll_back"
    WAIVE_WARNING = "waive_warning"
    EMERGENCY_OVERRIDE = "emergency_override"
    ADMINISTER_POLICY = "administer_policy"


class CapabilityEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class CapabilityCacheMode(StrEnum):
    NO_STORE = "no_store"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class CapabilityCachePolicy:
    mode: CapabilityCacheMode = CapabilityCacheMode.NO_STORE
    max_age_seconds: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", CapabilityCacheMode(self.mode))
        if self.max_age_seconds < 0:
            raise ValueError("capability cache max_age_seconds must be non-negative")
        if self.mode == CapabilityCacheMode.NO_STORE and self.max_age_seconds:
            raise ValueError("no-store capability decisions cannot declare a max age")
        if self.mode == CapabilityCacheMode.PRIVATE and self.max_age_seconds < 1:
            raise ValueError("private capability decisions require a positive max age")

    def to_dict(self) -> dict[str, object]:
        return {"mode": self.mode.value, "max_age_seconds": self.max_age_seconds}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityCachePolicy:
        return cls(
            mode=CapabilityCacheMode(str(value.get("mode") or "")),
            max_age_seconds=int(value.get("max_age_seconds") or 0),
        )


@dataclass(frozen=True, slots=True)
class CapabilitySubject:
    actor: str
    identity_source: str
    identity_fingerprint: str
    roles: tuple[str, ...]
    teams: tuple[str, ...]
    tenant: str
    workspace: str
    site: str
    trusted: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "actor",
            "identity_source",
            "identity_fingerprint",
            "tenant",
            "workspace",
            "site",
        ):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        object.__setattr__(self, "roles", _sorted_strings(self.roles))
        object.__setattr__(self, "teams", _sorted_strings(self.teams))
        if not self.roles:
            raise ValueError("capability subject requires at least one trusted role")

    @classmethod
    def from_gateway_identity(
        cls,
        identity: Any,
        *,
        identity_source: str = "trusted_gateway",
    ) -> CapabilitySubject:
        subject = identity.subject
        return cls(
            actor=subject.actor,
            identity_source=identity_source,
            identity_fingerprint=identity.claim_fingerprint,
            roles=tuple(role.value for role in subject.roles),
            teams=tuple(subject.teams),
            tenant=identity.tenant,
            workspace=identity.workspace,
            site=identity.site,
            trusted=True,
        )

    def to_dict(self, *, include_actor: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "identity_source": self.identity_source,
            "identity_fingerprint": self.identity_fingerprint,
            "roles": list(self.roles),
            "teams": list(self.teams),
            "tenant": self.tenant,
            "workspace": self.workspace,
            "site": self.site,
        }
        if include_actor:
            payload["actor"] = self.actor
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilitySubject:
        return cls(
            actor=str(value.get("actor") or ""),
            identity_source=str(value.get("identity_source") or ""),
            identity_fingerprint=str(value.get("identity_fingerprint") or ""),
            roles=_string_tuple(value.get("roles")),
            teams=_string_tuple(value.get("teams")),
            tenant=str(value.get("tenant") or ""),
            workspace=str(value.get("workspace") or ""),
            site=str(value.get("site") or ""),
            trusted=False,
        )


@dataclass(frozen=True, slots=True)
class CapabilityScope:
    tenant: str
    workspace: str
    site: str
    mount: str | None = None
    path: str | None = None
    lifecycle_state: str | None = None
    resulting_lifecycle: str | None = None
    owners: tuple[str, ...] = ()
    owner_teams: tuple[str, ...] = ()
    repository: str | None = None
    branch: str | None = None
    environment: str | None = None
    sensitivity: str | None = None
    public_impact: bool | None = None

    def __post_init__(self) -> None:
        for field_name in ("tenant", "workspace", "site"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        for field_name in (
            "mount",
            "lifecycle_state",
            "resulting_lifecycle",
            "repository",
            "branch",
            "environment",
            "sensitivity",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required(value, field_name))
        if self.path is not None:
            object.__setattr__(self, "path", _logical_path(self.path))
        object.__setattr__(self, "owners", _sorted_strings(self.owners))
        object.__setattr__(self, "owner_teams", _sorted_strings(self.owner_teams))

    def to_dict(self) -> dict[str, object]:
        return {
            "tenant": self.tenant,
            "workspace": self.workspace,
            "site": self.site,
            "mount": self.mount,
            "path": self.path,
            "lifecycle_state": self.lifecycle_state,
            "resulting_lifecycle": self.resulting_lifecycle,
            "owners": list(self.owners),
            "owner_teams": list(self.owner_teams),
            "repository": self.repository,
            "branch": self.branch,
            "environment": self.environment,
            "sensitivity": self.sensitivity,
            "public_impact": self.public_impact,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityScope:
        optional = {
            name: str(value[name]) if value.get(name) is not None else None
            for name in (
                "mount",
                "path",
                "lifecycle_state",
                "resulting_lifecycle",
                "repository",
                "branch",
                "environment",
                "sensitivity",
            )
        }
        return cls(
            tenant=str(value.get("tenant") or ""),
            workspace=str(value.get("workspace") or ""),
            site=str(value.get("site") or ""),
            owners=_string_tuple(value.get("owners")),
            owner_teams=_string_tuple(value.get("owner_teams")),
            public_impact=(
                bool(value["public_impact"]) if value.get("public_impact") is not None else None
            ),
            **optional,
        )


@dataclass(frozen=True, slots=True)
class CapabilityRule:
    rule_id: str
    effect: CapabilityEffect
    priority: int
    capabilities: tuple[Capability, ...]
    roles: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    actors: tuple[str, ...] = ()
    identity_sources: tuple[str, ...] = ()
    require_owner: bool = False
    require_owner_team: bool = False
    tenants: tuple[str, ...] = ()
    workspaces: tuple[str, ...] = ()
    sites: tuple[str, ...] = ()
    mounts: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    repositories: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()
    environments: tuple[str, ...] = ()
    lifecycle_states: tuple[str, ...] = ()
    resulting_lifecycles: tuple[str, ...] = ()
    sensitivities: tuple[str, ...] = ()
    public_impact: bool | None = None
    required_approvals: int = 0
    eligible_roles: tuple[str, ...] = ()
    eligible_teams: tuple[str, ...] = ()
    allow_self_approval: bool = True
    separation_rules: tuple[str, ...] = ()
    cache: CapabilityCachePolicy = CapabilityCachePolicy()
    reason: str = ""
    remediation: str = ""

    def __post_init__(self) -> None:
        if not _RULE_ID_RE.fullmatch(self.rule_id):
            raise ValueError(f"invalid capability rule_id: {self.rule_id!r}")
        object.__setattr__(self, "effect", CapabilityEffect(self.effect))
        capabilities = tuple(sorted({Capability(item) for item in self.capabilities}, key=str))
        if not capabilities:
            raise ValueError("capability rule requires at least one capability")
        object.__setattr__(self, "capabilities", capabilities)
        for field_name in (
            "roles",
            "teams",
            "actors",
            "identity_sources",
            "tenants",
            "workspaces",
            "sites",
            "mounts",
            "repositories",
            "branches",
            "environments",
            "lifecycle_states",
            "resulting_lifecycles",
            "sensitivities",
            "eligible_roles",
            "eligible_teams",
            "separation_rules",
        ):
            object.__setattr__(self, field_name, _sorted_strings(getattr(self, field_name)))
        object.__setattr__(
            self,
            "paths",
            tuple(sorted({_logical_pattern(pattern) for pattern in self.paths})),
        )
        if self.required_approvals < 0:
            raise ValueError("capability required_approvals must be non-negative")
        if self.effect == CapabilityEffect.DENY and (
            self.required_approvals
            or self.eligible_roles
            or self.eligible_teams
            or self.separation_rules
        ):
            raise ValueError("deny capability rules cannot declare approval requirements")
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "remediation", str(self.remediation or "").strip())

    @property
    def specificity(self) -> int:
        selectors = (
            self.roles,
            self.teams,
            self.actors,
            self.identity_sources,
            self.tenants,
            self.workspaces,
            self.sites,
            self.mounts,
            self.paths,
            self.repositories,
            self.branches,
            self.environments,
            self.lifecycle_states,
            self.resulting_lifecycles,
            self.sensitivities,
        )
        return sum(bool(item) for item in selectors) + sum(
            (
                self.require_owner,
                self.require_owner_team,
                self.public_impact is not None,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "effect": self.effect.value,
            "priority": self.priority,
            "capabilities": [item.value for item in self.capabilities],
            "roles": list(self.roles),
            "teams": list(self.teams),
            "actors": list(self.actors),
            "identity_sources": list(self.identity_sources),
            "require_owner": self.require_owner,
            "require_owner_team": self.require_owner_team,
            "tenants": list(self.tenants),
            "workspaces": list(self.workspaces),
            "sites": list(self.sites),
            "mounts": list(self.mounts),
            "paths": list(self.paths),
            "repositories": list(self.repositories),
            "branches": list(self.branches),
            "environments": list(self.environments),
            "lifecycle_states": list(self.lifecycle_states),
            "resulting_lifecycles": list(self.resulting_lifecycles),
            "sensitivities": list(self.sensitivities),
            "public_impact": self.public_impact,
            "required_approvals": self.required_approvals,
            "eligible_roles": list(self.eligible_roles),
            "eligible_teams": list(self.eligible_teams),
            "allow_self_approval": self.allow_self_approval,
            "separation_rules": list(self.separation_rules),
            "cache": self.cache.to_dict(),
            "reason": self.reason,
            "remediation": self.remediation,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityRule:
        return cls(
            rule_id=str(value.get("rule_id") or ""),
            effect=CapabilityEffect(str(value.get("effect") or "")),
            priority=int(value.get("priority") or 0),
            capabilities=tuple(
                Capability(str(item)) for item in _sequence(value.get("capabilities"))
            ),
            roles=_string_tuple(value.get("roles")),
            teams=_string_tuple(value.get("teams")),
            actors=_string_tuple(value.get("actors")),
            identity_sources=_string_tuple(value.get("identity_sources")),
            require_owner=bool(value.get("require_owner", False)),
            require_owner_team=bool(value.get("require_owner_team", False)),
            tenants=_string_tuple(value.get("tenants")),
            workspaces=_string_tuple(value.get("workspaces")),
            sites=_string_tuple(value.get("sites")),
            mounts=_string_tuple(value.get("mounts")),
            paths=_string_tuple(value.get("paths")),
            repositories=_string_tuple(value.get("repositories")),
            branches=_string_tuple(value.get("branches")),
            environments=_string_tuple(value.get("environments")),
            lifecycle_states=_string_tuple(value.get("lifecycle_states")),
            resulting_lifecycles=_string_tuple(value.get("resulting_lifecycles")),
            sensitivities=_string_tuple(value.get("sensitivities")),
            public_impact=(
                bool(value["public_impact"]) if value.get("public_impact") is not None else None
            ),
            required_approvals=int(value.get("required_approvals") or 0),
            eligible_roles=_string_tuple(value.get("eligible_roles")),
            eligible_teams=_string_tuple(value.get("eligible_teams")),
            allow_self_approval=bool(value.get("allow_self_approval", True)),
            separation_rules=_string_tuple(value.get("separation_rules")),
            cache=CapabilityCachePolicy.from_dict(_mapping(value.get("cache"), "cache")),
            reason=str(value.get("reason") or ""),
            remediation=str(value.get("remediation") or ""),
        )


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    policy_version: str
    policy_digest: str
    rules: tuple[CapabilityRule, ...]
    extensions: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_version", _required(self.policy_version, "policy_version"))
        object.__setattr__(self, "policy_digest", _digest(self.policy_digest))
        rules = tuple(sorted(self.rules, key=lambda item: item.rule_id))
        if len({item.rule_id for item in rules}) != len(rules):
            raise ValueError("capability policy rule IDs must be unique")
        object.__setattr__(self, "rules", rules)
        extensions = dict(self.extensions or {})
        canonical_json_bytes(extensions)
        object.__setattr__(self, "extensions", extensions)
        if self.policy_digest != sha256_digest(canonical_json_bytes(self.digest_payload())):
            raise ValueError("capability policy_digest does not match policy payload")

    @classmethod
    def create(
        cls,
        *,
        policy_version: str,
        rules: tuple[CapabilityRule, ...],
        extensions: Mapping[str, Any] | None = None,
    ) -> CapabilityPolicy:
        normalized_version = _required(policy_version, "policy_version")
        normalized_rules = tuple(sorted(rules, key=lambda item: item.rule_id))
        payload = _policy_digest_payload(normalized_version, normalized_rules)
        return cls(
            policy_version=normalized_version,
            policy_digest=sha256_digest(canonical_json_bytes(payload)),
            rules=normalized_rules,
            extensions=extensions,
        )

    def digest_payload(self) -> dict[str, object]:
        return _policy_digest_payload(self.policy_version, self.rules)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "record_type": "furatena.capability.policy",
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "rules": [item.to_dict() for item in self.rules],
            "extensions": dict(self.extensions or {}),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityPolicy:
        _record_header(value, "furatena.capability.policy")
        return cls(
            policy_version=str(value.get("policy_version") or ""),
            policy_digest=str(value.get("policy_digest") or ""),
            rules=tuple(
                CapabilityRule.from_dict(_mapping(item, "rules item"))
                for item in _sequence(value.get("rules"))
            ),
            extensions=_mapping(value.get("extensions") or {}, "extensions"),
        )


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    subject: CapabilitySubject
    capability: Capability
    scope: CapabilityScope
    correlation_id: str
    evaluated_at: str
    plan_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability", Capability(self.capability))
        object.__setattr__(self, "correlation_id", _required(self.correlation_id, "correlation_id"))
        object.__setattr__(self, "evaluated_at", normalize_rfc3339(self.evaluated_at))
        if self.plan_digest is not None:
            object.__setattr__(self, "plan_digest", _digest(self.plan_digest))

    def to_dict(self, projection: CapabilityProjection = "trusted") -> dict[str, object]:
        _projection(projection)
        return {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "record_type": "furatena.capability.request",
            "subject": self.subject.to_dict(include_actor=projection == "trusted"),
            "capability": self.capability.value,
            "scope": self.scope.to_dict(),
            "correlation_id": self.correlation_id,
            "evaluated_at": self.evaluated_at,
            "plan_digest": self.plan_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityRequest:
        _record_header(value, "furatena.capability.request")
        return cls(
            subject=CapabilitySubject.from_dict(_mapping(value.get("subject"), "subject")),
            capability=Capability(str(value.get("capability") or "")),
            scope=CapabilityScope.from_dict(_mapping(value.get("scope"), "scope")),
            correlation_id=str(value.get("correlation_id") or ""),
            evaluated_at=str(value.get("evaluated_at") or ""),
            plan_digest=(
                str(value["plan_digest"]) if value.get("plan_digest") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    allowed: bool
    capability: Capability
    reason_code: str
    reason: str
    remediation: str
    matched_rule_id: str | None
    matched_effect: CapabilityEffect | None
    policy_version: str
    policy_digest: str
    approval_requirements: PublicationApprovalRequirements
    separation_rules: tuple[str, ...]
    cacheability: CapabilityCachePolicy
    decision_scope_digest: str
    evaluated_at: str
    plan_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability", Capability(self.capability))
        object.__setattr__(self, "reason_code", _required(self.reason_code, "reason_code"))
        object.__setattr__(self, "reason", _required(self.reason, "reason"))
        object.__setattr__(self, "remediation", _required(self.remediation, "remediation"))
        object.__setattr__(self, "policy_version", _required(self.policy_version, "policy_version"))
        object.__setattr__(self, "policy_digest", _digest(self.policy_digest))
        object.__setattr__(self, "decision_scope_digest", _digest(self.decision_scope_digest))
        object.__setattr__(self, "evaluated_at", normalize_rfc3339(self.evaluated_at))
        object.__setattr__(self, "separation_rules", _sorted_strings(self.separation_rules))
        if self.plan_digest is not None:
            object.__setattr__(self, "plan_digest", _digest(self.plan_digest))
        if (self.matched_rule_id is None) != (self.matched_effect is None):
            raise ValueError("capability matched rule ID and effect must be present together")
        if self.matched_rule_id is not None and not _RULE_ID_RE.fullmatch(self.matched_rule_id):
            raise ValueError("invalid matched capability rule ID")
        if self.matched_effect is not None:
            object.__setattr__(self, "matched_effect", CapabilityEffect(self.matched_effect))
        if self.allowed and self.matched_effect != CapabilityEffect.ALLOW:
            raise ValueError("allowed capability decision requires a matched allow rule")
        if not self.allowed and self.approval_requirements.required_count:
            raise ValueError("denied capability decisions cannot require approvals")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "record_type": "furatena.capability.decision",
            "allowed": self.allowed,
            "capability": self.capability.value,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "remediation": self.remediation,
            "matched_rule_id": self.matched_rule_id,
            "matched_effect": self.matched_effect.value if self.matched_effect else None,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "approval_requirements": self.approval_requirements.to_dict(),
            "separation_rules": list(self.separation_rules),
            "cacheability": self.cacheability.to_dict(),
            "decision_scope_digest": self.decision_scope_digest,
            "evaluated_at": self.evaluated_at,
            "plan_digest": self.plan_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CapabilityDecision:
        _record_header(value, "furatena.capability.decision")
        matched_effect = value.get("matched_effect")
        return cls(
            allowed=bool(value.get("allowed", False)),
            capability=Capability(str(value.get("capability") or "")),
            reason_code=str(value.get("reason_code") or ""),
            reason=str(value.get("reason") or ""),
            remediation=str(value.get("remediation") or ""),
            matched_rule_id=(
                str(value["matched_rule_id"]) if value.get("matched_rule_id") is not None else None
            ),
            matched_effect=(
                CapabilityEffect(str(matched_effect)) if matched_effect is not None else None
            ),
            policy_version=str(value.get("policy_version") or ""),
            policy_digest=str(value.get("policy_digest") or ""),
            approval_requirements=PublicationApprovalRequirements.from_dict(
                _mapping(value.get("approval_requirements"), "approval_requirements")
            ),
            separation_rules=_string_tuple(value.get("separation_rules")),
            cacheability=CapabilityCachePolicy.from_dict(
                _mapping(value.get("cacheability"), "cacheability")
            ),
            decision_scope_digest=str(value.get("decision_scope_digest") or ""),
            evaluated_at=str(value.get("evaluated_at") or ""),
            plan_digest=(
                str(value["plan_digest"]) if value.get("plan_digest") is not None else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CapabilityEvaluationService:
    """Evaluate immutable policies without trusting transport-provided identity."""

    def evaluate(
        self,
        policy: CapabilityPolicy,
        request: CapabilityRequest,
    ) -> CapabilityDecision:
        scope_digest = _decision_scope_digest(policy, request)
        if not request.subject.trusted:
            return _denied_decision(
                policy,
                request,
                scope_digest,
                reason_code="untrusted_identity",
                reason="capability subject was not derived from a trusted server identity",
                remediation="Authenticate through the trusted gateway or local-OS boundary.",
            )
        mismatch = _identity_scope_mismatch(request)
        if mismatch:
            return _denied_decision(
                policy,
                request,
                scope_digest,
                reason_code="identity_scope_mismatch",
                reason=mismatch,
                remediation="Use a resource within the trusted identity boundary.",
            )
        missing = _missing_context(request.capability, request.scope)
        if missing:
            return _denied_decision(
                policy,
                request,
                scope_digest,
                reason_code="context_incomplete",
                reason=f"capability context is missing: {', '.join(missing)}",
                remediation="Supply complete server-derived resource context and retry.",
            )
        matches = [rule for rule in policy.rules if _rule_matches(rule, request)]
        denies = sorted(
            (rule for rule in matches if rule.effect == CapabilityEffect.DENY),
            key=lambda rule: (-rule.priority, rule.rule_id),
        )
        if denies:
            rule = denies[0]
            return _denied_decision(
                policy,
                request,
                scope_digest,
                reason_code="rule_denied",
                reason=rule.reason or f"denied by capability rule {rule.rule_id}",
                remediation=rule.remediation or "Request access from the policy administrator.",
                rule=rule,
            )
        allows = sorted(
            (rule for rule in matches if rule.effect == CapabilityEffect.ALLOW),
            key=lambda rule: (-rule.priority, -rule.specificity, rule.rule_id),
        )
        if not allows:
            return _denied_decision(
                policy,
                request,
                scope_digest,
                reason_code="default_deny",
                reason="no capability policy rule allows this operation",
                remediation="Request a narrowly scoped policy grant.",
            )
        rule = allows[0]
        cache = (
            rule.cache if request.capability in _CACHEABLE_CAPABILITIES else CapabilityCachePolicy()
        )
        approvals = PublicationApprovalRequirements(
            policy_version=policy.policy_version,
            policy_digest=policy.policy_digest,
            required_count=rule.required_approvals,
            eligible_roles=rule.eligible_roles,
            eligible_teams=rule.eligible_teams,
            allow_self_approval=rule.allow_self_approval,
            separation_rules=rule.separation_rules,
        )
        return CapabilityDecision(
            allowed=True,
            capability=request.capability,
            reason_code="rule_allowed",
            reason=rule.reason or f"allowed by capability rule {rule.rule_id}",
            remediation=rule.remediation
            or "Proceed with server-side reauthorization at effect time.",
            matched_rule_id=rule.rule_id,
            matched_effect=rule.effect,
            policy_version=policy.policy_version,
            policy_digest=policy.policy_digest,
            approval_requirements=approvals,
            separation_rules=rule.separation_rules,
            cacheability=cache,
            decision_scope_digest=scope_digest,
            evaluated_at=request.evaluated_at,
            plan_digest=request.plan_digest,
        )


CAPABILITY_EVALUATOR = CapabilityEvaluationService()


def bind_trusted_capability_subject(
    request: CapabilityRequest,
    subject: CapabilitySubject,
) -> CapabilityRequest:
    """Replace transport display identity with server-derived trusted identity."""
    if not subject.trusted:
        raise PermissionError("capability subject is not trusted")
    return replace(request, subject=subject)


def capability_for_access_permission(permission: Any) -> Capability:
    """Map the existing #151 permission model onto governed capabilities."""
    from furatena.catalog.access import AccessPermission

    normalized = AccessPermission(permission)
    return {
        AccessPermission.READ: Capability.READ,
        AccessPermission.SEARCH: Capability.READ,
        AccessPermission.RETRIEVE: Capability.READ,
        AccessPermission.EXPORT: Capability.READ,
        AccessPermission.AUTHOR: Capability.EDIT,
        AccessPermission.PUBLISH: Capability.PUBLISH,
        AccessPermission.CONFIGURE: Capability.ADMINISTER_POLICY,
        AccessPermission.ADMINISTER: Capability.ADMINISTER_POLICY,
    }[normalized]


def capability_for_author_operation(operation: str) -> Capability:
    """Map legacy author operation names without changing their role matrix."""
    normalized = str(operation).strip().lower()
    mapping = {
        "status": Capability.READ,
        "read": Capability.READ,
        "validate": Capability.VALIDATE,
        "new": Capability.EDIT,
        "apply_edit": Capability.EDIT,
        "save_source": Capability.EDIT,
        "draft": Capability.EDIT,
        "publish": Capability.PUBLISH,
        "unpublish": Capability.UNPUBLISH,
        "archive": Capability.ARCHIVE,
        "inspect_publication_impact": Capability.REVIEW,
    }
    try:
        return mapping[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown author capability operation: {operation}") from exc


def require_current_publication_policy(
    plan: PublicationPlan,
    decision: CapabilityDecision,
    *,
    capability: Capability,
) -> None:
    """Reject execution decisions that do not authorize the exact current plan."""
    if not decision.allowed or decision.capability != capability:
        raise PermissionError(f"capability decision does not allow {capability.value}")
    if decision.plan_digest != plan.plan_digest:
        raise PermissionError("capability decision is not bound to the publication plan digest")
    if (
        decision.policy_version != plan.bindings.policy_version
        or decision.policy_digest != plan.bindings.policy_digest
    ):
        raise PermissionError("capability decision policy is stale for the publication plan")


def capability_record_envelope(
    record: CapabilityRequest | CapabilityDecision,
    *,
    transport: Literal["cli", "http", "mcp", "automation"],
) -> dict[str, object]:
    key = "capability_request" if isinstance(record, CapabilityRequest) else "capability_decision"
    payload = record.to_dict()
    if transport == "cli":
        return {"data": {key: payload}}
    if transport == "http":
        return {key: payload}
    if transport == "mcp":
        return {"structuredContent": {key: payload}}
    if transport == "automation":
        return {"payload": {key: payload}}
    raise ValueError(f"unsupported capability transport: {transport}")


def capability_record_from_envelope(
    envelope: Mapping[str, Any],
    *,
    transport: Literal["cli", "http", "mcp", "automation"],
) -> CapabilityRequest | CapabilityDecision:
    if transport == "cli":
        container = _mapping(envelope.get("data"), "data")
    elif transport == "http":
        container = envelope
    elif transport == "mcp":
        container = _mapping(envelope.get("structuredContent"), "structuredContent")
    elif transport == "automation":
        container = _mapping(envelope.get("payload"), "payload")
    else:
        raise ValueError(f"unsupported capability transport: {transport}")
    candidates = [
        (key, value) for key, value in container.items() if str(key).startswith("capability_")
    ]
    if len(candidates) != 1:
        raise ValueError("capability envelope must contain exactly one capability record")
    key, raw = candidates[0]
    value = _mapping(raw, "capability record")
    if key == "capability_request":
        return CapabilityRequest.from_dict(value)
    if key == "capability_decision":
        return CapabilityDecision.from_dict(value)
    raise ValueError(f"unsupported capability envelope record: {key}")


_CONTENT_CAPABILITIES = frozenset(
    {
        Capability.READ,
        Capability.EDIT,
        Capability.VALIDATE,
        Capability.REQUEST_REVIEW,
        Capability.REVIEW,
        Capability.APPROVE,
        Capability.PUBLISH,
        Capability.UNPUBLISH,
        Capability.ARCHIVE,
        Capability.WAIVE_WARNING,
    }
)
_REPOSITORY_CAPABILITIES = frozenset(
    {
        Capability.CREATE_CHANGE,
        Capability.OPEN_PR,
        Capability.MERGE_OBSERVE,
        Capability.BUILD,
    }
)
_ENVIRONMENT_CAPABILITIES = frozenset(
    {
        Capability.PROMOTE,
        Capability.DEPLOY,
        Capability.VERIFY,
        Capability.ROLL_BACK,
    }
)
_CACHEABLE_CAPABILITIES = frozenset(
    {
        Capability.READ,
        Capability.VALIDATE,
        Capability.REVIEW,
        Capability.MERGE_OBSERVE,
        Capability.VERIFY,
    }
)


def _missing_context(capability: Capability, scope: CapabilityScope) -> tuple[str, ...]:
    fields: list[str] = []
    if capability in _CONTENT_CAPABILITIES:
        for name in ("mount", "path", "lifecycle_state"):
            if getattr(scope, name) is None:
                fields.append(name)
    if capability in _REPOSITORY_CAPABILITIES:
        for name in ("repository", "branch"):
            if getattr(scope, name) is None:
                fields.append(name)
    if capability in _ENVIRONMENT_CAPABILITIES and scope.environment is None:
        fields.append("environment")
    return tuple(fields)


def _identity_scope_mismatch(request: CapabilityRequest) -> str | None:
    pairs = (
        ("tenant", request.subject.tenant, request.scope.tenant),
        ("workspace", request.subject.workspace, request.scope.workspace),
        ("site", request.subject.site, request.scope.site),
    )
    for label, trusted, requested in pairs:
        if trusted != requested:
            return f"trusted {label} does not match resource {label}"
    return None


def _rule_matches(rule: CapabilityRule, request: CapabilityRequest) -> bool:
    subject = request.subject
    scope = request.scope
    if request.capability not in rule.capabilities:
        return False
    if rule.roles and not set(rule.roles) & set(subject.roles):
        return False
    if rule.teams and not set(rule.teams) & set(subject.teams):
        return False
    if rule.actors and subject.actor not in rule.actors:
        return False
    if rule.identity_sources and subject.identity_source not in rule.identity_sources:
        return False
    if rule.require_owner and subject.actor not in scope.owners:
        return False
    if rule.require_owner_team and not set(subject.teams) & set(scope.owner_teams):
        return False
    predicates = (
        (rule.tenants, scope.tenant),
        (rule.workspaces, scope.workspace),
        (rule.sites, scope.site),
        (rule.mounts, scope.mount),
        (rule.paths, scope.path),
        (rule.repositories, scope.repository),
        (rule.branches, scope.branch),
        (rule.environments, scope.environment),
        (rule.lifecycle_states, scope.lifecycle_state),
        (rule.resulting_lifecycles, scope.resulting_lifecycle),
        (rule.sensitivities, scope.sensitivity),
    )
    if any(patterns and not _matches_any(patterns, value) for patterns, value in predicates):
        return False
    return rule.public_impact is None or rule.public_impact == scope.public_impact


def _matches_any(patterns: tuple[str, ...], value: str | None) -> bool:
    return value is not None and any(fnmatchcase(value, pattern) for pattern in patterns)


def _denied_decision(
    policy: CapabilityPolicy,
    request: CapabilityRequest,
    scope_digest: str,
    *,
    reason_code: str,
    reason: str,
    remediation: str,
    rule: CapabilityRule | None = None,
) -> CapabilityDecision:
    return CapabilityDecision(
        allowed=False,
        capability=request.capability,
        reason_code=reason_code,
        reason=reason,
        remediation=remediation,
        matched_rule_id=rule.rule_id if rule else None,
        matched_effect=rule.effect if rule else None,
        policy_version=policy.policy_version,
        policy_digest=policy.policy_digest,
        approval_requirements=PublicationApprovalRequirements(
            policy_version=policy.policy_version,
            policy_digest=policy.policy_digest,
        ),
        separation_rules=(),
        cacheability=CapabilityCachePolicy(),
        decision_scope_digest=scope_digest,
        evaluated_at=request.evaluated_at,
        plan_digest=request.plan_digest,
    )


def _decision_scope_digest(policy: CapabilityPolicy, request: CapabilityRequest) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {
                "schema_version": CAPABILITY_SCHEMA_VERSION,
                "subject": request.subject.to_dict(),
                "capability": request.capability.value,
                "scope": request.scope.to_dict(),
                "policy_digest": policy.policy_digest,
                "plan_digest": request.plan_digest,
            }
        )
    )


def _policy_digest_payload(
    policy_version: str,
    rules: tuple[CapabilityRule, ...],
) -> dict[str, object]:
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "record_type": "furatena.capability.policy",
        "policy_version": policy_version,
        "rules": [item.to_dict() for item in rules],
    }


def _record_header(value: Mapping[str, Any], expected: str) -> None:
    if value.get("schema_version") != CAPABILITY_SCHEMA_VERSION:
        raise ValueError(f"unsupported capability schema_version: {value.get('schema_version')!r}")
    if value.get("record_type") != expected:
        raise ValueError(f"expected capability record_type {expected!r}")


def _required(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"capability {label} is required")
    return text


def _digest(value: object) -> str:
    text = str(value or "")
    if not _DIGEST_RE.fullmatch(text):
        raise ValueError(f"invalid capability SHA-256 digest: {value!r}")
    return text


def _logical_path(value: object) -> str:
    text = _required(value, "logical path").replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
        raise ValueError(f"invalid capability logical path: {value!r}")
    return path.as_posix()


def _logical_pattern(value: object) -> str:
    text = _required(value, "path pattern").replace("\\", "/")
    if text.startswith("/") or ".." in PurePosixPath(text).parts:
        raise ValueError(f"invalid capability path pattern: {value!r}")
    return text


def _sorted_strings(values: object) -> tuple[str, ...]:
    return tuple(sorted({str(item).strip() for item in _sequence(values) if str(item).strip()}))


def _string_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(value))


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str | bytes | bytearray | Mapping):
        raise TypeError("capability sequence field must be an array")
    if not isinstance(value, Iterable):
        raise TypeError("capability sequence field must be an array")
    return tuple(value)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"capability {label} must be an object")
    return {str(key): item for key, item in value.items()}


def _projection(value: str) -> None:
    if value not in {"trusted", "display"}:
        raise ValueError(f"unsupported capability projection: {value}")
