"""Dependency-independent contracts for the existing sparse skin scaffold."""

from __future__ import annotations

import json
from pathlib import Path

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.theme_init import init_theme_pack
from furatena.cli.main import _build_parser, main

EXPECTED_SPARSE_SKIN_FILES = {
    "README.md",
    "assets/branding/README.md",
    "directives.css",
    "effects.css",
    "skin/chrome.css",
    "skin/error.css",
    "skin/fonts.css",
    "skin/hero.css",
    "skin/home.css",
    "skin/shell.css",
    "styles.css",
    "tokens.css",
}


def _relative_paths(root: Path, paths: list[Path]) -> set[str]:
    return {path.relative_to(root).as_posix() for path in paths}


def test_sparse_skin_scaffold_is_complete_and_deterministic(tmp_path: Path) -> None:
    assert_free_threading()
    target = tmp_path / "theme-skin"

    written = init_theme_pack(target)

    assert _relative_paths(target, written) == EXPECTED_SPARSE_SKIN_FILES
    assert {
        path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
    } == (EXPECTED_SPARSE_SKIN_FILES)
    assert "theme-skin/tokens.css" in (target / "README.md").read_text(encoding="utf-8")
    assert "furatena.themes" in (target / "README.md").read_text(encoding="utf-8")
    assert init_theme_pack(target) == []


def test_sparse_skin_scaffold_preserves_local_files_unless_forced(tmp_path: Path) -> None:
    target = tmp_path / "theme-skin"
    init_theme_pack(target)
    tokens = target / "tokens.css"
    tokens.write_text("/* local brand */\n", encoding="utf-8")
    unrelated = target / "notes.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")

    assert init_theme_pack(target) == []
    assert tokens.read_text(encoding="utf-8") == "/* local brand */\n"

    rewritten = init_theme_pack(target, force=True)

    assert _relative_paths(target, rewritten) == EXPECTED_SPARSE_SKIN_FILES
    assert "Brand tokens" in tokens.read_text(encoding="utf-8")
    assert unrelated.read_text(encoding="utf-8") == "keep me\n"


def test_theme_init_json_reports_repository_local_scaffold(
    tmp_path: Path,
    capsys,
) -> None:
    app_root = tmp_path / "docs-app"

    main(
        [
            "--app-root",
            str(app_root),
            "theme",
            "init",
            "brand",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    target = app_root / "brand"
    assert payload["ok"] is True
    assert payload["command"] == "theme init"
    assert Path(payload["data"]["target"]) == target
    assert set(payload["data"]["written"]) == EXPECTED_SPARSE_SKIN_FILES
    assert payload["data"]["count"] == len(EXPECTED_SPARSE_SKIN_FILES)


def test_theme_init_relative_directory_stays_beneath_selected_app_root(
    tmp_path: Path,
    capsys,
) -> None:
    app_root = tmp_path / "docs-app"

    main(["--app-root", str(app_root), "theme", "init", "themes/brand", "--json"])
    payload = json.loads(capsys.readouterr().out)

    target = app_root / "themes" / "brand"
    assert Path(payload["data"]["target"]) == target
    assert target.is_relative_to(app_root)
    assert not (tmp_path / "themes" / "brand").exists()


def test_theme_init_parser_keeps_default_relative_to_selected_app_root(tmp_path: Path) -> None:
    app_root = tmp_path / "other"

    args = _build_parser().parse_args(["--app-root", str(app_root), "theme", "init"])

    assert args.directory == "theme-skin"
    assert Path(args.app_root) == app_root


def test_theme_init_uses_selected_app_root_when_directory_is_omitted(
    tmp_path: Path,
    capsys,
) -> None:
    app_root = tmp_path / "other"

    main(["--app-root", str(app_root), "theme", "init"])

    target = app_root / "theme-skin"
    assert target.is_dir()
    assert {
        path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()
    } == EXPECTED_SPARSE_SKIN_FILES
    assert capsys.readouterr().out.startswith(f"initialized skin scaffold at {target}")


def test_theme_init_json_uses_selected_app_root_when_directory_is_omitted(
    tmp_path: Path,
    capsys,
) -> None:
    app_root = tmp_path / "other"

    main(["--app-root", str(app_root), "theme", "init", "--json"])
    payload = json.loads(capsys.readouterr().out)

    target = app_root / "theme-skin"
    assert Path(payload["data"]["target"]) == target
    assert set(payload["data"]["written"]) == EXPECTED_SPARSE_SKIN_FILES
    assert payload["data"]["count"] == len(EXPECTED_SPARSE_SKIN_FILES)


def test_theme_init_json_preserves_absolute_directory(
    tmp_path: Path,
    capsys,
) -> None:
    app_root = tmp_path / "other"
    target = tmp_path / "absolute-skin"

    main(
        [
            "--app-root",
            str(app_root),
            "theme",
            "init",
            str(target),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert Path(payload["data"]["target"]) == target
    assert set(payload["data"]["written"]) == EXPECTED_SPARSE_SKIN_FILES
    assert not app_root.exists()
