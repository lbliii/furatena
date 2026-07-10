"""Theme asset lint tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.config import (
    DocsConfig,
    ThemeConfig,
    ThemeEffectsConfig,
    ThemeMeasureConfig,
    load_docs_config,
)
from furatena.catalog.theme_lint import check_safe_filter_reasons, check_theme_assets


@pytest.fixture(scope="module")
def docs_config() -> DocsConfig:
    return load_docs_config(APP_ROOT / "docs.yaml")


class TestThemeLint:
    def test_default_app_theme_assets(self, docs_config: DocsConfig) -> None:
        errors, warnings = check_theme_assets(docs_config)
        assert errors == []
        assert not any("theme js/" in item and "missing" in item for item in warnings)

    def test_invalid_effect_preset(self) -> None:
        config = DocsConfig(
            root=APP_ROOT,
            theme=ThemeConfig(
                effects=ThemeEffectsConfig(code="neon", cards="flat", hero="wash"),
            ),
        )
        errors, _warnings = check_theme_assets(config)
        assert any("theme.effects.code invalid" in item for item in errors)

    def test_invalid_measure_preset(self) -> None:
        config = DocsConfig(
            root=APP_ROOT,
            theme=ThemeConfig(
                measure=ThemeMeasureConfig(prose="80ch; color: red"),
            ),
        )
        errors, _warnings = check_theme_assets(config)
        assert any("theme.measure.prose invalid" in item for item in errors)

    def test_undocumented_safe_filter_is_rejected(self, tmp_path: Path) -> None:
        template = tmp_path / "theme" / "templates" / "unsafe.html"
        template.parent.mkdir(parents=True)
        template.write_text("{{ value | safe }}\n", encoding="utf-8")
        config = DocsConfig(root=tmp_path)

        errors = check_safe_filter_reasons(config)

        assert len(errors) == 1
        assert "undocumented |safe filter" in errors[0]

        template.write_text(
            '{{ value | safe(reason="sanitized by test producer") }}\n',
            encoding="utf-8",
        )
        assert check_safe_filter_reasons(config) == []
