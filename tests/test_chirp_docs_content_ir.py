"""Wave 10 tests for Patitas content IR and docs check."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
CONTENT_ROOT = REPO / "content" / "chirp"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog import DocCatalog
from furatena.catalog.check import check_broken_internal_links, check_catalog
from furatena.catalog.content_ir import (
    content_ir_from_record,
    content_ir_record,
    extract_content_ir,
)
from furatena.catalog.export import catalog_graph
from furatena.catalog.graph import extract_page_links, normalize_internal_url
from furatena.catalog.render import DocsRenderer


@pytest.fixture(scope="module")
def catalog() -> DocCatalog:
    return DocCatalog(CONTENT_ROOT, autodoc=False, autodoc_config=None)


class TestContentIRExtraction:
    def test_extracts_headings_links_and_directives(self) -> None:
        renderer = DocsRenderer()
        document, _ = renderer.parse(
            "# Hello {#custom-id}\n\nSee [Install](/docs/get-started/installation/).\n\n:::{note}\nBody\n:::\n"
        )
        content_ir = extract_content_ir(document)
        assert len(content_ir.headings) == 1
        assert content_ir.headings[0].anchor == "custom-id"
        assert content_ir.headings[0].level == 1
        assert len(content_ir.links) == 1
        assert content_ir.links[0].href == "/docs/get-started/installation/"
        assert content_ir.directives[0].name == "note"

    def test_normalize_internal_url_strips_fragments(self) -> None:
        assert normalize_internal_url("/docs/foo/#bar") == "/docs/foo/"
        assert normalize_internal_url("https://example.com") is None


class TestCatalogContentIR:
    def test_indexed_pages_have_content_ir(self, catalog: DocCatalog) -> None:
        node = catalog.get("/docs/get-started/installation/")
        assert node is not None
        assert node.content_ir is not None
        assert node.content_ir.headings
        assert node.toc

    def test_catalog_export_includes_content_block(self, catalog: DocCatalog) -> None:
        graph = catalog_graph(catalog)
        page = next(
            item for item in graph["pages"] if item["slug"] == "docs/get-started/installation"
        )
        assert "content" in page
        assert page["content"]["headings"]

    def test_content_ir_round_trip_record(self, catalog: DocCatalog) -> None:
        node = catalog.get("/docs/get-started/installation/")
        assert node is not None
        raw = content_ir_record(node.content_ir)
        assert raw is not None
        restored = content_ir_from_record(raw)
        assert restored == node.content_ir


class TestContentIRLinks:
    def test_extract_page_links_prefers_content_ir(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        target = docs / "target.md"
        target.write_text("---\ntitle: Target\n---\n# Target\n", encoding="utf-8")
        source = docs / "source.md"
        source.write_text(
            "---\ntitle: Source\n---\n# Source\n\nGo to [Target](/docs/target/).\n",
            encoding="utf-8",
        )

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        source_node = catalog.get_by_slug("docs/source")
        target_node = catalog.get_by_slug("docs/target")
        assert source_node is not None
        assert target_node is not None
        assert target_node.url in extract_page_links(source_node)

    def test_check_reports_broken_internal_links(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "broken.md"
        page.write_text(
            "---\ntitle: Broken\n---\n# Broken\n\nSee [Missing](/docs/does-not-exist/).\n",
            encoding="utf-8",
        )

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        errors = check_broken_internal_links(catalog)
        assert any("does-not-exist" in error for error in errors)

    def test_check_passes_for_valid_catalog(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        target = docs / "target.md"
        target.write_text("---\ntitle: Target\n---\n# Target\n", encoding="utf-8")
        source = docs / "source.md"
        source.write_text(
            "---\ntitle: Source\n---\n# Source\n\nSee [Target](/docs/target/).\n",
            encoding="utf-8",
        )

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        errors, warnings = check_catalog(catalog)
        assert not errors
        assert isinstance(warnings, list)

    def test_check_resolves_index_alias_urls(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        section = content / "docs" / "get-started"
        section.mkdir(parents=True)
        index = section / "_index.md"
        index.write_text("---\ntitle: Get Started\n---\n# Get Started\n", encoding="utf-8")
        linker = content / "docs" / "linker.md"
        linker.write_text(
            "---\ntitle: Linker\n---\n# Linker\n\nSee [Get Started](/docs/get-started/_index/).\n",
            encoding="utf-8",
        )

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        errors = check_broken_internal_links(catalog)
        assert not errors

    def test_check_ignores_non_catalog_app_routes(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "home.md"
        page.write_text(
            "---\ntitle: Home\n---\n# Home\n\nSee [Search](/search/) and [LLMs](/llms.txt).\n",
            encoding="utf-8",
        )

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        errors = check_broken_internal_links(catalog)
        assert not errors
