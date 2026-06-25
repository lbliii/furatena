"""Docs-core and app-local theme asset resolution."""

from __future__ import annotations

import importlib
from pathlib import Path

# ``theme.id`` → importable docs-core assets module (``chirp`` kept as deprecated alias).
_DOCS_CORE_ASSETS: dict[str, str] = {
    "furatena": "furatena.themes.furatena",
    "chirp": "furatena.themes.furatena",
}


def _docs_core_assets_module(theme_id: str):
    module_path = _DOCS_CORE_ASSETS.get(theme_id)
    if module_path is None:
        return None
    return importlib.import_module(module_path)


def theme_assets_root(docs_root: Path) -> Path:
    """App-local theme assets (branding, images) under ``theme/assets/``."""
    return (docs_root / "theme" / "assets").resolve()


def docs_core_assets_root(theme_id: str) -> Path | None:
    """Installed docs-core bundle root for ``theme.id``."""
    module = _docs_core_assets_module(theme_id)
    if module is None:
        return None
    assets = getattr(module, "ASSETS", None)
    if assets is None:
        return None
    css_dir = Path(assets) / "css"
    return Path(assets) if css_dir.is_dir() else None


def packaged_theme_assets(
    theme_id: str,
    docs_root: Path,
) -> tuple[Path, Path | None, Path | None] | None:
    """Return ``(css_dir, fonts_dir, branding_dir)`` for the active theme."""
    return packaged_theme_assets_from_root(theme_id, theme_assets_root(docs_root))


def packaged_theme_assets_from_root(
    theme_id: str,
    app_assets_root: Path,
) -> tuple[Path, Path | None, Path | None] | None:
    """Return docs-core CSS from the installable pack plus app-local branding."""
    core = docs_core_assets_root(theme_id)
    if core is None:
        legacy_css = app_assets_root / "css"
        if not legacy_css.is_dir():
            return None
        core = app_assets_root

    css_dir = core / "css"
    if not css_dir.is_dir():
        return None

    fonts_dir = core / "fonts"
    fonts = fonts_dir if fonts_dir.is_dir() else None

    branding_dir = app_assets_root / "branding"
    branding = branding_dir if branding_dir.is_dir() else None
    return css_dir, fonts, branding


def packaged_theme_js(theme_id: str, docs_root: Path) -> Path | None:
    """Legacy hook — enhancement scripts live in skin packs via ``theme.use``."""
    if theme_id not in _DOCS_CORE_ASSETS:
        return None
    js_dir = (docs_root / "theme" / "js").resolve()
    return js_dir if js_dir.is_dir() else None


def theme_icons_dir(docs_root: Path, *, theme_id: str = "furatena") -> Path:
    """SVG icon library for the active docs-core bundle."""
    core = docs_core_assets_root(theme_id)
    if core is not None:
        icons = core / "icons"
        if icons.is_dir():
            return icons
    return theme_assets_root(docs_root) / "icons"
