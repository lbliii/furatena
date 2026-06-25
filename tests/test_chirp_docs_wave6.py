"""Wave 6/7 tests for the app catalog."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
CONTENT_ROOT = REPO / "content" / "chirp"
AUTODOC_CONFIG = REPO / "config" / "autodoc.yaml"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog import DocCatalog
from furatena.catalog.autodoc import generate_autodoc_nodes
from furatena.catalog.export import catalog_graph, search_json, tools_manifest
from furatena.catalog.seo import canonical_url, docs_base_url, json_ld_article
from furatena.catalog.versions import infer_release_channels, node_matches_channel


@pytest.fixture(scope="module")
def catalog() -> DocCatalog:
    return DocCatalog(
        CONTENT_ROOT,
        autodoc=False,
        autodoc_config=None,
    )


class TestSearchJson:
    def test_search_json_shape(self, catalog: DocCatalog) -> None:
        payload = search_json(catalog, base_url="https://docs.example.com")
        assert payload["version"] == 1
        assert payload["base_url"] == "https://docs.example.com"
        assert payload["page_count"] > 0
        entry = payload["entries"][0]
        assert entry["url"].startswith("https://docs.example.com")
        assert "title" in entry
        assert "snippet" in entry
        assert "section" in entry


class TestToolsManifest:
    def test_tools_manifest_lists_catalog_tools(self, catalog: DocCatalog) -> None:
        manifest = tools_manifest(catalog, base_url="https://docs.example.com")
        names = {tool["name"] for tool in manifest["tools"]}
        assert names == {"search_docs", "get_doc", "list_docs", "semantic_search", "retrieve_doc"}
        assert manifest["catalog_url"].endswith("/catalog.json")


class TestAutodoc:
    def test_python_autodoc_generates_module_nodes(self) -> None:
        nodes = generate_autodoc_nodes(AUTODOC_CONFIG, repo_root=REPO)
        assert len(nodes) > 10
        assert any(node.slug == "api" for node in nodes)
        assert any(node.meta.get("source") == "autodoc" for node in nodes)

    def test_docstrings_with_angle_brackets_render_as_html(self) -> None:
        nodes = generate_autodoc_nodes(AUTODOC_CONFIG, repo_root=REPO)
        node = next(n for n in nodes if n.slug == "api/chirp/templating/oob_registry")
        assert "&lt;pre&gt;" not in node.body_html
        assert "<pre>" in node.body_html


class TestVersionChannels:
    def test_release_channels_include_latest(self) -> None:
        channels = infer_release_channels(CONTENT_ROOT)
        assert channels[0].id == "latest"

    def test_node_matches_latest_channel(self) -> None:
        assert node_matches_channel(None, "latest")
        assert not node_matches_channel("0.8.0", "latest")


class TestSeo:
    def test_json_ld_article(self, catalog: DocCatalog) -> None:
        node = catalog.get("/")
        assert node is not None
        payload = json_ld_article(node=node, page_url="https://docs.example.com/")
        assert payload["@type"] == "TechArticle"
        assert payload["headline"] == node.title

    def test_canonical_url(self) -> None:
        assert canonical_url("https://docs.example.com", "/docs/about/") == (
            "https://docs.example.com/docs/about/"
        )


class TestIncrementalReindex:
    def test_reindex_single_dirty_file(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        first = docs / "first.md"
        second = docs / "second.md"
        first.write_text("---\ntitle: First\n---\nFirst body.\n", encoding="utf-8")
        second.write_text("---\ntitle: Second\n---\nSecond body.\n", encoding="utf-8")

        cat = DocCatalog(content, auto_reload=True, autodoc=False, autodoc_config=None)
        assert len(cat.nodes) == 2

        first.write_text("---\ntitle: First Updated\n---\nChanged.\n", encoding="utf-8")
        cat.refresh_if_stale()
        updated = cat.get_by_slug("docs/first")
        assert updated is not None
        assert updated.title == "First Updated"


class TestCatalogGraph:
    def test_catalog_graph_includes_channel(self, catalog: DocCatalog) -> None:
        graph = catalog_graph(catalog)
        assert graph["channel"] == catalog.active_channel
        assert graph["schema_version"] == 3
        assert graph["page_count"] == len(graph["pages"])
        assert graph["edges"]
