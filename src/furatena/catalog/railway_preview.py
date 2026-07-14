"""Railway adapter and runtime evidence for pull-request previews."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict
from urllib.parse import urlsplit

from furatena.catalog.build_identity import deployed_build_identity
from furatena.catalog.deployment_manifest import DEPLOYMENT_MANIFEST_SCHEMA_VERSION
from furatena.catalog.preview_contracts import (
    PreviewAccessPolicy,
    PreviewArtifact,
    PreviewCheck,
    PreviewCheckStatus,
    PreviewFailure,
    PreviewFailureDisposition,
    PreviewManifest,
    PreviewProviderIdentity,
    PreviewSource,
    PreviewState,
    PreviewSurfaces,
    PreviewTrustPolicy,
    sha256_digest,
)
from furatena.catalog.preview_security import PreviewEnvironment

_BUILDING = frozenset({"INITIALIZING", "QUEUED", "WAITING", "BUILDING", "DEPLOYING"})
_FAILED = frozenset({"FAILED", "CRASHED", "CANCELED", "SKIPPED", "SLEEPING"})


class _ManifestCommon(TypedDict):
    state_version: int
    source: PreviewSource
    provider: PreviewProviderIdentity
    access: PreviewAccessPolicy
    created_at: str
    updated_at: str
    expires_at: str
    extensions: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RailwayPreviewObservation:
    """Non-secret Railway state used to derive a provider-neutral manifest."""

    project_id: str
    environment_id: str | None
    deployment_id: str | None
    deployment_status: str
    deployment_sha: str | None
    origin: str | None
    observed_at: str
    readiness_ok: bool | None = None
    artifact_fingerprint: str | None = None
    frozen_at: str | None = None
    catalog_schema_version: int = DEPLOYMENT_MANIFEST_SCHEMA_VERSION


def railway_preview_manifest(
    source: PreviewSource,
    observation: RailwayPreviewObservation,
    *,
    expires_at: str,
    previous: PreviewManifest | None = None,
) -> PreviewManifest:
    """Translate one Railway observation without ever blessing a stale SHA."""

    status = observation.deployment_status.strip().upper()
    observed_at = observation.observed_at
    provider = PreviewProviderIdentity(
        provider_id="railway",
        project_id=observation.project_id,
        environment_id=observation.environment_id,
        deployment_id=observation.deployment_id,
        capabilities=("ephemeral_environment", "managed_domain", "teardown"),
    )
    common: _ManifestCommon = {
        "state_version": 1 if previous is None else previous.state_version + 1,
        "source": source,
        "provider": provider,
        "access": _access_policy(),
        "created_at": observed_at if previous is None else previous.created_at,
        "updated_at": observed_at,
        "expires_at": expires_at,
        "extensions": {"railway_deployment_status": status},
    }

    if observation.deployment_sha and observation.deployment_sha.lower() != source.head_sha:
        return PreviewManifest(
            state=PreviewState.FAILED,
            failure=PreviewFailure(
                disposition=PreviewFailureDisposition.CONFLICT,
                code="superseded_deployment",
                safe_message="Railway is serving a deployment other than the reviewed head SHA.",
                remediation="Wait for the newest head deployment and re-observe the environment.",
            ),
            checks=(_failed_sha_check(source, observation),),
            **common,
        )

    if status in _BUILDING:
        return PreviewManifest(
            state=PreviewState.BUILDING,
            checks=(
                PreviewCheck(
                    check_id="deployment",
                    status=PreviewCheckStatus.PENDING,
                    summary=f"Railway deployment is {status.lower()}.",
                    observed_at=observed_at,
                ),
            ),
            **common,
        )

    if status == "SUCCESS" and observation.readiness_ok is True:
        if not all(
            (
                observation.environment_id,
                observation.deployment_id,
                observation.origin,
                observation.deployment_sha,
                observation.artifact_fingerprint,
                observation.frozen_at,
            )
        ):
            raise ValueError("successful Railway preview observation lacks immutable evidence")
        surfaces = _surfaces(str(observation.origin))
        return PreviewManifest(
            state=PreviewState.READY,
            surfaces=surfaces,
            artifact=PreviewArtifact(
                fingerprint=str(observation.artifact_fingerprint),
                build_id=str(observation.deployment_id),
                source_sha=str(observation.deployment_sha),
                catalog_schema_version=observation.catalog_schema_version,
                frozen_at=str(observation.frozen_at),
            ),
            checks=(
                PreviewCheck(
                    check_id="deployment",
                    status=PreviewCheckStatus.PASS,
                    summary="Railway deployment succeeded for the reviewed head SHA.",
                    observed_at=observed_at,
                ),
                PreviewCheck(
                    check_id="readiness",
                    status=PreviewCheckStatus.PASS,
                    summary="Furatena readiness passed for the immutable frozen build.",
                    observed_at=observed_at,
                    details_url=surfaces.readiness_url,
                ),
            ),
            **common,
        )

    if status == "REMOVING":
        return PreviewManifest(state=PreviewState.STOPPING, **common)
    if status == "REMOVED":
        return PreviewManifest(state=PreviewState.STOPPED, stopped_at=observed_at, **common)

    code = "readiness_failed" if status == "SUCCESS" else "deployment_failed"
    message = (
        "Railway deployed the reviewed SHA, but Furatena did not become ready."
        if code == "readiness_failed"
        else f"Railway deployment ended with status {status or 'unknown'}."
    )
    remediation = (
        "Inspect /readyz and deployment logs, repair the failure, and rebuild the same head SHA."
    )
    return PreviewManifest(
        state=PreviewState.FAILED,
        failure=PreviewFailure(
            disposition=(
                PreviewFailureDisposition.AUTHORIZATION
                if status == "NEEDS_APPROVAL"
                else PreviewFailureDisposition.RETRYABLE
                if status in _FAILED or status == "SUCCESS"
                else PreviewFailureDisposition.TERMINAL
            ),
            code=code,
            safe_message=message,
            remediation=remediation,
        ),
        checks=(
            PreviewCheck(
                check_id="readiness" if code == "readiness_failed" else "deployment",
                status=PreviewCheckStatus.FAIL,
                summary=message,
                observed_at=observed_at,
                remediation=remediation,
            ),
        ),
        **common,
    )


def runtime_railway_preview_manifest(
    docs: Any,
    operational: Mapping[str, object],
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> PreviewManifest | None:
    """Return self-reported evidence from a running Railway PR deployment."""

    values = os.environ if environ is None else environ
    preview = PreviewEnvironment.from_environment(values)
    if preview is None:
        return None
    observed = (now or datetime.now(UTC)).replace(microsecond=0)
    observed_at = observed.isoformat().replace("+00:00", "Z")
    repository_id, repository_url = _repository_identity(preview.review_url)
    identity = deployed_build_identity(docs.catalog)
    fingerprint = sha256_digest(str(identity["freeze_fingerprint"]).encode("utf-8"))
    readiness = operational.get("readiness")
    if not isinstance(readiness, Mapping):
        raise ValueError("operational status lacks readiness evidence")
    artifacts = operational.get("artifacts")
    freeze = artifacts.get("freeze") if isinstance(artifacts, Mapping) else None
    frozen_at = str(
        (freeze.get("generated_at") if isinstance(freeze, Mapping) else None)
        or values.get("FURA_PREVIEW_FROZEN_AT")
        or observed_at
    )
    source = PreviewSource(
        repository_id=repository_id,
        repository_url=repository_url,
        pull_request_number=preview.pull_request_number,
        head_ref=values.get("RAILWAY_GIT_BRANCH", "").strip()
        or f"pr/{preview.pull_request_number}",
        head_sha=preview.head_sha,
        review_url=preview.review_url,
    )
    observation = RailwayPreviewObservation(
        project_id=_required(values, "RAILWAY_PROJECT_ID"),
        environment_id=_required(values, "RAILWAY_ENVIRONMENT_ID"),
        deployment_id=_required(values, "RAILWAY_DEPLOYMENT_ID"),
        deployment_status="SUCCESS",
        deployment_sha=str(identity["git_sha"]),
        origin=preview.origin,
        observed_at=observed_at,
        readiness_ok=bool(readiness.get("ok")),
        artifact_fingerprint=fingerprint,
        frozen_at=frozen_at,
    )
    return railway_preview_manifest(
        source,
        observation,
        expires_at=(observed + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
    )


def _access_policy() -> PreviewAccessPolicy:
    return PreviewAccessPolicy(
        authentication_required=True,
        authentication_scheme="basic-or-bearer-token",
        untrusted_forks=PreviewTrustPolicy.DENY,
        bot_pull_requests=PreviewTrustPolicy.DENY,
    )


def _surfaces(origin: str) -> PreviewSurfaces:
    root = origin.rstrip("/")
    return PreviewSurfaces(
        human_url=f"{root}/",
        markdown_url_template=f"{root}/{{path}}.md",
        llms_url=f"{root}/llms.txt",
        catalog_url=f"{root}/catalog.json",
        query_url=f"{root}/catalog/query.json",
        search_url=f"{root}/search.json",
        metadata_url=f"{root}/meta.json",
        health_url=f"{root}/healthz",
        readiness_url=f"{root}/readyz",
        extensions={"preview_manifest_url": f"{root}/preview-manifest.json"},
    )


def _failed_sha_check(
    source: PreviewSource,
    observation: RailwayPreviewObservation,
) -> PreviewCheck:
    return PreviewCheck(
        check_id="immutable-head-sha",
        status=PreviewCheckStatus.FAIL,
        summary=(
            f"Observed {observation.deployment_sha or 'no deployment SHA'}; "
            f"expected {source.head_sha}."
        ),
        observed_at=observation.observed_at,
        remediation="Discard the superseded deployment and wait for the current head SHA.",
    )


def _repository_identity(review_url: str) -> tuple[str, str]:
    parsed = urlsplit(review_url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname != "github.com" or len(parts) < 4 or parts[2] != "pull":
        raise ValueError("FURA_PREVIEW_REVIEW_URL must identify a GitHub pull request")
    owner, repository = parts[:2]
    return f"github:{owner}/{repository}", f"https://github.com/{owner}/{repository}"


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for a Railway pull-request preview")
    return value
