"""Local CI commands remain in parity with documented lane names."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
LANES = ("fast", "contract", "export", "browser", "agent", "release")


def test_makefile_exposes_documented_ci_lanes() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    docs = (REPO / "docs" / "CI.md").read_text(encoding="utf-8")

    for lane in LANES:
        target = f"ci-{lane}"
        assert f"{target}:" in makefile
        assert f"make {target}" in docs


def test_ci_lanes_use_shared_project_commands() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert "$(UV_RUN) ruff check src tests app" in makefile
    assert "$(UV_RUN) fura check" in makefile
    assert "$(MAKE) pages-build" in makefile
    assert "-m browser tests/test_author_sse_browser.py" in makefile
    assert "$(UV_RUN) fura check --agent-only --json" in makefile
    assert "uv build" in makefile


def test_github_actions_uses_named_make_lanes_and_scoped_caches() -> None:
    workflow_path = REPO / ".github" / "workflows" / "pages.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]

    assert set(jobs) == {*LANES, "deploy"}
    for lane in LANES:
        job = jobs[lane]
        commands = [step.get("run") for step in job["steps"] if "run" in step]
        assert f"make ci-{lane}" in commands
        setup = next(
            step
            for step in job["steps"]
            if step.get("uses") == "astral-sh/setup-uv@v8.2.0"
        )
        assert setup["with"]["cache-suffix"] == "${{ github.job }}"

    for lane in ("export", "browser", "agent", "release"):
        assert jobs[lane]["if"] == "github.event_name != 'pull_request'"
    assert "if" not in jobs["fast"]
    assert "if" not in jobs["contract"]
    assert set(jobs["deploy"]["needs"]) == set(LANES)

    export_uses = {step.get("uses") for step in jobs["export"]["steps"]}
    release_uses = {step.get("uses") for step in jobs["release"]["steps"]}
    assert "actions/upload-pages-artifact@v3" in export_uses
    assert "actions/upload-artifact@v4" in release_uses
