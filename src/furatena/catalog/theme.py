"""Theme loading for Furatena — tokens, styles, packaged skin assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from furatena.catalog.assets import bundle_css, load_assets_manifest
from furatena.catalog.config import DocsConfig, ThemeConfig
from furatena.catalog.theme_assets import packaged_theme_assets


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
    reload_dirs: tuple[Path, ...]

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
        packaged = packaged_theme_assets(theme_cfg.id, docs.root)

        stylesheet_hrefs: list[str] = []
        static_mounts: list[ThemeAssets] = []
        reload_dirs: list[Path] = [theme_dir]

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
        elif packaged is not None:
            css_dir, fonts_dir, branding_dir = packaged
            entry = css_dir / "style.css"
            bundle_path, digest = bundle_css(entry, cache_dir=cache_dir)
            static_mounts.append(ThemeAssets(url_prefix="/docs-assets", directory=cache_dir))
            stylesheet_hrefs.append(f"/docs-assets/theme.{digest}.css")
            if fonts_dir is not None:
                static_mounts.append(ThemeAssets(url_prefix="/docs-theme/fonts", directory=fonts_dir))
            if branding_dir is not None:
                static_mounts.append(
                    ThemeAssets(url_prefix="/docs-theme/branding", directory=branding_dir)
                )

        if not any(mount.url_prefix == "/docs-theme/branding" for mount in static_mounts):
            fallback = packaged_theme_assets(theme_cfg.id, docs.root)
            if fallback is not None:
                _css, _fonts, branding_dir = fallback
                if branding_dir is not None:
                    static_mounts.append(
                        ThemeAssets(url_prefix="/docs-theme/branding", directory=branding_dir)
                    )

        tokens_path = _resolve_theme_file(docs.root, theme_cfg.tokens)
        styles_path = _resolve_theme_file(docs.root, theme_cfg.styles)
        if tokens_path is not None:
            static_mounts.append(
                ThemeAssets(url_prefix="/docs-theme/tokens", directory=tokens_path.parent)
            )
            rel = tokens_path.name
            stylesheet_hrefs.append(f"/docs-theme/tokens/{rel}")
            reload_dirs.append(tokens_path.parent)
        if styles_path is not None:
            static_mounts.append(
                ThemeAssets(url_prefix="/docs-theme/local", directory=styles_path.parent)
            )
            rel = styles_path.name
            stylesheet_hrefs.append(f"/docs-theme/local/{rel}")
            directives_path = styles_path.parent / "directives.css"
            if directives_path.is_file():
                stylesheet_hrefs.append("/docs-theme/local/directives.css")
            reload_dirs.append(styles_path.parent)

        template_roots: list[Path] = []
        theme_templates = _resolve_theme_dir(docs.root, theme_cfg.templates)
        if theme_templates is not None:
            template_roots.append(theme_templates)
            reload_dirs.append(theme_templates)
        template_roots.append(theme_dir)

        js_dir = (docs.root / "theme" / "js").resolve()
        if js_dir.is_dir():
            static_mounts.append(ThemeAssets(url_prefix="/docs-theme/local/js", directory=js_dir))
            reload_dirs.append(js_dir)

        return cls(
            config=theme_cfg,
            template_roots=tuple(template_roots),
            stylesheet_hrefs=tuple(stylesheet_hrefs),
            static_mounts=tuple(static_mounts),
            reload_dirs=tuple(dict.fromkeys(reload_dirs)),
        )


def _resolve_theme_dir(root: Path, rel: str) -> Path | None:
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    return path if path.is_dir() else None


def _resolve_theme_file(root: Path, rel: str) -> Path | None:
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    return path if path.is_file() else None


# Re-export for freeze/tests that import the legacy private helpers.
def _packaged_theme_assets(theme_id: str, docs_root: Path | None = None) -> tuple[Path, Path | None, Path | None] | None:
    if docs_root is None:
        return None
    return packaged_theme_assets(theme_id, docs_root)


def _packaged_theme_js(theme_id: str, docs_root: Path | None = None) -> Path | None:
    if docs_root is None:
        return None
    return packaged_theme_js(theme_id, docs_root)
