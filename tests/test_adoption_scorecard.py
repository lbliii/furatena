"""Versioned adoption-readiness scorecard contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from furatena.catalog.adoption_scorecard import build_adoption_scorecard
from furatena.catalog.benchmarks import assert_free_threading
from furatena.cli.main import run_command

REPO = Path(__file__).resolve().parents[1]


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "decision_date": "2026-07-07",
        "evidence": {
            "activation": {
                "owner": "docs-product",
                "remediation": "Run more opted-in activation sessions.",
                "new_site_first_edit_median_seconds": 540,
                "imported_site_first_edit_median_seconds": 1500,
                "first_publish_sample_count": 3,
                "clean_migration_sample_count": 2,
            },
            "migration": {
                "owner": "platform-docs",
                "remediation": "Repair blocking migrations and assign the remainder.",
                "clean_page_ratio": 0.92,
                "blockers_grouped_by_owner_source": True,
            },
            "build": {
                "owner": "release-engineering",
                "remediation": "Profile and reduce the slow build stage.",
                "check_p95_seconds": 250,
                "static_export_p95_seconds": 500,
                "pdf_batch_documented": True,
            },
            "retrieval": {
                "owner": "search-platform",
                "remediation": "Clean metadata and repair access-boundary regressions.",
                "recall_at_3": 0.82,
                "private_leaks": 0,
            },
            "agent": {
                "owner": "agent-platform",
                "remediation": "Repair failed evals and attach warning remediations.",
                "error_count": 0,
                "private_leaks": 0,
                "warnings_with_remediation": True,
            },
            "buyer_confidence": {
                "owner": "product",
                "remediation": "Choose a supported profile and name approval blockers.",
                "deployment_profile": "static-pages",
                "approval_blockers_named": True,
            },
        },
    }


def test_passing_scorecard_uses_fixed_documented_gates() -> None:
    report = build_adoption_scorecard(_manifest())

    assert report["decision"] == "go"
    assert report["ok"] is True
    assert report["gate_count"] == 16
    assert report["passed_gate_count"] == 16
    assert report["unmet_gates"] == []
    assert report["policy"] == {
        "id": "furatena-beta-adoption-readiness",
        "version": "1.0.0",
        "definitions": "docs/concepts/adoption-research#success-metrics",
    }


def test_published_beta_scorecard_matches_its_versioned_evidence() -> None:
    evidence = json.loads((REPO / "docs/adoption-evidence-v1.json").read_text(encoding="utf-8"))
    published = json.loads(
        (REPO / "docs/adoption-scorecard-v1.json").read_text(encoding="utf-8")
    )

    assert build_adoption_scorecard(evidence) == published
    assert published["decision"] == "no-go"


def test_no_go_lists_gate_owner_remediation_and_decision_date() -> None:
    manifest = _manifest()
    evidence = manifest["evidence"]
    assert isinstance(evidence, dict)
    activation = evidence["activation"]
    retrieval = evidence["retrieval"]
    assert isinstance(activation, dict)
    assert isinstance(retrieval, dict)
    activation["new_site_first_edit_median_seconds"] = 601
    retrieval["private_leaks"] = 1

    report = build_adoption_scorecard(manifest)

    assert report["decision"] == "no-go"
    assert report["decision_date"] == "2026-07-07"
    assert report["unmet_gate_count"] == 2
    assert report["unmet_gates"] == [
        {
            "gate_id": "activation.new_site_first_edit",
            "owner": "docs-product",
            "remediation": "Run more opted-in activation sessions.",
            "observed": 601,
            "target": {"operator": "<=", "value": 600.0},
        },
        {
            "gate_id": "retrieval.private_leaks",
            "owner": "search-platform",
            "remediation": "Clean metadata and repair access-boundary regressions.",
            "observed": 1,
            "target": {"operator": "<=", "value": 0.0},
        },
    ]


def test_unavailable_evidence_is_explicitly_no_go() -> None:
    manifest = _manifest()
    evidence = manifest["evidence"]
    assert isinstance(evidence, dict)
    activation = evidence["activation"]
    assert isinstance(activation, dict)
    activation["first_publish_sample_count"] = None

    report = build_adoption_scorecard(manifest)

    assert report["decision"] == "no-go"
    assert report["unmet_gates"][0]["observed"] is None


def test_manifest_is_strict_versioned_and_range_checked() -> None:
    manifest = _manifest()
    manifest["unexpected"] = True
    with pytest.raises(ValueError, match=r"extra=\['unexpected'\]"):
        build_adoption_scorecard(manifest)

    manifest = _manifest()
    evidence = manifest["evidence"]
    assert isinstance(evidence, dict)
    retrieval = evidence["retrieval"]
    assert isinstance(retrieval, dict)
    retrieval["recall_at_3"] = 1.1
    with pytest.raises(ValueError, match="between 0 and 1"):
        build_adoption_scorecard(manifest)


def test_scorecard_is_deterministic_and_free_thread_safe() -> None:
    assert_free_threading()

    with ThreadPoolExecutor(max_workers=16) as executor:
        reports = list(executor.map(lambda _: build_adoption_scorecard(_manifest()), range(64)))

    assert {report["evidence_sha256"] for report in reports} == {reports[0]["evidence_sha256"]}
    assert {report["decision"] for report in reports} == {"go"}


def test_cli_writes_no_go_scorecard_with_standard_diagnostics(tmp_path: Path) -> None:
    manifest = _manifest()
    evidence = manifest["evidence"]
    assert isinstance(evidence, dict)
    build = evidence["build"]
    assert isinstance(build, dict)
    build["check_p95_seconds"] = 300
    source = tmp_path / "evidence.json"
    output = tmp_path / "scorecard.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")

    result = run_command(
        ["scorecard", "--input", str(source), "--output", str(output), "--json"]
    )

    assert result is not None
    assert result.command == "scorecard"
    assert result.ok is False
    assert result.exit_code == 2
    assert result.diagnostics[0].rule_id == "fura.scorecard.build.check_p95"
    assert result.data["decision"] == "no-go"
    assert json.loads(output.read_text(encoding="utf-8"))["decision"] == "no-go"
