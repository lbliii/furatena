"""The documented first Pages deployment remains executable."""

from __future__ import annotations

from pathlib import Path

from furatena.catalog.artifact_audit import audit_static_artifacts
from furatena.cli.main import main

REPO = Path(__file__).resolve().parents[1]
TUTORIAL = REPO / "content" / "furatena" / "docs" / "get-started" / "first-github-pages-deploy.md"
BASE_PATH = "/example-docs"
SITE_URL = "https://example.github.io/example-docs"


def test_tutorial_commands_build_and_verify_pages_artifact(tmp_path: Path, monkeypatch) -> None:
    app_root = tmp_path / "app"
    frozen = app_root / "frozen"
    public = app_root / "public"
    monkeypatch.setenv("PYTHON_GIL", "0")
    monkeypatch.setenv("FURA_APP_ROOT", str(app_root))
    monkeypatch.setenv("FURA_BASE_URL", SITE_URL)
    monkeypatch.setenv("FURA_BASE_PATH", BASE_PATH)

    main(["init", str(app_root), "--name", "Pages Tutorial"])
    main(["--app-root", str(app_root), "check", "--content-only", "--warnings-as-errors"])
    main(["--app-root", str(app_root), "freeze", str(frozen), "--workers", "2"])
    main(
        [
            "--app-root",
            str(app_root),
            "export",
            str(public),
            "--frozen",
            str(frozen),
            "--base-url",
            SITE_URL,
            "--base-path",
            BASE_PATH,
        ]
    )

    report = audit_static_artifacts(public, base_path=BASE_PATH, site_url=SITE_URL)
    assert report.ok, [finding.format() for finding in report.findings]
    assert (public / ".nojekyll").is_file()
    assert (public / "docs" / "get-started" / "index.html").is_file()
    assert (public / "channels.json").is_file()


def test_tutorial_contains_tested_workflow_and_recovery_contracts() -> None:
    text = TUTORIAL.read_text(encoding="utf-8")

    for command in (
        "uv run fura check --content-only --warnings-as-errors",
        "uv run fura freeze app/frozen",
        "uv run fura export app/public",
        "python -m furatena.catalog.artifact_audit app/public",
    ):
        assert command in text
    assert 'PYTHON_GIL: "0"' in text
    assert 'python-version: "3.14t"' in text
    assert "FURA_BASE_URL" in text
    assert "FURA_BASE_PATH" in text
    assert "repeated-base-path" in text
    assert "missing-target" in text
    assert "stale frozen graph" in text
