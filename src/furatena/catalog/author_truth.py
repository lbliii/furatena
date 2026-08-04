"""Immutable presentation truth for the author state-and-action surface."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import urlsplit

from furatena.catalog.publication_contracts import normalize_rfc3339

AUTHOR_TRUTH_SCHEMA_VERSION = 1
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")


class AuthorPlaneKey(StrEnum):
    LIFECYCLE = "lifecycle"
    REPOSITORY = "repository"
    ARTIFACT = "artifact"
    DEPLOYMENT = "deployment"


class LifecycleTruthState(StrEnum):
    DRAFT = "draft"
    PUBLIC = "public"
    PRIVATE = "private"
    INTERNAL = "internal"
    UNLISTED = "unlisted"
    ARCHIVED = "archived"
    RESTRICTED = "restricted"


class RepositoryTruthState(StrEnum):
    UNAVAILABLE = "unavailable"
    CLEAN = "clean"
    MODIFIED = "modified"
    COMMITTED = "committed"
    PR = "pr"
    REVIEWED = "reviewed"
    MERGED = "merged"


class ArtifactTruthState(StrEnum):
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    BUILDING = "building"
    BUILT = "built"
    VERIFIED = "verified"
    REJECTED = "rejected"


class DeploymentTruthState(StrEnum):
    UNAVAILABLE = "unavailable"
    ABSENT = "absent"
    PENDING = "pending"
    DEPLOYED = "deployed"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ROLLED_BACK = "rolled_back"


class AuthorFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class AuthorActionKey(StrEnum):
    VALIDATE = "validate"
    INSPECT_PUBLIC = "inspect_public"
    REVIEW_PLAN = "review_plan"
    REQUEST_APPROVAL = "request_approval"
    MARK_PUBLIC = "mark_public"
    MARK_DRAFT = "mark_draft"
    CREATE_PR = "create_pr"
    BUILD_ARTIFACT = "build_artifact"
    PROMOTE = "promote"
    VERIFY = "verify"
    ROLLBACK = "rollback"


class AuthorActionState(StrEnum):
    AVAILABLE = "available"
    BLOCKED = "blocked"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"


class AuthorJobState(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AuthorConnectionState(StrEnum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    POLLING = "polling"
    RECONNECTING = "reconnecting"


type PlaneState = (
    LifecycleTruthState | RepositoryTruthState | ArtifactTruthState | DeploymentTruthState
)


@dataclass(frozen=True, slots=True)
class AuthorTruthPlane:
    key: AuthorPlaneKey
    label: str
    state: PlaneState
    freshness: AuthorFreshness
    summary: str
    identity_label: str
    identity: str | None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", AuthorPlaneKey(self.key))
        expected = {
            AuthorPlaneKey.LIFECYCLE: LifecycleTruthState,
            AuthorPlaneKey.REPOSITORY: RepositoryTruthState,
            AuthorPlaneKey.ARTIFACT: ArtifactTruthState,
            AuthorPlaneKey.DEPLOYMENT: DeploymentTruthState,
        }[self.key]
        if not isinstance(self.state, expected):
            object.__setattr__(self, "state", expected(self.state))
        object.__setattr__(self, "freshness", AuthorFreshness(self.freshness))
        for name in ("label", "summary", "identity_label"):
            object.__setattr__(self, name, _plain(getattr(self, name), name))
        if self.identity is not None:
            object.__setattr__(self, "identity", _identity(self.identity, "identity"))
        if self.updated_at is not None:
            object.__setattr__(self, "updated_at", normalize_rfc3339(self.updated_at))

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key.value,
            "label": self.label,
            "state": self.state.value,
            "freshness": self.freshness.value,
            "summary": self.summary,
            "identity_label": self.identity_label,
            "identity": self.identity,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class AuthorActionCapability:
    allowed: bool
    state: AuthorActionState
    reason: str
    remediation: str
    href: str | None = None
    job_state: AuthorJobState = AuthorJobState.IDLE

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", AuthorActionState(self.state))
        object.__setattr__(self, "job_state", AuthorJobState(self.job_state))
        object.__setattr__(self, "reason", _plain(self.reason, "capability reason"))
        object.__setattr__(
            self,
            "remediation",
            _plain(self.remediation, "capability remediation"),
        )
        if self.href is not None:
            object.__setattr__(self, "href", _safe_href(self.href))
        if self.allowed and self.state in {AuthorActionState.BLOCKED, AuthorActionState.DENIED}:
            raise ValueError(
                "Allowed author capabilities cannot declare blocked or denied action state."
            )
        if not self.allowed and self.state not in {
            AuthorActionState.BLOCKED,
            AuthorActionState.DENIED,
            AuthorActionState.FAILED,
        }:
            raise ValueError(
                "Unavailable author capabilities require blocked, denied, or failed action state."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "state": self.state.value,
            "reason": self.reason,
            "remediation": self.remediation,
            "href": self.href,
            "job_state": self.job_state.value,
        }


@dataclass(frozen=True, slots=True)
class AuthorPublicationTruth:
    """Optional records projected from existing publication services."""

    repository: AuthorTruthPlane
    artifact: AuthorTruthPlane
    deployment: AuthorTruthPlane
    plan_id: str | None = None
    plan_digest: str | None = None
    approval_summary: str = "No approval evaluation is connected."
    capabilities: Mapping[AuthorActionKey, AuthorActionCapability] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected_keys = (
            (self.repository, AuthorPlaneKey.REPOSITORY),
            (self.artifact, AuthorPlaneKey.ARTIFACT),
            (self.deployment, AuthorPlaneKey.DEPLOYMENT),
        )
        for plane, expected in expected_keys:
            if plane.key != expected:
                raise ValueError(
                    "Publication truth planes must retain their declared presentation plane key."
                )
        if self.plan_id is not None:
            object.__setattr__(self, "plan_id", _identity(self.plan_id, "plan identifier"))
        if self.plan_digest is not None:
            object.__setattr__(self, "plan_digest", _digest(self.plan_digest))
        object.__setattr__(
            self,
            "approval_summary",
            _plain(self.approval_summary, "approval summary"),
        )
        capabilities = MappingProxyType(
            {AuthorActionKey(key): value for key, value in dict(self.capabilities).items()}
        )
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(
            self,
            "blockers",
            tuple(_plain(item, "publication blocker") for item in self.blockers),
        )


class AuthorTruthProvider(Protocol):
    """Trusted composition adapter over existing publication read services."""

    def snapshot(
        self,
        node: Any,
        *,
        source_revision: str | None,
    ) -> AuthorPublicationTruth | None: ...


_ACTION_PRESENTATION: dict[AuthorActionKey, dict[str, object]] = {
    AuthorActionKey.VALIDATE: {
        "label": "Validate",
        "effect": "Refresh validation against the current source and catalog generation.",
        "confirmation": "Read-only; no confirmation required.",
        "kind": "get",
        "phase": "validation",
    },
    AuthorActionKey.INSPECT_PUBLIC: {
        "label": "Inspect public output",
        "effect": "Inspect whether this exact source revision is eligible for public output.",
        "confirmation": "Read-only; no confirmation required.",
        "kind": "get",
        "phase": "inspection",
    },
    AuthorActionKey.REVIEW_PLAN: {
        "label": "Review lifecycle diff",
        "effect": "Compute a dry-run source diff without changing lifecycle or delivery state.",
        "confirmation": "Dry-run only; no source mutation is authorized.",
        "kind": "dry_run",
        "phase": "plan",
    },
    AuthorActionKey.REQUEST_APPROVAL: {
        "label": "Request approval",
        "effect": "Request plan-bound approval without committing, building, or deploying.",
        "confirmation": "Creates an approval request bound to the displayed plan digest.",
        "kind": "link",
        "phase": "approval",
    },
    AuthorActionKey.MARK_PUBLIC: {
        "label": "Mark public",
        "effect": "Change source lifecycle metadata to public only.",
        "confirmation": "Confirms a source metadata change; it does not commit, build, or deploy.",
        "kind": "transition",
        "operation": "publish",
        "phase": "lifecycle",
    },
    AuthorActionKey.MARK_DRAFT: {
        "label": "Mark draft",
        "effect": "Change source lifecycle metadata to draft only.",
        "confirmation": "Confirms a source metadata change; it does not change deployed output.",
        "kind": "transition",
        "operation": "draft",
        "phase": "lifecycle",
    },
    AuthorActionKey.CREATE_PR: {
        "label": "Create PR",
        "effect": "Create the configured Git review effect for the exact approved changeset.",
        "confirmation": "Creates a provider review; it does not report the change as merged.",
        "kind": "link",
        "phase": "repository",
    },
    AuthorActionKey.BUILD_ARTIFACT: {
        "label": "Build artifact",
        "effect": "Build one immutable artifact from the exact merged commit.",
        "confirmation": "Starts a long-running build bound to the displayed plan and commit.",
        "kind": "link",
        "phase": "artifact",
    },
    AuthorActionKey.PROMOTE: {
        "label": "Promote",
        "effect": "Promote the displayed verified artifact digest to the next environment.",
        "confirmation": "Changes serving state only after trusted automation reauthorizes.",
        "kind": "link",
        "phase": "deployment",
    },
    AuthorActionKey.VERIFY: {
        "label": "Verify deployment",
        "effect": "Check readiness, build identity, public projection, privacy, and smoke evidence.",
        "confirmation": "Read-only serving verification; no confirmation required.",
        "kind": "link",
        "phase": "verification",
    },
    AuthorActionKey.ROLLBACK: {
        "label": "Roll back",
        "effect": "Restore a verified artifact from successful destination history.",
        "confirmation": "Requires a reason and fresh rollback authorization in trusted automation.",
        "kind": "link",
        "phase": "rollback",
    },
}


def build_author_truth_surface(
    *,
    lifecycle: LifecycleTruthState,
    source_revision: str | None,
    source_freshness: AuthorFreshness,
    source_summary: str,
    validation_ok: bool,
    validation_error_count: int,
    validation_warning_count: int,
    export_included: bool,
    local_capabilities: Mapping[AuthorActionKey, AuthorActionCapability],
    actions: Mapping[str, str],
    publication: AuthorPublicationTruth | None = None,
    feedback: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Return one escaped-template-ready projection without inventing delivery truth."""

    lifecycle_plane = AuthorTruthPlane(
        key=AuthorPlaneKey.LIFECYCLE,
        label="Lifecycle",
        state=lifecycle,
        freshness=source_freshness,
        summary=source_summary,
        identity_label="Source revision",
        identity=source_revision,
    )
    if publication is None:
        publication = unavailable_publication_truth()
    capabilities = dict(publication.capabilities)
    capabilities.update({AuthorActionKey(key): value for key, value in local_capabilities.items()})
    rendered_actions = []
    for key in AuthorActionKey:
        capability = capabilities.get(key, _unavailable_capability(key))
        presentation = _ACTION_PRESENTATION[key]
        enabled = (
            capability.allowed
            and capability.state != AuthorActionState.RUNNING
            and (
                capability.job_state
                not in {
                    AuthorJobState.LOADING,
                    AuthorJobState.RUNNING,
                    AuthorJobState.RECONNECTING,
                }
            )
        )
        operation = presentation.get("operation")
        if key == AuthorActionKey.REVIEW_PLAN:
            operation = "draft" if lifecycle == LifecycleTruthState.PUBLIC else "publish"
        href = capability.href
        if key == AuthorActionKey.VALIDATE:
            href = actions["validate"]
        elif key == AuthorActionKey.INSPECT_PUBLIC:
            href = actions["inspect_public"]
        rendered_actions.append(
            {
                "key": key.value,
                "label": presentation["label"],
                "effect": presentation["effect"],
                "confirmation": presentation["confirmation"],
                "kind": presentation["kind"],
                "phase": presentation["phase"],
                "operation": operation,
                "allowed": capability.allowed,
                "enabled": enabled,
                "state": capability.state.value,
                "reason": capability.reason,
                "remediation": capability.remediation,
                "href": href,
                "job_state": capability.job_state.value,
            }
        )
    blockers = list(publication.blockers)
    if not validation_ok:
        blockers.append(
            f"Validation has {validation_error_count} blocking error(s); correct them and validate again."
        )
    if validation_warning_count:
        blockers.append(
            f"Validation has {validation_warning_count} warning(s); review or waive them through policy."
        )
    feedback_record = _feedback(feedback)
    return {
        "schema_version": AUTHOR_TRUTH_SCHEMA_VERSION,
        "planes": [
            lifecycle_plane.to_dict(),
            publication.repository.to_dict(),
            publication.artifact.to_dict(),
            publication.deployment.to_dict(),
        ],
        "identities": {
            "source_revision": source_revision,
            "plan_id": publication.plan_id,
            "plan_digest": publication.plan_digest,
            "artifact_id": publication.artifact.identity,
            "deployment_id": publication.deployment.identity,
        },
        "approval_summary": publication.approval_summary,
        "actions": rendered_actions,
        "blockers": blockers,
        "validation": {
            "ok": validation_ok,
            "error_count": validation_error_count,
            "warning_count": validation_warning_count,
        },
        "export_included": export_included,
        "feedback": feedback_record,
        "connection": {
            "state": AuthorConnectionState.CONNECTING.value,
            "label": "Connecting live author updates",
        },
    }


def unavailable_publication_truth() -> AuthorPublicationTruth:
    """Return fail-closed defaults when publication services are not composed."""

    return AuthorPublicationTruth(
        repository=AuthorTruthPlane(
            key=AuthorPlaneKey.REPOSITORY,
            label="Repository",
            state=RepositoryTruthState.UNAVAILABLE,
            freshness=AuthorFreshness.UNKNOWN,
            summary="Repository state is not connected to this author session.",
            identity_label="Commit or review",
            identity=None,
        ),
        artifact=AuthorTruthPlane(
            key=AuthorPlaneKey.ARTIFACT,
            label="Artifact",
            state=ArtifactTruthState.UNAVAILABLE,
            freshness=AuthorFreshness.UNKNOWN,
            summary="No plan-bound artifact state is connected.",
            identity_label="Artifact digest",
            identity=None,
        ),
        deployment=AuthorTruthPlane(
            key=AuthorPlaneKey.DEPLOYMENT,
            label="Deployment",
            state=DeploymentTruthState.UNAVAILABLE,
            freshness=AuthorFreshness.UNKNOWN,
            summary="No environment promotion state is connected.",
            identity_label="Deployment identity",
            identity=None,
        ),
        blockers=(
            "Connect configured publication workflow records before repository actions.",
            "Build and verify an exact artifact before promotion or rollback.",
        ),
    )


def _unavailable_capability(key: AuthorActionKey) -> AuthorActionCapability:
    return AuthorActionCapability(
        allowed=False,
        state=AuthorActionState.BLOCKED,
        reason=f"{_ACTION_PRESENTATION[key]['label']} is unavailable in this author session.",
        remediation="Connect the corresponding publication service and obtain server authorization.",
    )


def _feedback(value: Mapping[str, Any] | None) -> dict[str, object] | None:
    if value is None:
        return None
    diagnostics = value.get("diagnostics")
    return {
        "operation": _plain(value.get("operation") or "author action", "feedback operation"),
        "ok": bool(value.get("ok")),
        "dry_run": bool(value.get("dry_run")),
        "diff": _plain_multiline(value.get("diff") or "No source changes.", "feedback diff"),
        "diagnostics": [
            {
                "severity": _plain(item.get("severity") or "info", "diagnostic severity"),
                "message": _plain(item.get("message") or "No detail.", "diagnostic message"),
                "next_action": _plain(
                    item.get("next_action") or "Review the action state before retrying.",
                    "diagnostic recovery",
                ),
            }
            for item in diagnostics
            if isinstance(item, Mapping)
        ]
        if isinstance(diagnostics, list)
        else [],
    }


def _plain(value: object, label: str) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise ValueError(f"Author truth {label} requires a non-empty plain-text value.")
    if "<" in normalized or ">" in normalized:
        raise ValueError(f"Author truth {label} cannot contain markup delimiters or tags.")
    return normalized


def _plain_multiline(value: object, label: str) -> str:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError(f"Author truth {label} requires non-empty text for presentation.")
    if "<script" in normalized.lower() or "</script" in normalized.lower():
        raise ValueError(f"Author truth {label} cannot contain script markup or tags.")
    return normalized


def _identity(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if _DIGEST.fullmatch(normalized) is not None:
        return normalized
    if _IDENTIFIER.fullmatch(normalized) is None:
        raise ValueError(f"Author truth {label} contains an invalid identity value.")
    return normalized


def _digest(value: object) -> str:
    normalized = str(value or "").strip()
    if _DIGEST.fullmatch(normalized) is None:
        raise ValueError("Author truth plan digests require an exact lowercase SHA-256 value.")
    return normalized


def _safe_href(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized.startswith("/") and not normalized.startswith("//"):
        parsed = urlsplit(normalized)
        if parsed.scheme or parsed.netloc:
            raise ValueError("Author truth action links cannot override the local server origin.")
        return normalized
    raise ValueError("Author truth action links require a trusted local server path only.")
