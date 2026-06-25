"""Runtime, assets, watch, and serve-mode tests."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
FROZEN_DIR = APP_ROOT / "frozen"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.assets import bundle_css
from furatena.catalog.paths import catalog_root
from furatena.catalog.registry import load_mounts
from furatena.catalog.dev_banner import extra_reload_dirs, format_serve_startup
from furatena.catalog.runtime import ServeConfig, ServeMode, resolve_serve_config
from furatena.catalog.theme import DocsTheme
from furatena.catalog.watch import SourceWatcher


class TestCssBundle:
    def test_bundle_produces_single_file(self) -> None:
        from furatena.catalog.theme import _packaged_theme_assets

        packaged = _packaged_theme_assets("chirp", APP_ROOT)
        if packaged is None:
            pytest.skip("vendored theme assets missing")
        css_dir, _fonts, _branding = packaged
        cache = APP_ROOT / ".docs-cache-test"
        cache.mkdir(exist_ok=True)
        bundle, digest = bundle_css(css_dir / "style.css", cache_dir=cache)
        assert bundle.is_file()
        assert digest
        assert bundle.name == f"theme.{digest}.css"
        text = bundle.read_text(encoding="utf-8")
        assert "chirp-theme-docs-layout" in text
        assert "@import url(" not in text


class TestServeMode:
    def test_author_when_freeze_is_stale(self) -> None:
        if not FROZEN_DIR.is_dir():
            pytest.skip("no frozen catalog")
        import os

        mounts = load_mounts(APP_ROOT / "mounts.yaml", repo_root=REPO)
        # Ensure content is newer than frozen export (post-freeze edits or touched files).
        content_roots = tuple(m.content_root for m in mounts)
        for root in content_roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*.md"):
                os.utime(path, None)
                break
        cfg = resolve_serve_config(
            docs_root=APP_ROOT,
            content_roots=content_roots,
        )
        assert cfg.mode == ServeMode.AUTHOR
        assert cfg.frozen_dir is None
        assert cfg.auto_reload is True
        assert cfg.lazy_html is False

    def test_hybrid_when_freeze_is_current(self, tmp_path: Path) -> None:
        import os

        frozen = tmp_path / "frozen"
        pages = frozen / "pages"
        pages.mkdir(parents=True)
        (frozen / "catalog.json").write_text('{"pages":[]}', encoding="utf-8")
        (pages / "index.html").write_text("<p>ok</p>", encoding="utf-8")
        content = tmp_path / "content"
        content.mkdir()
        (content / "doc.md").write_text("---\ntitle: Doc\n---\n\nBody\n", encoding="utf-8")
        docs_root = tmp_path / "docs"
        (docs_root / "catalog").mkdir(parents=True)
        (docs_root / "theme").mkdir(parents=True)

        freeze_time = time.time() + 60
        content_time = time.time() - 60
        for path in frozen.rglob("*"):
            if path.is_file():
                os.utime(path, (freeze_time, freeze_time))
        for path in (content / "doc.md", docs_root / "catalog", docs_root / "theme"):
            os.utime(path, (content_time, content_time))

        from furatena.catalog.renderer_fingerprint import renderer_fingerprint, write_renderer_fingerprint

        write_renderer_fingerprint(frozen, renderer_fingerprint(docs_root))

        cfg = resolve_serve_config(
            docs_root=docs_root,
            content_roots=(content,),
            frozen_dir=frozen,
        )
        assert cfg.mode == ServeMode.HYBRID
        assert cfg.frozen_dir == frozen
        assert cfg.lazy_html is True

    def test_preview_mode(self) -> None:
        mounts = load_mounts(APP_ROOT / "mounts.yaml", repo_root=REPO)
        cfg = resolve_serve_config(
            docs_root=APP_ROOT,
            content_roots=tuple(m.content_root for m in mounts),
            mode=ServeMode.PREVIEW,
        )
        assert cfg.mode == ServeMode.PREVIEW
        assert cfg.auto_reload is False


class TestDevReloadWiring:
    def test_extra_reload_dirs_include_catalog(self) -> None:
        from furatena.catalog.config import load_docs_config

        docs = load_docs_config(APP_ROOT / "docs.yaml")
        dirs = extra_reload_dirs(docs, REPO)
        catalog_dir = catalog_root()
        templates_dir = docs.framework_templates_dir.resolve()
        assert catalog_dir in dirs
        assert templates_dir in dirs

    def test_extra_reload_dirs_include_src_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from furatena.catalog.config import load_docs_config

        monkeypatch.setenv("FURA_RELOAD_SRC", "1")
        docs = load_docs_config(APP_ROOT / "docs.yaml")
        dirs = extra_reload_dirs(docs, REPO)
        assert (REPO / "src" / "furatena").resolve() in dirs

    def test_docs_app_registers_catalog_reload_dir(self) -> None:
        from furatena.catalog.docs_app import DocsApp
        from furatena.catalog.runtime import ServeConfig

        serve = ServeConfig(ServeMode.AUTHOR, None, False, True)
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=serve,
        )
        catalog_dir = str(catalog_root().resolve())
        assert catalog_dir in docs.app._reload_dirs_extra

    def test_format_serve_startup_author(self) -> None:
        lines = format_serve_startup(
            ServeConfig(ServeMode.AUTHOR, None, False, True),
            page_count=10,
            mount_count=2,
            url="http://127.0.0.1:8001/",
        )
        assert "Mode: author" in lines[0]
        assert "reload: content (htmx)" in lines[0]
        assert lines[1] == "Open http://127.0.0.1:8001/"

    def test_format_serve_startup_preview(self) -> None:
        lines = format_serve_startup(
            ServeConfig(ServeMode.PREVIEW, FROZEN_DIR, True, False),
            page_count=10,
            mount_count=2,
            url="http://127.0.0.1:8001/",
        )
        assert "no live reload" in lines[0]
        assert "reload:" not in lines[0]


class TestServeDebugAndCache:
    def test_preview_disables_debug(self) -> None:
        if not FROZEN_DIR.is_dir():
            pytest.skip("no frozen catalog")
        from furatena.catalog.docs_app import DocsApp
        from furatena.catalog.runtime import ServeConfig

        serve = ServeConfig(ServeMode.PREVIEW, FROZEN_DIR, True, False)
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=serve,
        )
        assert docs.app.config.debug is False
        assert docs.app.config.skip_contract_checks is True

    def test_hybrid_skips_contract_checks_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if not FROZEN_DIR.is_dir():
            pytest.skip("no frozen catalog")
        from furatena.catalog.docs_app import DocsApp
        from furatena.catalog.runtime import ServeConfig

        monkeypatch.setenv("CHIRP_SKIP_CONTRACT_CHECKS", "1")
        serve = ServeConfig(ServeMode.HYBRID, FROZEN_DIR, True, True)
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=serve,
        )
        assert docs.app.config.debug is True
        assert docs.app.config.skip_contract_checks is True

    def test_bundled_css_has_immutable_cache(self, docs_client_hybrid) -> None:
        async def _fetch() -> str | None:
            resp = await docs_client_hybrid.get("/docs/get-started/installation/")
            css_path = None
            for part in resp.text.split('"'):
                if part.startswith("/docs-assets/theme.") and part.endswith(".css"):
                    css_path = part
                    break
            if css_path is None:
                return None
            asset = await docs_client_hybrid.get(css_path)
            for name, value in asset.headers:
                if name == "cache-control":
                    return value
            return None

        cache = asyncio.run(_fetch())
        assert cache == "public, max-age=31536000, immutable"

    def test_local_theme_short_cache_in_hybrid(self, docs_client_hybrid) -> None:
        async def _fetch() -> str | None:
            resp = await docs_client_hybrid.get("/docs-theme/local/directives.css")
            if resp.status != 200:
                return None
            for name, value in resp.headers:
                if name == "cache-control":
                    return value
            return None

        cache = asyncio.run(_fetch())
        assert cache == "public, max-age=300"


class TestBundledTheme:
    def test_stylesheet_uses_bundle(self) -> None:
        from furatena.catalog.config import load_docs_config

        docs = load_docs_config(APP_ROOT / "docs.yaml")
        theme = DocsTheme.from_docs_config(docs)
        assert any("/docs-assets/theme." in href for href in theme.stylesheet_hrefs)
        assert not any("/docs-theme/core/style.css" == href for href in theme.stylesheet_hrefs)
        assert "/docs-theme/local/directives.css" in theme.stylesheet_hrefs


class TestHighlight:
    def test_highlight_code_block_includes_rosettes_and_wrapper(self) -> None:
        from furatena.catalog.highlight import highlight_code_block

        html = highlight_code_block("python", "import chirp")
        assert 'class="rosettes"' in html
        assert "code-block-wrapper" in html
        assert "data-fura-copy-code" in html
        assert "syntax-import" in html


class TestRendererFingerprint:
    def test_fingerprint_roundtrip(self, tmp_path: Path) -> None:
        from furatena.catalog.renderer_fingerprint import (
            read_renderer_fingerprint,
            renderer_fingerprint,
            write_renderer_fingerprint,
        )

        docs_root = tmp_path / "docs"
        (docs_root / "catalog").mkdir(parents=True)
        (docs_root / "theme").mkdir(parents=True)
        frozen = tmp_path / "frozen"
        frozen.mkdir()
        fp = renderer_fingerprint(docs_root)
        write_renderer_fingerprint(frozen, fp)
        assert read_renderer_fingerprint(frozen) == fp


@pytest.fixture(scope="module")
def docs_client_hybrid():
    if not FROZEN_DIR.is_dir():
        pytest.skip("no frozen catalog")
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.runtime import ServeConfig
    from chirp.testing import TestClient

    serve = ServeConfig(ServeMode.HYBRID, FROZEN_DIR, True, True)
    docs = DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc_config=REPO / "config" / "autodoc.yaml",
        autodoc=False,
        serve=serve,
    )
    return TestClient(docs.create_app())


class TestHybridPerf:
    def test_hybrid_startup_is_fast(self) -> None:
        if not FROZEN_DIR.is_dir():
            pytest.skip("no frozen catalog")
        from furatena.catalog.docs_app import DocsApp
        from furatena.catalog.runtime import ServeConfig

        serve = ServeConfig(ServeMode.HYBRID, FROZEN_DIR, True, True)
        t0 = time.perf_counter()
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=serve,
        )
        elapsed = time.perf_counter() - t0
        assert len(docs.catalog.nodes) > 100
        assert elapsed < 3.5

    def test_refresh_without_full_scan(self, docs_client_hybrid) -> None:
        from furatena.catalog.docs_app import DocsApp
        from furatena.catalog.runtime import ServeConfig

        serve = ServeConfig(ServeMode.HYBRID, FROZEN_DIR, True, True)
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=serve,
        )
        for shard in docs.catalog._shards.values():
            assert shard._watcher is not None
        t0 = time.perf_counter()
        for _ in range(50):
            docs.catalog.refresh_if_stale()
        elapsed = (time.perf_counter() - t0) / 50
        assert elapsed < 0.001

    def test_bundled_css_in_html(self, docs_client_hybrid) -> None:
        async def _fetch() -> str:
            resp = await docs_client_hybrid.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "/docs-assets/theme." in html

    def test_branding_assets_in_hybrid_mode(self, docs_client_hybrid) -> None:
        async def _fetch(path: str) -> tuple[int, int]:
            resp = await docs_client_hybrid.get(path)
            return resp.status, len(resp.body)

        status, size = asyncio.run(_fetch("/docs-theme/branding/favicon.svg"))
        assert status == 200
        assert size < 10_000


class TestSourceWatcher:
    def test_marks_dirty_on_touch(self, tmp_path: Path) -> None:
        md = tmp_path / "doc.md"
        md.write_text("---\ntitle: T\n---\n\nbody\n", encoding="utf-8")
        watcher = SourceWatcher((tmp_path,), interval=0.05)
        watcher.start()
        time.sleep(0.15)
        assert watcher.drain_dirty() == set()
        md.write_text("---\ntitle: T2\n---\n\nbody\n", encoding="utf-8")
        time.sleep(0.2)
        dirty = watcher.drain_dirty()
        watcher.stop()
        assert md.resolve() in dirty
