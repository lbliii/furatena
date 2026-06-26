from __future__ import annotations

from pathlib import Path

from furatena.cli.main import main


def test_init_scaffolds_standalone_app(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])

    assert (app_root / "docs.yaml").is_file()
    assert (app_root / "mounts.yaml").is_file()
    assert (app_root / "content" / "docs" / "get-started.md").is_file()
    assert (app_root / "theme" / "views" / "doc.html").is_file()
    assert (app_root / "theme" / "search.html").is_file()


def test_init_app_passes_strict_content_check(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main([
        "--app-root",
        str(app_root),
        "check",
        "--content-only",
        "--warnings-as-errors",
    ])


def test_init_app_freezes_and_exports_static_site(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main(["--app-root", str(app_root), "freeze"])
    main(["--app-root", str(app_root), "export", "--fresh", "--base-path", ""])

    assert (app_root / "frozen" / "catalog.json").is_file()
    assert (app_root / "frozen" / "search.json").is_file()
    assert (app_root / "public" / "docs" / "get-started" / "index.html").is_file()
    assert (app_root / "public" / "search.json").is_file()


def test_theme_inspect_and_eject_framework_template(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main(["--app-root", str(app_root), "theme", "inspect", "directives/callout.html"])
    inspect_output = capsys.readouterr().out

    assert "directives/callout.html" in inspect_output
    assert "framework" in inspect_output
    assert "theme/templates/directives/callout.html" in inspect_output

    main(["--app-root", str(app_root), "theme", "eject", "directives/callout.html"])
    eject_output = capsys.readouterr().out
    target = app_root / "theme" / "templates" / "directives" / "callout.html"

    assert target.is_file()
    assert "Ejected from framework:directives/callout.html" in target.read_text(encoding="utf-8")
    assert "ejected directives/callout.html" in eject_output

    main(["--app-root", str(app_root), "theme", "eject", "directives/callout.html"])
    skip_output = capsys.readouterr().out

    assert "skip directives/callout.html" in skip_output


def test_theme_diff_reports_override_drift(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main(["--app-root", str(app_root), "theme", "eject", "directives/callout.html"])
    capsys.readouterr()

    main(["--app-root", str(app_root), "theme", "diff", "directives/callout.html"])
    clean_diff = capsys.readouterr().out
    assert "no differences" in clean_diff

    target = app_root / "theme" / "templates" / "directives" / "callout.html"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n{# local edit #}\n",
        encoding="utf-8",
    )

    main(["--app-root", str(app_root), "theme", "diff", "directives/callout.html"])
    diff_output = capsys.readouterr().out

    assert "--- framework:directives/callout.html" in diff_output
    assert "+++ project:directives/callout.html" in diff_output
    assert "local edit" in diff_output


def test_ejected_template_keeps_check_and_export_working(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main(["--app-root", str(app_root), "theme", "eject", "directives/callout.html"])
    main([
        "--app-root",
        str(app_root),
        "check",
        "--content-only",
        "--warnings-as-errors",
    ])
    main(["--app-root", str(app_root), "export", "--fresh", "--base-path", ""])

    assert (app_root / "public" / "docs" / "get-started" / "index.html").is_file()
