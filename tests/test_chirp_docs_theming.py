"""View registry and theme configuration tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.config import load_docs_config
from furatena.catalog.loader import DocCatalog
from furatena.catalog.views import ViewRegistry


@pytest.fixture(scope="module")
def docs_config():
    return load_docs_config(APP_ROOT / "docs.yaml")


@pytest.fixture(scope="module")
def catalog() -> DocCatalog:
    return DocCatalog(REPO / "content" / "chirp", autodoc=False, autodoc_config=None)


@pytest.fixture(scope="module")
def views(docs_config) -> ViewRegistry:
    return ViewRegistry(docs_config)


class TestViewRegistry:
    def test_home_uses_home_view(self, views: ViewRegistry, catalog: DocCatalog) -> None:
        node = catalog.get("/")
        assert node is not None
        assert views.resolve(node) == "views/home.html"

    def test_doc_layout_maps_to_doc_view(self, views: ViewRegistry, catalog: DocCatalog) -> None:
        node = catalog.get_by_slug("docs/get-started/installation")
        assert node is not None
        assert views.resolve(node, catalog) == "views/doc.html"

    def test_section_index_uses_doc_list_view(self, views: ViewRegistry, catalog: DocCatalog) -> None:
        node = catalog.get_by_slug("docs/tutorials")
        assert node is not None
        assert views.resolve(node, catalog) == "views/doc_list.html"

    def test_collection_layout(self, views: ViewRegistry, catalog: DocCatalog) -> None:
        node = catalog.get_by_slug("docs/get-started/read-through")
        assert node is not None
        assert node.layout == "collection"
        assert views.resolve(node) == "views/collection.html"

    def test_compose_collection_sections(self, views: ViewRegistry, catalog: DocCatalog) -> None:
        node = catalog.get_by_slug("docs/get-started/read-through")
        assert node is not None
        ctx = views.compose(node, catalog)
        assert ctx["collection"] is not None
        assert len(ctx["collection_sections"]) >= 2

    def test_explicit_view_override_in_front_matter(self, views: ViewRegistry) -> None:
        from furatena.catalog.models import DocNode, TocEntry

        node = DocNode(
            url="/demo/",
            slug="demo",
            title="Demo",
            description="",
            layout="doc",
            weight=100,
            section="demo",
            tags=frozenset(),
            body_md="",
            body_html="",
            toc=(),
            source_path="demo.md",
            meta={"view": "views/page.html"},
        )
        assert views.resolve(node) == "views/page.html"

    def test_tutorials_sidebar_scoped_to_section(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/tutorials/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-docs-nav__section--depth-0" in html
        assert html.count("chirp-theme-docs-nav__section--depth-0") == 1
        assert "Get Started" not in html or "chirp-theme-doc-catalog-rail" in html

    def test_build_apps_nested_nav_toggle(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/build-apps/pages-navigation/routes/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-docs-nav__section--has-toggle" in html
        assert "chirp-theme-docs-nav__toggle" in html
        assert "Pages and Navigation" in html
        assert 'id="search-modal"' in html
        assert "nav-search-trigger" in html

    def test_tutorials_page_uses_doc_list_contract(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/tutorials/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'data-chirp-theme-surface="doc-list"' in html
        assert "chirp-theme-page-actions" in html
        assert "chirp-theme-doc-catalog-rail" in html
        assert 'popovertarget="theme-menu-rail"' in html
        assert 'data-appearance="dark"' in html
        assert "chirp-theme-directive-cards" in html
        assert "chirp-theme-directive-card" in html


class TestDocsTheme:
    def test_packaged_stylesheets(self, docs_config) -> None:
        from furatena.catalog.theme import DocsTheme

        theme = DocsTheme.from_docs_config(docs_config)
        assert any("/docs-assets/theme." in href for href in theme.stylesheet_hrefs)
        assert any("tokens.css" in href for href in theme.stylesheet_hrefs)


@pytest.fixture(scope="module")
def docs_client():
    from furatena.catalog.docs_app import DocsApp
    from chirp.testing import TestClient

    docs = DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
    )
    return TestClient(docs.create_app())


class TestThemeHtmlContract:
    def test_doc_page_uses_theme_layout_contract(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-docs-layout__main" in html
        assert "chirp-theme-docs-layout__content" in html
        assert "chirp-theme-docs-layout__hero" in html
        assert 'data-chirp-theme-surface="doc"' in html
        assert "/docs-assets/theme." in html
        assert 'class="fura-main"' in html
        assert "chirpui-app-shell__topbar" not in html
        assert 'id="docs-sidebar"' in html
        assert 'data-fura-surface="catalog"' in html
        assert "chirp-theme-docs-nav" in html
        assert "chirp-theme-docs-layout--app-shell" not in html
        assert "chirp-theme-page-actions" in html
        assert 'x-data="chirpuiPopover()"' in html
        assert 'popovertarget="theme-menu-rail"' in html
        page_actions_at = html.find("data-chirp-page-actions")
        if page_actions_at >= 0:
            assert "popovertarget" not in html[page_actions_at : page_actions_at + 800]

    def test_doc_page_catalog_shell_class(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-shell--rail-only" in html
        assert 'hx-select="#page-root"' in html
        assert 'class="chirpui-app-shell' not in html
        assert "chirpui-app-shell__topbar" not in html

    def test_catalog_boost_skips_topbar_oob(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get(
                "/docs/get-started/installation/",
                headers={"HX-Request": "true", "HX-Boosted": "true"},
            )
            return resp.text

        html = asyncio.run(_fetch())
        assert 'id="page-root"' in html
        assert "chirpui-topbar-breadcrumbs" not in html
        assert "chirpui-sidebar-nav" not in html

    def test_doc_page_branding_assets(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "/docs-theme/branding/favicon.svg" in html
        assert "/docs-theme/branding/site.webmanifest" in html

    def test_branding_assets_are_served(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> None:
            for path in (
                "/docs-theme/branding/favicon.svg",
                "/docs-theme/branding/favicon-32x32.png",
                "/docs-theme/branding/favicon-16x16.png",
                "/docs-theme/branding/site.webmanifest",
            ):
                resp = await docs_client.get(path)
                assert resp.status == 200, path

        asyncio.run(_fetch())

    def test_doc_page_catalog_chrome(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'class="skip-link"' in html
        assert "fura-shell-nav" not in html
        assert "docs-version-select" in html
        assert "version-selector__select" in html

    def test_home_page_has_site_nav(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "fura-shell-nav" in html
        assert "chirp-theme-shell__nav-link" in html

    def test_doc_page_toc_contract(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'data-chirp-docs="toc"' in html
        assert "data-toc-item=" in html
        assert "toc-progress-bar" in html
        assert "toc-scroll-container" in html

    def test_search_page_has_app_surface(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/search")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-docs-layout" in html
        assert 'data-chirp-theme-surface="search"' in html
        assert 'data-fura-surface="catalog"' in html
        assert 'id="search-page-input"' in html

    def test_collection_page_toc_contract(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/read-through/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'data-chirp-docs="toc"' in html
        assert "data-toc-item=" in html
        assert "In this collection" in html

    def test_home_page_uses_theme_home_surface(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-home" in html
        assert "chirp-theme-home__hero" in html
        assert "/docs-assets/theme." in html
