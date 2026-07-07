"""Health, readiness, freshness, and artifact-age contracts."""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from chirp.testing import TestClient

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.deployment_manifest import (
    DeploymentArtifact,
    DeploymentManifest,
    write_deployment_manifest,
)
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.operational_status import operational_status
from furatena.catalog.runtime import ServeConfig, ServeMode
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]


def _docs(tmp_path: Path) -> tuple[DocsApp, Path]:
    app_root = tmp_path / "app"
    content_root = tmp_path / "content"
    app_root.mkdir()
    content_root.mkdir()
    copy_app_theme(app_root, REPO / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(app_root / "mounts.yaml", content_root)
    source = content_root / "_index.md"
    source.write_text("---\ntitle: Home\n---\n\n# Home\n", encoding="utf-8")
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    return docs, source


def test_health_and_readiness_are_distinct_http_contracts(tmp_path: Path) -> None:
    docs, _source = _docs(tmp_path)
    client = TestClient(docs.create_app())

    async def fetch(path: str):
        response = await client.get(path)
        return response.status, json.loads(response.text)

    health_status, health = asyncio.run(fetch("/healthz"))
    ready_status, readiness = asyncio.run(fetch("/readyz"))

    assert health_status == 200
    assert health["kind"] == "health"
    assert health["ok"] is True
    assert health["status"] == "healthy"
    assert health["http_status"] == 200
    assert ready_status == 200
    assert readiness["kind"] == "readiness"
    assert readiness["status"] == "ready"
    assert {item["id"] for item in readiness["checks"]} == {
        "source:chirp",
        "index:chirp",
    }

    docs.catalog._source_sync_status["chirp"] = {
        "status": "failed",
        "stage": "source",
        "provider": "filesystem",
    }
    degraded_status, degraded = asyncio.run(fetch("/readyz"))
    health_after_status, health_after = asyncio.run(fetch("/healthz"))

    assert degraded_status == 503
    assert degraded["status"] == "not_ready"
    assert degraded["remediation"] == [
        "Restore source access or repair the provider sync error."
    ]
    assert health_after_status == 200
    assert health_after["status"] == "healthy"


def test_freshness_and_artifact_age_track_source_freeze_export_order(tmp_path: Path) -> None:
    docs, source = _docs(tmp_path)
    frozen = docs.config.root / "frozen"
    public = docs.config.root / "public"
    write_deployment_manifest(
        frozen / "freeze.manifest.json",
        DeploymentManifest(
            target="freeze",
            mode="freeze",
            page_count=1,
            artifacts=(DeploymentArtifact("catalog.json"),),
        ),
    )
    write_deployment_manifest(
        public / "export.manifest.json",
        DeploymentManifest(
            target="static",
            mode="static",
            page_count=1,
            artifacts=(DeploymentArtifact("index.html"),),
        ),
    )
    os.utime(source, (100.0, 100.0))
    os.utime(frozen / "freeze.manifest.json", (200.0, 200.0))
    os.utime(public / "export.manifest.json", (300.0, 300.0))

    current = operational_status(docs, now=400.0)
    assert current["freshness"]["status"] == "fresh"
    assert current["artifacts"]["freeze"]["age_seconds"] == 200.0
    assert current["artifacts"]["export"]["age_seconds"] == 100.0
    assert current["artifacts"]["freeze"]["artifact_count"] == 1

    os.utime(source, (350.0, 350.0))
    stale = operational_status(docs, now=400.0)
    assert stale["freshness"]["status"] == "stale"
    assert stale["artifacts"]["freeze"]["freshness"] == "stale"
    assert stale["freshness"]["remediation"] == [
        "Run `fura freeze` to refresh the catalog artifact."
    ]


def test_operational_status_is_free_threading_safe(tmp_path: Path) -> None:
    assert_free_threading()
    docs, _source = _docs(tmp_path)

    with ThreadPoolExecutor(max_workers=64) as pool:
        reports = list(pool.map(lambda _: operational_status(docs), range(256)))

    assert len(reports) == 256
    assert {report["readiness"]["status"] for report in reports} == {"ready"}
    assert {report["health"]["process"]["pid"] for report in reports} == {os.getpid()}
