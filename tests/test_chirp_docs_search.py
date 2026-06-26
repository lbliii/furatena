"""Hybrid catalog-native search (no Lunr)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from chirp.testing import TestClient

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.search_experience import (
    build_search_url,
    group_search_hits,
    highlight_search_terms,
    hybrid_search_hits,
    search_hit_url,
)
from furatena.catalog.semantic import hybrid_search


@pytest.fixture(scope="module")
def registry() -> CatalogRegistry:
    return CatalogRegistry.from_config(
        APP_ROOT / "mounts.yaml",
        repo_root=REPO,
        autodoc=False,
        autodoc_config=None,
    )


@pytest.fixture(scope="module")
def embedding_index(registry: CatalogRegistry) -> EmbeddingIndex:
    frozen_semantic = APP_ROOT / "frozen" / "semantic.json"
    loaded = EmbeddingIndex.load(frozen_semantic)
    if loaded is not None:
        return loaded
    return EmbeddingIndex.from_nodes(list(registry.nodes), documents=registry.ast_documents())


@pytest.fixture(scope="module")
def docs_client() -> TestClient:
    docs = DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
    )
    return TestClient(docs.create_app())


class TestHybridSearchCore:
    def test_hybrid_search_returns_hits(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        result = hybrid_search(registry, embedding_index, "htmx", limit=5)
        hits = result.hits
        assert hits
        assert all(hit.node.title for hit in hits)

    def test_search_hit_url_uses_chunk_anchor(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        result = hybrid_search(registry, embedding_index, "htmx navigation", limit=12)
        hits = result.hits
        anchored = [hit for hit in hits if hit.chunk_id and "#" in search_hit_url(hit, embedding_index)]
        if not anchored:
            # Fall back to keyword-heavy query when semantic rank lacks chunk anchors.
            result = hybrid_search(registry, embedding_index, "hypermedia", limit=12)
            hits = result.hits
            anchored = [hit for hit in hits if hit.chunk_id and "#" in search_hit_url(hit, embedding_index)]
        assert anchored, "expected at least one chunk-aware deep link"

    def test_section_filter(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        sections = sorted({node.section for node in registry.doc_nodes() if node.section})
        assert sections
        section = sections[0]
        hits = hybrid_search_hits(
            registry,
            embedding_index,
            "chirp",
            limit=8,
            section=section,
        ).hits
        assert hits
        assert all(hit.node.section == section for hit in hits)

    def test_group_search_hits_preserves_section_order(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        result = hybrid_search(registry, embedding_index, "chirp", limit=12)
        hits = result.hits
        groups = group_search_hits(hits)
        assert groups
        assert sum(len(group.hits) for group in groups) == len(hits)

    def test_highlight_search_terms_marks_matches(self) -> None:
        highlighted = highlight_search_terms("HTMX hypermedia navigation", "htmx")
        assert "<mark>HTMX</mark>" in highlighted

    def test_global_search_ignores_section_scope(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        sections = sorted({node.section for node in registry.doc_nodes() if node.section})
        assert sections
        scoped = hybrid_search_hits(
            registry,
            embedding_index,
            "chirp",
            limit=12,
            section=sections[0],
        ).hits
        global_hits = hybrid_search_hits(
            registry,
            embedding_index,
            "chirp",
            limit=12,
            section=sections[0],
            global_search=True,
        ).hits
        assert len(global_hits) >= len(scoped)

    def test_build_search_url_includes_global_flag(self) -> None:
        assert build_search_url(query="htmx", section="Get Started", global_search=True) == (
            "/search?section=Get+Started&q=htmx&global=1"
        )

    def test_build_search_url_includes_mount_and_tag(self) -> None:
        assert build_search_url(mount="chirp", tag="htmx", query="streaming") == (
            "/search?mount=chirp&tag=htmx&q=streaming"
        )

    def test_rail_mark_precomputed_on_scope_items(
        self,
        registry: CatalogRegistry,
    ) -> None:
        from furatena.catalog.search_experience import rail_mark, search_scope_rail_items

        items = search_scope_rail_items(registry, active_section="", query="htmx")
        assert items
        assert items[0]["mark"] == rail_mark("All sections")
        assert all("mark" in item for item in items)

    def test_search_mount_rail_items_include_nav_attrs(
        self,
        registry: CatalogRegistry,
    ) -> None:
        from furatena.catalog.search_experience import search_mount_rail_items

        items = search_mount_rail_items(registry, active_mount="", query="htmx")
        assert items
        assert items[0]["mark"] == "ALL"
        assert items[0]["nav_attrs"]["hx-target"] == "#search-results-panel"

    def test_build_search_page_cards_groups_by_node(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        from furatena.catalog.search_experience import build_search_page_cards

        result = hybrid_search_hits(registry, embedding_index, "htmx", limit=8)
        cards = build_search_page_cards(result.hits, result.semantic_hits, "htmx", index=embedding_index)
        assert cards
        node_ids = [card.node.node_id for card in cards]
        assert len(node_ids) == len(set(node_ids))

    def test_build_search_page_cards_does_not_rescan_index(
        self,
        registry: CatalogRegistry,
        embedding_index: EmbeddingIndex,
    ) -> None:
        from unittest.mock import MagicMock

        from furatena.catalog.search_experience import build_search_page_cards

        result = hybrid_search_hits(registry, embedding_index, "htmx", limit=8)
        mock_index = MagicMock(wraps=embedding_index)
        build_search_page_cards(
            result.hits,
            result.semantic_hits,
            "htmx",
            index=mock_index,
        )
        mock_index.search.assert_not_called()

    def test_search_scope_rail_items_include_nav_attrs(
        self,
        registry: CatalogRegistry,
    ) -> None:
        from furatena.catalog.search_experience import search_scope_rail_items

        items = search_scope_rail_items(registry, active_section="", query="htmx")
        assert items
        assert items[0]["nav_attrs"]["hx-target"] == "#search-results-panel"
        assert items[0]["nav_attrs"]["hx-disinherit"] == "hx-select hx-target hx-swap"


class TestHybridSearchRoutes:
    def test_search_page_uses_catalog_shell(self, docs_client: TestClient) -> None:
        async def _fetch() -> str:
            resp = await docs_client.get("/search?q=htmx")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-docs-layout" in html
        assert "search-workspace" in html
        assert "search-scope-panel" in html
        assert "search-discovery" in html
        assert "search-workspace-panel" in html or "search-workspace__panel" in html
        assert "search-product-card" in html or "search-product-grid" in html

    def test_search_page_empty_state(self, docs_client: TestClient) -> None:
        async def _fetch() -> str:
            resp = await docs_client.get("/search")
            return resp.text

        html = asyncio.run(_fetch())
        assert "search-product-grid" in html or "search-results-browse" in html

    def test_search_suggest_returns_fragment(self, docs_client: TestClient) -> None:
        async def _fetch() -> str:
            resp = await docs_client.get(
                "/search/suggest?q=installation",
                headers={"HX-Request": "true"},
            )
            return resp.text

        html = asyncio.run(_fetch())
        assert "search-modal__result-link" in html
        assert "search-modal__view-all" in html

    def test_search_hx_partial_returns_results_panel(self, docs_client: TestClient) -> None:
        async def _fetch() -> str:
            resp = await docs_client.get(
                "/search?q=installation",
                headers={"HX-Request": "true", "HX-Target": "search-results-panel"},
            )
            return resp.text

        html = asyncio.run(_fetch())
        assert "search-product-card" in html or "search-result" in html or "search-results-group" in html
        assert "search-scope-rail-panel" in html
        assert "search-workspace-panel" in html or "search-workspace__panel" in html
        assert "search-discovery-panel" in html
        assert "search-workspace__header" not in html

    def test_search_scope_rail_uses_partial_nav_attrs(self, docs_client: TestClient) -> None:
        async def _fetch() -> str:
            resp = await docs_client.get("/search")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'hx-disinherit="hx-select hx-target hx-swap"' in html
        assert 'hx-target="#search-results-panel"' in html
        assert "search-shell-scope-rail__item" in html

    def test_search_scope_partial_oob(self, docs_client: TestClient) -> None:
        import re

        async def _run() -> str:
            page = await docs_client.get("/search")
            match = re.search(r'href="/search\?section=([^"]+)"', page.text)
            if not match:
                pytest.skip("no section scope links in catalog")
            section = match.group(1)
            resp = await docs_client.get(
                f"/search?section={section}",
                headers={"HX-Request": "true", "HX-Target": "search-results-panel"},
            )
            return resp.text

        html = asyncio.run(_run())
        assert "search-scope-rail-panel" in html
        assert "hx-swap-oob" in html

    def test_search_semantic_json_still_hybrid(self, docs_client: TestClient) -> None:
        async def _fetch() -> str:
            resp = await docs_client.get("/search/semantic?q=htmx")
            return resp.text

        body = asyncio.run(_fetch())
        assert '"mode": "hybrid"' in body
        assert '"count":' in body
