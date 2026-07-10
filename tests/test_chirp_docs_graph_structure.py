"""AST-native graph edges, structure indexes, and query filters."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.content_ir import ContentDirective, ContentIR, collect_node_link_urls
from furatena.catalog.export import catalog_graph
from furatena.catalog.graph import extract_page_links
from furatena.catalog.graph_schema import EdgeKind, build_graph_edges, edge_record
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

    def test_semantic_edges_from_front_matter(self) -> None:
        node = replace(
            _node(slug="docs/page", url="/docs/page/"),
            meta={
                "owner": "docs-platform",
                "implements": "api:get-user",
                "generated_from": "specs/openapi.yaml",
                "requires": "/docs/auth/",
                "available_in": "latest",
                "validates": "tests/api/test_users.py",
                "api_tags": ["Users", "Admin"],
                "api_schemas": ["User", "Error"],
                "api_request_bodies": "CreateUser",
                "api_responses": ["200", "default"],
                "api_examples": "create-user",
                "api_auth": "oauth2",
                "api_environments": ["prod", "sandbox"],
            },
        )
        target = _node(slug="docs/auth", url="/docs/auth/")

        class _Catalog:
            nodes = (node, target)

            def get_by_slug(self, slug: str):
                return {"docs/page": node, "docs/auth": target}.get(slug)

            def prev_next(self, _node):
                return (None, None)

        records = [edge_record(edge) for edge in build_graph_edges(_Catalog())]
        by_kind = {(edge["kind"], edge["target"]) for edge in records}
        assert (EdgeKind.OWNED_BY.value, "owner:docs-platform") in by_kind
        assert (EdgeKind.GENERATED_FROM.value, "source:docs/page.md") in by_kind
        assert (EdgeKind.IMPLEMENTS.value, "api:get-user") in by_kind
        assert (EdgeKind.GENERATED_FROM.value, "source:specs/openapi.yaml") in by_kind
        assert (EdgeKind.REQUIRES.value, target.node_id) in by_kind
        assert (EdgeKind.AVAILABLE_IN.value, "release:latest") in by_kind
        assert (EdgeKind.VALIDATES.value, "source:tests/api/test_users.py") in by_kind
        assert (EdgeKind.API_TAG.value, "api-tag:Users") in by_kind
        assert (EdgeKind.API_TAG.value, "api-tag:Admin") in by_kind
        assert (EdgeKind.API_SCHEMA.value, "schema:User") in by_kind
        assert (EdgeKind.API_SCHEMA.value, "schema:Error") in by_kind
        assert (EdgeKind.API_REQUEST_BODY.value, "request-body:CreateUser") in by_kind
        assert (EdgeKind.API_RESPONSE.value, "response:200") in by_kind
        assert (EdgeKind.API_RESPONSE.value, "response:default") in by_kind
        assert (EdgeKind.API_EXAMPLE.value, "example:create-user") in by_kind
        assert (EdgeKind.API_AUTH.value, "auth:oauth2") in by_kind
        assert (EdgeKind.API_ENVIRONMENT.value, "environment:prod") in by_kind
        assert (EdgeKind.API_ENVIRONMENT.value, "environment:sandbox") in by_kind

    def test_catalog_graph_preserves_api_external_edges(self) -> None:
        node = replace(
            _node(slug="docs/api", url="/docs/api/"),
            meta={
                "api_schemas": "User",
                "api_examples": "create-user",
                "api_auth": "oauth2",
                "api_environments": "prod",
            },
        )

        class _Catalog:
            active_channel = "latest"
            nodes = (node,)

            def doc_nodes(self):
                return [node]

            def backlinks_for(self, _node):
                return []

            def get_by_slug(self, slug: str):
                return {"docs/api": node}.get(slug)

            def prev_next(self, _node):
                return (None, None)

            def graph_edges(self):
                return [edge_record(edge) for edge in build_graph_edges(self)]

            def namespaces(self):
                return []

        payload = catalog_graph(_Catalog())
        by_kind = {(edge["kind"], edge["target"]) for edge in payload["edges"]}
        assert (EdgeKind.GENERATED_FROM.value, "source:docs/api.md") in by_kind
        assert (EdgeKind.API_SCHEMA.value, "schema:User") in by_kind
        assert (EdgeKind.API_EXAMPLE.value, "example:create-user") in by_kind
        assert (EdgeKind.API_AUTH.value, "auth:oauth2") in by_kind
        assert (EdgeKind.API_ENVIRONMENT.value, "environment:prod") in by_kind
        graph_nodes = {(node["kind"], node["id"], node["label"]) for node in payload["graph_nodes"]}
        assert ("source_file", "source:docs/api.md", "docs/api.md") in graph_nodes
        assert ("api_schema", "schema:User", "User") in graph_nodes
        assert ("api_example", "example:create-user", "create-user") in graph_nodes
        assert ("api_auth", "auth:oauth2", "oauth2") in graph_nodes
        assert ("api_environment", "environment:prod", "prod") in graph_nodes

    def test_graph_nodes_include_release_targets(self) -> None:
        node = replace(
            _node(slug="docs/release-note", url="/docs/release-note/"),
            meta={"available_in": "2026.06"},
        )

        class _Catalog:
            active_channel = "latest"
            nodes = (node,)

            def doc_nodes(self):
                return [node]

            def backlinks_for(self, _node):
                return []

            def get_by_slug(self, slug: str):
                return {"docs/release-note": node}.get(slug)

            def prev_next(self, _node):
                return (None, None)

            def graph_edges(self):
                return [edge_record(edge) for edge in build_graph_edges(self)]

            def namespaces(self):
                return []

        payload = catalog_graph(_Catalog())
        graph_nodes = {(node["kind"], node["id"], node["label"]) for node in payload["graph_nodes"]}
        assert ("release", "release:2026.06", "2026.06") in graph_nodes


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
                "nodes": (_node(slug="docs/page", url="/docs/page/", content_ir=content_ir),),
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
