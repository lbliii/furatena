"""Shared builders for provider-neutral preview contract tests."""

from __future__ import annotations

from furatena.catalog.preview_contracts import (
    PreviewAccessPolicy,
    PreviewAction,
    PreviewArtifact,
    PreviewCheck,
    PreviewCheckStatus,
    PreviewManifest,
    PreviewProviderIdentity,
    PreviewRequest,
    PreviewSource,
    PreviewState,
    PreviewSurfaces,
    sha256_digest,
)

HEAD_SHA = "a" * 40


def sample_source(*, head_sha: str = HEAD_SHA) -> PreviewSource:
    return PreviewSource(
        repository_id="github:lbliii/furatena",
        repository_url="https://github.com/lbliii/furatena",
        pull_request_number=422,
        head_ref="codex/issue-422-preview-contract",
        head_sha=head_sha,
        review_url="https://github.com/lbliii/furatena/pull/440",
    )


def sample_request(
    *,
    action: PreviewAction = PreviewAction.CREATE,
    source: PreviewSource | None = None,
    idempotency_key: str = "preview-create-422-a",
    requested_at: str = "2030-01-01T00:00:00Z",
    correlation_id: str | None = "github-delivery-1",
) -> PreviewRequest:
    return PreviewRequest(
        action=action,
        source=source or sample_source(),
        provider_id="reference",
        idempotency_key=idempotency_key,
        expected_state_version=None if action == PreviewAction.CREATE else 3,
        requested_at=requested_at,
        expires_at="2030-01-08T00:00:00Z",
        previous_preview_id=None if action == PreviewAction.CREATE else "preview-previous",
        correlation_id=correlation_id,
    )


def sample_access() -> PreviewAccessPolicy:
    return PreviewAccessPolicy(
        authentication_required=True,
        authentication_scheme="reviewer-sso",
    )


def sample_provider(*, ready: bool = True) -> PreviewProviderIdentity:
    return PreviewProviderIdentity(
        provider_id="reference",
        project_id="project-1",
        environment_id="environment-422" if ready else None,
        deployment_id="deployment-a" if ready else None,
        capabilities=("ephemeral_environment", "managed_domain", "teardown"),
    )


def sample_surfaces() -> PreviewSurfaces:
    origin = "https://preview-422.example.test"
    return PreviewSurfaces(
        human_url=f"{origin}/",
        markdown_url_template=f"{origin}/{{path}}.md",
        llms_url=f"{origin}/llms.txt",
        catalog_url=f"{origin}/catalog.json",
        query_url=f"{origin}/catalog/query.json",
        search_url=f"{origin}/search.json",
        metadata_url=f"{origin}/meta.json",
        health_url=f"{origin}/healthz",
        readiness_url=f"{origin}/readyz",
    )


def sample_artifact(*, source_sha: str = HEAD_SHA) -> PreviewArtifact:
    return PreviewArtifact(
        fingerprint=sha256_digest(b"frozen-preview-422"),
        build_id="build-422-a",
        source_sha=source_sha,
        catalog_schema_version=3,
        frozen_at="2030-01-01T00:02:00Z",
    )


def sample_checks() -> tuple[PreviewCheck, ...]:
    return (
        PreviewCheck(
            check_id="cross-surface",
            status=PreviewCheckStatus.PASS,
            summary="Human and agent surfaces share the reviewed build.",
            observed_at="2030-01-01T00:04:00Z",
        ),
        PreviewCheck(
            check_id="readiness",
            status=PreviewCheckStatus.PASS,
            summary="Readiness endpoint returned the reviewed SHA.",
            observed_at="2030-01-01T00:03:00Z",
            details_url="https://preview-422.example.test/readyz",
        ),
    )


def sample_ready_manifest() -> PreviewManifest:
    return PreviewManifest(
        state=PreviewState.READY,
        state_version=3,
        source=sample_source(),
        provider=sample_provider(),
        access=sample_access(),
        surfaces=sample_surfaces(),
        artifact=sample_artifact(),
        checks=sample_checks(),
        created_at="2030-01-01T00:00:00Z",
        updated_at="2030-01-01T00:04:00Z",
        expires_at="2030-01-08T00:00:00Z",
        correlation_id="github-delivery-1",
    )
