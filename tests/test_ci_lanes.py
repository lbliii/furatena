"""Local CI commands remain in parity with documented lane names."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
LANES = ("fast", "contract", "coverage", "export", "browser", "agent", "release")


def test_makefile_exposes_documented_ci_lanes() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    docs = (REPO / "docs" / "CI.md").read_text(encoding="utf-8")

    for lane in LANES:
        target = f"ci-{lane}"
        assert f"{target}:" in makefile
        assert f"make {target}" in docs


def test_ci_lanes_use_shared_project_commands() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert "FREE_THREADED = env PYTHON_GIL=0" in makefile
    assert "$(UV_RUN) ruff check src tests app" in makefile
    assert "$(UV_RUN) fura check" in makefile
    assert "$(COVERAGE) run --branch" in makefile
    assert 'FURA_TEST_FROZEN_DIR="$$(mktemp -d)/frozen"' in makefile
    assert "scripts/check_core_coverage.py" in makefile
    assert "$(MAKE) pages-build" in makefile
    assert '$(PYTEST) -m "browser and browser_smoke" $(BROWSER_TESTS)' in makefile
    assert '$(PYTEST) -m "browser and browser_authoring" $(BROWSER_TESTS)' in makefile
    assert '$(PYTEST) -m "browser and browser_responsive" $(BROWSER_TESTS)' in makefile
    assert '$(PYTEST) -m "browser and browser_full" $(BROWSER_TESTS)' in makefile
    assert "$(UV_RUN) fura check --agent-only --json" in makefile
    assert "uv build" in makefile
    assert "env -u FURA_BASE_URL -u FURA_BASE_PATH -u FURA_WORKERS $(PYTEST)" in makefile
    assert "FURA_BASE_URL=https://lbliii.github.io/furatena" in makefile
    assert "FURA_BASE_PATH=/furatena" in makefile
    assert "FURA_WORKERS=8" in makefile
    assert "python -m furatena.catalog.artifact_audit app/public" in makefile


def test_github_actions_uses_named_make_lanes_and_scoped_caches() -> None:
    workflow_path = REPO / ".github" / "workflows" / "pages.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert workflow["env"]["PYTHON_GIL"] == "0"
    assert set(jobs) == {*LANES, "deploy"}
    for lane in LANES:
        job = jobs[lane]
        commands = [step.get("run") for step in job["steps"] if "run" in step]
        assert f"make ci-{lane}" in commands
        setup = next(
            step for step in job["steps"] if step.get("uses") == "astral-sh/setup-uv@v8.2.0"
        )
        assert setup["with"]["cache-suffix"] == "${{ github.job }}"
        assert any(step.get("uses") == "actions/checkout@v7.0.0" for step in job["steps"])

    for lane in ("export", "browser", "agent", "release"):
        assert jobs[lane]["if"] == "github.event_name != 'pull_request'"
    assert "if" not in jobs["fast"]
    assert "if" not in jobs["contract"]
    assert set(jobs["deploy"]["needs"]) == set(LANES)
    export_lane = next(
        step for step in jobs["export"]["steps"] if step.get("name") == "Export lane"
    )
    assert "env" not in export_lane

    export_uses = {step.get("uses") for step in jobs["export"]["steps"]}
    release_uses = {step.get("uses") for step in jobs["release"]["steps"]}
    assert "actions/upload-pages-artifact@v3" in export_uses
    assert "actions/upload-artifact@v4" in release_uses
