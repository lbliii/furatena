"""Error page templates and hypermedia recovery."""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.error_experience import (
    build_error_context,
    guess_search_query,
    recovery_hits_for_query,
    split_recovery_hits,
)
from furatena.catalog.semantic import HybridHit
from tests.support import write_minimal_docs_yaml, write_mounts_yaml


class TestErrorExperienceHelpers:
    def test_guess_search_query_from_path(self) -> None:
        assert guess_search_query("/docs/reference/routing/") == "reference routing"
        assert guess_search_query("/docs/get-started/") == "get started"
        assert guess_search_query("/") == ""

    def test_split_recovery_hits(self) -> None:
        class Node:
            def __init__(self, node_id: str, title: str = "T", section: str = ""):
                self.node_id = node_id
                self.title = title
                self.section = section
                self.description = ""

        hits = (
            HybridHit(node=Node("a"), score=10, snippet="a", keyword_score=5, semantic_score=0),
            HybridHit(node=Node("b"), score=8, snippet="b", keyword_score=0, semantic_score=8),
        )
        keyword, semantic = split_recovery_hits(hits)
        assert len(keyword) == 1
        assert len(semantic) == 1
        assert keyword[0].node.node_id == "a"
        assert semantic[0].node.node_id == "b"


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


class TestErrorPages:
    def test_not_found_renders_recovery_shell(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/definitely-missing-page/")
            assert resp.status == 404
            return resp.text

        html = asyncio.run(_fetch())
        assert "fura-error-page" in html
        assert "fura-error-page--404" in html
        assert "fura-error__hero" in html
        assert "fura-error__search-input" in html
        assert "404 — Page not found" in html
        assert "/definitely-missing-page/" in html

    def test_not_found_prefills_search_from_path(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/reference/nonexistent-topic/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'value="reference nonexistent topic"' in html or 'value="nonexistent topic"' in html

    def test_error_suggest_fragment(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/errors/suggest?q=routing")
            assert resp.status == 200
            return resp.text

        html = asyncio.run(_fetch())
        assert (
            "fura-error__suggest-list" in html
            or "fura-error__suggest-empty" in html
            or "Related pages" in html
        )

    def test_boosted_not_found_swaps_page_root(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get(
                "/missing-boost-target/",
                headers={"HX-Request": "true", "HX-Boosted": "true"},
            )
            assert resp.status == 404
            return resp.text

        html = asyncio.run(_fetch())
        assert 'id="page-root"' in html
        assert "fura-error-page" in html
        assert "hx-swap-oob" in html

    def test_method_not_allowed_page(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            landing = await docs_client.get("/")
            match = re.search(r'<meta name="csrf-token" content="([^"]+)">', landing.text)
            assert match is not None
            headers = {str(key).lower(): str(value) for key, value in landing.headers}
            cookie = headers["set-cookie"].split(";", 1)[0]
            resp = await docs_client.request(
                "POST",
                "/catalog.json",
                headers={"Cookie": cookie, "X-CSRF-Token": match.group(1)},
            )
            assert resp.status == 405
            return resp.text

        html = asyncio.run(_fetch())
        assert "405 — Method not allowed" in html
        assert "fura-error-page--405" in html
        assert "GET" in html
        assert "fura-error__search-input" not in html

    def test_build_error_context_sets_title(self) -> None:
        from furatena.catalog.docs_app import DocsApp

        docs = DocsApp.from_paths(APP_ROOT / "docs.yaml", repo_root=REPO, autodoc=False)
        ctx = build_error_context(docs, None, status=404)
        ctx["error_path"] = "/docs/missing/"
        assert ctx["error_title"] == "404 — Page not found"
        assert ctx["error_show_search"] is True

    def test_build_error_context_for_other_statuses(self) -> None:
        from furatena.catalog.docs_app import DocsApp

        docs = DocsApp.from_paths(APP_ROOT / "docs.yaml", repo_root=REPO, autodoc=False)
        for status, title, show_search in (
            (403, "403 — Forbidden", False),
            (405, "405 — Method not allowed", False),
            (413, "413 — Payload too large", False),
            (500, "500 — Something went wrong", False),
        ):
            ctx = build_error_context(docs, None, status=status)
            assert ctx["error_title"] == title
            assert ctx["error_show_search"] is show_search

    def test_recovery_hits_use_hybrid_search(self) -> None:
        from furatena.catalog.docs_app import DocsApp

        docs = DocsApp.from_paths(APP_ROOT / "docs.yaml", repo_root=REPO, autodoc=False)
        keyword, semantic = recovery_hits_for_query(docs, "routing", limit=4)
        assert isinstance(keyword, tuple)
        assert isinstance(semantic, tuple)


class TestErrorTemplateOverride:
    def test_theme_shadow_wins_over_framework(self, tmp_path: Path) -> None:
        from chirp.templating.integration import create_environment

        from furatena.catalog.docs_app import DocsApp

        shutil.copytree(APP_ROOT / "theme", tmp_path / "theme")
        write_minimal_docs_yaml(tmp_path / "docs.yaml")
        content = tmp_path / "content"
        content.mkdir()
        write_mounts_yaml(tmp_path / "mounts.yaml", content)
        shadow_dir = tmp_path / "theme" / "templates"
        shadow_dir.mkdir(parents=True, exist_ok=True)
        marker = "{# theme error override #}"
        (shadow_dir / "error.html").write_text(marker, encoding="utf-8")

        docs = DocsApp.from_paths(tmp_path / "docs.yaml", repo_root=REPO, autodoc=False)
        app = docs.create_app()
        env = create_environment(
            app.config,
            app._mutable_state.template_filters,
            app._mutable_state.template_globals,
        )
        source, filename = env.loader.get_source("error.html")
        assert marker in source
        assert "theme/templates" in filename.replace("\\", "/")
