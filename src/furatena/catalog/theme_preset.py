"""Generate preset CSS from ``docs.yaml`` theme measure and font config."""

from __future__ import annotations

import re
from pathlib import Path

from furatena.catalog.config import ThemeConfig

_PRESET_NAME = "theme-preset.css"
_CSS_TOKEN_RE = re.compile(r"^[a-zA-Z0-9#.%\-/(),\s]+$")


def render_theme_preset(theme: ThemeConfig) -> str:
    """Return CSS that maps YAML measure/font knobs to custom properties."""
    measure = theme.measure
    fonts = theme.fonts
    sans = _font_stack(fonts.sans)
    display = _font_stack(fonts.display)
    return (
        "/* Generated from docs.yaml theme.measure + theme.fonts — do not edit. */\n"
        "@layer docs.tokens {\n"
        "  .chirp-theme-docs-layout {\n"
        f"    --chirpui-prose-max-width: {measure.prose};\n"
        f"    --chirpui-docs-reading-measure: min({measure.reading}, 100%);\n"
        f"    --type-measure-docs: {measure.docs};\n"
        f"    --chirpui-container-max: min({measure.container}, calc(100vw - 2rem));\n"
        "  }\n\n"
        "  .chirp-theme-docs-layout__hero {\n"
        f"    --chirpui-docs-reading-measure: min({measure.reading}, 100%);\n"
        "  }\n\n"
        "  :root {\n"
        f"    --font-display: {display};\n"
        f"    --font-display-default: {display};\n"
        f"    --font-family-sans: {sans};\n"
        "  }\n"
        "}\n"
    )


def write_theme_preset(theme: ThemeConfig, *, cache_dir: Path) -> Path:
    """Write preset CSS into ``.docs-cache/`` and return the path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / _PRESET_NAME
    content = render_theme_preset(theme)
    if not path.is_file() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
    return path


def validate_measure_value(value: str, *, field: str) -> str | None:
    """Return an error message when *value* is not a safe CSS token."""
    text = value.strip()
    if not text:
        return f"theme.measure.{field} must not be empty"
    if not _CSS_TOKEN_RE.fullmatch(text):
        return f"theme.measure.{field} invalid: {value!r}"
    return None


def validate_font_name(value: str, *, field: str) -> str | None:
    """Return an error message when a font family name is unsafe."""
    text = value.strip()
    if not text:
        return f"theme.fonts.{field} must not be empty"
    if any(char in text for char in '";{}\\'):
        return f"theme.fonts.{field} invalid: {value!r}"
    return None


def _font_stack(name: str) -> str:
    safe = name.strip().strip('"').strip("'") or "Inter"
    return f'"{safe}", ui-sans-serif, system-ui, sans-serif'
