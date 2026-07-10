"""Theme asset lint tests."""

from __future__ import annotations

import sys
from dataclasses import replace
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
from furatena.catalog.theme_lint import (
    _local_template_class_usages,
    check_safe_filter_reasons,
    check_theme_assets,
)


@pytest.fixture(scope="module")
def docs_config() -> DocsConfig:
    return load_docs_config(APP_ROOT / "docs.yaml")


class TestThemeLint:
    def test_default_app_theme_assets(self, docs_config: DocsConfig) -> None:
        errors, warnings = check_theme_assets(docs_config)
        assert errors == []
        assert not any("theme js/" in item and "missing" in item for item in warnings)

    def test_local_class_contract_uses_resolved_css_and_physical_sources(
        self,
        docs_config: DocsConfig,
        tmp_path: Path,
    ) -> None:
        theme_dir = tmp_path / "theme"
        shadow_dir = theme_dir / "templates" / "partials"
        shadow_dir.mkdir(parents=True)
        template = shadow_dir / "local_contract.html"
        template.write_text(
            '<div class="chirp-theme-test-backed chirp-theme-test-commented '
            'chirp-theme-test-missing"></div>',
            encoding="utf-8",
        )
        styles = theme_dir / "styles.css"
        styles.write_text(
            ".chirp-theme-test-backed { display: block; }\n"
            "/* .chirp-theme-test-commented { display: block; } */\n",
            encoding="utf-8",
        )
        config = replace(
            docs_config,
            root=tmp_path,
            theme=replace(
                docs_config.theme,
                overrides=replace(docs_config.theme.overrides, styles="theme/styles.css"),
            ),
        )

        errors, _warnings = check_theme_assets(config)

        assert not any(".chirp-theme-test-backed" in item for item in errors)
        assert any(".chirp-theme-test-commented" in item for item in errors)
        missing = [item for item in errors if ".chirp-theme-test-missing" in item]
        assert len(missing) == 1
        assert "theme/templates/partials/local_contract.html" in missing[0]
        usages = _local_template_class_usages((theme_dir / "templates", theme_dir))
        assert usages["chirp-theme-test-missing"] == (template.resolve(),)

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
