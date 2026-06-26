"""View registry and theme configuration tests."""

from __future__ import annotations

import sys
from dataclasses import replace
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

    def test_prefixed_mount_section_index_uses_doc_list_view(
        self,
        views: ViewRegistry,
        chirp_fixture_config,
    ) -> None:
        from furatena.catalog.docs_app import DocsApp

        docs = DocsApp(chirp_fixture_config, repo_root=REPO, autodoc=False)
        node = docs.catalog.get_by_slug("docs/tutorials", mount="chirp")
        assert node is not None
        assert views.resolve(node, docs.catalog) == "views/doc_list.html"

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
        from furatena.catalog.models import DocNode

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
            resp = await docs_client.get("/chirp/docs/tutorials/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-docs-nav__section--depth-0" in html
        assert html.count("chirp-theme-docs-nav__section--depth-0") == 1
        assert "Get Started" not in html or "chirp-theme-doc-catalog-rail" in html

    def test_build_apps_nested_nav_toggle(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/chirp/docs/build-apps/pages-navigation/routes/")
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
            resp = await docs_client.get("/chirp/docs/tutorials/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'data-chirp-theme-surface="doc-list"' in html
        assert "chirp-theme-page-actions" in html
        assert "chirp-theme-doc-catalog-rail" in html
        assert 'popovertarget="theme-menu-rail"' in html
        assert 'data-appearance="dark"' in html
        assert "chirp-theme-directive-cards" in html
        assert "chirp-theme-directive-card" in html


class TestThemeHeroContract:
    def test_doc_hero_markup(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirpui-hero--page-editorial chirpui-hero--solid chirp-theme-docs-layout__hero" in html
        assert "chirpui-hero__inner" in html
        assert "chirpui-hero__metadata" in html

    def test_local_stylesheet_imports_skin_slices(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs-theme/local/styles.css")
            return resp.text

        css = asyncio.run(_fetch())
        assert "@import url(\"skin/fonts.css\")" in css
        assert "@import url(\"skin/hero.css\")" in css
        assert "@import url(\"effects.css\")" in css

    def test_generated_preset_css(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs-theme/generated/theme-preset.css")
            return resp.text

        css = asyncio.run(_fetch())
        assert "--chirpui-prose-max-width: 80ch" in css
        assert "--font-family-sans:" in css
        assert "Inter" in css
        assert "Outfit" in css


class TestThemeEffects:
    def test_default_effects(self, docs_config) -> None:
        assert docs_config.theme.effects.code == "flat"
        assert docs_config.theme.effects.cards == "flat"
        assert docs_config.theme.effects.hero == "wash"

    def test_doc_page_effect_attributes(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'data-fura-effects-code","flat"' in html
        assert 'data-fura-effects-cards","flat"' in html
        assert 'data-fura-effects-hero","wash"' in html


class TestDocsTheme:
    def test_packaged_stylesheets(self, docs_config) -> None:
        from furatena.catalog.theme import DocsTheme

        theme = DocsTheme.from_docs_config(docs_config)
        assert any("/docs-assets/theme." in href for href in theme.stylesheet_hrefs)
        assert any("tokens.css" in href for href in theme.stylesheet_hrefs)
        assert any("theme-preset.css" in href for href in theme.stylesheet_hrefs)


@pytest.fixture(scope="module")
def chirp_fixture_config(tmp_path_factory):
    mounts = tmp_path_factory.mktemp("mounts") / "mounts.yaml"
    mounts.write_text(
        f"""\
mounts:
  - id: furatena
    label: Furatena Documentation
    content_root: {REPO / "content" / "furatena"}
    default: true
  - id: chirp
    label: Chirp Documentation
    content_root: {REPO / "content" / "chirp"}
    url_prefix: /chirp
  - id: shared
    label: Shared Reference
    content_root: {APP_ROOT / "content" / "shared"}
    url_prefix: /shared
    extensions: [".md", ".html"]
    format_map:
      ".md": patitas-markdown
      ".html": html
""",
        encoding="utf-8",
    )
    return replace(load_docs_config(APP_ROOT / "docs.yaml"), mounts_path=mounts)


@pytest.fixture(scope="module")
def docs_client(chirp_fixture_config):
    from chirp.testing import TestClient

    from furatena.catalog.docs_app import DocsApp

    docs = DocsApp(
        chirp_fixture_config,
        repo_root=REPO,
        autodoc=False,
    )
    return TestClient(docs.create_app())


class TestNativeShell:
    def test_shell_extends_fura_frame_not_chirp(self) -> None:
        shell = (APP_ROOT / "theme" / "shell.html").read_text(encoding="utf-8")
        assert 'extends "layouts/fura_shell.html"' in shell
        assert "chirp/layouts/shell.html" not in shell

    def test_doc_page_has_document_frame(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert 'lang="' in html
        assert 'data-chirp="htmx"' in html
        assert 'id="main"' in html
        assert "__chirpuiShellRuntimeInitialized" in html or "htmx:configRequest" in html


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
            resp = await docs_client.get("/chirp/docs/get-started/installation/")
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
        assert "Furatena" in html
        assert 'brand-word">Chirp' not in html
        assert "chirp-theme-shell__header" in html
        assert "chirp-theme-shell__nav-dropdown" in html
        assert "chirp-theme-shell__mega" in html
        assert "chirp-theme-home__hero-title" in html
        assert "chirpui-surface--glass" in html
        assert "chirpui-cta-band" in html
        assert "chirp-theme-shell__nav-link" in html

    def test_doc_page_toc_contract(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'data-fura-docs="toc"' in html
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
            resp = await docs_client.get("/chirp/docs/get-started/read-through/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'data-fura-docs="toc"' in html
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
        assert "chirpui-feature-section" in html
        assert "Write markdown. Get living documentation." in html
        assert "chirp-theme-home__explore" in html
        assert "/docs-assets/theme." in html
