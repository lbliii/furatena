"""Trusted publishing, integrity metadata, and release recovery contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_release.py"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _run_prepare(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (sys.executable, str(SCRIPT), *arguments),
        cwd=ROOT,
        env={**os.environ, "PYTHON_GIL": "0"},
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_release_metadata_is_deterministic_and_free_threaded(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    output = tmp_path / "release"
    dist.mkdir()
    wheel = dist / "furatena-0.1.1-py3-none-any.whl"
    sdist = dist / "furatena-0.1.1.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")

    completed = _run_prepare(
        "--tag",
        "v0.1.1",
        "--commit",
        "a" * 40,
        "--dist-dir",
        str(dist),
        "--output-dir",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    manifest = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["tag"] == "v0.1.1"
    assert manifest["commit"] == "a" * 40
    assert manifest["runtime"] == {
        "PYTHON_GIL": "0",
        "abi": "free-threaded",
        "gil_enabled": False,
        "implementation": "CPython",
        "version": "3.14",
    }
    assert [item["filename"] for item in manifest["artifacts"]] == [wheel.name, sdist.name]
    checksums = (output / "SHA256SUMS").read_text(encoding="utf-8")
    assert f"  {wheel.name}\n" in checksums
    assert f"  {sdist.name}\n" in checksums


def test_release_tag_must_exactly_match_project_version() -> None:
    completed = _run_prepare("--tag", "v0.2.0", "--validate-only")

    assert completed.returncode != 0
    assert "does not match project version" in completed.stderr


def test_release_workflow_separates_build_from_oidc_publish_identity() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    jobs = workflow["jobs"]

    assert workflow["env"]["PYTHON_GIL"] == "0"
    assert set(jobs) == {"validate", "build", "attest", "publish-pypi", "github-release"}
    assert jobs["validate"]["permissions"] == {"contents": "read"}
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["attest"]["permissions"]["id-token"] == "write"
    assert jobs["publish-pypi"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert jobs["publish-pypi"]["environment"]["name"] == "pypi"
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert "PYPI_TOKEN" not in source
    assert "actions/attest@v4" in source
    assert "pypa/gh-action-pypi-publish@release/v1" in source
    assert "actions/upload-artifact@v7" in source
    assert "actions/download-artifact@v8" in source
    assert "towncrier build --draft" in source
    assert "--notes-file" in source
    assert "--verify-tag" in source
    assert "--fail-on-no-commits" in source


def test_private_repository_skips_only_unavailable_github_provenance_storage() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "if: ${{ !github.event.repository.private }}" in source
    assert "if: ${{ github.event.repository.private }}" in source
    assert "user-owned private repository" in source
    assert "PyPI Trusted Publishing attestations remain release requirements" in source


def test_release_runbook_covers_yank_rollback_and_compromise() -> None:
    runbook = (ROOT / "docs" / "RELEASING.md").read_text(encoding="utf-8").lower()

    for required in (
        "trusted publisher",
        "sha256sums",
        "pypi-attestations verify pypi",
        "user-owned private repositories",
        "yank and rollback",
        "compromised-release response",
        "do not delete",
        "never reuse",
    ):
        assert required in runbook
