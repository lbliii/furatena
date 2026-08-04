"""Public catalog-search MCP App behavior across native, Milo, and stdio hosts."""

from __future__ import annotations

import json
from pathlib import Path

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.mcp import FuraMCPServer, build_milo_cli
from furatena.catalog.mcp_apps import (
    CATALOG_SEARCH_APP_ASSET,
    CATALOG_SEARCH_APP_SHA256,
    CATALOG_SEARCH_APP_URI,
    MCP_APP_MIME_TYPE,
    MCP_APPS_EXTENSION_ID,
    catalog_search_app_html,
    catalog_search_app_metadata,
    catalog_search_resource_descriptor,
)
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.cli.main import main


def _docs(tmp_path: Path) -> DocsApp:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Catalog Search App Tests"])
    private = app_root / "content" / "docs" / "private-canary.md"
    private.write_text(
        "---\ntitle: Private Search Canary\nvisibility: private\n---\n"
        "# Private Search Canary\n\nsecret-search-token-332\n",
        encoding="utf-8",
    )
    return DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )


def _apps_params() -> dict[str, object]:
    return {
        "protocolVersion": "2025-06-18",
        "capabilities": {
            "extensions": {
                MCP_APPS_EXTENSION_ID: {"mimeTypes": [MCP_APP_MIME_TYPE]},
            }
        },
    }


def test_catalog_search_app_asset_is_bundled_bounded_and_deny_by_default() -> None:
    import hashlib

    html = catalog_search_app_html()
    meta = catalog_search_app_metadata()

    assert CATALOG_SEARCH_APP_ASSET == "mcp_app_assets/catalog-search-v1.html"
    assert hashlib.sha256(html.encode()).hexdigest() == CATALOG_SEARCH_APP_SHA256
    assert '<meta name="furatena-app"' in html
    assert 'role="status"' in html
    assert 'aria-live="assertive"' in html
    assert "const MAX_TEXT = 100000" in html
    assert "const MAX_RESULTS = 25" in html
    assert "const REQUEST_TIMEOUT = 15000" in html
    assert "const MAX_PENDING = 16" in html
    assert "result.structuredContent" in html
    assert "assertBoundedJson(result.structuredContent)" in html
    assert "pending.delete(id)" in html
    assert "JSON.parse(item.text)" in html
    assert "id: message.id, result: {}" in html
    assert "textContent" in html
    assert "fetch(" not in html
    assert "innerHTML" not in html
    assert meta["ui"]["csp"] == {
        "connectDomains": [],
        "resourceDomains": [],
        "frameDomains": [],
        "baseUriDomains": [],
    }
    assert meta["ui"]["permissions"] == {}
    assert meta["io.furatena/mcp-app"]["redaction"] == "public-only"
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "agent-contracts" / "v1" / "public.json").read_text(
            encoding="utf-8"
        )
    )
    assert fixture["surfaces"]["mcp_apps"]["resources"][0] == catalog_search_resource_descriptor()


def test_native_server_exposes_app_only_after_public_negotiation(tmp_path: Path) -> None:
    server = FuraMCPServer(_docs(tmp_path))

    assert CATALOG_SEARCH_APP_URI not in {item["uri"] for item in server.list_resources()}
    assert "_meta" not in server.list_tools()[0]
    denied = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "denied",
            "method": "resources/read",
            "params": {"uri": CATALOG_SEARCH_APP_URI},
        }
    )
    assert denied["error"]["code"] == -32602

    initialized = server.handle_request(
        {"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": _apps_params()}
    )
    resources = server.list_resources()
    tools = server.list_tools()
    content = server.read_resource(CATALOG_SEARCH_APP_URI)

    assert initialized["result"]["capabilities"]["extensions"] == {
        MCP_APPS_EXTENSION_ID: {"mimeTypes": [MCP_APP_MIME_TYPE]}
    }
    app = next(item for item in resources if item["uri"] == CATALOG_SEARCH_APP_URI)
    assert app["mimeType"] == MCP_APP_MIME_TYPE
    assert app["_meta"]["ui"]["permissions"] == {}
    assert tools[0]["_meta"]["ui"] == {
        "resourceUri": CATALOG_SEARCH_APP_URI,
        "visibility": ["model", "app"],
    }
    assert content["text"] == catalog_search_app_html()
    assert content["_meta"]["io.furatena/mcp-app"]["audience"] == "public"


def test_milo_hosts_negotiate_link_read_gateway_and_progressive_fallback(tmp_path: Path) -> None:
    from milo.testing import MCPClient
    from milo.verify import _check_mcp_apps_gateway, _check_mcp_apps_in_process

    cli = build_milo_cli(FuraMCPServer(_docs(tmp_path)))
    fallback = MCPClient(cli)
    fallback.initialize()

    assert CATALOG_SEARCH_APP_URI not in {item["uri"] for item in fallback.list_resources()}
    fallback_search = next(tool for tool in fallback.list_tools() if tool.name == "semantic_search")
    assert fallback_search.meta is None or "ui" not in fallback_search.meta
    result = fallback.call("semantic_search", query="Get started", limit=5)
    assert result.is_error is False
    assert result.structured["filters"]["include_private"] is False
    assert json.loads(result.text) == result.structured

    negotiated = MCPClient(cli)
    initialized = negotiated.initialize(_apps_params())
    app = next(
        item for item in negotiated.list_resources() if item["uri"] == CATALOG_SEARCH_APP_URI
    )
    linked = next(tool for tool in negotiated.list_tools() if tool.name == "semantic_search")
    retrieve = next(tool for tool in negotiated.list_tools() if tool.name == "retrieve_node")
    graph = next(tool for tool in negotiated.list_tools() if tool.name == "query_graph")

    assert MCP_APPS_EXTENSION_ID in initialized["capabilities"]["extensions"]
    assert app["mimeType"] == MCP_APP_MIME_TYPE
    assert negotiated.read_resource(CATALOG_SEARCH_APP_URI)["contents"][0]["text"].startswith(
        "<!doctype html>"
    )
    assert linked.meta["ui"]["resourceUri"] == CATALOG_SEARCH_APP_URI
    # Stable Apps defaults omitted visibility to model+app. Keeping these tools
    # unlinked prevents them from becoming independent App rendering entry points.
    assert retrieve.meta is None or "ui" not in retrieve.meta
    assert graph.meta is None or "ui" not in graph.meta
    node_id = result.structured["results"][0]["node_id"]
    assert negotiated.call("retrieve_node", node_id=node_id).is_error is False
    assert negotiated.call("query_graph", source=node_id, limit=10).is_error is False
    assert _check_mcp_apps_in_process(cli).status == "ok"
    assert _check_mcp_apps_gateway(cli).status == "ok"


def test_catalog_search_app_is_absent_from_private_sessions_and_public_results(
    tmp_path: Path,
) -> None:
    from milo.testing import MCPClient

    docs = _docs(tmp_path)
    private_server = FuraMCPServer(docs, include_private=True)
    private = MCPClient(build_milo_cli(private_server))
    private.initialize(_apps_params())

    assert CATALOG_SEARCH_APP_URI not in {item["uri"] for item in private.list_resources()}
    assert all(tool.meta is None or "ui" not in tool.meta for tool in private.list_tools())

    public = MCPClient(build_milo_cli(FuraMCPServer(docs)))
    public.initialize(_apps_params())
    search = public.call("semantic_search", query="secret-search-token-332", limit=25)
    rendered_results = json.dumps(search.structured["results"], sort_keys=True)

    assert search.structured["count"] == 0
    assert search.structured["filters"]["include_private"] is False
    assert "Private Search Canary" not in rendered_results
    assert "secret-search-token-332" not in rendered_results


def test_catalog_search_app_survives_real_stdio_transport(tmp_path: Path) -> None:
    from milo.verify import _check_subprocess_mcp

    docs = _docs(tmp_path)
    script = tmp_path / "catalog_search_stdio.py"
    script.write_text(
        "from pathlib import Path\n"
        "from furatena.catalog.docs_app import DocsApp\n"
        "from furatena.catalog.mcp import FuraMCPServer, build_milo_cli\n"
        "from furatena.catalog.runtime import ServeConfig, ServeMode\n"
        f"root = Path({str(docs.repo_root)!r})\n"
        "docs = DocsApp.from_paths(root / 'docs.yaml', repo_root=root, autodoc=False, "
        "serve=ServeConfig(ServeMode.AUTHOR, None, False, False))\n"
        "if __name__ == '__main__':\n"
        "    build_milo_cli(FuraMCPServer(docs)).run()\n",
        encoding="utf-8",
    )

    transport, apps = _check_subprocess_mcp(
        script,
        timeout=15.0,
        ui_resource_uris=(CATALOG_SEARCH_APP_URI,),
    )

    assert transport.status == "ok", transport.details
    assert apps.status == "ok", apps.details
