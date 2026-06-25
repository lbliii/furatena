"""Load ``docs.yaml`` — Furatena app configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from furatena.catalog.i18n import DocsI18nConfig, load_i18n_config


@dataclass(frozen=True, slots=True)
class ThemeConfig:
    id: str = "chirp"
    tokens: str = "theme/tokens.css"
    styles: str = "theme/styles.css"
    templates: str = "theme/templates"


@dataclass(frozen=True, slots=True)
class ComposeConfig:
    data: Path | None = None


@dataclass(frozen=True, slots=True)
class DocsConfig:
    """Declarative configuration for a Furatena hypermedia app."""

    root: Path
    shell: str = "shell.html"
    views: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)
    compose: dict[str, ComposeConfig] = field(default_factory=dict)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    mounts_path: Path | None = None
    rewrites_path: Path | None = None
    inventories_path: Path | None = None
    i18n: DocsI18nConfig = field(default_factory=DocsI18nConfig)
    locales_dir: Path | None = None

    @property
    def theme_dir(self) -> Path:
        return self.root / "theme"

    @property
    def templates_dir(self) -> Path:
        """Project override templates (sparse — only files you shadow)."""
        return self.root / "templates"

    @property
    def framework_templates_dir(self) -> Path:
        """Built-in catalog templates (directives, partials, search)."""
        return Path(__file__).resolve().parent / "_templates"

    @property
    def views_dir(self) -> Path:
        """Project view overrides (sparse — only templates you shadow)."""
        return self.root / "templates" / "views"

    @property
    def theme_views_dir(self) -> Path:
        """Theme-owned view templates."""
        return self.theme_dir / "views"


def _resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def load_docs_config(path: Path) -> DocsConfig:
    """Parse a ``docs.yaml`` file."""
    root = path.parent.resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    views_raw = raw.get("views") or {}
    views = {str(key): str(value) for key, value in views_raw.items() if value}

    overrides_raw = raw.get("overrides") or {}
    overrides = {str(key): str(value) for key, value in overrides_raw.items() if value}

    compose: dict[str, ComposeConfig] = {}
    compose_raw = raw.get("compose") or {}
    for name, item in compose_raw.items():
        if not isinstance(item, dict):
            continue
        data_raw = item.get("data")
        data = _resolve_path(root, str(data_raw)) if data_raw else None
        compose[str(name)] = ComposeConfig(data=data)

    theme_raw = raw.get("theme") or {}
    if not isinstance(theme_raw, dict):
        theme_raw = {}
    theme = ThemeConfig(
        id=str(theme_raw.get("id") or "chirp"),
        tokens=str(theme_raw.get("tokens") or "theme/tokens.css"),
        styles=str(theme_raw.get("styles") or "theme/styles.css"),
        templates=str(theme_raw.get("templates") or "theme/templates"),
    )

    mounts_raw = raw.get("mounts")
    mounts_path = _resolve_path(root, str(mounts_raw)) if mounts_raw else root / "mounts.yaml"

    rewrites_raw = raw.get("rewrites")
    rewrites_path = _resolve_path(root, str(rewrites_raw)) if rewrites_raw else None
    if rewrites_path is None:
        default_rewrites = root / ".." / ".." / "site" / "data" / "url_rewrites.yaml"
        if default_rewrites.is_file():
            rewrites_path = default_rewrites.resolve()

    inventories_raw = raw.get("inventories")
    if inventories_raw:
        inventories_path = _resolve_path(root, str(inventories_raw))
    else:
        candidate = root / "inventories.yaml"
        inventories_path = candidate if candidate.is_file() else None

    i18n = load_i18n_config(raw.get("i18n") if isinstance(raw.get("i18n"), dict) else None)

    locales_raw = raw.get("locales_dir") or raw.get("locales")
    if locales_raw:
        locales_dir = _resolve_path(root, str(locales_raw))
    else:
        candidate = root / "locales"
        locales_dir = candidate if candidate.is_dir() else root / "locales"

    return DocsConfig(
        root=root,
        shell=str(raw.get("shell") or "shell.html"),
        views=views,
        overrides=overrides,
        compose=compose,
        theme=theme,
        mounts_path=mounts_path,
        rewrites_path=rewrites_path,
        inventories_path=inventories_path,
        i18n=i18n,
        locales_dir=locales_dir,
    )
