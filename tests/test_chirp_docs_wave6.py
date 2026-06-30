"""Wave 6/7 tests for the app catalog."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
CONTENT_ROOT = REPO / "content" / "chirp"
AUTODOC_CONFIG = REPO / "config" / "autodoc.yaml"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog import DocCatalog
from furatena.catalog.autodoc import generate_autodoc_nodes
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.export import catalog_graph, search_json, tools_manifest
from furatena.catalog.graph_schema import EdgeKind, build_graph_edges, edge_record
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.models import DocNode
from furatena.catalog.search import search_nodes
from furatena.catalog.semantic import retrieve_node
from furatena.catalog.seo import canonical_url, json_ld_article
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


class TestSearchRanking:
    def test_search_scores_url_tags_and_keywords_for_agent_retrieval(self) -> None:
        install = DocNode(
            url="/docs/get-started/installation/",
            slug="docs/get-started/installation",
            title="Installation",
            description="Install Furatena and development dependencies.",
            layout="doc",
            weight=10,
            section="docs",
            tags=frozenset({"installation", "setup"}),
            body_md="Run uv sync before serving the docs.",
            body_html="",
            toc=(),
            source_path="content/furatena/docs/get-started/installation.md",
            meta={"keywords": ["install", "uv", "pip", "fura"]},
            mount="furatena",
        )
        api = DocNode(
            url="/api/furatena/catalog/config/",
            slug="api/furatena/catalog/config",
            title="Catalog Config",
            description="Furatena catalog configuration API.",
            layout="doc",
            weight=1,
            section="api",
            tags=frozenset({"api"}),
            body_md="Configure dependencies and Furatena internals.",
            body_html="",
            toc=(),
            source_path="src/furatena/catalog/config.py",
            meta={"source": "autodoc"},
            mount="furatena",
        )

        hits = search_nodes(
            [api, install],
            "Installation Install Furatena and development dependencies /docs/get-started/installation/",
        )

        assert hits
        assert hits[0].node.node_id == install.node_id


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
        node = next(n for n in nodes if n.slug == "api/furatena/catalog/code_blocks")
        assert "&lt;pre&gt;" not in node.body_html
        assert "<pre>" in node.body_html

    def test_openapi_autodoc_generates_operation_projection(self, tmp_path: Path) -> None:
        spec = tmp_path / "specs" / "openapi.yaml"
        spec.parent.mkdir()
        spec.write_text(
            """
openapi: 3.1.0
info:
  title: Acme API
  version: 1.0.0
servers:
  - url: https://api.example.com
    description: prod
security:
  - oauth2: []
paths:
  /users:
    post:
      operationId: createUser
      summary: Create a user
      externalDocs:
        description: Authentication guide
        url: /docs/auth/
      tags: [Users]
      security:
        - apiKey: []
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateUser'
            examples:
              sample:
                value:
                  name: Ada
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/User'
        default:
          description: Error
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Error'
components:
  schemas:
    CreateUser:
      type: object
    User:
      type: object
    Error:
      type: object
""".lstrip(),
            encoding="utf-8",
        )
        config = tmp_path / "config" / "autodoc.yaml"
        config.parent.mkdir()
        config.write_text(
            """
autodoc:
  github_repo: lbliii/furatena
  github_branch: main
  python:
    enabled: false
  openapi:
    enabled: true
    output_prefix: api/rest
    display_name: REST API
    specs:
      - specs/openapi.yaml
""".lstrip(),
            encoding="utf-8",
        )

        nodes = generate_autodoc_nodes(config, repo_root=tmp_path)
        operation = next(node for node in nodes if node.meta.get("operation_id") == "createUser")
        api_operation = operation.meta["api_operation"]
        assert operation.content_format == "openapi-operation"
        assert operation.meta["source_provider"] == "openapi"
        assert api_operation["method"] == "POST"
        assert api_operation["path"] == "/users"
        assert api_operation["tags"] == ["Users"]
        assert api_operation["schemas"] == ["CreateUser", "Error", "User"]
        assert api_operation["request_bodies"] == ["CreateUser"]
        assert api_operation["responses"] == ["201", "default"]
        assert api_operation["examples"] == ["sample"]
        assert api_operation["auth"] == ["apiKey"]
        assert api_operation["environments"] == ["prod"]
        assert api_operation["external_docs"] == [
            {"description": "Authentication guide", "url": "/docs/auth/"}
        ]
        assert "[Authentication guide](/docs/auth/)" in operation.body_md

        auth_guide = DocNode(
            url="/docs/auth/",
            slug="docs/auth",
            title="Authentication",
            description="Authentication guide.",
            layout="doc",
            weight=10,
            section="docs",
            tags=frozenset({"guide"}),
            body_md="# Authentication\n",
            body_html="",
            toc=(),
            source_path="docs/auth.md",
        )
        nodes_tuple = (*nodes, auth_guide)

        class _Catalog:
            active_channel = "latest"
            nodes = nodes_tuple

            def doc_nodes(self):
                return list(nodes_tuple)

            def backlinks_for(self, _node):
                return []

            def get_by_slug(self, slug: str):
                return {node.slug: node for node in nodes_tuple}.get(slug)

            def get_by_node_id(self, node_id: str):
                return {node.node_id: node for node in nodes_tuple}.get(node_id)

            def prev_next(self, _node):
                return (None, None)

            def ast_documents(self):
                return {}

            def graph_edges(self):
                return [edge_record(edge) for edge in build_graph_edges(self)]

            def namespaces(self):
                return []

        payload = catalog_graph(_Catalog())
        page = next(page for page in payload["pages"] if page["slug"] == operation.slug)
        assert page["api_operation"]["operation_id"] == "createUser"
        assert page["source_provider"] == "openapi"
        edges = {(edge["kind"], edge["target"]) for edge in payload["edges"]}
        assert (EdgeKind.API_SCHEMA.value, "schema:User") in edges
        assert (EdgeKind.API_EXAMPLE.value, "example:sample") in edges
        assert (EdgeKind.API_AUTH.value, "auth:apiKey") in edges
        assert (EdgeKind.API_ENVIRONMENT.value, "environment:prod") in edges
        assert (EdgeKind.LINK.value, auth_guide.node_id) in edges
        graph_nodes = {(item["kind"], item["id"], item["label"]) for item in payload["graph_nodes"]}
        assert ("api_schema", "schema:User", "User") in graph_nodes
        assert ("api_example", "example:sample", "sample") in graph_nodes
        assert ("api_auth", "auth:apiKey", "apiKey") in graph_nodes
        assert ("api_environment", "environment:prod", "prod") in graph_nodes

        hits = search_nodes(list(nodes), "create user")
        assert hits and hits[0].node.node_id == operation.node_id
        retrieved = retrieve_node(_Catalog(), EmbeddingIndex.from_nodes(list(nodes)), operation.node_id)
        assert retrieved is not None
        assert retrieved["api_operation"]["operation_id"] == "createUser"
        assert retrieved["api_operation"]["schemas"] == ["CreateUser", "Error", "User"]

        server = FuraMCPServer(SimpleNamespace(catalog=_Catalog(), embedding_index=None))
        resource = server._api_operations()
        observed = next(item for item in resource["operations"] if item.get("operation_id") == "createUser")
        assert observed["method"] == "POST"
        assert observed["schemas"] == ["CreateUser", "Error", "User"]
        assert observed["external_docs"] == [
            {"description": "Authentication guide", "url": "/docs/auth/"}
        ]


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
