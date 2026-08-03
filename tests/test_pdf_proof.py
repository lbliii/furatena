from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate

from furatena.catalog.pdf_proof import analyze_raster, apply_baseline, inspect_pdf


def test_pdf_inspection_records_structure_text_links_and_reported_page_drift(
    tmp_path: Path,
) -> None:
    pdf = tmp_path / "proof.pdf"
    SimpleDocTemplate(str(pdf), title="Proof title").build(
        [
            Paragraph(
                'FURA_PDF_PUBLIC_SENTINEL_434 <a href="https://example.com">Example</a>',
                getSampleStyleSheet()["BodyText"],
            )
        ]
    )

    record = inspect_pdf(
        pdf,
        raster_dir=tmp_path / "rasters",
        sentinels=("FURA_PDF_PUBLIC_SENTINEL_434",),
        forbidden_sentinels=("FURA_PDF_PROTECTED_SENTINEL_434",),
        require_tagged=True,
        require_outline=True,
        require_annotations=True,
        reported_page_count=6,
        render=False,
    )

    assert record["page_count"] == 1
    assert record["metadata"]["Title"] == "Proof title"
    assert record["annotation_count"] == 1
    assert record["annotation_urls"] == ["https://example.com"]
    assert record["sentinels"] == {
        "FURA_PDF_PROTECTED_SENTINEL_434": True,
        "FURA_PDF_PUBLIC_SENTINEL_434": True,
    }
    assert record["sentinel_counts"] == {
        "FURA_PDF_PROTECTED_SENTINEL_434": 0,
        "FURA_PDF_PUBLIC_SENTINEL_434": 1,
    }
    rules = {item["rule"] for item in record["diagnostics"]}
    assert rules == {"pdf.outline", "pdf.reported-page-count", "pdf.tagged"}


def test_pdf_baseline_only_allows_owned_native_renderer_gaps() -> None:
    records = [
        {
            "head": "native",
            "scope": "page",
            "path": "native/page.pdf",
            "diagnostics": [
                {"rule": "pdf.tagged", "message": "known"},
                {"rule": "pdf.reported-page-count", "message": "known"},
                {"rule": "raster.blank-final-page", "message": "regression"},
            ],
        },
        {
            "head": "browser",
            "scope": "local",
            "path": "browser/page.pdf",
            "diagnostics": [],
        },
    ]
    baseline = {
        "allowed_failures": {
            "native": ["pdf.tagged"],
            "native:page": ["pdf.reported-page-count"],
        }
    }

    outcome = apply_baseline(records, baseline)

    assert outcome["ok"] is False
    assert [item["rule"] for item in outcome["known_gaps"]] == [
        "pdf.tagged",
        "pdf.reported-page-count",
    ]
    assert [item["rule"] for item in outcome["blocking"]] == ["raster.blank-final-page"]


def test_raster_analysis_records_dark_and_edge_ink(tmp_path: Path) -> None:
    raster = tmp_path / "page.png"
    image = Image.new("RGB", (100, 120), "white")
    for x in range(96, 100):
        for y in range(120):
            image.putpixel((x, y), (0, 0, 0))
    image.save(raster)

    metrics = analyze_raster(raster, text="visible proof text")

    assert metrics["width"] == 100
    assert metrics["height"] == 120
    assert metrics["edge_ink_ratio"] == 1.0
    assert metrics["dark_ratio"] > 0


def test_pdf_proof_fixture_and_workflow_cover_both_rendering_heads() -> None:
    root = Path(__file__).parents[1]
    public_fixture = (root / "content/furatena/proof/pdf-stress.md").read_text(encoding="utf-8")
    protected_fixture = (root / "content/furatena/proof/pdf-protected-canary.md").read_text(
        encoding="utf-8"
    )
    workflow = (root / ".github/workflows/pdf-proof.yml").read_text(encoding="utf-8")
    print_runtime = (root / "src/furatena/themes/lagoon/js/docs-enhance.js").read_text(
        encoding="utf-8"
    )
    baseline = json.loads((root / "config/pdf-proof-baseline.json").read_text(encoding="utf-8"))

    assert "FURA_PDF_PUBLIC_SENTINEL_434" in public_fixture
    assert "FURA_PDF_PROTECTED_SENTINEL_434" in protected_fixture
    assert "visibility: internal" in protected_fixture
    assert "page.pdf(" in (root / "scripts/pdf_proof.py").read_text(encoding="utf-8")
    assert "beforeprint" in print_runtime
    assert "afterprint" in print_runtime
    assert "cleanPrintUrl" in print_runtime
    for rendering_input in (
        "app/**",
        "content/**",
        "src/furatena/catalog/render.py",
        "src/furatena/themes/**",
        "scripts/pdf_proof.py",
    ):
        assert rendering_input in workflow
    assert '"src/**"' not in workflow
    assert baseline["owner"] == "issue-433"
    assert baseline["allowed_failures"] == {}


def test_pdf_inspection_can_require_semantic_structure_types(tmp_path: Path) -> None:
    pdf = tmp_path / "plain.pdf"
    SimpleDocTemplate(str(pdf)).build(
        [Paragraph("Plain paragraph", getSampleStyleSheet()["BodyText"])]
    )

    record = inspect_pdf(
        pdf,
        raster_dir=tmp_path / "rasters",
        required_structure_types=("H1", "P", "Table"),
        render=False,
    )

    diagnostic = next(
        item for item in record["diagnostics"] if item["rule"] == "pdf.structure-types"
    )
    assert "H1" in diagnostic["message"]
    assert "Table" in diagnostic["message"]
