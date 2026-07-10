"""Shared deterministic publication-contract samples."""

from __future__ import annotations

from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationApprovalRequirements,
    PublicationBindings,
    PublicationChangeset,
    PublicationFieldChange,
    PublicationIdentity,
    PublicationImpact,
    PublicationOperation,
    PublicationOutputIntent,
    PublicationPlan,
    PublicationRequest,
    PublicationRisk,
    PublicationValidation,
    sha256_digest,
)


def digest(label: str) -> str:
    return sha256_digest(label.encode("utf-8"))


def sample_actor(actor: str = "publisher@example.com") -> PublicationActor:
    return PublicationActor(
        actor=actor,
        identity_source="test-oidc",
        roles=("publisher", "contributor"),
        teams=("docs",),
    )


def sample_plan(
    *,
    correlation_id: str = "corr-001",
    idempotency_key: str = "idem-001",
    operation: PublicationOperation = PublicationOperation.PUBLISH,
    required_count: int = 1,
    validation_error_count: int = 0,
    policy_version: str = "policy-v2",
    policy_digest_value: str | None = None,
    expires_at: str = "2035-01-01T00:00:00Z",
    outputs: tuple[PublicationOutputIntent, ...] | None = None,
) -> PublicationPlan:
    unified_diff = (
        "--- a/docs/guide.md\n"
        "+++ b/docs/guide.md\n"
        "@@ -1,3 +1,3 @@\n"
        "-visibility: draft\n"
        "+visibility: public\n"
    )
    actor = sample_actor()
    bound_policy_digest = policy_digest_value or digest(policy_version)
    return PublicationPlan.create(
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        creator=actor,
        expires_at=expires_at,
        identity=PublicationIdentity(
            tenant="acme",
            workspace="docs",
            site="developer",
            mount="product",
            node_id="product:latest:docs/guide",
            source_path="docs/guide.md",
        ),
        request=PublicationRequest(
            operation=operation,
            previous_visibility="draft",
            resulting_visibility="public",
        ),
        bindings=PublicationBindings(
            source_revision=digest("source-old"),
            catalog_generation="generation-42",
            config_digest=digest("config-v3"),
            policy_version=policy_version,
            policy_digest=bound_policy_digest,
            validation_snapshot_id="validation-42",
            validation_digest=digest("validation-42"),
        ),
        changeset=PublicationChangeset(
            paths=("docs/guide.md",),
            unified_diff=unified_diff,
            diff_sha256=sha256_digest(unified_diff.encode("utf-8")),
            previous_source_revision=digest("source-old"),
            resulting_source_revision=digest("source-new"),
            front_matter_changes=(
                PublicationFieldChange(
                    field="visibility",
                    previous="draft",
                    resulting="public",
                ),
            ),
        ),
        validation=PublicationValidation(
            run_id="validation-run-42",
            snapshot_id="validation-42",
            catalog_generation="generation-42",
            error_count=validation_error_count,
            warning_count=1,
            info_count=2,
            diagnostic_ids=("warning:links:docs/guide.md:4",),
            waivable_warning_ids=("warning:links:docs/guide.md:4",),
            diagnostics_digest=digest("diagnostics-42"),
        ),
        impact=PublicationImpact(
            affected_projections=("search", "navigation", "agent", "export"),
            risk=PublicationRisk.MEDIUM,
            reasons=("adds_to_public_output",),
        ),
        approval_requirements=PublicationApprovalRequirements(
            policy_version=policy_version,
            policy_digest=bound_policy_digest,
            required_count=required_count,
            eligible_roles=("publisher",),
            eligible_teams=("docs",),
            allow_self_approval=False,
            separation_rules=("author_cannot_approve",),
        ),
        intended_outputs=outputs
        if outputs is not None
        else (
            PublicationOutputIntent(
                kind="repository_change",
                target="product-docs",
                environment="review",
            ),
            PublicationOutputIntent(
                kind="deployment",
                target="developer-docs",
                environment="production",
            ),
        ),
    )
