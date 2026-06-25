"""Load ``docs.yaml`` — Furatena app configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from furatena.catalog.catalog_nav import CatalogNavConfig, parse_catalog_nav
from furatena.catalog.i18n import DocsI18nConfig, load_i18n_config


_EFFECTS_CODE = frozenset({"flat", "subtle", "glow"})
_EFFECTS_CARDS = frozenset({"flat", "elevated"})
_EFFECTS_HERO = frozenset({"wash", "minimal"})


@dataclass(frozen=True, slots=True)
class ThemeEffectsConfig:
    """Visual effect presets — mapped to ``data-fura-effects-*`` on ``<html>``."""

    code: str = "flat"
    cards: str = "flat"
    hero: str = "wash"


@dataclass(frozen=True, slots=True)
class ThemeMeasureConfig:
    """Reading width tokens — emitted to ``theme-preset.css``."""

    prose: str = "80ch"
    reading: str = "76ch"
    docs: str = "80ch"
    container: str = "90rem"


@dataclass(frozen=True, slots=True)
class ThemeFontsConfig:
    """Font family names — ``@font-face`` files stay in ``theme/skin/fonts.css``."""

    sans: str = "Inter"
    display: str = "Inter"


@dataclass(frozen=True, slots=True)
class ThemeOverridesConfig:
    """Per-app paths that win over a ``theme.use`` pack (relative to app root)."""

    tokens: str | None = None
    styles: str | None = None
    directives: str | None = None
    js: str | None = None
    fonts: str | None = None
    templates: str | None = None


@dataclass(frozen=True, slots=True)
class SiteCtaConfig:
    label: str
    href: str


@dataclass(frozen=True, slots=True)
class SiteMetricConfig:
    value: str
    label: str
    hint: str


@dataclass(frozen=True, slots=True)
class SiteHomeVisualConfig:
    aria_label: str = "Product preview"
    eyebrow: str = "Example interface"
    title: str = "Docs as data, HTML on demand."
    description: str = "Markdown indexed into a live graph — pages and fragments served through an htmx shell."
    proof_tags: tuple[str, ...] = ("htmx", "catalog", "freeze")
    feature_title: str = "Author reload"
    feature_body: str = "Edit markdown and see partial swaps on the open page — no export loop."
    cta_label: str = "Open get started"
    cta_href: str = "/docs/get-started/"


@dataclass(frozen=True, slots=True)
class SiteHomeConfig:
    aria_label: str = "Overview"
    hero_points: tuple[str, ...] = ()
    cta_primary: SiteCtaConfig = field(
        default_factory=lambda: SiteCtaConfig(label="Get started", href="/docs/get-started/")
    )
    cta_secondary: SiteCtaConfig = field(
        default_factory=lambda: SiteCtaConfig(label="Reference", href="/docs/reference/")
    )
    metrics: tuple[SiteMetricConfig, ...] = ()
    visual: SiteHomeVisualConfig = field(default_factory=SiteHomeVisualConfig)


@dataclass(frozen=True, slots=True)
class SiteNavLinkConfig:
    href: str
    label: str
    blurb: str
    icon: str = "file-text"


@dataclass(frozen=True, slots=True)
class SiteNavSectionConfig:
    menu_label: str
    dropdown_href: str
    overview_href: str
    overview_kicker: str
    overview_title: str
    overview_blurb: str
    links: tuple[SiteNavLinkConfig, ...]


@dataclass(frozen=True, slots=True)
class SiteNavigationConfig:
    documentation: SiteNavSectionConfig
    develop: SiteNavSectionConfig


@dataclass(frozen=True, slots=True)
class SiteConfig:
    """Product branding and shell copy — variabilizes home and top navigation."""

    name: str = "Furatena"
    tagline: str = "Hypermedia documentation catalog"
    description: str = (
        "Furatena documentation — markdown indexed live, served as htmx fragments."
    )
    mark: str = "𒀭"
    home: SiteHomeConfig = field(default_factory=SiteHomeConfig)
    navigation: SiteNavigationConfig | None = None


@dataclass(frozen=True, slots=True)
class ThemeConfig:
    id: str = "furatena"
    use: str | None = None
    tokens: str = "theme/tokens.css"
    styles: str = "theme/styles.css"
    templates: str = "theme/templates"
    effects: ThemeEffectsConfig = field(default_factory=ThemeEffectsConfig)
    measure: ThemeMeasureConfig = field(default_factory=ThemeMeasureConfig)
    fonts: ThemeFontsConfig = field(default_factory=ThemeFontsConfig)
    overrides: ThemeOverridesConfig = field(default_factory=ThemeOverridesConfig)


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
    site: SiteConfig = field(default_factory=SiteConfig)
    catalog: CatalogNavConfig = field(default_factory=CatalogNavConfig)
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


def _optional_str(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _parse_cta(raw: object, *, default: SiteCtaConfig) -> SiteCtaConfig:
    if not isinstance(raw, dict):
        return default
    label = str(raw.get("label") or default.label).strip()
    href = str(raw.get("href") or default.href).strip()
    return SiteCtaConfig(label=label or default.label, href=href or default.href)


def _parse_metrics(raw: object) -> tuple[SiteMetricConfig, ...]:
    if not isinstance(raw, list):
        return ()
    metrics: list[SiteMetricConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        label = str(item.get("label") or "").strip()
        hint = str(item.get("hint") or "").strip()
        if value and label:
            metrics.append(SiteMetricConfig(value=value, label=label, hint=hint))
    return tuple(metrics)


def _parse_home_visual(raw: object, *, defaults: SiteHomeVisualConfig) -> SiteHomeVisualConfig:
    if not isinstance(raw, dict):
        return defaults
    proof_raw = raw.get("proof_tags") or raw.get("proof") or ()
    proof_tags = tuple(str(tag).strip() for tag in proof_raw if str(tag).strip())
    return SiteHomeVisualConfig(
        aria_label=str(raw.get("aria_label") or defaults.aria_label),
        eyebrow=str(raw.get("eyebrow") or defaults.eyebrow),
        title=str(raw.get("title") or defaults.title),
        description=str(raw.get("description") or defaults.description),
        proof_tags=proof_tags or defaults.proof_tags,
        feature_title=str(raw.get("feature_title") or defaults.feature_title),
        feature_body=str(raw.get("feature_body") or defaults.feature_body),
        cta_label=str(raw.get("cta_label") or defaults.cta_label),
        cta_href=str(raw.get("cta_href") or defaults.cta_href),
    )


def _parse_home_config(raw: object, *, site_name: str) -> SiteHomeConfig:
    defaults = SiteHomeConfig(
        hero_points=(
            "Live catalog graph",
            "htmx shell navigation",
            "Dual Content + Presentation IR",
        ),
        metrics=(
            SiteMetricConfig("1", "markdown corpus", "Index pages from content/ mounts at serve time."),
            SiteMetricConfig("0", "export loop", "Author mode reloads the open page via htmx partial swaps."),
            SiteMetricConfig("∞", "graph edges", "Links, nav, search, and agents read the same catalog."),
        ),
        visual=SiteHomeVisualConfig(
            aria_label=f"Preview of {site_name} documentation surfaces",
            cta_href="/docs/get-started/",
        ),
    )
    if not isinstance(raw, dict):
        return defaults
    hero_raw = raw.get("hero_points") or ()
    hero_points = tuple(str(item).strip() for item in hero_raw if str(item).strip())
    metrics_raw = raw.get("metrics")
    metrics = _parse_metrics(metrics_raw) if metrics_raw is not None else defaults.metrics
    visual = _parse_home_visual(raw.get("visual"), defaults=defaults.visual)
    return SiteHomeConfig(
        aria_label=str(raw.get("aria_label") or f"{site_name} overview"),
        hero_points=hero_points or defaults.hero_points,
        cta_primary=_parse_cta(raw.get("cta_primary"), default=defaults.cta_primary),
        cta_secondary=_parse_cta(raw.get("cta_secondary"), default=defaults.cta_secondary),
        metrics=metrics,
        visual=visual,
    )


def _parse_nav_links(raw: object) -> tuple[SiteNavLinkConfig, ...]:
    if not isinstance(raw, list):
        return ()
    links: list[SiteNavLinkConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "").strip()
        label = str(item.get("label") or "").strip()
        if not href or not label:
            continue
        links.append(
            SiteNavLinkConfig(
                href=href,
                label=label,
                blurb=str(item.get("blurb") or "").strip(),
                icon=str(item.get("icon") or "file-text").strip() or "file-text",
            )
        )
    return tuple(links)


def _parse_nav_section(raw: object, *, defaults: SiteNavSectionConfig) -> SiteNavSectionConfig:
    if not isinstance(raw, dict):
        return defaults
    overview_raw = raw.get("overview") if isinstance(raw.get("overview"), dict) else {}
    links = _parse_nav_links(raw.get("links"))
    return SiteNavSectionConfig(
        menu_label=str(raw.get("menu_label") or defaults.menu_label),
        dropdown_href=str(raw.get("dropdown_href") or defaults.dropdown_href),
        overview_href=str(overview_raw.get("href") or raw.get("overview_href") or defaults.overview_href),
        overview_kicker=str(overview_raw.get("kicker") or defaults.overview_kicker),
        overview_title=str(overview_raw.get("title") or defaults.overview_title),
        overview_blurb=str(overview_raw.get("blurb") or defaults.overview_blurb),
        links=links or defaults.links,
    )


def default_site_navigation(site_name: str) -> SiteNavigationConfig:
    """Default top-bar mega menus for a Furatena docs app."""
    return SiteNavigationConfig(
        documentation=SiteNavSectionConfig(
            menu_label="Documentation",
            dropdown_href="/docs/",
            overview_href="/docs/",
            overview_kicker="Explore",
            overview_title="Documentation",
            overview_blurb=f"Guides and reference for building with {site_name}.",
            links=(
                SiteNavLinkConfig(
                    "/docs/get-started/",
                    "Get started",
                    f"Install {site_name} and run your first docs site.",
                    "book-open",
                ),
                SiteNavLinkConfig(
                    "/docs/concepts/",
                    "Concepts",
                    "Catalog graph, views, shell, and dual IR.",
                    "layers",
                ),
                SiteNavLinkConfig(
                    "/docs/authoring/",
                    "Authoring",
                    "Markdown, directives, navigation, and collections.",
                    "pencil",
                ),
                SiteNavLinkConfig(
                    "/docs/theming/",
                    "Theming",
                    "Tokens, skin packs, views, and branding.",
                    "palette",
                ),
                SiteNavLinkConfig(
                    "/docs/operations/",
                    "Operations",
                    "Serve, freeze, export, check, and deploy.",
                    "rocket",
                ),
                SiteNavLinkConfig(
                    "/docs/reference/",
                    "Reference",
                    "CLI, config files, and glossary.",
                    "code",
                ),
                SiteNavLinkConfig(
                    "/docs/about/",
                    "About",
                    "Philosophy, roadmap, and ecosystem.",
                    "info",
                ),
            ),
        ),
        develop=SiteNavSectionConfig(
            menu_label="Develop",
            dropdown_href="/develop/",
            overview_href="/develop/",
            overview_kicker="Build",
            overview_title="Developer tools",
            overview_blurb="Machine-readable indexes and release channels.",
            links=(
                SiteNavLinkConfig("/api/", "Autodoc API", "Python modules indexed from the repo.", "code"),
                SiteNavLinkConfig("/shared/", "Shared reference", "Cross-mount glossary and formats.", "globe"),
                SiteNavLinkConfig("/releases/", "Releases", "Version notes and channel history.", "rocket"),
                SiteNavLinkConfig("/develop/catalog/", "Catalog JSON", "Live document graph export.", "file-code"),
                SiteNavLinkConfig("/develop/llms/", "llms.txt", "Agent-friendly page index.", "article"),
                SiteNavLinkConfig("/develop/search/", "search.json", "Keyword search index.", "magnifying-glass"),
                SiteNavLinkConfig("/develop/tools/", "tools.json", "Agent tool manifest.", "stack"),
            ),
        ),
    )


def _parse_site_config(raw: object) -> SiteConfig:
    site_raw = raw if isinstance(raw, dict) else {}
    name = str(site_raw.get("name") or "Furatena").strip() or "Furatena"
    tagline = str(site_raw.get("tagline") or "Hypermedia documentation catalog").strip()
    description = str(
        site_raw.get("description")
        or f"{name} documentation — markdown indexed live, served as htmx fragments."
    ).strip()
    mark = str(site_raw.get("mark") or "𒀭").strip() or "𒀭"
    home = _parse_home_config(site_raw.get("home"), site_name=name)
    nav_raw = site_raw.get("navigation")
    navigation = default_site_navigation(name)
    if isinstance(nav_raw, dict):
        navigation = SiteNavigationConfig(
            documentation=_parse_nav_section(nav_raw.get("documentation"), defaults=navigation.documentation),
            develop=_parse_nav_section(nav_raw.get("develop"), defaults=navigation.develop),
        )
    return SiteConfig(
        name=name,
        tagline=tagline,
        description=description,
        mark=mark,
        home=home,
        navigation=navigation,
    )


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
    effects_raw = theme_raw.get("effects") if isinstance(theme_raw.get("effects"), dict) else {}
    measure_raw = theme_raw.get("measure") if isinstance(theme_raw.get("measure"), dict) else {}
    fonts_raw = theme_raw.get("fonts") if isinstance(theme_raw.get("fonts"), dict) else {}
    overrides_raw = theme_raw.get("overrides") if isinstance(theme_raw.get("overrides"), dict) else {}
    use_raw = theme_raw.get("use")
    use = str(use_raw).strip() if use_raw else None
    code = str(effects_raw.get("code") or "flat")
    cards = str(effects_raw.get("cards") or "flat")
    hero = str(effects_raw.get("hero") or "wash")
    theme = ThemeConfig(
        id=str(theme_raw.get("id") or "furatena"),
        use=use or None,
        tokens=str(theme_raw.get("tokens") or "theme/tokens.css"),
        styles=str(theme_raw.get("styles") or "theme/styles.css"),
        templates=str(theme_raw.get("templates") or "theme/templates"),
        effects=ThemeEffectsConfig(
            code=code if code in _EFFECTS_CODE else "flat",
            cards=cards if cards in _EFFECTS_CARDS else "flat",
            hero=hero if hero in _EFFECTS_HERO else "wash",
        ),
        measure=ThemeMeasureConfig(
            prose=str(measure_raw.get("prose") or "80ch"),
            reading=str(measure_raw.get("reading") or "76ch"),
            docs=str(measure_raw.get("docs") or "80ch"),
            container=str(measure_raw.get("container") or "90rem"),
        ),
        fonts=ThemeFontsConfig(
            sans=str(fonts_raw.get("sans") or "Inter"),
            display=str(fonts_raw.get("display") or fonts_raw.get("sans") or "Inter"),
        ),
        overrides=ThemeOverridesConfig(
            tokens=_optional_str(overrides_raw.get("tokens")),
            styles=_optional_str(overrides_raw.get("styles")),
            directives=_optional_str(overrides_raw.get("directives")),
            js=_optional_str(overrides_raw.get("js")),
            fonts=_optional_str(overrides_raw.get("fonts")),
            templates=_optional_str(overrides_raw.get("templates")),
        ),
    )

    mounts_raw = raw.get("mounts")
    mounts_path = _resolve_path(root, str(mounts_raw)) if mounts_raw else root / "mounts.yaml"

    rewrites_raw = raw.get("rewrites")
    rewrites_path = _resolve_path(root, str(rewrites_raw)) if rewrites_raw else None
    if rewrites_path is None:
        default_rewrites = root / ".." / "data" / "url_rewrites.yaml"
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

    site = _parse_site_config(raw.get("site"))
    catalog = parse_catalog_nav(raw.get("catalog"))

    return DocsConfig(
        root=root,
        shell=str(raw.get("shell") or "shell.html"),
        views=views,
        overrides=overrides,
        compose=compose,
        theme=theme,
        site=site,
        catalog=catalog,
        mounts_path=mounts_path,
        rewrites_path=rewrites_path,
        inventories_path=inventories_path,
        i18n=i18n,
        locales_dir=locales_dir,
    )
