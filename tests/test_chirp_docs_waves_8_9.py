"""Wave 8/9 tests for federated graph platform and semantic retrieval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
CONTENT_ROOT = REPO / "content" / "chirp"
MOUNTS_CONFIG = APP_ROOT / "mounts.yaml"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog import CatalogRegistry, DocCatalog
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.export import catalog_graph, tools_manifest
from furatena.catalog.graph_schema import EdgeKind, make_node_id
from furatena.catalog.semantic import hybrid_search, retrieve_node


@pytest.fixture(scope="module")
def registry() -> CatalogRegistry:
    return CatalogRegistry.from_config(
        MOUNTS_CONFIG,
        repo_root=REPO,
        autodoc=False,
        autodoc_config=None,
    )


@pytest.fixture(scope="module")
def embedding_index(registry: CatalogRegistry) -> EmbeddingIndex:
    return EmbeddingIndex.from_nodes(list(registry.nodes))


class TestWave8GraphPlatform:
    def test_node_ids_include_mount_and_edition(self, registry: CatalogRegistry) -> None:
        node = registry.get("/chirp/docs/get-started/installation/")
        assert node is not None
        assert node.mount == "chirp"
        assert node.node_id == make_node_id("chirp", registry.active_channel, node.slug)

    def test_shared_mount_resolves(self, registry: CatalogRegistry) -> None:
        node = registry.get("/shared/")
        assert node is not None
        assert node.mount == "shared"

    def test_catalog_graph_v3_has_edges_and_namespaces(self, registry: CatalogRegistry) -> None:
        graph = catalog_graph(registry)
        assert graph["schema_version"] == 3
        assert graph["edges"]
        assert graph["namespaces"]
        kinds = {edge["kind"] for edge in graph["edges"]}
        assert EdgeKind.LINK.value in kinds
        assert EdgeKind.PARENT.value in kinds

    def test_backlinks_scoped_to_mount(self, registry: CatalogRegistry) -> None:
        chirp_node = registry.get("/chirp/docs/about/architecture/")
        assert chirp_node is not None
        for ref in registry.backlinks_for(chirp_node):
            target = registry.get(ref["href"])
            assert target is not None
            assert target.mount == "chirp"

    def test_portal_mounts_lists_shards(self, registry: CatalogRegistry) -> None:
        mounts = registry.portal_mounts()
        ids = {item["id"] for item in mounts}
        assert ids == {"chirp", "furatena", "shared"}

    def test_default_docs_route_does_not_fall_through_to_other_mount(self, registry: CatalogRegistry) -> None:
        """A /docs/... URL must not serve a page that only exists under another mount."""
        node = registry.get("/docs/reference/api/")
        assert node is None
        assert registry.get_by_slug("docs/reference/api") is None
        chirp_node = registry.get_by_slug("docs/reference/api", mount="chirp")
        assert chirp_node is not None
        assert chirp_node.mount == "chirp"

    def test_resolve_link_prefers_source_mount(self, registry: CatalogRegistry) -> None:
        node = registry.resolve_link("docs/reference/api", source_mount="chirp")
        assert node is not None
        assert node.mount == "chirp"
        assert registry.resolve_link("docs/reference/api", source_mount="furatena") is None
        assert registry.resolve_link("docs/reference", source_mount="furatena") is not None

    def test_get_by_slug_prefers_default_mount_for_duplicate_slugs(self, registry: CatalogRegistry) -> None:
        node = registry.get_by_slug("docs/reference")
        assert node is not None
        assert node.mount == registry.default_mount.id
        assert node.url == "/docs/reference/"


@pytest.fixture(scope="module")
def federated_docs_app():
    from furatena.catalog.docs_app import DocsApp

    return DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
    )


class TestFederatedDocsRouting:
    def test_missing_default_docs_page_returns_not_found(self, federated_docs_app) -> None:
        import asyncio

        from chirp.testing import TestClient

        client = TestClient(federated_docs_app.create_app())

        async def _fetch(path: str) -> int:
            resp = await client.get(path)
            return resp.status

        status = asyncio.run(_fetch("/docs/reference/api/"))
        assert status == 404

    def test_resolve_page_from_path_scopes_to_request_url(self, federated_docs_app) -> None:
        from chirp.errors import NotFound

        match = federated_docs_app._resolve_page_from_path("/docs/reference/")
        assert match.node.mount == "furatena"
        assert match.node.url == "/docs/reference/"

        with pytest.raises(NotFound):
            federated_docs_app._resolve_page_from_path("/docs/reference/api/")


class TestWave8LazyFrozen:
    def test_lazy_frozen_loads_html_on_demand(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "hello.md"
        page.write_text("---\ntitle: Hello\n---\nHello world.\n", encoding="utf-8")

        live = DocCatalog(content, autodoc=False, autodoc_config=None)
        node = live.get_by_slug("docs/hello")
        assert node is not None

        frozen = tmp_path / "frozen"
        pages = frozen / "pages"
        pages.mkdir(parents=True)
        graph = catalog_graph(live)
        (frozen / "catalog.json").write_text(json.dumps(graph), encoding="utf-8")
        (pages / "docs").mkdir(parents=True)
        (pages / "docs/hello.html").write_text("<p>Hello world.</p>", encoding="utf-8")

        cold = DocCatalog.from_frozen(frozen, content_root=content, lazy_html=True)
        cold_node = cold.get_by_slug("docs/hello")
        assert cold_node is not None
        assert cold_node.body_html == ""
        assert cold.resolve_body_html(cold_node) == "<p>Hello world.</p>"


class TestWave9SemanticLayer:
    def test_semantic_search_finds_related_topic(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        result = hybrid_search(registry, embedding_index, "hypermedia fragment navigation", limit=5)
        hits = result.hits
        assert hits
        titles = " ".join(hit.node.title.lower() for hit in hits)
        bodies = " ".join(hit.snippet.lower() for hit in hits)
        assert "htmx" in titles or "htmx" in bodies or "hypermedia" in bodies

    def test_retrieve_node_returns_chunks_and_similar(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        node = registry.get("/docs/get-started/installation/")
        assert node is not None
        payload = retrieve_node(registry, embedding_index, node.node_id)
        assert payload is not None
        assert payload["node_id"] == node.node_id
        assert payload["chunks"]
        assert "backlinks" in payload

    def test_tools_manifest_includes_semantic_tools(self, registry: CatalogRegistry) -> None:
        manifest = tools_manifest(registry, base_url="https://docs.example.com")
        names = {tool["name"] for tool in manifest["tools"]}
        assert "semantic_search" in names
        assert "retrieve_doc" in names
