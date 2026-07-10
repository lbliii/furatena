"""Fail-closed trusted gateway and SSO claim mapping."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from furatena.catalog.access import AccessRole, AccessSubject
from furatena.catalog.identity import normalize_identity

_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}$")
_CANONICAL_CLAIMS = frozenset({"actor", "roles", "teams", "tenant", "workspace", "site"})


class GatewayIdentityError(ValueError):
    """Raised when gateway identity context is missing, ambiguous, or untrusted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_diagnostic(self) -> dict[str, str]:
        return {
            "severity": "error",
            "rule_id": f"fura.identity.{self.code}",
            "message": self.message,
            "next_action": "Reject the request and fix the trusted gateway claim mapping.",
        }


@dataclass(frozen=True, slots=True)
class GatewayClaimMapping:
    """Provider-neutral aliases for canonical Furatena identity claims."""

    actor: tuple[str, ...] = ("sub", "actor")
    roles: tuple[str, ...] = ("roles", "role")
    teams: tuple[str, ...] = ("teams", "team")
    tenant: tuple[str, ...] = ("tenant", "tenant_id", "tid")
    workspace: tuple[str, ...] = ("workspace", "workspace_id")
    site: tuple[str, ...] = ("site", "site_id")

    def aliases(self, canonical: str) -> tuple[str, ...]:
        if canonical not in _CANONICAL_CLAIMS:
            raise KeyError(canonical)
        return getattr(self, canonical)


@dataclass(frozen=True, slots=True)
class TrustedGatewayPolicy:
    """Trust boundary and authorization constraints for mapped claims."""

    mapping: GatewayClaimMapping = field(default_factory=GatewayClaimMapping)
    required_claims: frozenset[str] = frozenset({"actor", "roles", "tenant", "site"})
    allowed_roles: frozenset[AccessRole] = frozenset(AccessRole)
    allowed_tenants: frozenset[str] = frozenset()
    allowed_sites: frozenset[str] = frozenset()
    default_workspace: str = "default"

    def __post_init__(self) -> None:
        unknown = self.required_claims - _CANONICAL_CLAIMS
        if unknown:
            raise ValueError(f"unknown required identity claims: {', '.join(sorted(unknown))}")
        if not self.allowed_roles:
            raise ValueError("allowed_roles cannot be empty")
        _validated_value(self.default_workspace, claim="default_workspace")


@dataclass(frozen=True, slots=True)
class GatewayIdentity:
    """Canonical identity and access subject derived from a trusted transport."""

    subject: AccessSubject
    tenant: str
    workspace: str
    site: str
    claim_fingerprint: str

    def to_session(self) -> dict[str, Any]:
        """Return the payload safe to place in the server-signed browser session."""
        return {
            "schema_version": 1,
            "source": "trusted_gateway",
            "actor": self.subject.actor,
            "roles": sorted(role.value for role in self.subject.roles),
            "teams": sorted(self.subject.teams),
            "tenant": self.tenant,
            "workspace": self.workspace,
            "site": self.site,
            "claim_fingerprint": self.claim_fingerprint,
        }


def map_gateway_claims(
    claims: Mapping[str, Any],
    *,
    trusted_transport: bool,
    policy: TrustedGatewayPolicy | None = None,
    expected_identity: Mapping[str, object] | None = None,
) -> GatewayIdentity:
    """Map trusted SSO claims into a subject, failing closed on ambiguity.

    ``trusted_transport`` must be decided from deployment-owned state such as
    the direct proxy peer, mTLS, or an authenticated middleware contract. A
    request header claiming that the transport is trusted is not sufficient.
    """
    if not trusted_transport:
        raise GatewayIdentityError(
            "untrusted_transport",
            "identity claims were supplied outside a deployment-verified trust boundary",
        )
    active_policy = policy or TrustedGatewayPolicy()
    values: dict[str, Any] = {}
    for canonical in _CANONICAL_CLAIMS:
        aliases = active_policy.mapping.aliases(canonical)
        values[canonical] = _claim_value(claims, canonical=canonical, aliases=aliases)

    missing = sorted(
        name for name in active_policy.required_claims if values.get(name) in (None, "", ())
    )
    if missing:
        raise GatewayIdentityError(
            "missing_claim",
            f"trusted identity is missing required claim(s): {', '.join(missing)}",
        )

    actor = _validated_value(values.get("actor"), claim="actor")
    roles = _roles(values.get("roles"), allowed=active_policy.allowed_roles)
    teams = frozenset(
        _validated_value(value, claim="teams")
        for value in _strings(values.get("teams"), claim="teams")
    )
    tenant = _validated_value(values.get("tenant"), claim="tenant")
    workspace = _validated_value(
        values.get("workspace") or active_policy.default_workspace,
        claim="workspace",
    )
    site = _validated_value(values.get("site"), claim="site")

    if active_policy.allowed_tenants and tenant not in active_policy.allowed_tenants:
        raise GatewayIdentityError("tenant_denied", f"tenant is not allowed: {tenant}")
    if active_policy.allowed_sites and site not in active_policy.allowed_sites:
        raise GatewayIdentityError("site_denied", f"site is not allowed: {site}")
    _validate_expected_identity(
        tenant=tenant,
        workspace=workspace,
        site=site,
        expected=expected_identity,
    )

    canonical = {
        "actor": actor,
        "roles": sorted(role.value for role in roles),
        "teams": sorted(teams),
        "tenant": tenant,
        "workspace": workspace,
        "site": site,
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return GatewayIdentity(
        subject=AccessSubject.from_values(actor=actor, roles=roles, teams=teams),
        tenant=tenant,
        workspace=workspace,
        site=site,
        claim_fingerprint=fingerprint,
    )


def identity_from_trusted_session(
    raw: Mapping[str, Any],
    *,
    expected_identity: Mapping[str, object] | None = None,
) -> GatewayIdentity:
    """Rehydrate a gateway identity from a server-verified signed session."""
    if raw.get("source") != "trusted_gateway" or raw.get("schema_version") != 1:
        raise GatewayIdentityError(
            "untrusted_session",
            "session identity is not marked as a server-owned trusted gateway payload",
        )
    mapped = map_gateway_claims(
        {
            "actor": raw.get("actor"),
            "roles": raw.get("roles"),
            "teams": raw.get("teams"),
            "tenant": raw.get("tenant"),
            "workspace": raw.get("workspace"),
            "site": raw.get("site"),
        },
        trusted_transport=True,
        policy=TrustedGatewayPolicy(
            mapping=GatewayClaimMapping(
                actor=("actor",),
                roles=("roles",),
                teams=("teams",),
                tenant=("tenant",),
                workspace=("workspace",),
                site=("site",),
            )
        ),
        expected_identity=expected_identity,
    )
    if raw.get("claim_fingerprint") != mapped.claim_fingerprint:
        raise GatewayIdentityError(
            "session_conflict",
            "signed session identity fingerprint does not match its canonical claims",
        )
    return mapped


def _claim_value(
    claims: Mapping[str, Any],
    *,
    canonical: str,
    aliases: tuple[str, ...],
) -> Any:
    observed = [(alias, claims[alias]) for alias in aliases if alias in claims]
    if not observed:
        return None
    normalized = [_comparable(value, claim=canonical) for _, value in observed]
    if any(value != normalized[0] for value in normalized[1:]):
        names = ", ".join(alias for alias, _ in observed)
        raise GatewayIdentityError(
            "conflicting_claim",
            f"conflicting aliases supplied for {canonical}: {names}",
        )
    return observed[0][1]


def _comparable(value: Any, *, claim: str) -> Any:
    if claim in {"roles", "teams"}:
        return tuple(sorted(_strings(value, claim=claim)))
    return _validated_value(value, claim=claim)


def _strings(value: Any, *, claim: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        items = tuple(part for part in re.split(r"[\s,]+", value.strip()) if part)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = tuple(str(item).strip() for item in value if str(item).strip())
    else:
        raise GatewayIdentityError(
            "invalid_claim",
            f"{claim} must be a string or array of strings",
        )
    return tuple(_validated_value(item, claim=claim) for item in items)


def _roles(value: Any, *, allowed: frozenset[AccessRole]) -> frozenset[AccessRole]:
    roles: set[AccessRole] = set()
    for item in _strings(value, claim="roles"):
        try:
            role = AccessRole(item.lower())
        except ValueError as exc:
            raise GatewayIdentityError("unknown_role", f"unknown access role: {item}") from exc
        if role not in allowed:
            raise GatewayIdentityError("role_denied", f"access role is not allowed: {item}")
        roles.add(role)
    if not roles:
        raise GatewayIdentityError("missing_claim", "trusted identity has no access roles")
    return frozenset(roles)


def _validated_value(value: Any, *, claim: str) -> str:
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        raise GatewayIdentityError("invalid_claim", f"{claim} must be a scalar string")
    normalized = str(value or "").strip()
    if not normalized or not _VALUE_RE.fullmatch(normalized):
        raise GatewayIdentityError("invalid_claim", f"{claim} has an invalid value")
    return normalized


def _validate_expected_identity(
    *,
    tenant: str,
    workspace: str,
    site: str,
    expected: Mapping[str, object] | None,
) -> None:
    if expected is None:
        return
    normalized = normalize_identity(expected)
    observed = {"tenant": tenant, "workspace": workspace, "site": site}
    conflicts = [name for name, value in observed.items() if normalized.get(name) != value]
    if conflicts:
        raise GatewayIdentityError(
            "identity_conflict",
            f"gateway identity conflicts with configured catalog: {', '.join(conflicts)}",
        )
