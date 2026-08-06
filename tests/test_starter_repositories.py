"""Maintained clone-to-export starter repository contracts."""

from __future__ import annotations

import json
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from furatena import __version__
from furatena.catalog.benchmarks import assert_free_threading
from furatena.cli.main import run_command


@pytest.mark.parametrize(
    ("starter", "expected_marker"),
    (
        ("minimal", "docs/get-started"),
        ("api-portal", "listWidgets"),
        ("multi-mount", "sdk:latest:docs/sdk-quickstart"),
        ("governed-preview", "docs/get-started"),
    ),
)
def test_starter_runs_from_init_through_static_export(
    tmp_path: Path,
    starter: str,
    expected_marker: str,
) -> None:
    assert_free_threading()
    app_root = tmp_path / starter

    initialized = run_command(
        ["init", str(app_root), "--name", f"{starter} docs", "--starter", starter, "--json"]
    )
    checked = run_command(
        [
            "--app-root",
            str(app_root),
            "check",
            "--content-only",
            "--warnings-as-errors",
            "--json",
        ]
    )
    run_command(["--app-root", str(app_root), "freeze", "--json"])
    run_command(["--app-root", str(app_root), "export", "--base-path", "", "--json"])

    assert initialized is not None and initialized.ok
    assert initialized.data["starter"] == starter
    assert checked is not None and checked.ok
    assert (app_root / "frozen/channels.json").is_file()
    assert (app_root / "public/channels.json").is_file()
    catalog = (app_root / "frozen/catalog.json").read_text(encoding="utf-8")
    assert expected_marker in catalog


@pytest.mark.parametrize("starter", ("minimal", "api-portal", "multi-mount", "governed-preview"))
def test_starter_dependencies_ci_and_audience_stay_release_aligned(
    tmp_path: Path,
    starter: str,
) -> None:
    app_root = tmp_path / starter
    result = run_command(["init", str(app_root), "--name", "Acme Platform", "--starter", starter])

    assert result is not None and result.ok
    with (app_root / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["requires-python"] == ">=3.14,<3.15"
    assert project["dependencies"] == [f"furatena=={__version__}"]
    workflow = (app_root / ".github/workflows/docs.yml").read_text(encoding="utf-8")
    assert "astral-sh/setup-uv@v8.2.0" in workflow
    assert "uv python install 3.14t" in workflow
    assert 'PYTHON_GIL: "0"' in workflow
    assert "assert not sys._is_gil_enabled()" in workflow
    assert "fura check --content-only --warnings-as-errors" in workflow
    assert "fura freeze" in workflow
    assert "fura export" in workflow
    readme = (app_root / "README.md").read_text(encoding="utf-8")
    assert f"`{starter}` starter" in readme
    assert "**Audience:**" in readme


def test_starter_generation_is_safe_under_free_threading(tmp_path: Path) -> None:
    assert_free_threading()
    starters = ("minimal", "api-portal", "multi-mount", "governed-preview")

    def generate(index: int) -> tuple[str, int]:
        starter = starters[index % len(starters)]
        result = run_command(
            [
                "init",
                str(tmp_path / f"starter-{index}"),
                "--starter",
                starter,
                "--json",
            ]
        )
        assert result is not None and result.ok
        return starter, int(result.data["count"])

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(generate, range(36)))

    assert len(results) == 36
    assert {starter for starter, _ in results} == set(starters)
    assert all(count >= 14 for _, count in results)


def test_governed_preview_starter_has_secure_reference_integration(tmp_path: Path) -> None:
    app_root = tmp_path / "governed"
    run_command(["init", str(app_root), "--starter", "governed-preview"])

    railway = tomllib.loads((app_root / "railway.toml").read_text(encoding="utf-8"))
    assert railway["build"]["builder"] == "dockerfile"
    assert railway["deploy"]["healthcheckPath"] == "/readyz"
    assert railway["environments"]["pr"]["deploy"] == {
        "numReplicas": 1,
        "overlapSeconds": 0,
        "drainingSeconds": 0,
    }
    dockerfile = (app_root / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG FURA_PREVIEW_AUTH_TOKEN" not in dockerfile
    assert "FURA_BUILD_GIT_SHA=$RAILWAY_GIT_COMMIT_SHA" in dockerfile
    conformance = (app_root / "scripts/preview_report.py").read_text(encoding="utf-8")
    assert "preview_reporting import main" in conformance
    workflow = (app_root / ".github/workflows/preview-report.yml").read_text(encoding="utf-8")
    assert "pull_request_target" in workflow
    assert "pull-requests: write" in workflow
    assert "issues: write" not in workflow
    assert "head.repo.full_name == github.repository" in workflow
    assert "user.type != 'Bot'" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "uses: lbliii/furatena" not in workflow
    assert "FURA_PREVIEW_AUTH_TOKEN: ${{ secrets.FURA_PREVIEW_AUTH_TOKEN }}" in workflow
    preview_docs = (app_root / "PREVIEWS.md").read_text(encoding="utf-8")
    assert "Never" in preview_docs
    assert "one replica" in preview_docs
    assert ".env.preview" in (app_root / ".gitignore").read_text(encoding="utf-8")


def test_governed_preview_guide_does_not_advertise_unavailable_hosted_access() -> None:
    guide = (Path(__file__).resolve().parents[1] / "docs/GOVERNED_PR_PREVIEWS.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(guide.split())

    assert "Status: planned, not available in the current release." in normalized
    assert "Hosted GitHub sign-in is not the released Railway default." in normalized
    assert "do not ship `FURA_PREVIEW_AUTH_MODE`" in normalized
    assert "Keep `FURA_PREVIEW_AUTH_TOKEN` configured" in normalized
    assert "This is a release-readiness gate, not an actionable migration procedure." in normalized
    assert "A future hosted default must retain an explicit password rollback path" in normalized


def test_api_portal_openapi_fixture_is_valid_json_projection(tmp_path: Path) -> None:
    app_root = tmp_path / "api"
    run_command(["init", str(app_root), "--starter", "api-portal"])
    run_command(["--app-root", str(app_root), "freeze"])

    payload = json.loads((app_root / "frozen/catalog.json").read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True)
    assert "listWidgets" in serialized
    assert "openapi-operation" in serialized
