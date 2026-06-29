"""Wave 13 tests for Kida view lint and author selective reload."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from chirp.testing.sse import extract_sse_attrs

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from chirp.testing.client import TestClient

from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.incremental import is_partial_reload
from furatena.catalog.registry import CatalogRegistry, MountConfig
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.theme import DocsTheme
from furatena.catalog.view_lint import check_view_templates
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml


@pytest.fixture(scope="module")
def docs_config():
    return load_docs_config(APP_ROOT / "docs.yaml")


@pytest.fixture(scope="module")
def docs_theme(docs_config):
    return DocsTheme.from_docs_config(docs_config)


class TestViewLint:
    def test_registered_views_load_without_errors(self, docs_config, docs_theme) -> None:
        registry = CatalogRegistry.from_config(
            APP_ROOT / "mounts.yaml",
            repo_root=REPO,
            autodoc=False,
            autodoc_config=None,
        )
        errors, warnings = check_view_templates(
            docs_config,
            docs_theme,
            repo_root=REPO,
            catalog=registry,
        )
        assert not errors, errors
        assert isinstance(warnings, list)

    def test_search_shell_partials_smoke_render(self, docs_config, docs_theme) -> None:
        from furatena.catalog.template_env import check_search_shell_templates

        registry = CatalogRegistry.from_config(
            APP_ROOT / "mounts.yaml",
            repo_root=REPO,
            autodoc=False,
            autodoc_config=None,
        )
        from furatena.catalog.template_env import build_docs_template_env

        env = build_docs_template_env(docs_config, docs_theme, repo_root=REPO)
        errors = check_search_shell_templates(env, registry)
        assert not errors, errors


class TestAuthorInvalidationRegistry:
    def test_registry_delegates_hints_and_clear(self, tmp_path: Path) -> None:
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "page.md"
        page.write_text("---\ntitle: Page\n---\n# Page\n\nHello.\n", encoding="utf-8")

        registry = CatalogRegistry(
            (
                MountConfig(
                    id="chirp",
                    label="Chirp",
                    content_root=content,
                    default=True,
                ),
            ),
            repo_root=tmp_path,
            autodoc=False,
            auto_reload=True,
        )
        shard = registry._shards["chirp"]

        page.write_text(
            "---\ntitle: Page\n---\n# Page\n\n## Section\n\nHello.\n",
            encoding="utf-8",
        )
        shard._reindex_paths({page})
        hints = registry.invalidation_hints("docs/page")
        assert "toc-panel" in hints
        assert is_partial_reload(hints)

        entries = registry.author_stale_entries("docs/page")
        assert entries and entries[0]["slug"] == "docs/page"

        registry.clear_invalidation_hints("docs/page")
        assert registry.invalidation_hints("docs/page") == ()
        assert registry.author_stale_entries("docs/page") == []

    def test_single_page_full_reload_preserves_author_hint(self, tmp_path: Path) -> None:
        import os
        import time

        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "page.md"
        page.write_text("---\ntitle: Page\n---\n# Page\n\nHello.\n", encoding="utf-8")

        registry = CatalogRegistry(
            (
                MountConfig(
                    id="chirp",
                    label="Chirp",
                    content_root=content,
                    default=True,
                ),
            ),
            repo_root=tmp_path,
            autodoc=False,
            auto_reload=True,
        )
        assert registry.get_by_slug("docs/page") is not None
        registry._watcher = None
        registry._shards["chirp"]._watcher = None

        page.write_text("---\ntitle: Page\n---\n# Page\n\nUpdated.\n", encoding="utf-8")
        now = time.time() + 2
        os.utime(page, (now, now))

        assert registry.refresh_if_stale() is True
        assert registry.invalidation_hints("docs/page") == (
            "page-root",
            "toc-panel",
            "head-meta",
            "docs-sidebar",
        )


class TestAuthorStaleRoute:
    def _write_author_app(self, tmp_path: Path) -> tuple[DocsApp, Path]:
        copy_app_theme(tmp_path, APP_ROOT)
        write_minimal_docs_yaml(tmp_path / "docs.yaml")
        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "page.md"
        page.write_text("---\ntitle: Page\n---\n# Page\n\nHello.\n", encoding="utf-8")
        write_mounts_yaml(tmp_path / "mounts.yaml", content)
        app = DocsApp.from_paths(
            tmp_path / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
        )
        return app, page

    def test_author_stale_json_when_auto_reload(self, docs_config) -> None:
        import asyncio

        app = DocsApp(
            docs_config,
            repo_root=REPO,
            autodoc=False,
            serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
        ).create_app()
        client = TestClient(app)

        async def _fetch():
            return await client.get("/docs/_author/stale?slug=docs")

        response = asyncio.run(_fetch())
        assert response.status == 200
        payload = json.loads(response.text.split("<", 1)[0])
        assert "stale" in payload
        assert "current" in payload

    def test_author_stale_empty_when_not_auto_reload(self, docs_config) -> None:
        import asyncio

        app = DocsApp(
            docs_config,
            repo_root=REPO,
            autodoc=False,
            serve=ServeConfig(ServeMode.PREVIEW, None, True, False),
        ).create_app()
        client = TestClient(app)

        async def _fetch():
            return await client.get("/docs/_author/stale?slug=docs")

        response = asyncio.run(_fetch())
        payload = json.loads(response.text.split("<", 1)[0])
        assert payload["stale"] == []
        assert payload["current"] is None

    def test_author_sse_event_reports_invalidation_payload(self, tmp_path: Path) -> None:
        import asyncio

        docs, _page = self._write_author_app(tmp_path)
        docs.catalog._shards["chirp"]._last_invalidations["docs/page"] = ("toc-panel",)
        client = TestClient(docs.create_app())

        async def _collect():
            return await client.sse("/docs/_author/events?slug=docs/page", max_events=1)

        result = asyncio.run(_collect())
        assert result.status == 200
        assert result.events
        event = result.events[0]
        assert event.event == "author-invalidate"
        payload = json.loads(event.data)
        assert payload["generation"]
        assert payload["current"]["slug"] == "docs/page"
        assert "toc-panel" in payload["current"]["target_hints"]
        assert payload["current"]["dirty_paths"] == ["docs/page.md"]

    def test_author_page_wires_htmx_sse_before_polling_fallback(self, tmp_path: Path) -> None:
        import asyncio

        docs, _page = self._write_author_app(tmp_path)
        client = TestClient(docs.create_app())

        async def _fetch():
            return await client.get("/docs/page/")

        response = asyncio.run(_fetch())
        assert response.status == 200
        connects, swaps = extract_sse_attrs(response.text)
        assert "/docs/_author/events?slug=docs/page" in connects
        assert "author-invalidate" in swaps
        assert 'window.__furaAuthorReloadMode = "sse"' in response.text
        assert "if (!startSseReload())" in response.text
        assert "window.__furaDocsAuthorReloadState" in response.text
        assert "if (window.__furaDocsAuthorReload) return;" not in response.text
        assert 'marker.dataset.furaAuthorSseBound !== "1"' in response.text
        assert 'marker.addEventListener("htmx:sseMessage"' in response.text
        assert "state.eventSourceSlug === slug" in response.text
        assert "function stopPollingFallback" in response.text
        assert "window.clearInterval(state.pollTimer)" in response.text
        assert "function restoreViewport" in response.text
        assert "function requestHardReload" in response.text
        assert "window.__furaAuthorLastReloadKind" in response.text
        assert "var forceFullReload = Boolean(payload.current.reload);" in response.text
        assert "reloadCurrentPage(forceFullReload);" in response.text
        assert "if (forceFullReload) requestHardReload();" in response.text
        assert "window.__furaAuthorReloadMode = \"poll\"" in response.text
        assert "function setupPageActionCopies" in response.text
        assert 'target.closest("[data-action]")' in response.text
        assert "copyPayloadForAction(button, action)" in response.text
        assert "data-copy-state" in response.text
        assert response.text.index("function startSseReload") < response.text.index("startPollingFallback();")

    def test_author_page_actions_contract_is_stable_for_mobile_and_htmx(self, tmp_path: Path) -> None:
        import asyncio

        docs, _page = self._write_author_app(tmp_path)
        client = TestClient(docs.create_app())

        async def _fetch():
            return await client.get("/docs/page/")

        response = asyncio.run(_fetch())
        assert response.status == 200
        assert 'data-chirp-page-actions' in response.text
        assert 'data-action="copy-source-path"' in response.text
        assert 'data-source-path="' in response.text
        assert "Author controls" in response.text
        assert "Local only" in response.text
        assert "fura-author-chrome__meta-icon" in response.text
        assert 'aria-label="Lifecycle actions"' in response.text
        assert 'aria-label="Source and output actions"' in response.text
        assert "Lifecycle" in response.text
        assert "Inspect public output" in response.text
        assert "/docs/_author/page.json?slug=docs/page&amp;inspect_public=1" in response.text
        assert 'id="fura-author-chrome"' in response.text
        assert response.text.count("Copy source path") >= 2

        css = (REPO / "src/furatena/themes/furatena/assets/css/chirp-theme.css").read_text(
            encoding="utf-8"
        )
        assert "width: min(22rem, calc(100vw - 1rem));" in css
        assert "grid-template-columns: 1.35rem minmax(0, 1fr);" in css
        assert ".fura-author-chrome__body" in css
        assert ".fura-author-chrome__metadata" in css
        assert ".fura-author-chrome__action-group--primary" in css
        assert ".fura-author-chrome__meta-icon" in css
        assert "grid-template-columns: minmax(14rem, 0.9fr) minmax(18rem, 1.15fr);" in css
        assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in css
        assert "@media (max-width: 480px)" in css

    def test_author_reload_after_source_edit_updates_dom_and_clears_hints(self, tmp_path: Path) -> None:
        import asyncio
        import os
        import time

        docs, page = self._write_author_app(tmp_path)
        client = TestClient(docs.create_app())

        page.write_text(
            "---\ntitle: Page\n---\n# Page\n\n## New section\n\nUpdated body.\n",
            encoding="utf-8",
        )
        now = time.time() + 2
        os.utime(page, (now, now))
        docs.catalog._shards["chirp"]._reindex_paths({page})

        async def _reload():
            return await client.get(
                "/docs/page/",
                headers={"HX-Request": "true", "HX-Docs-Author-Reload": "1"},
            )

        response = asyncio.run(_reload())
        assert response.status == 200
        assert "Updated body." in response.text
        assert "New section" in response.text
        assert 'hx-swap-oob="true:#toc-panel"' in response.text or 'id="toc-panel"' in response.text
        assert docs.catalog.invalidation_hints("docs/page") == ()

    def test_author_sse_payload_marks_full_reload_for_theme_level_hints(self, tmp_path: Path) -> None:
        import asyncio

        docs, _page = self._write_author_app(tmp_path)
        docs.catalog._shards["chirp"]._last_invalidations["docs/page"] = (
            "page-root",
            "toc-panel",
            "head-meta",
            "docs-sidebar",
        )
        client = TestClient(docs.create_app())

        async def _fetch():
            return await client.get("/docs/_author/stale?slug=docs/page")

        response = asyncio.run(_fetch())
        payload = json.loads(response.text.split("<", 1)[0])
        assert payload["current"]["reload"] is True
        assert payload["current"]["target_hints"] == [
            "page-root",
            "toc-panel",
            "head-meta",
            "docs-sidebar",
        ]
