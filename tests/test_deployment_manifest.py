"""Shared deployment manifest schema and compatibility readers."""

from __future__ import annotations

import json
from pathlib import Path

from furatena.catalog.deployment_manifest import (
    DEPLOYMENT_MANIFEST_SCHEMA_VERSION,
    DeploymentArtifact,
    DeploymentManifest,
    collect_deployment_artifacts,
    read_deployment_manifest,
    write_deployment_manifest,
)
from furatena.catalog.renderer_fingerprint import read_renderer_fingerprint


def test_deployment_manifest_round_trips_common_envelope(tmp_path: Path) -> None:
    artifact = tmp_path / "catalog.json"
    artifact.write_text('{"schema_version": 3}\n', encoding="utf-8")
    manifest = DeploymentManifest(
        target="static",
        mode="static",
        page_count=12,
        artifacts=collect_deployment_artifacts(tmp_path, ("catalog.json", "channels.json")),
        fingerprints={"renderer": "renderer-v3", "routes": {"/": "home-v3"}},
        sync={"incremental": True, "skipped_count": 4},
        extensions={"paths": ["catalog.json", "channels.json"]},
    )
    path = tmp_path / "export.manifest.json"

    write_deployment_manifest(path, manifest)
    loaded = read_deployment_manifest(path)

    assert loaded == manifest
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == DEPLOYMENT_MANIFEST_SCHEMA_VERSION
    assert payload["manifest_type"] == "furatena.deployment"
    assert loaded is not None
    assert loaded.route_fingerprints == {"/": "home-v3"}
    assert loaded.renderer_fingerprint == "renderer-v3"
    assert loaded.artifact_paths == ("catalog.json", "channels.json")
    assert loaded.artifacts[0].bytes == artifact.stat().st_size
    assert loaded.artifacts[1].bytes is None


def test_deployment_manifest_normalizes_legacy_static_and_freeze_payloads(
    tmp_path: Path,
) -> None:
    static_path = tmp_path / "export.manifest.json"
    static_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "page_count": 3,
                "paths": ["index.html"],
                "fingerprints": {"/": "legacy-home"},
                "incremental": True,
                "skipped_count": 2,
            }
        ),
        encoding="utf-8",
    )
    freeze_path = tmp_path / "freeze.manifest.json"
    freeze_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "page_count": 3,
                "dirty_mounts": ["docs"],
                "renderer": {"fingerprint": "legacy-renderer", "changed": True},
            }
        ),
        encoding="utf-8",
    )

    static = read_deployment_manifest(static_path, target_hint="static")
    freeze = read_deployment_manifest(freeze_path, target_hint="freeze")

    assert static is not None and freeze is not None
    assert static.artifact_paths == ("index.html",)
    assert static.route_fingerprints == {"/": "legacy-home"}
    assert static.sync == {"incremental": True, "skipped_count": 2}
    assert freeze.renderer_fingerprint == "legacy-renderer"
    assert freeze.sync["dirty_mounts"] == ["docs"]


def test_hybrid_renderer_reader_consumes_shared_freeze_manifest(tmp_path: Path) -> None:
    write_deployment_manifest(
        tmp_path / "freeze.manifest.json",
        DeploymentManifest(
            target="freeze",
            mode="freeze",
            page_count=1,
            artifacts=(DeploymentArtifact(path="catalog.json"),),
            fingerprints={"renderer": "manifest-renderer"},
            sync={"mount_status": [{"mount": "docs", "status": "frozen"}]},
        ),
    )

    assert read_renderer_fingerprint(tmp_path) == "manifest-renderer"


def test_hybrid_renderer_reader_rejects_failed_freeze_manifest(tmp_path: Path) -> None:
    write_deployment_manifest(
        tmp_path / "freeze.manifest.json",
        DeploymentManifest(
            target="freeze",
            mode="freeze",
            page_count=0,
            fingerprints={"renderer": "uncommitted-renderer"},
            sync={"mount_status": [{"mount": "docs", "status": "failed"}]},
        ),
    )

    assert read_renderer_fingerprint(tmp_path) is None
