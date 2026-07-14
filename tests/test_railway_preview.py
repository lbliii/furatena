"""Railway lifecycle translation and runtime preview evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from furatena.catalog.preview_contracts import (
    PreviewFailureDisposition,
    PreviewManifest,
    PreviewState,
)
from furatena.catalog.railway_preview import (
    RailwayPreviewObservation,
    railway_preview_manifest,
    runtime_railway_preview_manifest,
)
from tests.preview_support import HEAD_SHA, sample_source

OBSERVED = "2030-01-01T00:04:00Z"
EXPIRES = "2030-01-08T00:00:00Z"


def _observation(**changes: object) -> RailwayPreviewObservation:
    values: dict[str, object] = {
        "project_id": "project-1",
        "environment_id": "environment-pr-424",
        "deployment_id": "deployment-a",
        "deployment_status": "BUILDING",
        "deployment_sha": HEAD_SHA,
        "origin": "https://furatena-pr-424.example.test",
        "observed_at": OBSERVED,
    }
    values.update(changes)
    return RailwayPreviewObservation(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("status", ["QUEUED", "BUILDING", "DEPLOYING"])
def test_active_railway_states_map_to_building(status: str) -> None:
    manifest = railway_preview_manifest(
        sample_source(), _observation(deployment_status=status), expires_at=EXPIRES
    )

    assert manifest.state == PreviewState.BUILDING
    assert manifest.checks[0].status.value == "pending"
    assert manifest.extensions["railway_deployment_status"] == status


def test_success_requires_sha_artifact_and_readiness_evidence() -> None:
    manifest = railway_preview_manifest(
        sample_source(),
        _observation(
            deployment_status="SUCCESS",
            readiness_ok=True,
            artifact_fingerprint="sha256:" + "b" * 64,
            frozen_at="2030-01-01T00:02:00Z",
        ),
        expires_at=EXPIRES,
    )

    assert manifest.state == PreviewState.READY
    assert manifest.artifact is not None
    assert manifest.artifact.source_sha == HEAD_SHA
    assert manifest.provider.environment_id == "environment-pr-424"
    assert manifest.surfaces is not None
    assert manifest.surfaces.extensions["preview_manifest_url"].endswith("/preview-manifest.json")
    assert PreviewManifest.from_dict(manifest.to_dict()) == manifest


def test_superseded_deployment_fails_closed() -> None:
    manifest = railway_preview_manifest(
        sample_source(),
        _observation(deployment_status="SUCCESS", deployment_sha="c" * 40),
        expires_at=EXPIRES,
    )

    assert manifest.state == PreviewState.FAILED
    assert manifest.failure is not None
    assert manifest.failure.disposition == PreviewFailureDisposition.CONFLICT
    assert manifest.failure.code == "superseded_deployment"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("REMOVING", PreviewState.STOPPING), ("REMOVED", PreviewState.STOPPED)],
)
def test_teardown_states_are_idempotent(status: str, expected: PreviewState) -> None:
    manifest = railway_preview_manifest(
        sample_source(), _observation(deployment_status=status), expires_at=EXPIRES
    )

    assert manifest.state == expected
    assert (manifest.stopped_at is not None) is (expected == PreviewState.STOPPED)


def test_runtime_manifest_binds_railway_and_frozen_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "furatena.catalog.railway_preview.deployed_build_identity",
        lambda catalog: {"git_sha": HEAD_SHA, "freeze_fingerprint": "freeze-424"},
    )
    environ = {
        "FURA_PR_PREVIEW": "1",
        "FURA_PREVIEW_PR_NUMBER": "424",
        "FURA_PREVIEW_SHA": HEAD_SHA,
        "FURA_BUILD_GIT_SHA": HEAD_SHA,
        "FURA_PREVIEW_REVIEW_URL": "https://github.com/lbliii/furatena/pull/441",
        "RAILWAY_PUBLIC_DOMAIN": "furatena-pr-424.example.test",
        "RAILWAY_PROJECT_ID": "project-1",
        "RAILWAY_ENVIRONMENT_ID": "environment-pr-424",
        "RAILWAY_DEPLOYMENT_ID": "deployment-a",
        "RAILWAY_GIT_BRANCH": "codex/issue-424-railway-preview",
    }
    operational = {
        "readiness": {"ok": True},
        "artifacts": {"freeze": {"generated_at": "2030-01-01T00:02:00Z"}},
    }

    manifest = runtime_railway_preview_manifest(
        SimpleNamespace(catalog=object()),
        operational,
        environ=environ,
        now=datetime(2030, 1, 1, 0, 4, tzinfo=UTC),
    )

    assert manifest is not None
    assert manifest.state == PreviewState.READY
    assert manifest.preview_id.startswith("preview-")
    assert manifest.source.repository_id == "github:lbliii/furatena"
    assert manifest.source.pull_request_number == 424
    assert manifest.artifact is not None
    assert manifest.artifact.frozen_at == "2030-01-01T00:02:00Z"


def test_runtime_manifest_is_absent_outside_preview() -> None:
    assert (
        runtime_railway_preview_manifest(
            SimpleNamespace(catalog=object()),
            {},
            environ={},
            now=datetime(2030, 1, 1, tzinfo=UTC),
        )
        is None
    )
