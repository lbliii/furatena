"""Local CI commands remain in parity with documented lane names."""

from pathlib import Path

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
