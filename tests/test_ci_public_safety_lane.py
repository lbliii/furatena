"""The public-safety CI lane stays aligned with the Makefile and map."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

REQUIRED_TESTS = (
    "tests/test_public_projection.py",
    "tests/test_visibility_audit.py",
    "tests/test_chirp_docs_rbac.py",
    "tests/test_retrieval_conformance.py",
    "tests/test_access_isolation.py",
    "tests/test_federation_artifacts.py",
    "tests/test_mcp_apps_contract.py",
)


def test_makefile_exposes_public_safety_lane() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    docs = (REPO / "docs" / "CI.md").read_text(encoding="utf-8")
    map_doc = (REPO / "docs" / "ci-public-safety-map.md").read_text(encoding="utf-8")

    assert "ci-public-safety:" in makefile
    assert "PUBLIC_SAFETY_TESTS" in makefile
    assert "public-safety: ci-public-safety" in makefile
    assert "make ci-public-safety" in docs
    assert "make ci-public-safety" in map_doc
    for relative_path in REQUIRED_TESTS:
        assert relative_path in makefile


def test_public_safety_lane_runs_under_free_threaded_pytest() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert "FREE_THREADED = env PYTHON_GIL=0" in makefile
    assert "ci-public-safety:\n\t$(PYTEST) $(PUBLIC_SAFETY_TESTS)" in makefile
