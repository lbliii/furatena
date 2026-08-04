"""Contracts for the immutable author truth projection."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, cast

import pytest

from furatena.catalog.author_truth import (
    ArtifactTruthState,
    AuthorActionCapability,
    AuthorActionKey,
    AuthorActionState,
    AuthorFreshness,
    AuthorJobState,
    AuthorPlaneKey,
    AuthorPublicationTruth,
    AuthorTruthPlane,
    DeploymentTruthState,
    LifecycleTruthState,
    RepositoryTruthState,
    build_author_truth_surface,
    unavailable_publication_truth,
)


def _capability(href: str) -> AuthorActionCapability:
    return AuthorActionCapability(
        allowed=True,
        state=AuthorActionState.AVAILABLE,
        reason="Trusted publication automation authorizes this exact operation.",
        remediation="No remediation is required.",
        href=href,
        job_state=AuthorJobState.IDLE,
    )


def _publication_truth() -> AuthorPublicationTruth:
    return AuthorPublicationTruth(
        repository=AuthorTruthPlane(
            key=AuthorPlaneKey.REPOSITORY,
            label="Repository",
            state=RepositoryTruthState.MERGED,
            freshness=AuthorFreshness.CURRENT,
            summary="The reviewed change is merged.",
            identity_label="Merged commit",
            identity="8cd567dcd30ccd7bd7e13224955ec4fc3f70be51",
            updated_at="2026-08-03T12:00:00Z",
        ),
        artifact=AuthorTruthPlane(
            key=AuthorPlaneKey.ARTIFACT,
            label="Artifact",
            state=ArtifactTruthState.VERIFIED,
            freshness=AuthorFreshness.CURRENT,
            summary="The immutable artifact passed verification.",
            identity_label="Artifact digest",
            identity=f"sha256:{'a' * 64}",
        ),
        deployment=AuthorTruthPlane(
            key=AuthorPlaneKey.DEPLOYMENT,
            label="Deployment",
            state=DeploymentTruthState.HEALTHY,
            freshness=AuthorFreshness.CURRENT,
            summary="The deployment is serving the verified artifact.",
            identity_label="Deployment identity",
            identity="production@deploy-394",
        ),
        plan_id="plan-394",
        plan_digest=f"sha256:{'b' * 64}",
        approval_summary="Approval is bound to the displayed plan digest.",
        capabilities={
            AuthorActionKey.REQUEST_APPROVAL: _capability("/publication/approval/plan-394"),
            AuthorActionKey.CREATE_PR: _capability("/publication/review/plan-394"),
            AuthorActionKey.BUILD_ARTIFACT: _capability("/publication/build/plan-394"),
            AuthorActionKey.PROMOTE: _capability("/publication/promote/plan-394"),
            AuthorActionKey.VERIFY: _capability("/publication/verify/deploy-394"),
            AuthorActionKey.ROLLBACK: _capability("/publication/rollback/deploy-394"),
        },
    )


def test_truth_projection_keeps_four_planes_exact_identities_and_distinct_actions() -> None:
    local_validate = AuthorActionCapability(
        allowed=False,
        state=AuthorActionState.DENIED,
        reason="The server denied validation for this session.",
        remediation="Authenticate with the required author role.",
    )
    publication = _publication_truth()
    surface = build_author_truth_surface(
        lifecycle=LifecycleTruthState.PUBLIC,
        source_revision=f"sha256:{'c' * 64}",
        source_freshness=AuthorFreshness.CURRENT,
        source_summary="Source matches the indexed catalog revision.",
        validation_ok=True,
        validation_error_count=0,
        validation_warning_count=0,
        export_included=True,
        local_capabilities={AuthorActionKey.VALIDATE: local_validate},
        actions={
            "validate": "/docs/_author/page.json?slug=docs/page&validate=1",
            "inspect_public": "/docs/_author/page.json?slug=docs/page&inspect_public=1",
        },
        publication=publication,
    )

    planes = surface["planes"]
    assert [plane["key"] for plane in planes] == [
        "lifecycle",
        "repository",
        "artifact",
        "deployment",
    ]
    assert surface["identities"] == {
        "source_revision": f"sha256:{'c' * 64}",
        "plan_id": "plan-394",
        "plan_digest": f"sha256:{'b' * 64}",
        "artifact_id": f"sha256:{'a' * 64}",
        "deployment_id": "production@deploy-394",
    }
    actions = {action["key"]: action for action in surface["actions"]}
    assert list(actions) == [key.value for key in AuthorActionKey]
    assert len({action["label"] for action in actions.values()}) == len(AuthorActionKey)
    assert actions["validate"]["state"] == "denied"
    assert actions["validate"]["href"].endswith("validate=1")
    assert actions["promote"]["href"] == "/publication/promote/plan-394"
    assert actions["review_plan"]["operation"] == "draft"

    assert isinstance(publication.capabilities, MappingProxyType)
    with pytest.raises(TypeError):
        cast(Any, publication.capabilities)[AuthorActionKey.PROMOTE] = local_validate


def test_unavailable_publication_truth_fails_closed_without_inventing_state() -> None:
    publication = unavailable_publication_truth()
    assert [
        publication.repository.state,
        publication.artifact.state,
        publication.deployment.state,
    ] == [
        RepositoryTruthState.UNAVAILABLE,
        ArtifactTruthState.UNAVAILABLE,
        DeploymentTruthState.UNAVAILABLE,
    ]
    assert publication.capabilities == {}
    assert publication.blockers


@pytest.mark.parametrize(
    ("key", "state"),
    [
        (AuthorPlaneKey.REPOSITORY, RepositoryTruthState.REVIEWED),
        (AuthorPlaneKey.REPOSITORY, RepositoryTruthState.MERGED),
        (AuthorPlaneKey.ARTIFACT, ArtifactTruthState.STALE),
        (AuthorPlaneKey.ARTIFACT, ArtifactTruthState.BUILT),
        (AuthorPlaneKey.ARTIFACT, ArtifactTruthState.VERIFIED),
        (AuthorPlaneKey.ARTIFACT, ArtifactTruthState.REJECTED),
        (AuthorPlaneKey.DEPLOYMENT, DeploymentTruthState.DEPLOYED),
        (AuthorPlaneKey.DEPLOYMENT, DeploymentTruthState.HEALTHY),
        (AuthorPlaneKey.DEPLOYMENT, DeploymentTruthState.DEGRADED),
        (AuthorPlaneKey.DEPLOYMENT, DeploymentTruthState.ROLLED_BACK),
    ],
)
def test_delivery_plane_vocabulary_preserves_progress_stale_and_error_states(
    key: AuthorPlaneKey,
    state: RepositoryTruthState | ArtifactTruthState | DeploymentTruthState,
) -> None:
    plane = AuthorTruthPlane(
        key=key,
        label=key.value.title(),
        state=state,
        freshness=AuthorFreshness.STALE,
        summary=f"The exact {key.value} record is stale or requires attention.",
        identity_label="Exact identity",
        identity="fixture-394",
    )
    assert plane.to_dict()["state"] == state.value
    assert plane.to_dict()["freshness"] == "stale"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", "Trusted <strong>markup</strong>"),
        ("remediation", "Retry after <script>alert(1)</script>"),
    ],
)
def test_capability_text_rejects_provider_markup(field: str, value: str) -> None:
    kwargs = {
        "allowed": True,
        "state": AuthorActionState.AVAILABLE,
        "reason": "Authorized by the server.",
        "remediation": "No remediation is required.",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match="cannot contain markup"):
        AuthorActionCapability(**kwargs)


@pytest.mark.parametrize(
    "href",
    ["https://attacker.example/action", "//attacker.example/action", "javascript:alert(1)"],
)
def test_capability_links_are_restricted_to_local_server_paths(href: str) -> None:
    with pytest.raises(ValueError, match=r"local server|local server origin"):
        _capability(href)


def test_plan_digest_rejects_noncanonical_identity_instead_of_rewriting_it() -> None:
    publication = _publication_truth()
    with pytest.raises(ValueError, match="exact lowercase SHA-256"):
        AuthorPublicationTruth(
            repository=publication.repository,
            artifact=publication.artifact,
            deployment=publication.deployment,
            plan_digest=f"sha256:{'A' * 64}",
        )


def test_running_job_is_truthful_but_not_duplicate_action_enabled() -> None:
    publication = _publication_truth()
    publication = AuthorPublicationTruth(
        repository=publication.repository,
        artifact=publication.artifact,
        deployment=publication.deployment,
        capabilities={
            AuthorActionKey.PROMOTE: AuthorActionCapability(
                allowed=True,
                state=AuthorActionState.RUNNING,
                reason="Promotion is already running for this exact artifact.",
                remediation="Wait for the current promotion job to finish.",
                href="/publication/promote/plan-394",
                job_state=AuthorJobState.RUNNING,
            )
        },
    )
    surface = build_author_truth_surface(
        lifecycle=LifecycleTruthState.PUBLIC,
        source_revision=f"sha256:{'c' * 64}",
        source_freshness=AuthorFreshness.CURRENT,
        source_summary="Source matches the indexed catalog revision.",
        validation_ok=True,
        validation_error_count=0,
        validation_warning_count=0,
        export_included=True,
        local_capabilities={},
        actions={"validate": "/validate", "inspect_public": "/inspect"},
        publication=publication,
    )
    promote = next(action for action in surface["actions"] if action["key"] == "promote")
    assert promote["allowed"] is True
    assert promote["enabled"] is False
    assert promote["state"] == "running"
    assert promote["job_state"] == "running"
