"""Cross-surface retrieval identity, metadata, and safety contracts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from chirp.testing import TestClient

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.export import catalog_graph, search_json
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.query import query_catalog_graph
from furatena.catalog.registry import CatalogRegistry, MountConfig
from furatena.catalog.retrieval_dataset import load_known_answer_dataset
from furatena.catalog.semantic import hybrid_search, semantic_index_json

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def docs() -> DocsApp:
    return DocsApp.from_paths(
        REPO / "app" / "docs.yaml",
        repo_root=REPO,
        autodoc_config=REPO / "config" / "autodoc.yaml",
        autodoc=True,
    )


def test_browser_dcp_sidecars_and_mcp_share_retrieval_contract(docs: DocsApp) -> None:
    dataset = load_known_answer_dataset()
    case = next(item for item in dataset.cases if item.id == "compare-installation-across-surfaces")
    target = case.targets[0]
    expected_filters = {
        "mount": "furatena",
        "edition": "latest",
        "tag": None,
        "url_prefix": "/docs/",
        "include_private": False,
    }

    browser = hybrid_search(
        docs.catalog,
        docs.embedding_index,
        case.query,
        mount="furatena",
        edition="latest",
        url_prefix="/docs/",
        include_private=False,
    )
    mcp_result = FuraMCPServer(docs).call_tool(
        "semantic_search",
        {
            "query": case.query,
            "mount": "furatena",
            "edition": "latest",
            "url_prefix": "/docs/",
        },
    )
    mcp = mcp_result["structuredContent"]
    dcp = catalog_graph(docs.catalog)
    dcp_query = query_catalog_graph(docs.catalog, mount="furatena")
    search_sidecar = search_json(docs.catalog)
    semantic_sidecar = semantic_index_json(docs.catalog, docs.embedding_index)

    browser_ids = [hit.node.node_id for hit in browser.hits]
    mcp_ids = [str(item["node_id"]) for item in mcp["results"]]
    assert browser_ids == mcp_ids
    assert target.node_id in browser_ids[:3]
    assert browser.ranking == mcp["ranking"] == "keyword_guarded"
    assert mcp["filters"] == expected_filters
    assert dcp_query["query"]["mount"] == "furatena"

    dcp_by_id = {str(page["node_id"]): page for page in dcp["pages"]}
    search_by_id = {
        str(entry["node_id"]): entry for entry in search_sidecar["entries"]
    }
    semantic_chunks = {
        str(chunk["chunk_id"]): chunk for chunk in semantic_sidecar["chunks"]
    }
    mcp_by_id = {str(item["node_id"]): item for item in mcp["results"]}
    observed_chunks = 0
    for hit in browser.hits:
        node_id = hit.node.node_id
        dcp_page = dcp_by_id[node_id]
        search_entry = search_by_id[node_id]
        mcp_hit = mcp_by_id[node_id]
        assert hit.node.url == dcp_page["url"] == search_entry["url"] == mcp_hit["url"]
        assert hit.score == mcp_hit["score"]
        assert hit.node.mount == dcp_page["mount"] == search_entry["mount"] == mcp_hit["mount"]
        assert (
            hit.node.edition
            == dcp_page["edition"]
            == search_entry["edition"]
            == mcp_hit["edition"]
        )
        assert sorted(hit.node.tags) == dcp_page["tags"] == search_entry["tags"] == mcp_hit["tags"]
        assert dcp_page["provenance"] == search_entry["provenance"] == mcp_hit["provenance"]
        if hit.chunk_id is not None:
            observed_chunks += 1
            assert semantic_chunks[hit.chunk_id]["node_id"] == node_id
            assert mcp_hit["chunk_id"] == hit.chunk_id
    assert observed_chunks > 0

    client = TestClient(docs.create_app())

    async def _browser_and_json() -> tuple[str, dict[str, object]]:
        browser_response = await client.get(f"/search?{urlencode({'q': case.query})}")
        semantic_response = await client.get(
            f"/search/semantic?{urlencode({'q': case.query, 'mount': 'furatena', 'edition': 'latest', 'url_prefix': '/docs/'})}"
        )
        return browser_response.text, json.loads(semantic_response.text.split("<script", 1)[0])

    browser_html, semantic_json = asyncio.run(_browser_and_json())
    assert f'href="{target.url}"' in browser_html
    assert [item["node_id"] for item in semantic_json["results"]] == browser_ids
    assert semantic_json["filters"] == expected_filters


def test_private_and_archived_nodes_are_consistent_across_all_surfaces(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    (content / "public.md").write_text(
        "---\ntitle: Public recovery guide\n---\n\npublic recovery overview\n",
        encoding="utf-8",
    )
    (content / "private.md").write_text(
        "---\ntitle: Private recovery guide\nvisibility: private\n---\n\nquartz-violet private sequence\n",
        encoding="utf-8",
    )
    (content / "archived.md").write_text(
        "---\ntitle: Archived recovery guide\narchived_at: 2025-01-01T00:00:00Z\n---\n\nember-slate retired sequence\n",
        encoding="utf-8",
    )
    catalog = CatalogRegistry(
        (MountConfig(id="docs", label="Docs", content_root=content, default=True),),
        repo_root=tmp_path,
        app_root=tmp_path,
        include_private=True,
        autodoc=False,
    )
    index = EmbeddingIndex.from_nodes(list(catalog.nodes))
    app = SimpleNamespace(catalog=catalog, embedding_index=index)

    for query, slug in (
        ("quartz-violet private sequence", "private"),
        ("ember-slate retired sequence", "archived"),
    ):
        target = catalog.get_by_slug(slug)
        assert target is not None
        public = _surface_node_ids(app, query, include_private=False)
        trusted = _surface_node_ids(app, query, include_private=True)
        assert all(target.node_id not in node_ids for node_ids in public.values())
        assert all(target.node_id in node_ids for node_ids in trusted.values())


def _surface_node_ids(
    app: SimpleNamespace,
    query: str,
    *,
    include_private: bool,
) -> dict[str, set[str]]:
    catalog = app.catalog
    index = app.embedding_index
    browser = hybrid_search(
        catalog,
        index,
        query,
        include_private=include_private,
    )
    dcp = catalog_graph(catalog, include_private=include_private)
    search_sidecar = search_json(catalog, include_private=include_private)
    semantic_sidecar = semantic_index_json(
        catalog,
        index,
        include_private=include_private,
    )
    mcp = FuraMCPServer(app, include_private=include_private).call_tool(
        "semantic_search",
        {"query": query},
    )["structuredContent"]
    return {
        "browser": {hit.node.node_id for hit in browser.hits},
        "dcp": {str(page["node_id"]) for page in dcp["pages"]},
        "search_sidecar": {
            str(entry["node_id"]) for entry in search_sidecar["entries"]
        },
        "semantic_sidecar": {
            str(chunk["node_id"]) for chunk in semantic_sidecar["chunks"]
        },
        "mcp": {str(item["node_id"]) for item in mcp["results"]},
    }
