"""Internationalization tests for Furatena."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import shutil

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.i18n import (
    build_translation_index,
    collect_i18n_export_routes,
    detect_lang_from_path,
    fallback_context,
    load_i18n_config,
    locale_context,
    locale_slug,
    locale_url,
    resolve_localized_node,
    resolve_translation_key,
)
from furatena.catalog.export import search_json
from furatena.catalog.loader import DocCatalog
from furatena.catalog.registry import CatalogRegistry, load_mounts
from furatena.catalog.sitemap import sitemap_xml


def _write_docs_tree(root: Path) -> None:
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "hello.md").write_text(
        "---\ntitle: Hello\n---\n\nEnglish body.\n",
        encoding="utf-8",
    )
    locale = root / "_locale" / "es" / "docs"
    locale.mkdir(parents=True)
    (locale / "hello.md").write_text(
        "---\ntitle: Hola\nlang: es\n---\n\nCuerpo en español.\n",
        encoding="utf-8",
    )


def _write_i18n_docs_yaml(path: Path) -> None:
    path.write_text(
        """
shell: shell.html
views:
  doc: views/doc.html
  default: views/doc.html
i18n:
  default_language: en
  languages:
    - { code: en, name: English }
    - { code: es, name: Español }
mounts: mounts.yaml
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _write_mounts(path: Path, content_root: Path) -> None:
    rel = content_root.relative_to(path.parent)
    path.write_text(
        f"""
mounts:
  - id: chirp
    label: Test
    content_root: {rel.as_posix()}
    default: true
""".strip()
        + "\n",
        encoding="utf-8",
    )


class TestI18nConfig:
    def test_disabled_with_single_language(self) -> None:
        config = load_i18n_config({"default_language": "en", "languages": ["en"]})
        assert config.enabled is False

    def test_enabled_with_multiple_languages(self) -> None:
        config = load_i18n_config(
            {
                "default_language": "en",
                "languages": [{"code": "en", "name": "English"}, {"code": "es", "name": "Español"}],
            }
        )
        assert config.enabled is True
        assert config.language_codes() == frozenset({"en", "es"})


class TestI18nHelpers:
    def test_locale_url_prefix_for_non_default(self) -> None:
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        assert locale_url("/docs/hello/", "es", config) == "/es/docs/hello/"
        assert locale_url("/docs/hello/", "en", config) == "/docs/hello/"

    def test_locale_slug(self) -> None:
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        assert locale_slug("docs/hello", "es", config) == "es/docs/hello"
        assert locale_slug("docs/hello", "en", config) == "docs/hello"

    def test_detect_lang_from_path(self) -> None:
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        assert detect_lang_from_path("/es/docs/hello/", config) == "es"
        assert detect_lang_from_path("/docs/hello/", config) == "en"

    def test_translation_key_defaults_to_canonical_slug(self) -> None:
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        key = resolve_translation_key({}, slug="es/docs/hello", lang="es", config=config)
        assert key == "docs/hello"


class TestLocaleOverlayScan:
    def test_indexes_english_and_spanish_pages(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        _write_docs_tree(content)
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        catalog = DocCatalog(content, i18n_config=config)

        en = catalog.get_by_slug("docs/hello")
        es = catalog.get_by_slug("es/docs/hello")
        assert en is not None
        assert es is not None
        assert en.lang == "en"
        assert es.lang == "es"
        assert en.url == "/docs/hello/"
        assert es.url == "/es/docs/hello/"
        assert en.translation_key == es.translation_key == "docs/hello"

    def test_translation_index_links_locales(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        _write_docs_tree(content)
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        catalog = DocCatalog(content, i18n_config=config)
        index = build_translation_index(catalog.nodes)
        assert index["docs/hello"]["en"] == "/docs/hello/"
        assert index["docs/hello"]["es"] == "/es/docs/hello/"

    def test_doc_nodes_filtered_by_language(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        _write_docs_tree(content)
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        catalog = DocCatalog(content, i18n_config=config)
        en_nodes = catalog.doc_nodes(lang="en")
        es_nodes = catalog.doc_nodes(lang="es")
        assert all(node.lang == "en" for node in en_nodes)
        assert all(node.lang == "es" for node in es_nodes)


def _bootstrap_app_root(app_root: Path, content: Path) -> None:
    _write_i18n_docs_yaml(app_root / "docs.yaml")
    _write_mounts(app_root / "mounts.yaml", content)
    theme_src = APP_ROOT / "theme"
    if theme_src.is_dir():
        shutil.copytree(theme_src, app_root / "theme")
    locales_src = APP_ROOT / "locales"
    if locales_src.is_dir():
        shutil.copytree(locales_src, app_root / "locales")


class TestI18nRouting:
    @pytest.fixture(autouse=True)
    def _require_chirp_ui(self) -> None:
        pytest.importorskip("chirp_ui")

    def test_spanish_doc_route(self, tmp_path: Path) -> None:
        import asyncio

        from chirp.testing import TestClient

        app_root = tmp_path / "app"
        app_root.mkdir()
        content = app_root / "content"
        _write_docs_tree(content)
        _bootstrap_app_root(app_root, content)

        from furatena.catalog.docs_app import DocsApp

        docs = DocsApp.from_paths(
            app_root / "docs.yaml",
            repo_root=app_root,
            autodoc=False,
        )
        client = TestClient(docs.create_app())

        async def _fetch() -> str:
            resp = await client.get("/es/docs/hello/")
            assert resp.status == 200
            return resp.text

        assert "Hola" in asyncio.run(_fetch())

    def test_locale_context_in_page(self, tmp_path: Path) -> None:
        import asyncio

        from chirp.testing import TestClient

        app_root = tmp_path / "app"
        app_root.mkdir()
        content = app_root / "content"
        _write_docs_tree(content)
        _bootstrap_app_root(app_root, content)

        from furatena.catalog.docs_app import DocsApp

        docs = DocsApp.from_paths(
            app_root / "docs.yaml",
            repo_root=app_root,
            autodoc=False,
        )
        client = TestClient(docs.create_app())

        async def _fetch() -> str:
            resp = await client.get("/docs/hello/")
            assert resp.status == 200
            return resp.text

        assert 'hreflang="es"' in asyncio.run(_fetch())


class TestRegistryI18n:
    def test_registry_passes_i18n_to_shards(self, tmp_path: Path) -> None:
        app_root = tmp_path / "app"
        app_root.mkdir()
        content = app_root / "content"
        _write_docs_tree(content)
        _write_mounts(app_root / "mounts.yaml", content)
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        mounts = load_mounts(app_root / "mounts.yaml", repo_root=app_root)
        registry = CatalogRegistry(mounts, repo_root=app_root, i18n_config=config, autodoc=False)
        assert registry.i18n_config.enabled
        assert registry.get_by_slug("es/docs/hello") is not None
        ctx = locale_context(
            config,
            active_lang="en",
            node=registry.get_by_slug("docs/hello"),
            translation_index=registry.translation_index,
        )
        assert len(ctx["doc_languages"]) == 2
        es_entry = next(item for item in ctx["doc_languages"] if item["code"] == "es")
        assert es_entry["href"] == "/es/docs/hello/"


class TestI18nFallback:
    def test_resolve_fallback_for_missing_translation(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        _write_docs_tree(content)
        (content / "docs" / "only-en.md").write_text(
            "---\ntitle: Only EN\n---\n\nEnglish only.\n",
            encoding="utf-8",
        )
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        catalog = DocCatalog(content, i18n_config=config)
        match = resolve_localized_node(
            catalog,
            "es/docs/only-en",
            requested_lang="es",
            config=config,
        )
        assert match is not None
        assert match.fallback is True
        assert match.node.lang == "en"
        assert match.requested_url == "/es/docs/only-en/"

    def test_collect_export_routes_for_fallback_pages(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        _write_docs_tree(content)
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        catalog = DocCatalog(content, i18n_config=config)
        routes = collect_i18n_export_routes(catalog, config)
        assert "/es/docs/hello/" not in routes
        (content / "docs" / "only-en.md").write_text(
            "---\ntitle: Only EN\n---\n\nEnglish only.\n",
            encoding="utf-8",
        )
        catalog = DocCatalog(content, i18n_config=config)
        routes = collect_i18n_export_routes(catalog, config)
        assert "/es/docs/only-en/" in routes

    def test_fallback_route_renders_banner(self, tmp_path: Path) -> None:
        import asyncio

        from chirp.testing import TestClient

        app_root = tmp_path / "app"
        app_root.mkdir()
        content = app_root / "content"
        _write_docs_tree(content)
        (content / "docs" / "only-en.md").write_text(
            "---\ntitle: Only EN\n---\n\nEnglish only page.\n",
            encoding="utf-8",
        )
        _bootstrap_app_root(app_root, content)

        from furatena.catalog.docs_app import DocsApp

        docs = DocsApp.from_paths(
            app_root / "docs.yaml",
            repo_root=app_root,
            autodoc=False,
        )
        client = TestClient(docs.create_app())

        async def _fetch() -> str:
            resp = await client.get("/es/docs/only-en/")
            assert resp.status == 200
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-locale-fallback" in html
        assert "Only EN" in html


class TestI18nSitemapAndSearch:
    def test_sitemap_includes_hreflang(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        _write_docs_tree(content)
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        catalog = DocCatalog(content, i18n_config=config)
        xml = sitemap_xml(catalog)
        assert 'hreflang="en"' in xml
        assert 'hreflang="es"' in xml

    def test_search_json_includes_lang(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        _write_docs_tree(content)
        config = load_i18n_config(
            {"default_language": "en", "languages": ["en", "es"], "strategy": "subdir"}
        )
        catalog = DocCatalog(content, i18n_config=config)
        payload = search_json(catalog)
        langs = {entry["lang"] for entry in payload["entries"]}
        assert langs == {"en", "es"}
        assert "lang" in payload["facets"]
