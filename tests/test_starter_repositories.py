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


@pytest.mark.parametrize("starter", ("minimal", "api-portal", "multi-mount"))
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
    starters = ("minimal", "api-portal", "multi-mount")

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
    assert all(count >= 25 for _, count in results)


def test_api_portal_openapi_fixture_is_valid_json_projection(tmp_path: Path) -> None:
    app_root = tmp_path / "api"
    run_command(["init", str(app_root), "--starter", "api-portal"])
    run_command(["--app-root", str(app_root), "freeze"])

    payload = json.loads((app_root / "frozen/catalog.json").read_text(encoding="utf-8"))
    serialized = json.dumps(payload, sort_keys=True)
    assert "listWidgets" in serialized
    assert "openapi-operation" in serialized
