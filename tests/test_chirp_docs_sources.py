"""Tests for format-agnostic documentation source ingestion."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.export import catalog_graph
from furatena.catalog.loader import DocCatalog
from furatena.catalog.models import DocNode
from furatena.catalog.sources import FilesystemScanner, MountSourceConfig, get_content_adapter
from furatena.catalog.sources.registry import registered_formats
from furatena.catalog.sources.scanner import file_to_url


class TestMountSourceConfig:
    def test_registered_formats(self) -> None:
        formats = registered_formats()
        assert "html" in formats
        assert "docutils-rst" in formats
        assert "mdx" in formats

    def test_default_tracks_markdown(self) -> None:
        config = MountSourceConfig()
        assert ".md" in config.tracked_extensions()
        assert config.content_format_for(Path("docs/page.md")) == "patitas-markdown"

    def test_from_mount_dict(self) -> None:
        config = MountSourceConfig.from_mount_dict(
            {
                "extensions": [".md", ".html", ".rst", ".mdx"],
            }
        )
        assert config.content_format_for(Path("page.html")) == "html"
        assert config.content_format_for(Path("page.rst")) == "docutils-rst"
        assert config.content_format_for(Path("page.mdx")) == "mdx"
        assert "index.html" in config.index_files
        assert "index.rst" in config.index_files


class TestFilesystemScanner:
    def test_scan_indexes_markdown(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        page = docs / "hello.md"
        page.write_text(
            "---\ntitle: Hello\n---\n\n# Hello\n\n[Link](/docs/world/)\n",
            encoding="utf-8",
        )
        scanner = FilesystemScanner(MountSourceConfig())
        pages = scanner.scan(tmp_path)
        assert len(pages) == 1
        assert pages[0].slug == "docs/hello"
        assert pages[0].content_format == "patitas-markdown"
        assert "Hello" in pages[0].body

    def test_index_file_maps_to_section_url(self, tmp_path: Path) -> None:
        section = tmp_path / "docs" / "guide"
        section.mkdir(parents=True)
        (section / "_index.md").write_text("---\ntitle: Guide\n---\n\nBody\n", encoding="utf-8")
        url, slug = file_to_url(tmp_path, section / "_index.md")
        assert slug == "docs/guide"
        assert url == "/docs/guide/"


class TestPatitasMarkdownAdapter:
    def test_adapt_populates_content_ir_and_sections(self, tmp_path: Path) -> None:
        adapter = get_content_adapter("patitas-markdown")
        source_path = "docs/test.md"
        body = "# Title\n\nIntro.\n\n## Section\n\nDetails.\n"
        from furatena.catalog.context import NodeStub
        from furatena.catalog.sources.types import PageSource

        source = PageSource(
            path=tmp_path / source_path,
            content_format="patitas-markdown",
            meta={"title": "Title"},
            body=body,
            source_path=source_path,
            url="/docs/test/",
            slug="docs/test",
        )
        stubs = {
            "docs/test": NodeStub(
                slug="docs/test",
                url="/docs/test/",
                title="Title",
                description="",
                weight=100,
                page_type="doc",
            )
        }

        def render_markdown(*_args, **_kwargs) -> str:
            return ""

        adapted = adapter.adapt(
            source,
            stubs=stubs,
            render_markdown=render_markdown,
            get_backlinks=lambda _url: [],
            content_root=tmp_path,
        )
        assert adapted.content_ir is not None
        assert adapted.content_ir.headings
        assert adapted.body_html
        assert adapted.body_text
        assert adapted.sections
        assert any(section.heading == "Section" for section in adapted.sections)


class TestCatalogGraphV3:
    def test_export_includes_v3_fields(self) -> None:
        node = DocNode(
            url="/docs/test/",
            slug="docs/test",
            title="Test",
            description="Desc",
            layout="doc",
            weight=1,
            section="test",
            tags=frozenset({"alpha"}),
            body_md="# Test\n",
            body_html="<h1>Test</h1>",
            toc=(),
            source_path="docs/test.md",
            content_format="patitas-markdown",
            body_text="Test body",
            sections=(),
        )

        class _Catalog:
            active_channel = "latest"
            nodes = (node,)

            def doc_nodes(self):
                return [node]

            def backlinks_for(self, _node):
                return []

            def graph_edges(self):
                return []

            def namespaces(self):
                return []

        payload = catalog_graph(_Catalog(), schema_version=3)
        assert payload["schema_version"] == 3
        page = payload["pages"][0]
        assert page["content_format"] == "patitas-markdown"
        assert page["body_source"] == "# Test"
        assert page["body_text"] == "Test body"
        assert page["body_md"] == "# Test"

    def test_v2_compat_export(self) -> None:
        node = DocNode(
            url="/docs/test/",
            slug="docs/test",
            title="Test",
            description="",
            layout="doc",
            weight=1,
            section="test",
            tags=frozenset(),
            body_md="Body",
            body_html="<p>Body</p>",
            toc=(),
            source_path="docs/test.md",
        )

        class _Catalog:
            active_channel = "latest"
            nodes = (node,)

            def doc_nodes(self):
                return [node]

            def backlinks_for(self, _node):
                return []

            def graph_edges(self):
                return []

            def namespaces(self):
                return []

        payload = catalog_graph(_Catalog(), schema_version=2)
        assert payload["schema_version"] == 2
        page = payload["pages"][0]
        assert "body_md" in page
        assert "content_format" not in page

    def test_live_catalog_indexes_site_content(self) -> None:
        content_root = REPO / "content" / "chirp"
        if not content_root.is_dir():
            return
        catalog = DocCatalog(content_root, autodoc=False)
        assert catalog.nodes
        sample = catalog.doc_nodes()[0]
        assert sample.content_format == "patitas-markdown"
        assert sample.body_text or sample.body_html


class TestHtmlAdapter:
    def test_extracts_headings_and_links(self) -> None:
        adapter = get_content_adapter("html")
        html = """
        <h1>Guide</h1>
        <p>See <a href="/docs/other/">Other</a>.</p>
        <h2>Details</h2>
        <div data-component="callout" data-tone="info">Note</div>
        """
        _doc, content_ir = adapter.parse(html)
        assert content_ir is not None
        assert any(h.text == "Guide" for h in content_ir.headings)
        assert any(link.href == "/docs/other/" for link in content_ir.links)
        assert any(ext.name == "callout" for ext in content_ir.directives)

    def test_catalog_indexes_html_file(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "legacy.html").write_text(
            "---\ntitle: Legacy\n---\n<h1>Legacy</h1><p>Still valid.</p>",
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".html"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        node = catalog.get_by_slug("docs/legacy")
        assert node is not None
        assert node.content_format == "html"
        assert "Legacy" in node.body_html


class TestRstAdapter:
    def test_extracts_rst_structure(self) -> None:
        pytest = __import__("pytest")
        docutils = pytest.importorskip("docutils")
        _ = docutils
        adapter = get_content_adapter("docutils-rst")
        source = (
            "Title\n"
            "=====\n\n"
            "`Other </docs/other/>`_\n\n"
            "Section\n"
            "-------\n\n"
            "Body text.\n"
        )
        _doc, content_ir = adapter.parse(source)
        assert content_ir is not None
        assert content_ir.headings
        assert content_ir.links

    def test_catalog_indexes_rst_file(self, tmp_path: Path) -> None:
        pytest = __import__("pytest")
        pytest.importorskip("docutils")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.rst").write_text(
            "---\ntitle: Guide\n---\n\nGuide\n=====\n\nHello RST.\n",
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".rst"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        node = catalog.get_by_slug("docs/guide")
        assert node is not None
        assert node.content_format == "docutils-rst"
        assert "Hello RST" in node.body_text


class TestMdxAdapter:
    def test_lowers_jsx_to_markdown_extensions(self) -> None:
        from furatena.catalog.sources.adapters.mdx import mdx_to_markdown

        source = "# Title\n\n<Callout tone=\"info\">Hello</Callout>\n"
        lowered = mdx_to_markdown(source)
        assert ":::callout" in lowered
        assert "Hello" in lowered

    def test_catalog_indexes_mdx_file(self, tmp_path: Path) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "page.mdx").write_text(
            "---\ntitle: MDX Page\n---\n\n# MDX Page\n\nPlain text.\n",
            encoding="utf-8",
        )
        config = MountSourceConfig.from_mount_dict({"extensions": [".mdx"]})
        catalog = DocCatalog(tmp_path, autodoc=False, source_config=config)
        node = catalog.get_by_slug("docs/page")
        assert node is not None
        assert node.content_format == "mdx"
        assert node.body_html
