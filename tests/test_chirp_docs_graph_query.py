"""Headless graph query endpoint tests."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from chirp.testing import TestClient

from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.export import catalog_graph
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.runtime import ServeConfig, ServeMode
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml


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
        "---\ntitle: Source\ntags: [guide]\nowner: docs-platform\n---\n\n"
        "# Source\n\n[Target](/docs/target/)\n",
        encoding="utf-8",
    )
    (docs / "target.md").write_text(
        "---\ntitle: Target\ntags: [api]\nowner: docs-platform\nlang: es\n---\n\n# Target\n",
        encoding="utf-8",
    )
    return app_root, content


def _client_for_app(app_root: Path, *, repo_root: Path, serve: ServeConfig | None = None) -> TestClient:
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=repo_root,
        autodoc=False,
        serve=serve or ServeConfig(ServeMode.PREVIEW, None, False, False),
    )
    return TestClient(docs.create_app())


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
                "mounts": [
                    {"id": "chirp", "label": "Test", "url_prefix": "/", "default": True}
                ],
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
