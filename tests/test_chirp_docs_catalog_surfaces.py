"""Catalog search, delivery, autodoc, metadata, and graph surfaces."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from chirp.testing import TestClient

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
CONTENT_ROOT = REPO / "content" / "chirp"
AUTODOC_CONFIG = REPO / "config" / "autodoc.yaml"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog import DocCatalog
from furatena.catalog.autodoc import generate_autodoc_nodes
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.export import (
    api_operations_json,
    catalog_graph,
    llms_txt,
    search_json,
    surface_json,
    tools_manifest,
)
from furatena.catalog.graph_schema import EdgeKind, build_graph_edges, edge_record
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.models import ContentDirective, ContentIR, DocNode
from furatena.catalog.rendering_heads import check_rendering_head_contracts
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.search import search_nodes
from furatena.catalog.semantic import retrieve_node
from furatena.catalog.seo import canonical_url, docs_base_url, json_ld_article
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from furatena.catalog.versions import infer_release_channels, node_matches_channel
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml


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
        assert names == {
            "search_docs",
            "get_doc",
            "list_docs",
            "semantic_search",
            "retrieve_doc",
            "list_api_operations",
        }
        assert manifest["catalog_url"].endswith("/catalog.json")


class TestRenderingHeads:
    def test_surface_json_exports_rendering_head_contracts(self) -> None:
        payload = surface_json()
        heads = {head["id"]: head for head in payload["rendering_heads"]}
        assert {"live-shell", "static-document", "embedded-fragment", "paged-output"} <= set(heads)
        assert heads["live-shell"]["navigation"] == [
            "persistent-shell",
            "htmx-boost",
            "prev-next",
            "toc",
            "search",
        ]
        assert "youtube" in heads["paged-output"]["unsupported_directives"]

    def test_rendering_head_check_reports_unsupported_directives(self) -> None:
        node = DocNode(
            url="/docs/video/",
            slug="docs/video",
            title="Video",
            description="Embedded media.",
            layout="doc",
            weight=1,
            section="docs",
            tags=frozenset({"media"}),
            body_md="",
            body_html="<p>Video</p>",
            toc=(),
            source_path="docs/video.md",
            content_ir=ContentIR(
                directives=(
                    ContentDirective(name="youtube", options={}, line=4),
                )
            ),
            body_text="Video",
        )

        class _Catalog:
            nodes = (node,)

        errors, warnings = check_rendering_head_contracts(_Catalog())
        assert errors == []
        assert any("embedded-fragment does not support directive 'youtube'" in item for item in warnings)
        assert any("paged-output does not support directive 'youtube'" in item for item in warnings)

    def test_delivery_config_check_reports_unknown_heads(self) -> None:
        from furatena.catalog.config import DeliveryConfig, DeliveryMountConfig, DocsConfig
        from furatena.catalog.delivery import check_delivery_config

        class _Mount:
            id = "furatena"

        class _Catalog:
            mounts = (_Mount(),)

        docs = DocsConfig(
            root=APP_ROOT,
            delivery=DeliveryConfig(
                head="missing-head",
                mounts={"ghost": DeliveryMountConfig(head="also-missing")},
            ),
        )

        errors, warnings = check_delivery_config(docs, _Catalog())
        assert "delivery.head unknown rendering head: 'missing-head'" in errors
        assert "delivery.mounts.ghost.head unknown rendering head: 'also-missing'" in errors
        assert "delivery.mounts.ghost does not match a configured mount" in warnings

    def test_mount_delivery_selection_matches_live_and_static_export(self, tmp_path: Path) -> None:
        from furatena.catalog.config import load_docs_config

        app_root = tmp_path / "app"
        content = tmp_path / "content"
        default_content = content / "main"
        shared_content = content / "shared"
        app_root.mkdir()
        (default_content / "docs").mkdir(parents=True)
        (shared_content / "docs").mkdir(parents=True)
        copy_app_theme(app_root, APP_ROOT)
        (default_content / "docs" / "home.md").write_text(
            "---\ntitle: Home\nlayout: doc\n---\n# Home\n\nDefault mount.\n",
            encoding="utf-8",
        )
        (shared_content / "docs" / "ref.md").write_text(
            "---\ntitle: Shared Ref\nlayout: doc\n---\n# Shared Ref\n\nShared mount.\n",
            encoding="utf-8",
        )
        (app_root / "docs.yaml").write_text(
            """
shell: shell.html
views:
  doc: views/doc.html
  default: views/doc.html
theme:
  use: lagoon
  id: furatena
  templates: theme/templates
mounts: mounts.yaml
delivery:
  head: live-shell
  theme:
    id: furatena
    use: lagoon
  mounts:
    shared:
      head: embedded-fragment
      theme:
        id: furatena
        use: lagoon
""".strip()
            + "\n",
            encoding="utf-8",
        )
        (app_root / "mounts.yaml").write_text(
            f"""
mounts:
  - id: furatena
    label: Default
    content_root: {default_content}
    default: true
  - id: shared
    label: Shared
    content_root: {shared_content}
    url_prefix: /shared
""".strip()
            + "\n",
            encoding="utf-8",
        )
        docs = DocsApp(
            load_docs_config(app_root / "docs.yaml"),
            repo_root=tmp_path,
            autodoc=False,
            serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        )
        client = TestClient(docs.create_app())

        async def _fetch_live() -> tuple[str, dict[str, object]]:
            page = await client.get("/shared/docs/ref/")
            surface = await client.get("/surface.json")
            assert page.status == 200
            assert surface.status == 200
            return page.text, json.loads(surface.text.split("<script", 1)[0])

        live_html, live_surface = asyncio.run(_fetch_live())
        assert 'data-fura-rendering-head="embedded-fragment"' in live_html
        assert 'data-fura-theme-id="furatena"' in live_html
        live_mounts = {item["mount"]: item for item in live_surface["delivery"]["mounts"]}
        assert live_mounts["shared"]["head"] == "embedded-fragment"
        assert live_mounts["shared"]["theme"] == {"id": "furatena", "use": "lagoon"}

        async def _fetch_catalog_surfaces() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
            merged = await client.get("/catalog.json")
            shard = await client.get("/catalog/mounts/shared.json")
            channels = await client.get("/channels.json")
            assert merged.status == 200
            assert shard.status == 200
            assert channels.status == 200
            return (
                json.loads(merged.text),
                json.loads(shard.text),
                json.loads(channels.text),
            )

        merged_graph, shared_graph, live_channels = asyncio.run(_fetch_catalog_surfaces())
        assert merged_graph == catalog_graph(docs.catalog)
        expected_shared = catalog_graph(docs.catalog._shards["shared"])
        expected_shared["mount"] = "shared"
        assert shared_graph == expected_shared
        live_agent = next(item for item in live_channels["channels"] if item["id"] == "agent")
        live_source = next(item for item in live_channels["sources"] if item["mount"] == "shared")
        live_shard_output = next(
            item for item in live_agent["outputs"] if item.get("mount") == "shared"
        )
        assert live_shard_output["url"].endswith("/catalog/mounts/shared.json")
        assert live_shard_output["fingerprint"] == live_source["fingerprint"]

        out = tmp_path / "public"
        export_static_site(
            docs,
            StaticExportOptions(
                output_dir=out,
                include_index_txt=False,
                include_portal=False,
                include_search=False,
            ),
        )
        static_surface = json.loads((out / "surface.json").read_text(encoding="utf-8"))
        static_mounts = {item["mount"]: item for item in static_surface["delivery"]["mounts"]}
        assert static_mounts["shared"]["head"] == live_mounts["shared"]["head"]
        assert static_mounts["shared"]["theme"] == live_mounts["shared"]["theme"]
        static_html = (out / "shared/docs/ref/index.html").read_text(encoding="utf-8")
        assert 'data-fura-rendering-head="embedded-fragment"' in static_html
        for mount_id in ("furatena", "shared"):
            shard_path = out / "catalog" / "mounts" / f"{mount_id}.json"
            assert shard_path.is_file()
            assert json.loads(shard_path.read_text(encoding="utf-8"))["mount"] == mount_id
        static_channels = json.loads((out / "channels.json").read_text(encoding="utf-8"))
        static_agent = next(item for item in static_channels["channels"] if item["id"] == "agent")
        static_shard = next(
            item for item in static_agent["outputs"] if item.get("mount") == "shared"
        )
        assert static_shard["url"].endswith("/catalog/mounts/shared.json")
        assert "catalog/mounts/shared.json" in next(
            item for item in static_channels["channels"] if item["id"] == "static"
        )["artifacts"]


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
        index = next(node for node in nodes if node.meta.get("element_type") == "api_index")
        api_operation = operation.meta["api_operation"]
        api_try_it = operation.meta["api_try_it"]
        assert index.layout == "api_reference"
        assert operation.layout == "api_reference"
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
        assert api_try_it["operation_id"] == "createUser"
        modes = {mode["id"]: mode for mode in api_try_it["modes"]}
        assert modes["static"]["available"] is True
        assert modes["static"]["request_behavior"] == "render_only"
        assert modes["mock"]["available"] is True
        assert modes["mock"]["request_behavior"] == "local_sample"
        assert modes["live"]["available"] is False
        assert modes["live"]["request_behavior"] == "authenticated_proxy"
        assert "server-side try-it proxy" in modes["live"]["disabled_reason"]
        assert api_try_it["boundaries"]["tenant"] == "default"
        assert api_try_it["boundaries"]["site"] == "docs"
        assert api_try_it["boundaries"]["mount"] == "catalog-mount"
        assert api_try_it["base_urls"] == [
            {
                "base_url_env": None,
                "id": "prod",
                "label": "prod",
                "scope": "tenant/site/mount",
                "url": "https://api.example.com",
            }
        ]
        assert api_try_it["auth"] == [
            {
                "configured": False,
                "exposed_to_static": False,
                "scheme": "apiKey",
                "storage": "server_only",
                "token_ref": None,
            }
        ]
        assert api_try_it["static_export"] == {
            "fallback_mode": "mock",
            "live_requests": "disabled",
            "reason": "Static exports never expose tokens or direct authenticated live requests.",
        }

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
        assert page["api_try_it"]["modes"][0]["id"] == "static"
        assert page["api_try_it"]["static_export"]["fallback_mode"] == "mock"
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

        frozen = tmp_path / "frozen"
        (frozen / "pages").mkdir(parents=True)
        (frozen / "catalog.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        cold = DocCatalog.from_frozen(frozen, mount="catalog-mount")
        cold_operation = cold.get_by_slug(operation.slug)
        assert cold_operation is not None
        assert cold_operation.meta["api_operation"] == api_operation
        assert cold_operation.meta["api_try_it"] == api_try_it
        cold_api_operations = api_operations_json(cold)
        assert cold_api_operations["operation_count"] == 1
        assert cold_api_operations["operations"][0]["operation_id"] == "createUser"

        hits = search_nodes(list(nodes), "create user")
        assert hits and hits[0].node.node_id == operation.node_id
        search_payload = search_json(_Catalog(), base_url="https://docs.example.com")
        search_entry = next(entry for entry in search_payload["entries"] if entry["node_id"] == operation.node_id)
        assert search_entry["api_operation"]["operation_id"] == "createUser"
        assert search_entry["api_operation"]["examples"] == ["sample"]
        assert search_entry["api_operation"]["source_spec"] == str(spec)
        llms_payload = llms_txt(_Catalog(), site_name="Acme Docs")
        assert llms_payload.startswith(
            "# Acme Docs Documentation\n\n> Documentation index for Acme Docs.\n\n## "
        )
        assert ".md)" in llms_payload
        assert "API: POST /users (createUser); examples: sample" in llms_payload
        api_operations_payload = api_operations_json(_Catalog(), base_url="https://docs.example.com")
        api_operations = {
            item["operation_id"]: item
            for item in api_operations_payload["operations"]
            if item.get("operation_id")
        }
        assert api_operations["createUser"]["url"].startswith("https://docs.example.com")
        assert api_operations["createUser"]["provenance"]["provider"] == "openapi"
        assert api_operations_payload["groups"][0]["name"] == "Users"
        tools_payload = tools_manifest(_Catalog(), base_url="https://docs.example.com", site_name="Acme Docs")
        assert tools_payload["api_operations_url"] == "https://docs.example.com/catalog/api-operations.json"
        assert tools_payload["api_operation_count"] == 1
        assert tools_payload["api_operation_groups"][0]["name"] == "Users"
        retrieved = retrieve_node(_Catalog(), EmbeddingIndex.from_nodes(list(nodes)), operation.node_id)
        assert retrieved is not None
        assert retrieved["api_operation"]["operation_id"] == "createUser"
        assert retrieved["api_operation"]["schemas"] == ["CreateUser", "Error", "User"]
        assert retrieved["api_try_it"]["modes"][2]["id"] == "live"
        assert retrieved["api_try_it"]["modes"][2]["available"] is False

        server = FuraMCPServer(SimpleNamespace(catalog=_Catalog(), embedding_index=None))
        resource = server._api_operations()
        observed = next(item for item in resource["operations"] if item.get("operation_id") == "createUser")
        assert observed["method"] == "POST"
        assert observed["schemas"] == ["CreateUser", "Error", "User"]
        assert observed["external_docs"] == [
            {"description": "Authentication guide", "url": "/docs/auth/"}
        ]
        assert observed["try_it"]["static_export"]["live_requests"] == "disabled"
        assert observed["try_it"]["auth"][0]["storage"] == "server_only"

    def test_openapi_try_it_contract_supports_configured_live_proxy(self, tmp_path: Path) -> None:
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
  - apiKey: []
paths:
  /users:
    get:
      operationId: listUsers
      summary: List users
      responses:
        '200':
          description: OK
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
    try_it:
      tenant: acme
      site: developer-docs
      mount: public-api
      base_url_env: ACME_API_BASE_URL
      tokens:
        apiKey: ACME_API_KEY
      live:
        enabled: true
        proxy_path: /_api/try-it
    specs:
      - specs/openapi.yaml
""".lstrip(),
            encoding="utf-8",
        )

        nodes = generate_autodoc_nodes(config, repo_root=tmp_path)
        operation = next(node for node in nodes if node.meta.get("operation_id") == "listUsers")
        contract = operation.meta["api_try_it"]
        modes = {mode["id"]: mode for mode in contract["modes"]}
        assert modes["static"]["available"] is True
        assert modes["mock"]["available"] is False
        assert modes["live"]["available"] is True
        assert modes["live"]["proxy_path"] == "/_api/try-it"
        assert contract["boundaries"] == {
            "mount": "public-api",
            "site": "developer-docs",
            "source_spec": str(spec),
            "tenant": "acme",
        }
        assert contract["base_urls"] == [
            {
                "base_url_env": "ACME_API_BASE_URL",
                "id": "prod",
                "label": "prod",
                "scope": "tenant/site/mount",
                "url": "https://api.example.com",
            }
        ]
        assert contract["auth"] == [
            {
                "configured": True,
                "exposed_to_static": False,
                "scheme": "apiKey",
                "storage": "server_only",
                "token_ref": "ACME_API_KEY",
            }
        ]
        assert contract["static_export"]["fallback_mode"] == "static"
        assert contract["static_export"]["live_requests"] == "disabled"

    def test_openapi_operation_renders_api_reference_view(self, tmp_path: Path) -> None:
        app_root = tmp_path / "app"
        content = tmp_path / "content"
        specs = tmp_path / "specs"
        app_root.mkdir()
        content.mkdir()
        specs.mkdir()
        copy_app_theme(app_root, APP_ROOT)
        write_minimal_docs_yaml(app_root / "docs.yaml")
        write_mounts_yaml(app_root / "mounts.yaml", content)
        (content / "_index.md").write_text(
            "---\ntitle: Home\n---\n# Home\n",
            encoding="utf-8",
        )
        (content / "docs").mkdir()
        (content / "docs" / "auth.md").write_text(
            "---\ntitle: Authentication\n---\n# Authentication\n",
            encoding="utf-8",
        )
        (specs / "openapi.yaml").write_text(
            """
openapi: 3.1.0
info:
  title: Acme API
  version: 1.0.0
servers:
  - url: https://api.example.com
    description: prod
security:
  - apiKey: []
paths:
  /users:
    post:
      operationId: createUser
      summary: Create a user
      externalDocs:
        description: Authentication guide
        url: /docs/auth/
      tags: [Users]
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
components:
  schemas:
    CreateUser:
      type: object
    User:
      type: object
""".lstrip(),
            encoding="utf-8",
        )
        autodoc_config = tmp_path / "autodoc.yaml"
        autodoc_config.write_text(
            """
autodoc:
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

        docs_app = DocsApp.from_paths(
            app_root / "docs.yaml",
            repo_root=tmp_path,
            autodoc_config=autodoc_config,
            autodoc=True,
        )
        operation = docs_app.catalog.get_by_slug("api/rest/acme-api/createuser")
        assert operation is not None
        assert operation.layout == "api_reference"

        client = TestClient(docs_app.create_app())

        async def _fetch(path: str):
            return await client.get(path)

        operation_response = asyncio.run(_fetch(operation.url))
        assert operation_response.status == 200
        assert 'data-chirp-theme-surface="api-reference"' in operation_response.text
        assert "Operation ID" in operation_response.text
        assert "<code>createUser</code>" in operation_response.text
        assert "Request bodies" in operation_response.text
        assert "<code>CreateUser</code>" in operation_response.text
        assert "Responses" in operation_response.text
        assert "<code>201</code>" in operation_response.text
        assert "Related docs" in operation_response.text
        assert "Authentication guide" in operation_response.text
        assert "Try it" in operation_response.text
        assert "<code>default</code>" in operation_response.text
        assert "authenticated_proxy" in operation_response.text
        assert "Static exports never expose tokens" in operation_response.text
        assert "https://api.example.com" in operation_response.text

        index_response = asyncio.run(_fetch("/api/rest/"))
        assert index_response.status == 200
        assert 'data-chirp-theme-surface="api-reference"' in index_response.text
        assert "Catalog-native API operation reference" in index_response.text


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
        assert canonical_url("https://lbliii.github.io/chirp", "/chirp/docs/") == (
            "https://lbliii.github.io/chirp/docs/"
        )
        assert canonical_url("https://lbliii.github.io/furatena", "/furatena/docs/get-started/") == (
            "https://lbliii.github.io/furatena/docs/get-started/"
        )

    def test_railway_domain_supplies_https_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FURA_BASE_URL", raising=False)
        monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "furatena-production.up.railway.app")

        assert docs_base_url("internal:8000") == (
            "https://furatena-production.up.railway.app"
        )

    def test_explicit_base_url_precedes_railway_domain(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("FURA_BASE_URL", "https://lbliii.github.io/furatena")
        monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "furatena-production.up.railway.app")

        assert docs_base_url("internal:8000") == "https://lbliii.github.io/furatena"


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
