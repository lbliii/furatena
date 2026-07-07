"""The persona coverage matrix remains complete and actionable."""

from __future__ import annotations

from pathlib import Path

MATRIX = (
    Path(__file__).resolve().parents[1]
    / "content"
    / "furatena"
    / "docs"
    / "concepts"
    / "persona-diataxis-matrix.md"
)
PERSONAS = {"solo-founder", "devrel-team", "platform-docs-team", "enterprise-admin"}
SURFACES = {"cli", "routes", "configuration", "mcp", "sidecars", "diagnostics", "deployment"}
STATUSES = ("Existing", "Partial", "Missing", "Intentionally internal")


def _matrix_rows() -> list[list[str]]:
    rows = []
    for line in MATRIX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 7 and cells[0].strip("`") in PERSONAS:
            rows.append(cells)
    return rows


def test_matrix_covers_every_persona_surface_pair() -> None:
    rows = _matrix_rows()
    observed = {(row[0].strip("`"), row[1].strip("`")) for row in rows}

    assert len(rows) == len(PERSONAS) * len(SURFACES)
    assert observed == {(persona, surface) for persona in PERSONAS for surface in SURFACES}


def test_matrix_cells_use_status_contract_and_assign_missing_work() -> None:
    for row in _matrix_rows():
        for cell in row[2:6]:
            assert cell.startswith(STATUSES), cell
            if cell.startswith("Missing"):
                assert cell.startswith(("Missing P0", "Missing P1")), cell
                assert "owner: `" in cell, cell
        assert any(f"#{issue}" in row[6] for issue in range(167, 175)), row[6]
