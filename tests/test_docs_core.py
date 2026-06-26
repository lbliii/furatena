"""Docs-core package tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_core import list_docs_core_ids, load_docs_core
from furatena.catalog.theme import DocsTheme
from furatena.catalog.theme_assets import packaged_theme_assets
from furatena.catalog.theme_pack import resolve_theme_paths


@pytest.fixture(scope="module")
def docs_config():
    return load_docs_config(APP_ROOT / "docs.yaml")


class TestDocsCorePack:
    def test_furatena_built_in(self) -> None:
        assert "furatena" in list_docs_core_ids()
        assert "chirp" in list_docs_core_ids()  # deprecated alias
        pack = load_docs_core("furatena")
        assert pack is not None
        assert (pack.css_dir / "style.css").is_file()
        assert "themes/furatena" in str(pack.root)

    def test_chirp_alias_resolves_same_assets(self) -> None:
        furatena = load_docs_core("furatena")
        chirp = load_docs_core("chirp")
        assert furatena is not None and chirp is not None
        assert furatena.root == chirp.root

    def test_packaged_assets_use_installable_css(self, docs_config) -> None:
        css_dir, _fonts, branding = packaged_theme_assets(docs_config.theme.id, docs_config.root)
        assert css_dir is not None
        assert "themes/furatena" in str(css_dir)
        assert branding is not None
        assert (branding / "favicon.svg").is_file()

    def test_resolved_paths_include_docs_core(self, docs_config) -> None:
        skin = resolve_theme_paths(docs_config)
        assert skin.docs_core is not None
        assert skin.docs_core.id == "furatena"
        assert (skin.app_assets_root / "branding").is_dir()

    def test_stylesheet_stack_still_bundles_docs_core(self, docs_config) -> None:
        theme = DocsTheme.from_docs_config(docs_config)
        assert any("/docs-assets/theme." in href for href in theme.stylesheet_hrefs)
