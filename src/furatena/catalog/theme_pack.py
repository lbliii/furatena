"""Theme pack discovery and path resolution."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path

from furatena.catalog.config import DocsConfig, ThemeConfig
from furatena.catalog.docs_core import DocsCorePack, load_docs_core

_ENTRY_GROUP = "furatena.themes"


@dataclass(frozen=True, slots=True)
class ThemePack:
    """Reusable skin shipped as a Python package (entry point → pack root directory)."""

    name: str
    root: Path
    packaged_id: str = "furatena"
    tokens: str = "tokens.css"
    styles: str = "styles.css"
    directives: str = "directives.css"
    js_dir: str = "js"
    fonts_dir: str = "assets/fonts"

    def file(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        return path


@dataclass(frozen=True, slots=True)
class ResolvedThemePaths:
    """Absolute paths for skin assets after applying ``theme.use`` + overrides."""

    pack: ThemePack | None
    tokens: Path
    styles: Path
    directives: Path
    js_dir: Path
    fonts_dir: Path | None
    templates: Path | None
    app_assets_root: Path
    docs_core: DocsCorePack | None


def load_theme_pack(name: str) -> ThemePack:
    """Load a registered theme pack by entry-point name."""
    key = name.strip()
    if not key:
        raise ValueError("theme.use must not be empty")
    for entry in _theme_entry_points():
        if entry.name == key:
            pack = entry.load()
            if isinstance(pack, ThemePack):
                return pack
            raise TypeError(f"furatena.themes.{key} must return a ThemePack, got {type(pack)!r}")
    available = ", ".join(sorted(ep.name for ep in _theme_entry_points())) or "(none)"
    raise LookupError(f"unknown theme pack {key!r}; available: {available}")


def list_theme_packs() -> tuple[str, ...]:
    """Return registered theme pack names."""
    return tuple(sorted(ep.name for ep in _theme_entry_points()))


def resolve_theme_paths(docs: DocsConfig) -> ResolvedThemePaths:
    """Merge ``theme.use`` pack defaults with per-app overrides."""
    theme = docs.theme
    pack = load_theme_pack(theme.use) if theme.use else None
    overrides = theme.overrides

    tokens = _resolve_path(docs.root, overrides.tokens) if overrides.tokens else None
    styles = _resolve_path(docs.root, overrides.styles) if overrides.styles else None
    directives = _resolve_path(docs.root, overrides.directives) if overrides.directives else None
    js_dir = _resolve_dir(docs.root, overrides.js) if overrides.js else None
    fonts_dir = _resolve_dir(docs.root, overrides.fonts) if overrides.fonts else None
    templates = _resolve_dir(docs.root, overrides.templates) if overrides.templates else None

    if pack is not None:
        tokens = tokens or pack.file(pack.tokens)
        styles = styles or pack.file(pack.styles)
        directives = directives or pack.file(pack.directives)
        js_dir = js_dir or pack.file(pack.js_dir)
        fonts_dir = fonts_dir or (
            pack.file(pack.fonts_dir) if pack.file(pack.fonts_dir).is_dir() else None
        )
    else:
        tokens = tokens or _resolve_path(docs.root, theme.tokens)
        styles = styles or _resolve_path(docs.root, theme.styles)
        directives = directives or (docs.theme_dir / "directives.css").resolve()
        js_dir = js_dir or (docs.theme_dir / "js").resolve()
        fonts_dir = fonts_dir or (docs.theme_dir / "assets" / "fonts").resolve()
        if fonts_dir is not None and not fonts_dir.is_dir():
            fonts_dir = None

    if templates is None and theme.templates:
        templates = _resolve_dir(docs.root, theme.templates)

    if tokens is None or styles is None or directives is None or js_dir is None:
        raise FileNotFoundError("theme skin paths could not be resolved (check theme.use and overrides)")

    for label, path in (
        ("tokens", tokens),
        ("styles", styles),
        ("directives", directives),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"theme {label} missing: {path}")

    if not js_dir.is_dir():
        raise FileNotFoundError(f"theme js dir missing: {js_dir}")

    return ResolvedThemePaths(
        pack=pack,
        tokens=tokens,
        styles=styles,
        directives=directives,
        js_dir=js_dir,
        fonts_dir=fonts_dir if fonts_dir and fonts_dir.is_dir() else None,
        templates=templates if templates and templates.is_dir() else None,
        app_assets_root=(docs.theme_dir / "assets").resolve(),
        docs_core=load_docs_core(theme.id),
    )


def _theme_entry_points():
    try:
        group = entry_points(group=_ENTRY_GROUP)
    except TypeError:
        group = entry_points().get(_ENTRY_GROUP, ())
    return group


def _resolve_path(root: Path, rel: str) -> Path | None:
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    return path if path.is_file() else None


def _resolve_dir(root: Path, rel: str) -> Path | None:
    path = Path(rel)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    return path if path.is_dir() else None
