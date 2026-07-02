"""Wave 24 landing surface — home contract, mobile nav, develop previews."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))


@pytest.fixture(scope="module")
def docs_client():
    from chirp.testing import TestClient

    from furatena.catalog.docs_app import DocsApp

    docs = DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
    )
    return TestClient(docs.create_app())


class TestLandingSurface:
    def test_home_includes_lagoon_stylesheet_stack(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "/docs-theme/local/styles.css" in html
        assert "chirp-theme-home__hero-title" in html
        assert "chirp-theme-home__hero-panel" in html
        assert "chirpui-surface--glass" in html
        assert "chirp-theme-home__product-visual" in html
        assert "chirp-theme-home__live-dot" in html
        assert "chirp-theme-home__hero-link" in html
        assert "chirp-theme-home__explore" in html
        assert "chirp-theme-home__pipeline-inline" in html
        assert "uv run fura serve" in html
        assert "Ready for search and AI tools" in html
        assert "chirp-theme-page__content" not in html
        assert "chirp-theme-home__metric-cards" in html
        assert "chirpui-cta-band" in html
        assert "chirpui-stepper" not in html

    def test_home_includes_mobile_shell_nav(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'id="fura-shell-mobile-nav"' in html
        assert "chirp-theme-shell__mobile-toggle" in html

    def test_doc_page_keeps_catalog_shell_without_mobile_site_drawer(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-doc-catalog" in html
        assert 'id="fura-shell-mobile-nav"' not in html


class TestDevelopExports:
    def test_develop_index_lists_exports(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/develop/")
            assert resp.status == 200
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-develop" in html
        assert 'href="/develop/catalog/"' in html
        assert 'href="/develop/llms/"' in html
        assert 'href="/develop/channels/"' in html

    def test_develop_preview_links_to_raw_export(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/develop/llms/")
            assert resp.status == 200
            return resp.text

        html = asyncio.run(_fetch())
        assert 'href="/llms.txt"' in html
        assert "chirp-theme-develop__sample" in html

    def test_channels_export_describes_publication_channels(self, docs_client) -> None:
        import asyncio
        import json

        async def _fetch() -> dict[str, object]:
            resp = await docs_client.get("/channels.json")
            assert resp.status == 200
            return json.loads(resp.text.split("<script", 1)[0])

        payload = asyncio.run(_fetch())
        channels = {item["id"]: item for item in payload["channels"]}
        assert payload["schema_version"] == 1
        assert {"live", "static", "agent", "pdf"} <= set(channels)
        assert channels["agent"]["status"] == "available"
        assert channels["pdf"]["status"] == "planned"
        assert payload["fingerprints"]["catalog"]

    def test_raw_llms_export_still_served(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> None:
            resp = await docs_client.get("/llms.txt")
            assert resp.status == 200
            assert b"# Furatena Documentation" in resp.body

        asyncio.run(_fetch())
