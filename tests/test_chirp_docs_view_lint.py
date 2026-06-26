"""Wave 13 tests for Kida view lint and author selective reload."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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


class TestAuthorStaleRoute:
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
