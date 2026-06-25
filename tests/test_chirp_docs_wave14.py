"""Tests for Wave 14 — AST-native graph and structure indexes."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.content_ir import collect_node_link_urls, ContentDirective, ContentIR
from furatena.catalog.export import catalog_graph
from furatena.catalog.graph import extract_page_links
from furatena.catalog.loader import DocCatalog
from furatena.catalog.models import DocNode
from furatena.catalog.query import query_catalog
from furatena.catalog.structure_index import build_structure_index


def _node(
    *,
    slug: str,
    url: str,
    body_md: str = "",
    content_ir: ContentIR | None = None,
    mount: str = "chirp",
) -> DocNode:
    return DocNode(
        url=url,
        slug=slug,
        title=slug.rsplit("/", 1)[-1],
        description="",
        layout="doc",
        weight=0,
        section="docs",
        tags=frozenset(),
        body_md=body_md,
        body_html="",
        toc=(),
        source_path=f"{slug}.md",
        meta={},
        mount=mount,
        edition="latest",
        section_root=False,
        content_format="patitas-markdown",
        body_text="",
        sections=(),
        content_ir=content_ir,
    )


class TestAstLinkExtraction:
    def test_child_cards_adds_child_urls(self) -> None:
        parent = _node(
            slug="docs/guide",
            url="/docs/guide/",
            content_ir=ContentIR(
                directives=(ContentDirective(name="child-cards", options={}),),
            ),
        )
        child = _node(slug="docs/guide/intro", url="/docs/guide/intro/")
        catalog = DocCatalog.__new__(DocCatalog)
        catalog._nodes = [parent, child]

        urls = collect_node_link_urls(parent, catalog=catalog)
        assert "/docs/guide/intro/" in urls

    def test_card_link_option_is_collected(self) -> None:
        node = _node(
            slug="docs/page",
            url="/docs/page/",
            content_ir=ContentIR(
                directives=(
                    ContentDirective(
                        name="card",
                        options={"link": "/docs/other/"},
                    ),
                ),
            ),
        )
        urls = extract_page_links(node)
        assert "/docs/other/" in urls


class TestStructureIndex:
    def test_build_structure_index_from_content_ir(self) -> None:
        from furatena.catalog.render import DocsRenderer

        _document, content_ir = DocsRenderer().parse("# Title\n\n:::{note}\nBody\n:::\n")
        catalog = type(
            "CatalogStub",
            (),
            {
                "active_channel": "latest",
                "nodes": (
                    _node(
                        slug="docs/page",
                        url="/docs/page/",
                        content_ir=content_ir,
                    ),
                ),
            },
        )()
        index = build_structure_index(catalog)
        assert index["heading_count"] >= 1
        assert "note" in index["directive_names"]

    def test_catalog_graph_includes_structure_summary(self, tmp_path: Path) -> None:
        content = tmp_path / "docs"
        content.mkdir()
        (content / "page.md").write_text("---\ntitle: Page\n---\n\n# Hi\n", encoding="utf-8")
        from furatena.catalog.render import DocsRenderer

        _document, content_ir = DocsRenderer().parse("# Hi\n")
        catalog = type(
            "CatalogStub",
            (),
            {
                "active_channel": "latest",
                "nodes": (
                    _node(slug="docs/page", url="/docs/page/", content_ir=content_ir),
                ),
                "backlinks_for": lambda _self, _node: [],
                "graph_edges": lambda _self: [],
                "namespaces": lambda _self: [],
            },
        )()
        payload = catalog_graph(catalog)
        assert "structure_index" in payload
        assert payload["structure_index"]["heading_count"] >= 1


class TestQueryFilters:
    def test_mount_filter(self, tmp_path: Path) -> None:
        chirp_root = tmp_path / "chirp"
        shared_root = tmp_path / "shared"
        chirp_docs = chirp_root / "docs"
        shared_docs = shared_root / "reference"
        chirp_docs.mkdir(parents=True)
        shared_docs.mkdir(parents=True)
        (chirp_docs / "one.md").write_text("---\ntitle: One\n---\n\n# One\n", encoding="utf-8")
        (shared_docs / "two.md").write_text("---\ntitle: Two\n---\n\n# Two\n", encoding="utf-8")

        from furatena.catalog.registry import CatalogRegistry, MountConfig

        registry = CatalogRegistry(
            (
                MountConfig(id="chirp", label="Chirp", content_root=chirp_root, default=True),
                MountConfig(
                    id="shared",
                    label="Shared",
                    content_root=shared_root,
                    url_prefix="/shared",
                ),
            ),
            repo_root=tmp_path,
            autodoc=False,
        )
        shared_only = query_catalog(registry, heading="two", mount="shared")
        assert len(shared_only) == 1
        assert shared_only[0]["mount"] == "shared"
