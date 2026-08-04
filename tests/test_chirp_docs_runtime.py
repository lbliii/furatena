"""Runtime, assets, watch, and serve-mode tests."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
FROZEN_DIR = APP_ROOT / "frozen"

sys.path.insert(0, str(REPO / "src"))

from furatena import __version__
from furatena.catalog.application_roots import ApplicationRoots
from furatena.catalog.assets import bundle_css
from furatena.catalog.dev_banner import (
    ServeStartupPhase,
    ServeStartupResult,
    configure_pounce_display_defaults,
    format_serve_preflight,
    serve_reload_behavior,
)
from furatena.catalog.dev_reload import (
    DevServerRecord,
    browser_reload_dirs,
    clear_dev_server_record,
    dev_server_pid_path,
    process_reload_dirs,
    read_dev_server_record,
    stop_dev_server,
    write_dev_server_record,
)
from furatena.catalog.registry import load_mounts
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

        from furatena.catalog.renderer_fingerprint import (
            renderer_fingerprint,
            write_renderer_fingerprint,
        )

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
    def test_process_reload_dirs_empty_by_default(self) -> None:
        dirs = process_reload_dirs(REPO)
        assert dirs == ()

    def test_process_reload_dirs_include_src_when_env_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FURA_RELOAD_SRC", "1")
        dirs = process_reload_dirs(REPO)
        assert (REPO / "src" / "furatena").resolve().as_posix() in dirs

    def test_docs_app_registers_browser_reload_dirs_in_config(self) -> None:
        from furatena.catalog.docs_app import DocsApp
        from furatena.catalog.runtime import ServeConfig

        serve = ServeConfig(ServeMode.AUTHOR, None, False, True)
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=serve,
        )
        theme_dirs = set(browser_reload_dirs(docs.theme))
        config_dirs = set(docs.app.config.reload_dirs)
        assert theme_dirs
        assert theme_dirs <= config_dirs
        assert docs.app._reload_dirs_extra == []

    def test_browser_reload_dirs_exclude_docs_cache(self) -> None:
        from furatena.catalog.config import load_docs_config

        docs = load_docs_config(APP_ROOT / "docs.yaml")
        theme = DocsTheme.from_docs_config(docs)
        cache_dir = (APP_ROOT / ".docs-cache").resolve()
        dirs = {Path(d) for d in browser_reload_dirs(theme)}
        assert cache_dir not in dirs

    def test_format_serve_preflight_author(self) -> None:
        serve = ServeConfig(ServeMode.AUTHOR, None, False, True)
        lines = format_serve_preflight(
            ServeStartupResult(
                phase=ServeStartupPhase.PREFLIGHT,
                mode=serve.mode,
                configured_url="http://127.0.0.1:8001/",
                page_count=10,
                mount_count=2,
                frozen_dir=None,
                stale_freeze=serve.warn_stale_freeze,
                reload=serve_reload_behavior(serve),
                checks_skipped=True,
                check_elapsed_ms=0.0,
                diagnostics=(),
            )
        )
        assert lines[0] == "Catalog   10 pages · 2 mounts · author"
        assert "Reload    content: htmx · theme: browser" in lines[-1]
        assert all("http://" not in line for line in lines)

    def test_format_serve_preflight_preview(self) -> None:
        serve = ServeConfig(ServeMode.PREVIEW, FROZEN_DIR, True, False)
        lines = format_serve_preflight(
            ServeStartupResult(
                phase=ServeStartupPhase.PREFLIGHT,
                mode=serve.mode,
                configured_url="http://127.0.0.1:8001/",
                page_count=10,
                mount_count=2,
                frozen_dir=str(FROZEN_DIR),
                stale_freeze=serve.warn_stale_freeze,
                reload=serve_reload_behavior(serve),
                checks_skipped=True,
                check_elapsed_ms=0.0,
                diagnostics=(),
            )
        )
        assert lines[0] == "Catalog   10 pages · 2 mounts · preview"
        assert lines[-1] == "Reload    frozen catalog · live reload off"
        assert all("http://" not in line for line in lines)

    def test_pounce_display_uses_furatena_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        names = (
            "POUNCE_APP_NAME",
            "POUNCE_APP_TAGLINE",
            "POUNCE_APP_VERSION",
            "POUNCE_SIGNAGE",
        )
        for name in names:
            monkeypatch.delenv(name, raising=False)

        configure_pounce_display_defaults()

        assert os.environ["POUNCE_APP_NAME"] == "Furatena"
        assert os.environ["POUNCE_APP_TAGLINE"] == "Live documentation from markdown"
        assert os.environ["POUNCE_APP_VERSION"] == __version__
        assert os.environ["POUNCE_SIGNAGE"] == "minimal"

    def test_pounce_display_preserves_operator_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        overrides = {
            "POUNCE_APP_NAME": "Custom docs",
            "POUNCE_APP_TAGLINE": "Custom tagline",
            "POUNCE_APP_VERSION": "2026.8",
            "POUNCE_SIGNAGE": "off",
        }
        for name, value in overrides.items():
            monkeypatch.setenv(name, value)

        configure_pounce_display_defaults()

        assert {name: os.environ[name] for name in overrides} == overrides


class TestServeDebugAndCache:
    def test_private_image_pins_one_pounce_serving_worker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from furatena.catalog.docs_app import DocsApp
        from furatena.catalog.runtime import ServeConfig

        monkeypatch.setenv("FURA_DISTRIBUTION", "private-image")
        monkeypatch.delenv("FURA_SERVER_WORKERS", raising=False)
        serve = ServeConfig(ServeMode.PREVIEW, FROZEN_DIR, True, False)
        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
            serve=serve,
        )

        assert docs.app.config.workers == 1

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
        assert not any(href == "/docs-theme/core/style.css" for href in theme.stylesheet_hrefs)
        assert "/docs-theme/local/directives.css" in theme.stylesheet_hrefs


class TestHighlight:
    def test_highlight_code_block_includes_rosettes_and_wrapper(self) -> None:
        from furatena.catalog.highlight import highlight_code_block

        html = highlight_code_block("python", "import chirp")
        assert 'class="rosettes"' in html
        assert "code-block-wrapper" in html
        assert "data-fura-copy-code" in html
        assert "syntax-import" in html
        assert "<pre><code>" in html
        assert "&lt;pre&gt;" not in html


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
    from chirp.testing import TestClient

    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.runtime import ServeConfig

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


class TestDevServerLifecycle:
    def test_pid_record_roundtrip(self, tmp_path: Path) -> None:
        path = dev_server_pid_path(tmp_path)
        write_dev_server_record(path, pid=1234, host="127.0.0.1", port=8001)
        record = read_dev_server_record(path)
        assert record == DevServerRecord(pid=1234, host="127.0.0.1", port=8001)
        clear_dev_server_record(path)
        assert read_dev_server_record(path) is None

    def test_managed_serve_record_uses_writable_runtime_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from furatena.catalog import docs_app as docs_app_module
        from furatena.catalog.docs_app import DocsApp

        site = tmp_path / "read-only-site"
        site.mkdir()
        site.chmod(0o555)
        state = tmp_path / "runtime-state"
        state.mkdir()
        expected = dev_server_pid_path(site, state_root=state)

        class FakeApp:
            config = SimpleNamespace(host="127.0.0.1", port=8001)

            @staticmethod
            def run(*, host: str | None, port: int | None) -> None:
                assert host is None
                assert port is None
                assert read_dev_server_record(expected) == DevServerRecord(
                    pid=os.getpid(),
                    host="127.0.0.1",
                    port=8001,
                )

        def fake_stop(
            repo_root: Path,
            *,
            host: str | None,
            port: int | None,
            state_root: Path | None,
        ) -> bool:
            assert repo_root == site
            assert host == "127.0.0.1"
            assert port == 8001
            assert state_root == state
            return False

        monkeypatch.setattr(docs_app_module, "stop_dev_server", fake_stop)
        docs = object.__new__(DocsApp)
        docs.repo_root = site
        docs.roots = ApplicationRoots(
            site=site,
            platform=tmp_path / "platform",
            state=state,
            output=tmp_path / "output",
            managed=True,
        )
        docs.app = FakeApp()
        docs.serve = ServeConfig(ServeMode.PREVIEW, None, True, False)

        try:
            docs.run_serve()
        finally:
            site.chmod(0o755)

        assert not expected.exists()
        assert not (site / ".context").exists()

    def test_stop_dev_server_reports_idle_port(self, tmp_path: Path) -> None:
        assert stop_dev_server(tmp_path, host="127.0.0.1", port=59999) is False

    def test_stop_dev_server_terminates_recorded_pid(self, tmp_path: Path) -> None:
        import subprocess

        proc = subprocess.Popen(["sleep", "30"])
        try:
            write_dev_server_record(
                dev_server_pid_path(tmp_path),
                pid=proc.pid,
                host="127.0.0.1",
                port=59998,
            )
            assert stop_dev_server(tmp_path, host="127.0.0.1", port=59998) is True
            assert proc.poll() is not None
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_stop_dev_server_preserves_recorded_pid_for_other_port(self, tmp_path: Path) -> None:
        import subprocess

        proc = subprocess.Popen(["sleep", "30"])
        try:
            pid_path = dev_server_pid_path(tmp_path)
            write_dev_server_record(
                pid_path,
                pid=proc.pid,
                host="127.0.0.1",
                port=59998,
            )
            assert stop_dev_server(tmp_path, host="127.0.0.1", port=59999) is False
            assert proc.poll() is None
            assert read_dev_server_record(pid_path) == DevServerRecord(
                pid=proc.pid,
                host="127.0.0.1",
                port=59998,
            )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
