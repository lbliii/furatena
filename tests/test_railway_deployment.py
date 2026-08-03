"""Railway deployment must preserve the frozen, free-threaded production shape."""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import pytest

from furatena.catalog.docs_app import _server_keep_alive_timeout, _server_workers

REPO = Path(__file__).resolve().parents[1]


def test_railway_service_is_single_replica_with_readiness_probe() -> None:
    config = tomllib.loads((REPO / "railway.toml").read_text(encoding="utf-8"))

    assert config["build"] == {
        "builder": "dockerfile",
        "dockerfilePath": "Dockerfile",
    }
    assert config["deploy"]["numReplicas"] == 1
    assert config["deploy"]["healthcheckPath"] == "/readyz"
    assert config["deploy"]["startCommand"] == "/app/scripts/railway-start.sh"
    assert config["deploy"]["overlapSeconds"] == 5
    assert config["deploy"]["drainingSeconds"] == 15


def test_container_installs_and_enforces_free_threaded_python() -> None:
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    start = (REPO / "scripts" / "railway-start.sh").read_text(encoding="utf-8")

    assert "uv python install 3.14t" in dockerfile
    assert "rm -f /usr/local/bin/uv" in dockerfile
    assert "python:3.14-slim@sha256:" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.10.8@sha256:" in dockerfile
    assert "PYTHON_GIL=0" in dockerfile
    assert "sys._is_gil_enabled()" in dockerfile
    assert "sys._is_gil_enabled()" in start
    assert "freeze --full --workers 1" in dockerfile
    assert "ARG FURA_BUILD_GIT_SHA=$RAILWAY_GIT_COMMIT_SHA" in dockerfile
    assert "FURA_DISTRIBUTION=private-image" in dockerfile
    assert "FURA_SERVER_WORKERS=1" in dockerfile
    assert "ca-certificates git" in dockerfile
    assert "--preview" in start
    assert "--workers 1" in start
    assert "fura content reconcile" in start
    assert "FURA_FROZEN_DIR" in start


def test_exact_digest_smoke_proves_sanitized_content_failures() -> None:
    workflow = (REPO / ".github/workflows/private-image.yml").read_text(encoding="utf-8")
    verifier = (REPO / "scripts/verify-content-diagnostics.sh").read_text(encoding="utf-8")

    smoke_job = workflow.split("  smoke:\n", 1)[1].split("\n  lifecycle:\n", 1)[0]
    assert "actions/checkout@" in smoke_job
    assert 'scripts/verify-content-diagnostics.sh "$SUBJECT"' in smoke_job
    assert "private-image-diagnostics-${{ github.sha }}" in smoke_job
    for required in (
        "read-only-state",
        "missing-subdirectory",
        "quota-exhaustion",
        "credential-rejection",
        "Traceback (most recent call last)",
        "failed_startup_exit_nonzero",
    ):
        assert required in verifier


def test_pull_request_image_conformance_does_not_publish_to_ghcr() -> None:
    workflow = (REPO / ".github/workflows/private-image.yml").read_text(encoding="utf-8")
    candidate = workflow.split("  candidate:\n", 1)[1].split("\n  smoke:\n", 1)[0]
    smoke = workflow.split("  smoke:\n", 1)[1].split("\n  lifecycle:\n", 1)[0]

    assert "push: ${{ github.event_name != 'pull_request' }}" in candidate
    assert "load: ${{ github.event_name == 'pull_request' }}" in candidate
    assert "Verify the pull-request runtime without publishing" in candidate
    assert "Prove pull-request managed-content diagnostics" in candidate
    assert "if: github.event_name != 'pull_request'" in smoke


def test_railway_uses_pounce_0_9_2_without_keep_alive_workaround() -> None:
    start = (REPO / "scripts" / "railway-start.sh").read_text(encoding="utf-8")

    assert version("bengal-pounce") == "0.9.2"
    assert "FURA_KEEP_ALIVE_TIMEOUT" not in start


def test_keep_alive_timeout_reaches_chirp_app_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FURA_KEEP_ALIVE_TIMEOUT", "75")
    assert _server_keep_alive_timeout() == 75.0


def test_private_image_defaults_to_one_serving_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FURA_DISTRIBUTION", "private-image")
    monkeypatch.delenv("FURA_SERVER_WORKERS", raising=False)
    assert _server_workers() == 1


def test_local_runtime_keeps_automatic_serving_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FURA_DISTRIBUTION", raising=False)
    monkeypatch.delenv("FURA_SERVER_WORKERS", raising=False)
    assert _server_workers() == 0


def test_serving_worker_override_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FURA_SERVER_WORKERS", "2")
    assert _server_workers() == 2


@pytest.mark.parametrize("value", ("0", "-1", "not-a-number"))
def test_serving_worker_override_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("FURA_SERVER_WORKERS", value)
    with pytest.raises(ValueError, match="FURA_SERVER_WORKERS must be a positive integer"):
        _server_workers()


@pytest.mark.parametrize("value", ("0", "-1", "not-a-number"))
def test_keep_alive_timeout_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("FURA_KEEP_ALIVE_TIMEOUT", value)
    with pytest.raises(ValueError, match="FURA_KEEP_ALIVE_TIMEOUT"):
        _server_keep_alive_timeout()


def test_railway_runbook_checks_bulk_artifact_integrity() -> None:
    runbook = (REPO / "docs" / "RAILWAY.md").read_text(encoding="utf-8")
    verifier = (REPO / "scripts" / "verify-live-artifacts.py").read_text(encoding="utf-8")

    assert 'python scripts/verify-live-artifacts.py "$ORIGIN"' in runbook
    for path in (
        "/catalog.json",
        "/catalog/query.json",
        "/search.json",
        "/semantic.json",
        "/llms-full.txt",
    ):
        assert path in verifier
