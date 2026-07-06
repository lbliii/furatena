"""Public artifact visibility canary contracts."""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen.canvas import Canvas

from furatena.catalog.registry import CatalogRegistry, MountConfig
from furatena.catalog.visibility_audit import (
    VisibilityCanary,
    scan_visibility_leaks,
    visibility_canaries,
)


def test_visibility_canaries_cover_draft_private_protected_and_archived(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    fixtures = {
        "draft.md": "---\ntitle: Canary Draft\ndraft: true\n---\nCANARY_DRAFT_BODY_7H2K\n",
        "private.md": "---\ntitle: Canary Private\nvisibility: private\n---\nCANARY_PRIVATE_BODY_8M3L\n",
        "protected.md": (
            "---\ntitle: Canary Protected\nvisibility: internal\naccess:\n  teams: [security]\n"
            "---\nCANARY_PROTECTED_BODY_9N4P\n"
        ),
        "archived.md": (
            "---\ntitle: Canary Archived\nvisibility: archived\narchived_at: 2026-07-01\n"
            "---\nCANARY_ARCHIVED_BODY_5Q6R\n"
        ),
        "public.md": "---\ntitle: Public\n---\nPublic body.\n",
    }
    for name, source in fixtures.items():
        (content / name).write_text(source, encoding="utf-8")
    registry = CatalogRegistry(
        (MountConfig(id="docs", label="Docs", content_root=content, default=True),),
        repo_root=tmp_path,
        autodoc=False,
    )

    canaries = visibility_canaries(registry)

    assert {item.boundary for item in canaries} == {"draft", "private", "protected", "archived"}
    assert any("CANARY_DRAFT_BODY_7H2K" in item.tokens for item in canaries)


def test_scanner_names_artifact_and_policy_boundary_across_formats(tmp_path: Path) -> None:
    canaries = (
        VisibilityCanary("draft.md", "draft", ("CANARY_DRAFT_BODY_7H2K",)),
        VisibilityCanary("private.md", "private", ("CANARY_PRIVATE_BODY_8M3L",)),
        VisibilityCanary("protected.md", "protected", ("CANARY_PROTECTED_BODY_9N4P",)),
        VisibilityCanary("archived.md", "archived", ("CANARY_ARCHIVED_BODY_5Q6R",)),
    )
    (tmp_path / "index.html").write_text("CANARY_DRAFT_BODY_7H2K", encoding="utf-8")
    (tmp_path / "search.json").write_text('{"value":"CANARY_PRIVATE_BODY_8M3L"}', encoding="utf-8")
    (tmp_path / "llms.txt").write_text("CANARY_PROTECTED_BODY_9N4P", encoding="utf-8")
    pdf_path = tmp_path / "bundle.pdf"
    canvas = Canvas(str(pdf_path))
    canvas.drawString(72, 720, "CANARY_ARCHIVED_BODY_5Q6R")
    canvas.save()

    report = scan_visibility_leaks(tmp_path, canaries)

    assert not report.ok
    assert {(str(item.artifact), item.boundary) for item in report.findings} == {
        ("index.html", "draft"),
        ("search.json", "private"),
        ("llms.txt", "protected"),
        ("bundle.pdf", "archived"),
    }
    assert "leaked archived content" in next(
        item.format() for item in report.findings if item.boundary == "archived"
    )


def test_scanner_accepts_public_artifacts_without_canaries(tmp_path: Path) -> None:
    (tmp_path / "catalog.json").write_text('{"title":"Public"}', encoding="utf-8")

    report = scan_visibility_leaks(
        tmp_path,
        [VisibilityCanary("private.md", "private", ("CANARY_PRIVATE_BODY_8M3L",))],
    )

    assert report.ok
    assert report.scanned_artifacts == 1
