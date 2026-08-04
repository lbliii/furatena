"""The public-safety map names proof for every anonymous delivery surface."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAP = REPO / "docs" / "ci-public-safety-map.md"


def test_public_safety_map_documents_surfaces_gaps_and_collateral() -> None:
    text = MAP.read_text(encoding="utf-8")

    assert "# CI public-safety map" in text
    assert "## Policy spine" in text
    assert "## Surface catalog" in text
    assert "## Proof gaps (planned)" in text
    assert "## Duplicate and misplaced proof" in text
    assert "## Collateral surfaces (explicit no-impact)" in text
    assert "## Source-of-truth hierarchy" in text
    assert "Generated artifacts" in text
    assert "treated as source of truth" in text
    for surface in (
        "Live HTML pages",
        "Static export tree",
        "MCP resources and tools",
        "PDF output",
        "Public projection",
    ):
        assert surface in text
    assert "test_public_projection.py" in text
    assert "test_visibility_audit.py" in text
