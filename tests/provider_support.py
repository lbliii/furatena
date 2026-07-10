"""Shared deterministic publication-provider contract samples."""

from __future__ import annotations

from furatena.catalog.publication_contracts import PublicationActor
from furatena.catalog.publication_provider import (
    ProviderCommitAttribution,
    ProviderOperation,
    ProviderProtectionState,
    PublicationChangeRequest,
    PublicationProfile,
    PublicationProfileConfig,
    RepositoryInspection,
    provider_operations_for,
)
from tests.publication_support import sample_plan


def sample_profile(profile: PublicationProfile) -> PublicationProfileConfig:
    if profile == PublicationProfile.LOCAL_ONLY:
        return PublicationProfileConfig(
            profile=profile,
            provider_id="local-filesystem",
            repository_id="local-workspace",
            allow_dirty_unrelated=True,
        )
    if profile == PublicationProfile.COMMIT:
        return PublicationProfileConfig(
            profile=profile,
            provider_id="generic-git",
            repository_id="acme/product-docs",
            base_ref="main",
        )
    if profile == PublicationProfile.PULL_REQUEST:
        return PublicationProfileConfig(
            profile=profile,
            provider_id="generic-git",
            repository_id="acme/product-docs",
            target_repository_id="publisher/product-docs",
            base_ref="main",
            branch_template="fura/{change_id}",
            review_target="main",
        )
    return PublicationProfileConfig(
        profile=profile,
        provider_id="external-ci",
        repository_id="remote/product-docs",
        external_target="publication-queue",
    )


def sample_attribution() -> ProviderCommitAttribution:
    return ProviderCommitAttribution(
        author_name="Docs Author",
        author_email="author@example.com",
        committer_name="Furatena Workflow",
        committer_email="workflow@example.com",
        workflow_actor="publisher@example.com",
        message="Publish docs/guide.md",
    )


def sample_provider_request(
    profile: PublicationProfile = PublicationProfile.PULL_REQUEST,
    *,
    operation: ProviderOperation = ProviderOperation.PREPARE,
    dry_run: bool = False,
    idempotency_key: str = "provider-idem-001",
) -> PublicationChangeRequest:
    plan = sample_plan()
    return PublicationChangeRequest.create(
        plan,
        sample_profile(profile),
        operation=operation,
        repository_base_revision="git-base-abc123",
        attribution=sample_attribution(),
        actor=PublicationActor(
            actor="publisher@example.com",
            identity_source="test-oidc",
            roles=("publisher",),
            teams=("docs",),
        ),
        idempotency_key=idempotency_key,
        dry_run=dry_run,
    )


def sample_inspection(
    profile: PublicationProfile = PublicationProfile.PULL_REQUEST,
    *,
    isolated: bool = True,
    protection: ProviderProtectionState = ProviderProtectionState.PROTECTED,
    staged_paths: tuple[str, ...] = (),
    unstaged_paths: tuple[str, ...] = (),
    untracked_paths: tuple[str, ...] = (),
    ignored_paths: tuple[str, ...] = (),
    conflicted_paths: tuple[str, ...] = (),
    permissions: tuple[ProviderOperation, ...] | None = None,
) -> RepositoryInspection:
    plan = sample_plan()
    config = sample_profile(profile)
    return RepositoryInspection.create(
        provider_id=config.provider_id,
        repository_id=config.target_repository_id or config.repository_id,
        source_repository_id=config.repository_id,
        base_source_revision=plan.changeset.previous_source_revision,
        repository_revision="git-base-abc123",
        current_branch=None if isolated else "main",
        detached=isolated,
        isolated=isolated,
        fork=config.target_repository_id is not None,
        protection=protection,
        permissions=permissions or provider_operations_for(profile),
        staged_paths=staged_paths,
        unstaged_paths=unstaged_paths,
        untracked_paths=untracked_paths,
        ignored_paths=ignored_paths,
        conflicted_paths=conflicted_paths,
        observed_at="2030-01-01T00:00:00Z",
    )
