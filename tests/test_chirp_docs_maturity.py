"""Wave 23 maturity checks — vendor scripts, favicon, error shell."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.links import shell_link_attrs


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


class TestMaturityAssets:
    def test_home_page_uses_vendored_htmx(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "/docs-vendor/htmx.min.js" in html
        assert "/docs-vendor/htmx-ext-preload.js" in html
        assert '<body hx-ext="preload"' in html
        assert "unpkg.com" not in html

    def test_vendor_scripts_are_served(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> None:
            for path in (
                "/docs-vendor/htmx.min.js",
                "/docs-vendor/htmx-ext-sse.js",
                "/docs-vendor/htmx-ext-preload.js",
            ):
                resp = await docs_client.get(path)
                assert resp.status == 200, path
                assert len(resp.body) > 100, path

        asyncio.run(_fetch())

    def test_favicon_ico_is_served(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> None:
            resp = await docs_client.get("/favicon.ico")
            assert resp.status == 200
            assert len(resp.body) > 0

        asyncio.run(_fetch())


class TestMaturityNav:
    def test_home_nav_drops_portal_link(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'href="/portal/"' not in html.split("chirp-theme-shell__desktop-nav")[1].split("</div>")[0]

    def test_develop_menu_links_shared_reference(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/")
            return resp.text

        html = asyncio.run(_fetch())
        assert 'href="/shared/"' in html
        assert 'href="/develop/catalog/"' in html
        assert 'hx-boost="true"' in html.split('href="/shared/"')[1].split(">", 1)[0]


class TestMaturityPreviewParity:
    def test_assets_manifest_supports_vendor_prefix(self, tmp_path: Path) -> None:
        from furatena.catalog.assets import load_assets_manifest, write_assets_manifest

        frozen = tmp_path / "frozen"
        write_assets_manifest(
            frozen,
            theme_href="theme.abc123.css",
            branding_prefix="branding",
            vendor_prefix="vendor",
        )
        manifest = load_assets_manifest(frozen)
        assert manifest is not None
        assert manifest.get("vendor_prefix") == "vendor"
        assert manifest.get("branding_prefix") == "branding"

    def test_preview_theme_mounts_frozen_vendor(self, tmp_path: Path) -> None:
        from furatena.catalog.assets import write_assets_manifest
        from furatena.catalog.config import load_docs_config
        from furatena.catalog.theme import DocsTheme
        from furatena.catalog.vendor_paths import VENDOR_FILES, vendor_dir

        frozen = tmp_path / "frozen"
        assets = frozen / "assets"
        vendor_dest = assets / "vendor"
        vendor_dest.mkdir(parents=True)
        vendor_src = Path(vendor_dir())
        for name in VENDOR_FILES:
            (vendor_dest / name).write_text((vendor_src / name).read_text(encoding="utf-8"), encoding="utf-8")
        write_assets_manifest(
            frozen,
            theme_href="theme.test.css",
            branding_prefix="branding",
            vendor_prefix="vendor",
        )
        (assets / "branding").mkdir(parents=True, exist_ok=True)
        (assets / "theme.test.css").write_text("/* test */", encoding="utf-8")

        docs = load_docs_config(APP_ROOT / "docs.yaml")
        theme = DocsTheme.from_docs_config(docs, frozen_dir=frozen)
        prefixes = {mount.url_prefix for mount in theme.static_mounts}
        assert "/docs-vendor" in prefixes
        assert "/docs-theme/branding" in prefixes


class TestMaturityErrorPage:
    def test_not_found_uses_app_shell(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/definitely-missing-page/")
            assert resp.status == 404
            return resp.text

        html = asyncio.run(_fetch())
        assert "fura-shell-nav" in html
        assert "chirp-theme-shell__header" in html
        assert "404" in html
        assert 'href="/docs/"' in html

    def test_not_found_links_use_shell_attrs(self) -> None:
        attrs = shell_link_attrs("/docs/")
        assert attrs.get("hx-boost") == "true"
