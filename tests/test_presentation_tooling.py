"""Presentation-pack developer tooling and generated-reference contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.presentation_pack import load_presentation_manifest
from furatena.catalog.presentation_tooling import (
    check_presentation_pack,
    generate_reference_preview,
    run_presentation_conformance,
)
from furatena.catalog.theme_init import ScaffoldKind, init_theme_pack
from furatena.catalog.view_kinds import VIEW_KINDS
from furatena.cli.main import main, run_command


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("kind", ("layout", "skin", "override"))
def test_all_scaffold_kinds_are_valid_and_byte_deterministic(
    tmp_path: Path,
    kind: ScaffoldKind,
) -> None:
    assert_free_threading()
    target = tmp_path / f"reference-{kind}"

    first = init_theme_pack(target, kind=kind, identity=f"reference-{kind}")
    first_hashes = _tree_hashes(target)
    second = init_theme_pack(target, force=True, kind=kind, identity=f"reference-{kind}")

    assert first
    assert {path.relative_to(target) for path in first} == {
        path.relative_to(target) for path in second
    }
    assert _tree_hashes(target) == first_hashes
    pack = load_presentation_manifest(target / "presentation-pack.json", source="local")
    assert pack.kind == kind
    assert check_presentation_pack(target).ok
    if kind == "layout":
        assert {name for name, _relative in pack.templates} == {spec.kind for spec in VIEW_KINDS}


def test_check_reports_malformed_manifest_with_surface_and_recovery(tmp_path: Path) -> None:
    target = tmp_path / "broken"
    target.mkdir()
    (target / "presentation-pack.json").write_text("{", encoding="utf-8")

    result = check_presentation_pack(target)

    assert not result.ok
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == "fura.presentation.manifest"
    assert diagnostic.surface == "manifest compatibility"
    assert "Correct presentation-pack.json" in diagnostic.recovery


def test_check_rejects_symlinks_even_when_manifest_files_are_regular(tmp_path: Path) -> None:
    target = tmp_path / "unsafe-layout"
    init_theme_pack(target, kind="layout", identity="unsafe-layout")
    (target / "unsafe-link").symlink_to(target / "styles.css")

    result = check_presentation_pack(target)

    assert not result.ok
    assert any(item.code == "fura.presentation.unsafe_path" for item in result.diagnostics)


def test_preview_covers_full_matrix_and_detects_generated_drift(tmp_path: Path) -> None:
    target = tmp_path / "matrix-layout"
    output = tmp_path / "preview"
    init_theme_pack(target, kind="layout", identity="matrix-layout")

    generated = generate_reference_preview(target, output)
    current = generate_reference_preview(target, output, check=True)

    assert generated.ok and current.ok
    assert generated.files == current.files
    assert set(generated.report["view_kinds"]) == {spec.kind for spec in VIEW_KINDS}
    assert generated.report["extra_states"] == ["error", "search"]
    assert {item["id"] for item in generated.report["responsive_states"]} == {
        "mobile",
        "tablet",
        "desktop",
    }
    assert len(tuple((output / "full").glob("*.html"))) == 10
    assert len(tuple((output / "fragment").glob("*.html"))) == 10

    changed = output / "full" / "doc.html"
    changed.write_text(changed.read_text(encoding="utf-8") + "<!-- drift -->\n", encoding="utf-8")
    drifted = generate_reference_preview(target, output, check=True)
    assert not drifted.ok
    assert drifted.drift == ("full/doc.html",)


def test_conformance_report_is_deterministic_and_cross_surface_complete(tmp_path: Path) -> None:
    target = tmp_path / "conformant-layout"
    output = tmp_path / "conformance"
    init_theme_pack(target, kind="layout", identity="conformant-layout")

    generated = run_presentation_conformance(target, output)
    current = run_presentation_conformance(target, output, check=True)

    assert generated.ok and current.ok
    assert all(generated.report["checks"].values())
    assert set(generated.report["checks"]) == {
        "accessibility_semantics",
        "agent_outputs_presentation_independent",
        "fragment_html",
        "full_html",
        "navigation_hooks",
        "pdf",
        "print_contract",
        "responsive_states",
        "search_hooks",
        "static_visibility",
    }
    assert json.loads((output / "conformance.json").read_text(encoding="utf-8")) == (
        generated.report
    )


@pytest.mark.parametrize("kind", ("skin", "override"))
def test_sparse_pack_conformance_uses_vanilla_semantics(
    tmp_path: Path,
    kind: ScaffoldKind,
) -> None:
    target = tmp_path / f"conformant-{kind}"
    output = tmp_path / f"report-{kind}"
    init_theme_pack(target, kind=kind, identity=f"conformant-{kind}")

    result = run_presentation_conformance(target, output)

    assert result.ok
    assert all(result.report["checks"].values())


def test_cli_results_preserve_nested_command_exit_and_json_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_root = tmp_path / "app"
    target = app_root / "presentation" / "cli-layout"
    init_theme_pack(target, kind="layout", identity="cli-layout")

    valid = run_command(["--app-root", str(app_root), "theme", "check", "presentation/cli-layout"])
    assert valid is not None and valid.ok and valid.command == "theme check"

    (target / "presentation-pack.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "--app-root",
                str(app_root),
                "theme",
                "check",
                "presentation/cli-layout",
                "--json",
            ]
        )
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "theme check"
    assert payload["exit_code"] == 2
    assert payload["diagnostics"][0]["rule_id"] == "fura.presentation.manifest"
    assert payload["diagnostics"][0]["next_action"]
