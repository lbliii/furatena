"""Pull-request CI event brakes skip expensive proof on draft revisions."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]

READY_PULL_REQUEST = (
    "github.event_name != 'pull_request' || github.event.pull_request.draft == false"
)


def test_pages_workflow_skips_expensive_lanes_on_draft_pull_requests() -> None:
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    )
    triggers = workflow.get("on") or workflow.get(True)

    assert "converted_to_draft" in triggers["pull_request"]["types"]
    for lane in ("coverage", "browser", "release"):
        assert READY_PULL_REQUEST in workflow["jobs"][lane]["if"]
    for lane in ("fast", "public-safety"):
        assert "if" not in workflow["jobs"][lane]
    assert workflow["jobs"]["contract"]["needs"] == "fast"
    assert workflow["jobs"]["public-safety"]["needs"] == "fast"
    assert "if" not in workflow["jobs"]["contract"]
    public_safety_runs = [
        step["run"]
        for step in workflow["jobs"]["public-safety"]["steps"]
        if isinstance(step.get("run"), str)
    ]
    assert any("make ci-public-safety" in run for run in public_safety_runs)


def test_private_image_workflow_skips_pull_request_proof_on_drafts() -> None:
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "private-image.yml").read_text(encoding="utf-8")
    )
    pull_request = workflow["jobs"]["pull-request"]

    assert "github.event.pull_request.draft == false" in pull_request["if"]
    assert "head.repo.full_name == github.repository" in pull_request["if"]


def test_pdf_proof_workflow_skips_pull_request_proof_on_drafts() -> None:
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "pdf-proof.yml").read_text(encoding="utf-8")
    )

    assert workflow["jobs"]["proof"]["if"] == READY_PULL_REQUEST


def test_preview_reporting_skips_railway_on_drafts_and_removes_on_conversion() -> None:
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "preview-report.yml").read_text(encoding="utf-8")
    )
    triggers = workflow.get("on") or workflow.get(True)

    assert "converted_to_draft" in triggers["pull_request_target"]["types"]
    report = workflow["jobs"]["report"]
    railway = workflow["jobs"]["railway"]

    assert "converted_to_draft" in report["if"]
    assert "github.event.pull_request.draft == false" in report["if"]
    assert "head.repo.full_name == github.repository" in report["if"]
    assert "user.type != 'Bot'" in report["if"]
    assert "converted_to_draft" in railway["if"]
    assert "github.event.pull_request.draft == false" in railway["if"]
    preview_state = report["env"]["PREVIEW_STATE"]
    assert "converted_to_draft" in preview_state
    assert "'removed'" in preview_state


def test_pull_request_workflows_cancel_superseded_runs_by_pr_number() -> None:
    pages = yaml.safe_load(
        (REPO / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    )
    private_image = yaml.safe_load(
        (REPO / ".github" / "workflows" / "private-image.yml").read_text(encoding="utf-8")
    )
    pdf_proof = yaml.safe_load(
        (REPO / ".github" / "workflows" / "pdf-proof.yml").read_text(encoding="utf-8")
    )

    for workflow, prefix in (
        (pages, "pages-pr-"),
        (private_image, "private-image-pr-"),
        (pdf_proof, "pdf-proof-pr-"),
    ):
        concurrency = workflow["concurrency"]
        assert "github.event.pull_request.number" in concurrency["group"]
        assert prefix in concurrency["group"]
        assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"

    lifecycle = private_image["concurrency"]
    assert "private-image-lifecycle" in lifecycle["group"]
    assert "github.sha" in lifecycle["group"]


def test_pages_workflow_classifies_pull_requests_from_exact_diffs() -> None:
    workflow = yaml.safe_load(
        (REPO / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    )
    scope = next(step for step in workflow["jobs"]["fast"]["steps"] if step.get("id") == "scope")

    assert "git diff --name-only --diff-filter=ACMRDT" in scope["run"]
    assert "ready_for_review" not in scope["run"]
    assert "set -euo pipefail" in scope["run"]
    assert "git cat-file -e" in scope["run"]


def test_ci_event_brake_documentation_records_baseline_and_rollback() -> None:
    docs = (REPO / "docs" / "CI.md").read_text(encoding="utf-8")

    assert "## CI event brakes" in docs
    assert "966 workflow runs" in docs
    assert "2,471 unrounded hosted-runner minutes" in docs
    assert "**Rollback:**" in docs
    assert "Convert to draft" in docs
    assert "ready_for_review` uses the same diff" in docs
