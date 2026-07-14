"""Headless graph query endpoint tests."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from chirp.testing import TestClient

from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.export import catalog_graph
from furatena.catalog.query import query_catalog_graph
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.runtime import ServeConfig, ServeMode
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

QUERY_MEDIA_TYPE = "application/vnd.furatena.catalog-query+json;version=1"
ACCEPT_QUERY = 'application/vnd.furatena.catalog-query+json;version="1"'


def _write_query_fixture(tmp_path: Path) -> tuple[Path, Path]:
    app_root = tmp_path / "app"
    content = tmp_path / "content"
    docs = content / "docs"
    docs.mkdir(parents=True)
    app_root.mkdir()
    copy_app_theme(app_root, APP_ROOT)
    write_minimal_docs_yaml(app_root / "docs.yaml", i18n=True)
    write_mounts_yaml(app_root / "mounts.yaml", content)
    (content / "_index.md").write_text(
        "---\ntitle: Home\n---\n\n# Home\n",
        encoding="utf-8",
    )
    (docs / "source.md").write_text(
        "---\ntitle: Source\ntags: [guide]\nowner: docs-platform\n"
        "api_schemas: [User]\napi_auth: oauth2\n---\n\n"
        "# Source\n\n[Target](/docs/target/)\n",
        encoding="utf-8",
    )
    (docs / "target.md").write_text(
        "---\ntitle: Target\ntags: [api]\nowner: docs-platform\nlang: es\n---\n\n# Target\n",
        encoding="utf-8",
    )
    return app_root, content


def _client_for_app(
    app_root: Path, *, repo_root: Path, serve: ServeConfig | None = None
) -> TestClient:
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=repo_root,
        autodoc=False,
        serve=serve or ServeConfig(ServeMode.PREVIEW, None, False, False),
    )
    return TestClient(docs.create_app())


async def _query(
    client: TestClient,
    payload: object,
    *,
    path: str = "/catalog/query.json",
    headers: dict[str, str] | None = None,
):
    request_headers = {"Content-Type": QUERY_MEDIA_TYPE, "Accept": "application/json"}
    request_headers.update(headers or {})
    return await client.request(
        "QUERY",
        path,
        headers=request_headers,
        body=json.dumps(payload).encode("utf-8"),
    )


def test_http_query_discovers_versioned_media_contract(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    options = asyncio.run(client.request("OPTIONS", "/catalog/query.json"))
    get = asyncio.run(client.get("/catalog/query.json?tag=guide"))

    assert options.status == 204
    assert options.header("Accept-Query") == ACCEPT_QUERY
    assert set((options.header("Allow") or "").split(", ")) == {
        "GET",
        "HEAD",
        "OPTIONS",
        "QUERY",
    }
    assert get.status == 200
    assert get.header("Accept-Query") == ACCEPT_QUERY


def test_http_query_reuses_get_response_contract(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    query_response = asyncio.run(_query(client, {"tag": "guide", "limit": 1}))
    get_response = asyncio.run(client.get("/catalog/query.json?tag=guide&limit=1"))

    assert query_response.status == get_response.status == 200
    assert json.loads(query_response.text) == json.loads(get_response.text)
    assert query_response.header("ETag")
    assert query_response.header("Cache-Control") == "private, max-age=0, must-revalidate"
    assert query_response.header("Vary") == "Accept, Content-Type"


@pytest.mark.parametrize(
    ("headers", "body", "status"),
    (
        ({}, b"{}", 400),
        ({"Content-Type": "text/plain"}, b"{}", 415),
        ({"Content-Type": QUERY_MEDIA_TYPE}, b"{", 400),
    ),
)
def test_http_query_rejects_missing_unsupported_or_malformed_content(
    tmp_path: Path,
    headers: dict[str, str],
    body: bytes,
    status: int,
) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    response = asyncio.run(
        client.request("QUERY", "/catalog/query.json", headers=headers, body=body)
    )

    assert response.status == status
    assert response.header("Accept-Query") == ACCEPT_QUERY


def test_http_query_rejects_non_object_and_unacceptable_response(
    tmp_path: Path,
) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    non_object = asyncio.run(_query(client, ["guide"]))
    unacceptable = asyncio.run(_query(client, {"tag": "guide"}, headers={"Accept": "text/csv"}))

    assert non_object.status == 422
    assert json.loads(non_object.text)["error"] == "catalog query content must be a JSON object"
    assert unacceptable.status == 406
    assert unacceptable.header("Accept-Query") == ACCEPT_QUERY


def test_http_query_etag_is_body_aware_and_supports_conditionals(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    first = asyncio.run(_query(client, {"tag": "guide", "limit": 1}))
    reordered = asyncio.run(_query(client, {"limit": 1, "tag": "guide"}))
    different = asyncio.run(_query(client, {"tag": "api", "limit": 1}))
    conditional = asyncio.run(
        _query(
            client,
            {"tag": "guide", "limit": 1},
            headers={"If-None-Match": first.header("ETag") or ""},
        )
    )

    assert first.header("ETag") == reordered.header("ETag")
    assert first.header("ETag") != different.header("ETag")
    assert conditional.status == 304
    assert conditional.header("ETag") == first.header("ETag")


def test_http_query_alias_redirect_preserves_method_and_get_fallback(
    tmp_path: Path,
) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    redirect = asyncio.run(_query(client, {"tag": "guide"}, path="/graph/query.json"))
    method_failure = asyncio.run(client.post("/catalog/query.json", body=b"{}"))
    fallback = asyncio.run(client.get("/catalog/query.json?tag=guide"))

    assert redirect.status == 308
    assert redirect.header("Location") == "/catalog/query.json"
    assert redirect.header("Accept-Query") == ACCEPT_QUERY
    assert method_failure.status == 405
    assert "GET" in (method_failure.header("Allow") or "")
    assert "QUERY" in (method_failure.header("Allow") or "")
    assert fallback.status == 200
    assert json.loads(fallback.text)["page_count"] == 1


def test_graph_query_endpoint_filters_live_catalog(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    async def _fetch() -> dict[str, object]:
        resp = await client.get(
            "/catalog/query.json?"
            "mount=chirp&tag=guide&format=patitas-markdown&owner=docs-platform&"
            "edge_kind=link&target=/docs/target/"
        )
        assert resp.status == 200
        return json.loads(resp.text)

    payload = asyncio.run(_fetch())
    assert payload["page_count"] == 1
    assert payload["edge_count"] == 1
    page = payload["pages"][0]
    assert page["slug"] == "docs/source"
    assert page["owner"] == "docs-platform"
    assert payload["edges"][0]["kind"] == "link"
    assert payload["query"]["target"] == "/docs/target/"
    assert payload["graph_nodes"] == []


def test_source_health_endpoint_reports_mounts(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    async def _fetch() -> dict[str, object]:
        resp = await client.get("/catalog/source-health.json?mount=chirp")
        assert resp.status == 200
        return json.loads(resp.text)

    payload = asyncio.run(_fetch())
    assert payload["schema_version"] == 1
    assert payload["ok"] is True
    assert payload["mount_count"] == 1
    mount = payload["mounts"][0]
    assert mount["id"] == "chirp"
    assert mount["status"] == "healthy"
    assert mount["loaded"] is True
    assert mount["page_count"] >= 1


def test_docs_app_identity_flows_to_catalog_graph(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    docs_yaml = app_root / "docs.yaml"
    docs_yaml.write_text(
        docs_yaml.read_text(encoding="utf-8")
        + "\nidentity:\n  tenant: acme\n  workspace: platform\n  site: developer-docs\n",
        encoding="utf-8",
    )
    client = _client_for_app(app_root, repo_root=tmp_path)

    async def _fetch() -> dict[str, object]:
        resp = await client.get("/catalog.json")
        assert resp.status == 200
        return json.loads(resp.text)

    payload = asyncio.run(_fetch())
    page = next(item for item in payload["pages"] if item["slug"] == "docs/source")
    assert page["tenant"] == "acme"
    assert page["workspace"] == "platform"
    assert page["site"] == "developer-docs"
    namespace = payload["namespaces"][0]
    assert namespace["tenant"] == "acme"
    assert namespace["workspace"] == "platform"
    assert namespace["site"] == "developer-docs"


def test_graph_query_endpoint_returns_typed_graph_nodes(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    async def _fetch() -> dict[str, object]:
        resp = await client.get("/catalog/query.json?edge_kind=api_schema&target=schema:User")
        assert resp.status == 200
        return json.loads(resp.text)

    payload = asyncio.run(_fetch())
    assert payload["page_count"] == 1
    assert payload["edge_count"] == 1
    assert payload["edges"][0]["target"] == "schema:User"
    assert payload["graph_nodes"] == [
        {
            "id": "schema:User",
            "kind": "api_schema",
            "label": "User",
            "mount": "chirp",
            "edition": "latest",
        }
    ]


def test_graph_query_endpoint_filters_by_locale(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    async def _fetch() -> dict[str, object]:
        resp = await client.get("/graph/query.json?locale=es&tag=api")
        assert resp.status == 200
        return json.loads(resp.text)

    payload = asyncio.run(_fetch())
    assert payload["page_count"] == 1
    assert payload["pages"][0]["slug"] == "docs/target"
    assert payload["pages"][0]["lang"] == "es"


def test_graph_query_endpoint_paginates_pages_and_edges(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    async def _fetch(offset: int) -> dict[str, object]:
        response = await client.get(f"/catalog/query.json?limit=1&offset={offset}")
        assert response.status == 200
        return json.loads(response.text)

    first = asyncio.run(_fetch(0))
    second = asyncio.run(_fetch(1))

    assert first["page_count"] == second["page_count"] == 1
    assert first["total"] == second["total"] == 3
    assert first["limit"] == 1
    assert first["offset"] == 0
    assert first["next_offset"] == 1
    assert second["offset"] == 1
    assert second["next_offset"] == 2
    assert first["pages"][0]["node_id"] != second["pages"][0]["node_id"]
    assert all(edge["source"] == first["pages"][0]["node_id"] for edge in first["edges"])


def test_graph_query_reuses_access_scoped_graph_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import furatena.catalog.export as export_module

    app_root, _content = _write_query_fixture(tmp_path)
    config = load_docs_config(app_root / "docs.yaml")
    registry = CatalogRegistry.from_config(
        config.mounts_path or app_root / "mounts.yaml",
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
        i18n_config=config.i18n,
    )
    real_catalog_graph = export_module.catalog_graph
    calls = 0

    def _counted_catalog_graph(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_catalog_graph(*args, **kwargs)

    monkeypatch.setattr(export_module, "catalog_graph", _counted_catalog_graph)
    query_catalog_graph(registry, limit=1)
    query_catalog_graph(registry, offset=1, limit=1)

    assert calls == 1


@pytest.mark.parametrize(
    ("query", "invalid_key", "invalid_value"),
    (
        ("unknown_filter=value", "unknown", ["unknown_filter"]),
        ("edge_kind=not-a-real-edge", "edge_kind", "not-a-real-edge"),
        ("limit=0", "limit", 0),
        ("limit=501", "limit", 501),
        ("limit=nope", "limit", "nope"),
        ("offset=-1", "offset", -1),
    ),
)
def test_graph_query_endpoint_rejects_invalid_filters(
    tmp_path: Path,
    query: str,
    invalid_key: str,
    invalid_value: object,
) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    client = _client_for_app(app_root, repo_root=tmp_path)

    async def _fetch():
        return await client.get(f"/catalog/query.json?{query}")

    response = asyncio.run(_fetch())
    payload = json.loads(response.text)

    assert response.status == 400
    assert payload["error"] == "invalid catalog query filters"
    assert payload["invalid_filters"][invalid_key] == invalid_value
    assert "edge_kind" in payload["allowed_filters"]
    assert "link" in payload["allowed_edge_kinds"]


def test_graph_query_endpoint_uses_frozen_catalog(tmp_path: Path) -> None:
    app_root, _content = _write_query_fixture(tmp_path)
    config = load_docs_config(app_root / "docs.yaml")
    registry = CatalogRegistry.from_config(
        config.mounts_path or app_root / "mounts.yaml",
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
        i18n_config=config.i18n,
    )
    graph = catalog_graph(registry)
    frozen = tmp_path / "frozen"
    mount_dir = frozen / "mounts" / "chirp"
    pages_dir = mount_dir / "pages"
    pages_dir.mkdir(parents=True)
    for page in graph["pages"]:
        slug_file = page["slug"] or "index"
        html_file = pages_dir / f"{slug_file}.html"
        html_file.parent.mkdir(parents=True, exist_ok=True)
        html_file.write_text(f"<h1>{page['title']}</h1>\n", encoding="utf-8")
    (mount_dir / "catalog.json").write_text(json.dumps(graph), encoding="utf-8")
    (frozen / "catalog.json").write_text(json.dumps(graph), encoding="utf-8")
    (frozen / "registry.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mounts": [{"id": "chirp", "label": "Test", "url_prefix": "/", "default": True}],
            }
        ),
        encoding="utf-8",
    )

    client = _client_for_app(
        app_root,
        repo_root=tmp_path,
        serve=ServeConfig(ServeMode.PREVIEW, frozen, True, False),
    )

    async def _fetch() -> dict[str, object]:
        resp = await client.get("/catalog/query.json?owner=docs-platform&edge=link")
        assert resp.status == 200
        return json.loads(resp.text)

    payload = asyncio.run(_fetch())
    assert payload["page_count"] == 2
    assert {page["slug"] for page in payload["pages"]} == {"docs/source", "docs/target"}
    assert payload["edge_count"] == 1

    async def _fetch_api_node() -> dict[str, object]:
        resp = await client.get("/graph/query.json?edge=api_auth&target=auth:oauth2")
        assert resp.status == 200
        return json.loads(resp.text)

    api_payload = asyncio.run(_fetch_api_node())
    assert api_payload["page_count"] == 1
    assert api_payload["edges"][0]["target"] == "auth:oauth2"
    assert api_payload["graph_nodes"] == [
        {
            "id": "auth:oauth2",
            "kind": "api_auth",
            "label": "oauth2",
            "mount": "chirp",
            "edition": "latest",
        }
    ]
