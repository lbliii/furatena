"""Wave 12 tests for AST persistence and incremental invalidation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog import DocCatalog
from furatena.catalog.ast_store import document_from_json, document_to_json
from furatena.catalog.export import catalog_graph
from furatena.catalog.incremental import htmx_swap_hints, invalidation_regions, is_partial_reload, needs_graph_rebuild
from furatena.catalog.render import DocsRenderer


class TestIncrementalInvalidation:
    def test_heading_change_invalidates_toc_not_graph_only(self) -> None:
        renderer = DocsRenderer()
        old_doc, _ = renderer.parse("# Title\n\nBody.\n")
        new_doc, _ = renderer.parse("# Title\n\n## Section\n\nBody.\n")
        regions = invalidation_regions(old_doc, new_doc)
        assert "toc" in regions
        assert "body" in regions
        assert htmx_swap_hints(regions) == ("toc-panel", "page-root")

    def test_partial_reload_detects_selective_hints(self) -> None:
        assert is_partial_reload(("toc-panel",))
        assert is_partial_reload(("page-root", "toc-panel"))
        assert not is_partial_reload(("page-root", "toc-panel", "head-meta", "docs-sidebar"))

    def test_identical_documents_invalidate_nothing(self) -> None:
        renderer = DocsRenderer()
        doc, _ = renderer.parse("# Same\n\nUnchanged.\n")
        assert invalidation_regions(doc, doc) == frozenset()
        assert not needs_graph_rebuild(frozenset())

    def test_new_page_invalidates_everything(self) -> None:
        renderer = DocsRenderer()
        doc, _ = renderer.parse("# New\n\n[Link](/docs/foo/).\n")
        regions = invalidation_regions(None, doc)
        assert "graph" in regions
        assert needs_graph_rebuild(regions)

    def test_link_change_invalidates_graph(self) -> None:
        renderer = DocsRenderer()
        old_doc, _ = renderer.parse("# Page\n\nStatic body.\n")
        new_doc, _ = renderer.parse("# Page\n\nSee [Docs](/docs/foo/).\n")
        regions = invalidation_regions(old_doc, new_doc)
        assert "graph" in regions
        assert needs_graph_rebuild(regions)

    def test_ast_json_round_trip(self) -> None:
        renderer = DocsRenderer()
        doc, _ = renderer.parse("# Hello\n\nWorld.\n")
        restored = document_from_json(document_to_json(doc))
        assert invalidation_regions(doc, restored) == frozenset()


class TestIncrementalReindex:
    def test_body_only_edit_skips_graph_rebuild(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "page.md"
        page.write_text("---\ntitle: Page\n---\n# Page\n\nHello.\n", encoding="utf-8")

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None, auto_reload=True)
        first_backlinks = dict(catalog._backlinks)

        page.write_text("---\ntitle: Page\n---\n# Page\n\nHello world.\n", encoding="utf-8")
        catalog._reindex_paths({page})

        assert catalog.get_by_slug("docs/page") is not None
        assert catalog._backlinks == first_backlinks
        hints = catalog.invalidation_hints("docs/page")
        assert "page-root" in hints
        assert catalog._backlinks == first_backlinks

    def test_heading_edit_rebuilds_toc_hints(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "page.md"
        page.write_text("---\ntitle: Page\n---\n# Page\n\nHello.\n", encoding="utf-8")

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None, auto_reload=True)
        page.write_text(
            "---\ntitle: Page\n---\n# Page\n\n## Section\n\nHello.\n",
            encoding="utf-8",
        )
        catalog._reindex_paths({page})

        hints = catalog.invalidation_hints("docs/page")
        assert "toc-panel" in hints
        assert "page-root" in hints

    def test_freeze_exports_ast_sidecar(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "hello.md"
        page.write_text("---\ntitle: Hello\n---\n# Hello\n\nBody.\n", encoding="utf-8")

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        node = catalog.get_by_slug("docs/hello")
        assert node is not None
        assert node.ast_json

        frozen = tmp_path / "frozen"
        pages = frozen / "pages"
        pages.mkdir(parents=True)
        ast_dir = frozen / "ast"
        ast_dir.mkdir(parents=True)
        graph = catalog_graph(catalog)
        (frozen / "catalog.json").write_text(json.dumps(graph), encoding="utf-8")
        (pages / "docs").mkdir(parents=True)
        (pages / "docs/hello.html").write_text("<p>Body.</p>", encoding="utf-8")
        (ast_dir / "docs").mkdir(parents=True)
        (ast_dir / "docs/hello.json").write_text(node.ast_json, encoding="utf-8")

        cold = DocCatalog.from_frozen(frozen, content_root=content, lazy_html=True)
        cold_node = cold.get_by_slug("docs/hello")
        assert cold_node is not None
        assert cold_node.ast_json == node.ast_json
        assert "docs/hello" in cold._ast_documents
