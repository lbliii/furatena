"""Local Furatena theme asset roots (vendored under ``theme/assets/``)."""

from __future__ import annotations

from pathlib import Path


def theme_assets_root(docs_root: Path) -> Path:
    """Directory containing vendored chirp-theme CSS, JS, icons, and branding."""
    return (docs_root / "theme" / "assets").resolve()


def packaged_theme_assets(
    theme_id: str,
    docs_root: Path,
) -> tuple[Path, Path | None, Path | None] | None:
    """Return ``(css_dir, fonts_dir, branding_dir)`` for the active theme."""
    if theme_id != "chirp":
        return None
    assets = theme_assets_root(docs_root)
    css_dir = assets / "css"
    if not css_dir.is_dir():
        return None
    fonts_dir = assets / "fonts"
    fonts = fonts_dir if fonts_dir.is_dir() else None
    branding_dir = assets / "branding"
    branding = branding_dir if branding_dir.is_dir() else None
    return css_dir, fonts, branding


def packaged_theme_js(theme_id: str, docs_root: Path) -> Path | None:
    """Chirp-native enhancement scripts live under ``theme/js/``, not vendored Bengal JS."""
    if theme_id != "chirp":
        return None
    js_dir = (docs_root / "theme" / "js").resolve()
    return js_dir if js_dir.is_dir() else None


def theme_icons_dir(docs_root: Path) -> Path:
    """SVG icon library shipped with the vendored theme assets."""
    return theme_assets_root(docs_root) / "icons"
