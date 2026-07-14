"""GitHub preview reporting is writable, idempotent, and fork-safe."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_preview_reporting_workflow_uses_trusted_default_branch_code() -> None:
    path = REPO / ".github" / "workflows" / "preview-report.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)

    assert set(triggers) == {"pull_request_target", "repository_dispatch", "workflow_dispatch"}
    assert workflow["permissions"] == {
        "contents": "read",
        "checks": "write",
        "pull-requests": "write",
    }
    report = workflow["jobs"]["report"]
    assert "head.repo.full_name == github.repository" in report["if"]
    assert "user.type != 'Bot'" in report["if"]
    checkout = report["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"


def test_reporter_upserts_one_check_and_marker_comment() -> None:
    script = (REPO / "scripts" / "preview_report.py").read_text(encoding="utf-8")

    assert 'CHECK_NAME = "Furatena preview conformance"' in script
    assert 'COMMENT_MARKER = "<!-- furatena-preview -->"' in script
    assert "api.patch(f\"/check-runs/{existing['id']}\"" in script
    assert "api.patch(f\"/issues/comments/{existing_comment['id']}\"" in script
    assert "FURA_PREVIEW_AUTH_TOKEN" in script
    assert (
        "token" not in script.partition("def _check_payload")[2].partition("def _check_summary")[0]
    )
