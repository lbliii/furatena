"""Theme pack registry tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.config import load_docs_config
from furatena.catalog.theme import DocsTheme
from furatena.catalog.theme_pack import list_theme_packs, load_theme_pack, resolve_theme_paths


@pytest.fixture(scope="module")
def docs_config():
    return load_docs_config(APP_ROOT / "docs.yaml")


class TestThemePackRegistry:
    def test_lagoon_entry_point(self) -> None:
        assert "lagoon" in list_theme_packs()
        pack = load_theme_pack("lagoon")
        assert pack.name == "lagoon"
        assert (pack.root / "tokens.css").is_file()
        assert (pack.root / "skin" / "hero.css").is_file()
        assert (pack.root / "js" / "fura-utils.js").is_file()

    def test_unknown_pack_raises(self) -> None:
        with pytest.raises(LookupError):
            load_theme_pack("does-not-exist")


class TestThemePackResolution:
    def test_app_uses_lagoon_pack(self, docs_config) -> None:
        assert docs_config.theme.use == "lagoon"
        skin = resolve_theme_paths(docs_config)
        assert skin.pack is not None
        assert skin.pack.name == "lagoon"
        assert skin.tokens.name == "tokens.css"
        assert "themes/lagoon" in str(skin.styles)

    def test_stylesheet_stack_includes_pack_assets(self, docs_config) -> None:
        theme = DocsTheme.from_docs_config(docs_config)
        assert any("tokens.css" in href for href in theme.stylesheet_hrefs)
        assert any("theme-preset.css" in href for href in theme.stylesheet_hrefs)
        assert any("/docs-theme/local/styles.css" in href for href in theme.stylesheet_hrefs)
