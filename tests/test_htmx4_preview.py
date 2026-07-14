"""Contracts for the opt-in htmx 4 preview runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from chirp.testing import TestClient

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.links import shell_link_attrs
from furatena.catalog.vendor_paths import HTMX4_PREVIEW_VERSION, resolve_htmx_preview

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


def test_preview_selector_rejects_unpinned_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FURA_HTMX_PREVIEW", "4.0.0-latest")

    with pytest.raises(ValueError, match=HTMX4_PREVIEW_VERSION):
        resolve_htmx_preview()


def test_preview_links_use_htmx4_preload_attribute() -> None:
    attrs = shell_link_attrs("/docs/guide/", htmx4=True)

    assert attrs["hx-preload"] == "mouseover"
    assert "preload" not in attrs


def test_preview_selects_only_the_pinned_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FURA_HTMX_PREVIEW", HTMX4_PREVIEW_VERSION)
    docs = DocsApp.from_paths(APP_ROOT / "docs.yaml", repo_root=REPO, autodoc=False)
    client = TestClient(docs.create_app())

    async def _fetch() -> tuple[str, int]:
        page = await client.get("/")
        asset = await client.get(f"/docs-vendor/htmx-{HTMX4_PREVIEW_VERSION}.min.js")
        return page.text, asset.status

    html, asset_status = asyncio.run(_fetch())

    assert asset_status == 200
    assert f"/docs-vendor/htmx-{HTMX4_PREVIEW_VERSION}.min.js" in html
    assert f"/docs-vendor/htmx-2-compat-{HTMX4_PREVIEW_VERSION}.min.js" in html
    assert f"/docs-vendor/hx-sse-{HTMX4_PREVIEW_VERSION}.min.js" in html
    assert f"/docs-vendor/hx-preload-{HTMX4_PREVIEW_VERSION}.min.js" in html
    assert f'data-fura-htmx-preview="{HTMX4_PREVIEW_VERSION}"' in html
    assert "/docs-vendor/htmx.min.js" not in html
    assert "/docs-vendor/htmx-ext-sse.js" not in html
    assert '<body hx-ext="preload"' not in html
    assert 'hx-preload="mouseover"' in html
