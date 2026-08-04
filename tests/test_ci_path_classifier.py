"""Pull-request CI routing stays conservative and deterministic."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location("classify_ci_paths", REPO / "scripts" / "classify_ci_paths.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
classify_paths = MODULE.classify_paths


def test_docs_only_changes_skip_expensive_lanes() -> None:
    assert classify_paths(("docs/CI.md", "changelog.d/466.changed.md")) == {
        "coverage": False,
        "browser": False,
        "release": False,
    }


def test_python_changes_keep_coverage_and_release_proof() -> None:
    result = classify_paths(("src/furatena/catalog/access.py",))

    assert result == {"coverage": True, "browser": False, "release": True}


def test_rendering_changes_reach_every_expensive_lane() -> None:
    result = classify_paths(("src/furatena/catalog/render.py",))

    assert result == {"coverage": True, "browser": True, "release": True}


def test_content_changes_require_browser_proof_only() -> None:
    result = classify_paths(("content/en/docs/index.md",))

    assert result == {"coverage": False, "browser": True, "release": False}


def test_force_all_keeps_main_and_dispatch_on_full_proof() -> None:
    assert classify_paths((), force_all=True) == {
        "coverage": True,
        "browser": True,
        "release": True,
    }


def test_deleted_render_paths_keep_expensive_proof() -> None:
    result = classify_paths(("src/furatena/catalog/render.py",))

    assert result == {"coverage": True, "browser": True, "release": True}


def test_renamed_paths_use_the_post_rename_path() -> None:
    result = classify_paths(("app/config.py",))

    assert result == {"coverage": False, "browser": True, "release": False}
