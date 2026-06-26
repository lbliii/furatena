"""Theme loading for Furatena — tokens, styles, packaged skin assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from furatena.catalog.assets import bundle_css, load_assets_manifest
from furatena.catalog.config import DocsConfig, ThemeConfig
from furatena.catalog.theme_assets import packaged_theme_assets_from_root
from furatena.catalog.theme_pack import resolve_theme_paths
from furatena.catalog.theme_preset import write_theme_preset
from furatena.catalog.vendor_paths import VENDOR_FILES, vendor_dir


@dataclass(frozen=True, slots=True)
class ThemeAssets:
    """Static asset mounts for a docs theme."""

    url_prefix: str
    directory: Path


@dataclass(frozen=True, slots=True)
class DocsTheme:
    """Resolved theme: template search roots + static asset mounts."""

    config: ThemeConfig
    template_roots: tuple[Path, ...]
    stylesheet_hrefs: tuple[str, ...]
    static_mounts: tuple[ThemeAssets, ...]
    browser_reload_dirs: tuple[Path, ...]

    @classmethod
    def from_docs_config(
        cls,
        docs: DocsConfig,
        *,
        frozen_dir: Path | None = None,
    ) -> DocsTheme:
        theme_cfg = docs.theme
        theme_dir = docs.theme_dir
        cache_dir = docs.root / ".docs-cache"
        skin = resolve_theme_paths(docs)
        packaged = packaged_theme_assets_from_root(theme_cfg.id, skin.app_assets_root)

        stylesheet_hrefs: list[str] = []
        static_mounts: list[ThemeAssets] = []
        browser_reload_dirs: list[Path] = [theme_dir]
        if skin.pack is not None:
            browser_reload_dirs.append(skin.pack.root)
        if skin.docs_core is not None:
            browser_reload_dirs.append(skin.docs_core.root)

        manifest = load_assets_manifest(frozen_dir) if frozen_dir is not None else None
        if manifest and manifest.get("theme_css"):
            assets_dir = frozen_dir / "assets"  # type: ignore[union-attr]
            static_mounts.append(ThemeAssets(url_prefix="/docs-assets", directory=assets_dir))
            stylesheet_hrefs.append(f"/docs-assets/{manifest['theme_css']}")
            fonts_prefix = manifest.get("fonts_prefix")
            if fonts_prefix:
                fonts_dir = assets_dir / "fonts"
                if fonts_dir.is_dir():
                    static_mounts.append(
                        ThemeAssets(url_prefix=f"/docs-assets/{fonts_prefix}", directory=fonts_dir)
                    )
            branding_prefix = manifest.get("branding_prefix")
            if branding_prefix:
                branding_dir = assets_dir / branding_prefix
                if branding_dir.is_dir():
                    static_mounts.append(
                        ThemeAssets(url_prefix="/docs-theme/branding", directory=branding_dir)
                    )
            vendor_prefix = manifest.get("vendor_prefix")
            if vendor_prefix:
                frozen_vendor = assets_dir / vendor_prefix
                if frozen_vendor.is_dir():
                    static_mounts.append(
                        ThemeAssets(url_prefix="/docs-vendor", directory=frozen_vendor)
                    )
        elif packaged is not None:
            css_dir, fonts_dir, branding_dir = packaged
            entry = css_dir / "style.css"
            bundle_path, digest = bundle_css(entry, cache_dir=cache_dir)
            static_mounts.append(ThemeAssets(url_prefix="/docs-assets", directory=cache_dir))
            stylesheet_hrefs.append(f"/docs-assets/theme.{digest}.css")
            if fonts_dir is not None and skin.fonts_dir is None:
                static_mounts.append(ThemeAssets(url_prefix="/docs-theme/fonts", directory=fonts_dir))
            if branding_dir is not None:
                static_mounts.append(
                    ThemeAssets(url_prefix="/docs-theme/branding", directory=branding_dir)
                )

        if not any(mount.url_prefix == "/docs-theme/branding" for mount in static_mounts):
            fallback = packaged_theme_assets_from_root(theme_cfg.id, skin.app_assets_root)
            if fallback is not None:
                _css, _fonts, branding_dir = fallback
                if branding_dir is not None:
                    static_mounts.append(
                        ThemeAssets(url_prefix="/docs-theme/branding", directory=branding_dir)
                    )

        preset_path = write_theme_preset(theme_cfg, cache_dir=cache_dir)
        if preset_path.is_file():
            static_mounts.append(
                ThemeAssets(url_prefix="/docs-theme/generated", directory=cache_dir)
            )
            stylesheet_hrefs.append("/docs-theme/generated/theme-preset.css")

        static_mounts.append(
            ThemeAssets(url_prefix="/docs-theme/tokens", directory=skin.tokens.parent)
        )
        stylesheet_hrefs.append(f"/docs-theme/tokens/{skin.tokens.name}")
        browser_reload_dirs.append(skin.tokens.parent)

        static_mounts.append(
            ThemeAssets(url_prefix="/docs-theme/local", directory=skin.styles.parent)
        )
        stylesheet_hrefs.append(f"/docs-theme/local/{skin.styles.name}")
        stylesheet_hrefs.append(f"/docs-theme/local/{skin.directives.name}")
        browser_reload_dirs.append(skin.styles.parent)

        if skin.fonts_dir is not None:
            static_mounts.append(
                ThemeAssets(url_prefix="/docs-theme/fonts", directory=skin.fonts_dir)
            )
            browser_reload_dirs.append(skin.fonts_dir)

        template_roots: list[Path] = []
        if skin.templates is not None:
            template_roots.append(skin.templates)
            browser_reload_dirs.append(skin.templates)
        template_roots.append(theme_dir)

        static_mounts.append(ThemeAssets(url_prefix="/docs-theme/local/js", directory=skin.js_dir))
        browser_reload_dirs.append(skin.js_dir)

        vendor_root = Path(vendor_dir())
        if vendor_root.is_dir() and all((vendor_root / name).is_file() for name in VENDOR_FILES):
            if not any(mount.url_prefix == "/docs-vendor" for mount in static_mounts):
                static_mounts.append(ThemeAssets(url_prefix="/docs-vendor", directory=vendor_root))

        return cls(
            config=theme_cfg,
            template_roots=tuple(template_roots),
            stylesheet_hrefs=tuple(stylesheet_hrefs),
            static_mounts=tuple(static_mounts),
            browser_reload_dirs=tuple(dict.fromkeys(browser_reload_dirs)),
        )


# Re-export for freeze/tests that import the legacy private helpers.
def _packaged_theme_assets(theme_id: str, docs_root: Path | None = None) -> tuple[Path, Path | None, Path | None] | None:
    if docs_root is None:
        return None
    return packaged_theme_assets_from_root(theme_id, docs_root / "theme" / "assets")


def _packaged_theme_js(theme_id: str, docs_root: Path | None = None) -> Path | None:
    if docs_root is None:
        return None
    js_dir = (docs_root / "theme" / "js").resolve()
    return js_dir if js_dir.is_dir() else None
