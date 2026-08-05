"""Ratchet the #583 public-safety proof map against named surfaces and proofs."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAP = REPO / "docs" / "PUBLIC_SAFETY_PROOF_MAP.md"

REQUIRED_SURFACES = (
    "Live HTML routes",
    "Fragments (HX)",
    "Static export",
    "Frozen catalog",
    "Search / suggest / semantic",
    "PDF",
    "Inventories / `objects.inv`",
    "DCP `catalog.json`",
    "`llms.txt` / `llms-full.txt`",
    "`tools.json`",
    "MCP resources/tools",
    "CLI author / query / check",
    "Railway preview",
    "`/develop/` previews",
    "Channel / version / editions",
    "Sitemap",
)

REQUIRED_PROOF_ANCHORS = (
    "tests/test_visibility_audit.py",
    "tests/test_public_projection.py",
    "tests/test_access_isolation.py",
    "tests/test_chirp_docs_rbac.py",
    "tests/test_retrieval_conformance.py",
    "make ci-export",
    "make ci-public-safety",
)

REQUIRED_NON_SOT = (
    "app/public/",
    "app/frozen/",
    "Generated output is not source of truth",
)


def test_public_safety_proof_map_names_every_delivery_surface() -> None:
    text = MAP.read_text(encoding="utf-8")
    missing = [surface for surface in REQUIRED_SURFACES if surface not in text]
    assert not missing, f"Proof map missing surfaces: {missing}"


def test_public_safety_proof_map_names_authoritative_proofs() -> None:
    text = MAP.read_text(encoding="utf-8")
    missing = [anchor for anchor in REQUIRED_PROOF_ANCHORS if anchor not in text]
    assert not missing, f"Proof map missing proof anchors: {missing}"


def test_public_safety_proof_map_rejects_generated_output_as_source_of_truth() -> None:
    text = MAP.read_text(encoding="utf-8")
    missing = [marker for marker in REQUIRED_NON_SOT if marker not in text]
    assert not missing, f"Proof map missing non-SoT markers: {missing}"


def test_public_safety_proof_map_records_duplicate_and_gap_sections() -> None:
    text = MAP.read_text(encoding="utf-8")
    assert "## Duplicate or misplaced proof" in text
    assert "## Gap backlog" in text
    assert "objects.inv" in text
    assert "Fragment" in text
