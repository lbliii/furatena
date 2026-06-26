"""Theme preset generation tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.config import (
    ThemeConfig,
    ThemeFontsConfig,
    ThemeMeasureConfig,
    load_docs_config,
)
from furatena.catalog.theme_preset import (
    render_theme_preset,
    validate_font_name,
    validate_measure_value,
    write_theme_preset,
)


@pytest.fixture(scope="module")
def docs_config():
    return load_docs_config(APP_ROOT / "docs.yaml")


class TestThemePreset:
    def test_docs_yaml_measure_and_fonts(self, docs_config) -> None:
        assert docs_config.theme.measure.prose == "80ch"
        assert docs_config.theme.measure.reading == "76ch"
        assert docs_config.theme.fonts.sans == "Inter"

    def test_render_preset_css(self) -> None:
        css = render_theme_preset(
            ThemeConfig(
                measure=ThemeMeasureConfig(prose="72ch", reading="68ch", docs="72ch", container="84rem"),
                fonts=ThemeFontsConfig(sans="Inter", display="Outfit"),
            )
        )
        assert "--chirpui-prose-max-width: 72ch" in css
        assert '"Outfit"' in css

    def test_write_preset_file(self, tmp_path: Path) -> None:
        path = write_theme_preset(ThemeConfig(), cache_dir=tmp_path)
        assert path.is_file()
        assert "80ch" in path.read_text(encoding="utf-8")

    def test_rejects_unsafe_measure(self) -> None:
        assert validate_measure_value("80ch;{}", field="prose") is not None

    def test_rejects_unsafe_font(self) -> None:
        assert validate_font_name('Inter"; evil', field="sans") is not None
