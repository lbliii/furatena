"""Dual-channel afdocs score artifact and regression-lane contracts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_afdocs_scores", REPO / "scripts" / "check_afdocs_scores.py"
)
assert SPEC is not None and SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)
EXPECTED_SPLIT = CHECKER.EXPECTED_SPLIT
build_baseline = CHECKER.build_baseline
build_evidence = CHECKER.build_evidence
find_regressions = CHECKER.find_regressions
main = CHECKER.main


def _report(
    url: str,
    *,
    score: int,
    negotiation: str,
    llms: str = "pass",
    timestamp: str = "2026-07-10T18:00:00.000Z",
) -> dict[str, object]:
    statuses = {
        "content-negotiation": negotiation,
        "llms-txt-exists": llms,
    }
    summary = {name: tuple(statuses.values()).count(name) for name in statuses.values()}
    for name in ("pass", "warn", "fail", "skip", "error"):
        summary.setdefault(name, 0)
    summary["total"] = len(statuses)
    return {
        "url": url,
        "timestamp": timestamp,
        "specUrl": "https://agentdocsspec.com/spec/v0.5.0",
        "samplingStrategy": "curated",
        "testedPages": 6,
        "discoverySources": ["curated"],
        "summary": summary,
        "results": [
            {
                "id": check_id,
                "category": "markdown-availability",
                "status": status,
                "message": f"{check_id}: {status}",
                "details": {"pageResults": [{"url": url, "status": status}]},
            }
            for check_id, status in reversed(statuses.items())
        ],
        "scoring": {
            "overall": score,
            "grade": "A" if score >= 90 else "B",
            "categoryScores": {},
            "checkScores": {},
            "diagnostics": [],
            "resolutions": {},
        },
    }


def _evidence(*, static_score: int = 80, live_score: int = 90) -> dict[str, object]:
    return build_evidence(
        _report("https://static.example", score=static_score, negotiation="fail"),
        _report("https://live.example", score=live_score, negotiation="pass"),
        afdocs_version="0.18.7",
    )


def test_baseline_records_scores_checks_and_required_channel_split() -> None:
    baseline = build_baseline(_evidence())

    assert baseline["afdocs_version"] == "0.18.7"
    assert baseline["required_split"] == EXPECTED_SPLIT
    assert baseline["channels"]["static"] == {
        "url": "https://static.example",
        "overall": 80,
        "grade": "B",
        "checks": {"content-negotiation": "fail", "llms-txt-exists": "pass"},
    }
    assert baseline["channels"]["live"]["checks"]["content-negotiation"] == "pass"


def test_committed_baseline_records_the_observed_static_live_gap() -> None:
    baseline = json.loads((REPO / "config" / "afdocs-baseline.json").read_text())

    assert baseline["afdocs_version"] == "0.18.7"
    assert baseline["spec_url"] == "https://agentdocsspec.com/spec/"
    assert baseline["required_split"] == EXPECTED_SPLIT
    assert baseline["channels"]["static"]["overall"] == 96
    assert baseline["channels"]["live"]["overall"] == 98
    assert baseline["channels"]["static"]["checks"]["content-negotiation"] == "fail"
    assert baseline["channels"]["live"]["checks"]["content-negotiation"] == "pass"
    assert all(len(channel["checks"]) == 23 for channel in baseline["channels"].values())


def test_score_and_per_check_regressions_fail_without_blocking_improvements() -> None:
    baseline = build_baseline(_evidence())
    improved = _evidence(static_score=82, live_score=91)
    assert find_regressions(improved, baseline) == []

    regressed = _evidence(static_score=79, live_score=89)
    regressed["channels"]["live"]["results"][0]["status"] = "fail"
    regressions = find_regressions(regressed, baseline)

    assert "static score regressed from 80 to 79" in regressions
    assert "live score regressed from 90 to 89" in regressions
    assert any("live check 'content-negotiation' regressed" in item for item in regressions)
    assert any("expected live content-negotiation to pass" in item for item in regressions)


def test_new_missing_and_version_drift_require_baseline_review() -> None:
    baseline = build_baseline(_evidence())
    current = _evidence()
    current["afdocs_version"] = "0.19.0"
    current["channels"]["static"]["results"].pop()
    current["channels"]["live"]["results"].append(
        {
            "id": "new-check",
            "category": "observability",
            "status": "pass",
            "message": "new",
        }
    )

    regressions = find_regressions(current, baseline)

    assert any("afdocs version changed" in item for item in regressions)
    assert any("disappeared" in item for item in regressions)
    assert any("new; record and review" in item for item in regressions)


def test_cli_records_and_then_enforces_baseline(tmp_path: Path) -> None:
    static = tmp_path / "static.json"
    live = tmp_path / "live.json"
    evidence = tmp_path / "evidence.json"
    regressions = tmp_path / "regressions.json"
    baseline = tmp_path / "baseline.json"
    static.write_text(
        json.dumps(_report("https://static.example", score=80, negotiation="warn")),
        encoding="utf-8",
    )
    live.write_text(
        json.dumps(_report("https://live.example", score=90, negotiation="pass")),
        encoding="utf-8",
    )
    common = [
        "--static",
        str(static),
        "--live",
        str(live),
        "--afdocs-version",
        "0.18.7",
        "--output",
        str(evidence),
        "--regressions",
        str(regressions),
    ]

    assert main([*common, "--record-baseline", str(baseline)]) == 0
    assert json.loads(regressions.read_text())["ok"] is True
    assert main([*common, "--baseline", str(baseline)]) == 0


def test_workflow_runs_after_deploy_and_on_schedule_with_retained_json() -> None:
    path = REPO / ".github" / "workflows" / "agent-score.yml"
    source = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    score = workflow["jobs"]["score"]
    steps = {step.get("name", step.get("uses")): step for step in score["steps"]}

    assert 'cron: "17 6 * * 1"' in source
    assert "pull_request:" in source
    assert "workflow_dispatch:" in source
    assert "workflow_run:" in source
    assert 'workflows: ["Validate and deploy Furatena"]' in source
    assert workflow["env"]["AFDOCS_VERSION"] == "0.18.7"
    assert workflow["env"]["STATIC_ORIGIN"] == "https://lbliii.github.io/furatena"
    assert workflow["env"]["LIVE_ORIGIN"] == "https://furatena-production.up.railway.app"
    assert score["timeout-minutes"] == 20
    assert "afdocs@${AFDOCS_VERSION}" in steps["Run pinned afdocs checks"]["run"]
    for flag in ("--format json", "--score", "--fixes", "--verbose"):
        assert flag in steps["Run pinned afdocs checks"]["run"]
    assert (
        "config/afdocs-baseline.json"
        in steps["Normalize evidence and enforce regression baseline"]["run"]
    )
    upload = steps["Upload score evidence"]
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v7"
    assert upload["with"]["path"] == "agent-score-results/*.json"
    assert upload["with"]["retention-days"] == 90
