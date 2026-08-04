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
    for lane in ("fast",):
        assert "if" not in workflow["jobs"][lane]
    assert workflow["jobs"]["contract"]["needs"] == "fast"
    assert "if" not in workflow["jobs"]["contract"]


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
