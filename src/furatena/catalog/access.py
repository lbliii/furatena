"""Role-based access model for catalog mounts and pages."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AccessRole(StrEnum):
    """Built-in roles ordered from least to most privileged."""

    ANONYMOUS = "anonymous"
    READER = "reader"
    CONTRIBUTOR = "contributor"
    PUBLISHER = "publisher"
    ADMIN = "admin"


class AccessPermission(StrEnum):
    """Permission names used by browser, export, authoring, and agent surfaces."""

    READ = "read"
    SEARCH = "search"
    RETRIEVE = "retrieve"
    EXPORT = "export"
    AUTHOR = "author"
    PUBLISH = "publish"
    CONFIGURE = "configure"
    ADMINISTER = "administer"


_ROLE_RANK = {
    AccessRole.ANONYMOUS: 0,
    AccessRole.READER: 1,
    AccessRole.CONTRIBUTOR: 2,
    AccessRole.PUBLISHER: 3,
    AccessRole.ADMIN: 4,
}

_PERMISSION_MIN_ROLE = {
    AccessPermission.READ: AccessRole.ANONYMOUS,
    AccessPermission.SEARCH: AccessRole.ANONYMOUS,
    AccessPermission.RETRIEVE: AccessRole.ANONYMOUS,
    AccessPermission.EXPORT: AccessRole.ANONYMOUS,
    AccessPermission.AUTHOR: AccessRole.CONTRIBUTOR,
    AccessPermission.PUBLISH: AccessRole.PUBLISHER,
    AccessPermission.CONFIGURE: AccessRole.ADMIN,
    AccessPermission.ADMINISTER: AccessRole.ADMIN,
}

_VISIBILITY_READ_MIN_ROLE = {
    "public": AccessRole.ANONYMOUS,
    "unlisted": AccessRole.READER,
    "internal": AccessRole.READER,
    "private": AccessRole.READER,
    "draft": AccessRole.CONTRIBUTOR,
    "archived": AccessRole.ADMIN,
}

_AUTHOR_OPERATION_PERMISSION = {
    "status": AccessPermission.AUTHOR,
    "validate": AccessPermission.AUTHOR,
    "read": AccessPermission.AUTHOR,
    "new": AccessPermission.AUTHOR,
    "apply_edit": AccessPermission.AUTHOR,
    "save_source": AccessPermission.AUTHOR,
    "draft": AccessPermission.AUTHOR,
    "publish": AccessPermission.PUBLISH,
    "unpublish": AccessPermission.PUBLISH,
    "archive": AccessPermission.ADMINISTER,
    "inspect_publication_impact": AccessPermission.PUBLISH,
}


@dataclass(frozen=True, slots=True)
class AccessSubject:
    """The actor and groups used to evaluate catalog access."""

    actor: str = "anonymous"
    roles: frozenset[AccessRole] = field(default_factory=lambda: frozenset({AccessRole.ANONYMOUS}))
    teams: frozenset[str] = frozenset()

    @classmethod
    def anonymous(cls) -> AccessSubject:
        return cls()

    @classmethod
    def from_values(
        cls,
        *,
        actor: str = "",
        roles: object = (),
        teams: object = (),
    ) -> AccessSubject:
        normalized_roles = _normalize_roles(roles)
        if not normalized_roles:
            normalized_roles = frozenset({AccessRole.ANONYMOUS})
        return cls(
            actor=actor.strip() or "anonymous",
            roles=normalized_roles,
            teams=frozenset(_normalize_strings(teams)),
        )

    def has_role_at_least(self, role: AccessRole) -> bool:
        return any(_ROLE_RANK[item] >= _ROLE_RANK[role] for item in self.roles)


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """Access requirements declared on a mount or page."""

    visibility: str = "public"
    roles: frozenset[AccessRole] = frozenset()
    teams: frozenset[str] = frozenset()
    admin_only: bool = False

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any] | None,
        *,
        default_visibility: str = "public",
    ) -> AccessPolicy:
        data = raw or {}
        access_raw = data.get("access")
        access = access_raw if isinstance(access_raw, Mapping) else {}
        raw_visibility = access.get("visibility") or data.get("visibility")
        if raw_visibility:
            visibility = str(raw_visibility).strip().lower()
        elif _truthy(data.get("draft")):
            visibility = "draft"
        elif data.get("archived_at"):
            visibility = "archived"
        else:
            visibility = str(default_visibility or "public").strip().lower()
        teams = set(_normalize_strings(access.get("teams")))
        admin_only = _truthy(access.get("admin_only") or data.get("admin_only"))
        roles = _normalize_roles(access.get("roles"))
        if admin_only:
            roles = frozenset({AccessRole.ADMIN})
        return cls(
            visibility=visibility or "public",
            roles=roles,
            teams=frozenset(teams),
            admin_only=admin_only,
        )

    @classmethod
    def from_page_meta(cls, meta: Mapping[str, Any]) -> AccessPolicy:
        return cls.from_mapping(meta, default_visibility="public")

    @classmethod
    def from_mount_dict(cls, raw: Mapping[str, Any]) -> AccessPolicy:
        return cls.from_mapping(raw, default_visibility="public")

    def to_meta(self) -> dict[str, object]:
        return {
            "visibility": self.visibility,
            "roles": [role.value for role in sorted(self.roles, key=lambda item: _ROLE_RANK[item])],
            "teams": sorted(self.teams),
            "admin_only": self.admin_only,
        }


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """Result of evaluating one permission against one policy."""

    allowed: bool
    permission: AccessPermission
    required_role: AccessRole
    reason: str


def evaluate_access(
    policy: AccessPolicy,
    subject: AccessSubject | None = None,
    *,
    permission: AccessPermission | str = AccessPermission.READ,
) -> AccessDecision:
    """Evaluate a role/team policy for one permission."""
    actor = subject or AccessSubject.anonymous()
    normalized_permission = _normalize_permission(permission)
    required_role = required_role_for(policy, normalized_permission)
    if policy.admin_only and not actor.has_role_at_least(AccessRole.ADMIN):
        return AccessDecision(False, normalized_permission, AccessRole.ADMIN, "admin-only surface")
    if not actor.has_role_at_least(required_role):
        return AccessDecision(
            False,
            normalized_permission,
            required_role,
            f"requires role {required_role.value}",
        )
    if policy.roles and not _has_any_allowed_role(actor, policy.roles):
        allowed = ", ".join(role.value for role in sorted(policy.roles, key=lambda item: _ROLE_RANK[item]))
        return AccessDecision(False, normalized_permission, required_role, f"requires one of roles: {allowed}")
    if policy.teams and AccessRole.ADMIN not in actor.roles and not (policy.teams & actor.teams):
        return AccessDecision(False, normalized_permission, required_role, "requires matching team")
    return AccessDecision(True, normalized_permission, required_role, "allowed")


def required_role_for(policy: AccessPolicy, permission: AccessPermission | str) -> AccessRole:
    """Return the minimum built-in role for a policy and permission."""
    normalized_permission = _normalize_permission(permission)
    permission_role = _PERMISSION_MIN_ROLE[normalized_permission]
    visibility_role = _VISIBILITY_READ_MIN_ROLE.get(policy.visibility, AccessRole.ADMIN)
    if normalized_permission in {
        AccessPermission.READ,
        AccessPermission.SEARCH,
        AccessPermission.RETRIEVE,
        AccessPermission.EXPORT,
    }:
        return _max_role(permission_role, visibility_role)
    return permission_role


def can_access(
    policy: AccessPolicy,
    subject: AccessSubject | None = None,
    *,
    permission: AccessPermission | str = AccessPermission.READ,
) -> bool:
    return evaluate_access(policy, subject, permission=permission).allowed


def author_permission_for(operation: str) -> AccessPermission:
    """Return the permission required by one author operation."""
    normalized = str(operation).strip().lower()
    try:
        return _AUTHOR_OPERATION_PERMISSION[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown author operation: {operation}") from exc


def evaluate_author_access(
    operation: str,
    policy: AccessPolicy,
    subject: AccessSubject | None = None,
) -> AccessDecision:
    """Evaluate the shared CLI, browser, and MCP author policy."""
    return evaluate_access(
        policy,
        subject,
        permission=author_permission_for(operation),
    )


def accessible_nodes(
    catalog: Any,
    nodes: Any,
    *,
    subject: AccessSubject | None = None,
    permission: AccessPermission | str = AccessPermission.READ,
    include_private: bool = False,
) -> list[Any]:
    """Filter nodes for a subject, preserving private-mode escape hatches."""
    items = list(nodes)
    if include_private:
        return items
    if hasattr(catalog, "can_access_node"):
        return [
            node
            for node in items
            if catalog.can_access_node(node, subject, permission=permission)
        ]
    from furatena.catalog.lifecycle import public_nodes

    return public_nodes(items)


def _normalize_permission(permission: AccessPermission | str) -> AccessPermission:
    if isinstance(permission, AccessPermission):
        return permission
    return AccessPermission(str(permission).strip().lower())


def _max_role(first: AccessRole, second: AccessRole) -> AccessRole:
    return first if _ROLE_RANK[first] >= _ROLE_RANK[second] else second


def _has_any_allowed_role(subject: AccessSubject, allowed: frozenset[AccessRole]) -> bool:
    return any(subject.has_role_at_least(role) for role in allowed)


def _normalize_roles(raw: object) -> frozenset[AccessRole]:
    roles: set[AccessRole] = set()
    for item in _normalize_strings(raw):
        try:
            roles.add(AccessRole(item))
        except ValueError:
            continue
    return frozenset(roles)


def _normalize_strings(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    if isinstance(raw, Iterable) and not isinstance(raw, Mapping):
        return tuple(str(item).strip().lower() for item in raw if str(item).strip())
    return (str(raw).strip().lower(),) if str(raw).strip() else ()


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
