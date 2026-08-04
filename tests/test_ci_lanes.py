"""Local CI commands remain in parity with documented lane names."""

import tomllib
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
    assert "format-check:" in makefile
    assert "$(UV_RUN) ruff format --check ." in makefile
    assert "ci-fast: format-check" in makefile
    assert "$(UV_RUN) ruff check src tests app" in makefile
    assert "$(UV_RUN) fura check" in makefile
    assert "$(COVERAGE) run --branch" in makefile
    assert 'FURA_TEST_FROZEN_DIR="$$(mktemp -d)/frozen"' in makefile
    assert "scripts/check_core_coverage.py" in makefile
    assert "$(MAKE) pages-build" in makefile
    assert "--junitxml=$(BROWSER_RESULTS)/smoke.xml" in makefile
    assert '-m "browser and browser_smoke" $(BROWSER_TESTS)' in makefile
    assert "--junitxml=$(BROWSER_RESULTS)/authoring.xml" in makefile
    assert '-m "browser and browser_authoring" $(BROWSER_TESTS)' in makefile
    assert "--junitxml=$(BROWSER_RESULTS)/responsive.xml" in makefile
    assert '-m "browser and browser_responsive" $(BROWSER_TESTS)' in makefile
    assert "--junitxml=$(BROWSER_RESULTS)/full.xml" in makefile
    assert '-m "browser and browser_full" $(BROWSER_TESTS)' in makefile
    assert "--junitxml=$(BROWSER_RESULTS)/htmx4-preview.xml" in makefile
    assert '-m "browser and browser_htmx4" $(BROWSER_TESTS)' in makefile
    assert "$(UV_RUN) fura check --agent-only --json" in makefile
    assert "fura docs-reference" in makefile
    assert "generated-cli-config.md --check" in makefile
    assert "$(UV_RUN) fura docs-quality" in makefile
    assert "tests/test_docs_quality.py" in makefile
    assert "tests/test_integrator_operations_reference.py" in makefile
    assert "tests/test_domain_errors.py" in makefile
    assert "$(UV_RUN) ty check" in makefile
    assert "tests/test_record_types.py" in makefile
    assert "tests/test_search_hot_paths.py" in makefile
    assert "tests/test_chirp_docs_incremental.py" in makefile
    assert "src/furatena/catalog/preview_contracts.py" in makefile
    assert "src/furatena/catalog/preview_security.py" in makefile
    assert "src/furatena/catalog/railway_preview.py" in makefile
    assert "src/furatena/catalog/railway_preview_controller.py" in makefile
    assert "src/furatena/catalog/preview_conformance.py" in makefile
    assert "src/furatena/catalog/federation_publish.py" in makefile
    assert "src/furatena/catalog/federation_s3.py" in makefile
    assert "src/furatena/cli/commands/publish_shard.py" in makefile
    assert "tests/test_preview_contracts.py" in makefile
    assert "tests/test_preview_schemas.py" in makefile
    assert "tests/test_preview_fixtures.py" in makefile
    assert "tests/test_preview_security.py" in makefile
    assert "tests/test_railway_preview.py" in makefile
    assert "tests/test_railway_preview_controller.py" in makefile
    assert "tests/test_preview_conformance.py" in makefile
    assert "tests/test_preview_reporting_workflow.py" in makefile
    assert "tests/test_federation_artifacts.py" in makefile
    assert "tests/test_federation_publish.py" in makefile
    assert "tests/test_federation_s3.py" in makefile
    assert "tests/test_cli_publish_shard.py" in makefile
    assert "uv build --clear --no-sources" in makefile
    assert "scripts/check_distributions.py --dist-dir dist" in makefile
    assert "env -u FURA_BASE_URL -u FURA_BASE_PATH -u FURA_WORKERS $(PYTEST)" in makefile
    assert "FURA_BASE_URL=https://lbliii.github.io/furatena" in makefile
    assert "FURA_BASE_PATH=/furatena" in makefile
    assert "FURA_WORKERS=8" in makefile
    assert "python -m furatena.catalog.artifact_audit app/public" in makefile
    assert "python scripts/benchmark_catalog.py $(BENCHMARK_ARGS)" in makefile


def test_dead_spikes_are_removed_and_public_returns_are_linted() -> None:
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))

    assert "ANN201" in config["tool"]["ruff"]["lint"]["select"]
    assert config["dependency-groups"]["dev"].count("ruff==0.15.20") == 1
    assert config["project"]["optional-dependencies"]["dev"].count("ruff==0.15.20") == 1
    for relative_path in (
        "app/spike_threading.py",
        "app/export_catalog.py",
        "app/freeze_catalog.py",
        "src/furatena/catalog/directives/templates.py",
    ):
        assert not (REPO / relative_path).exists()


def test_github_actions_uses_named_make_lanes_and_scoped_caches() -> None:
    workflow_path = REPO / ".github" / "workflows" / "pages.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    triggers = workflow.get("on") or workflow.get(True)

    assert workflow["env"]["PYTHON_GIL"] == "0"
    assert triggers["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
        "converted_to_draft",
    ]
    assert set(jobs) == {*LANES, "deploy", "hosted-pdf-proof"}
    for lane in LANES:
        job = jobs[lane]
        commands = [step.get("run") for step in job["steps"] if "run" in step]
        if lane == "browser":
            assert {
                "make ci-browser-smoke",
                "make ci-browser-htmx4-preview",
                "make ci-browser-full",
            } <= set(commands)
        else:
            assert f"make ci-{lane}" in commands
        setup = next(
            step for step in job["steps"] if step.get("uses") == "astral-sh/setup-uv@v8.2.0"
        )
        assert setup["with"]["cache-suffix"] == "${{ github.job }}"
        assert any(step.get("uses") == "actions/checkout@v7.0.0" for step in job["steps"])

    for lane in ("export", "agent"):
        assert jobs[lane]["if"] == "github.event_name != 'pull_request'"
    assert "if" not in jobs["fast"]
    assert jobs["contract"]["needs"] == "fast"
    assert "if" not in jobs["contract"]
    assert jobs["coverage"]["needs"] == "fast"
    assert jobs["coverage"]["if"] == (
        "needs.fast.outputs.coverage-required == 'true' && "
        "(github.event_name != 'pull_request' || github.event.pull_request.draft == false)"
    )
    assert jobs["browser"]["needs"] == "fast"
    assert jobs["browser"]["if"] == (
        "needs.fast.outputs.browser-required == 'true' && "
        "(github.event_name != 'pull_request' || github.event.pull_request.draft == false)"
    )
    assert jobs["release"]["needs"] == "fast"
    assert jobs["release"]["if"] == (
        "needs.fast.outputs.release-required == 'true' && "
        "(github.event_name != 'pull_request' || github.event.pull_request.draft == false)"
    )
    assert jobs["fast"]["outputs"] == {
        "coverage-required": "${{ steps.scope.outputs.coverage-required }}",
        "browser-required": "${{ steps.scope.outputs.browser-required }}",
        "release-required": "${{ steps.scope.outputs.release-required }}",
    }
    scope = next(step for step in jobs["fast"]["steps"] if step.get("id") == "scope")
    assert "scripts/classify_ci_paths.py --force-all" in scope["run"]
    assert "git diff --name-only --diff-filter=ACMR" in scope["run"]
    assert jobs["browser"]["timeout-minutes"] == 10
    assert set(jobs["deploy"]["needs"]) == set(LANES)
    export_lane = next(
        step for step in jobs["export"]["steps"] if step.get("name") == "Export lane"
    )
    assert "env" not in export_lane

    export_uses = {step.get("uses") for step in jobs["export"]["steps"]}
    release_uses = {step.get("uses") for step in jobs["release"]["steps"]}
    browser_uses = {step.get("uses") for step in jobs["browser"]["steps"]}
    assert "actions/upload-pages-artifact@v3" in export_uses
    assert "actions/upload-artifact@v4" in release_uses
    assert "actions/upload-artifact@v4" in browser_uses

    browser_steps = {step.get("name"): step for step in jobs["browser"]["steps"]}
    assert browser_steps["Browser smoke lane"]["if"] == "github.event_name == 'pull_request'"
    assert browser_steps["Browser full lane"]["if"] == "github.event_name != 'pull_request'"
    diagnostics = browser_steps["Upload browser diagnostics"]
    assert diagnostics["if"] == "always()"
    assert diagnostics["with"]["path"] == "browser-results/*.xml"
    assert diagnostics["with"]["retention-days"] == 14
    assert all("--reruns" not in str(step.get("run") or "") for step in jobs["browser"]["steps"])


def test_pdf_proof_workflow_routes_only_render_relevant_source_changes() -> None:
    workflow_path = REPO / ".github" / "workflows" / "pdf-proof.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    triggers = workflow.get("on") or workflow.get(True)

    for event in ("push", "pull_request"):
        paths = set(triggers[event]["paths"])
        assert "src/**" not in paths
        assert "src/furatena/catalog/render.py" in paths
        assert "src/furatena/catalog/_templates/**" in paths
        assert "src/furatena/themes/**" in paths
        assert "src/furatena/catalog/access.py" not in paths
