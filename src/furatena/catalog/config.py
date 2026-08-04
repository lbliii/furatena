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
    description: str = (
        "Your markdown becomes a live, queryable catalog — pages update instantly, no rebuild loop."
    )
    proof_tags: tuple[str, ...] = ("htmx", "catalog", "freeze")
    feature_title: str = "Author reload"
    feature_body: str = "Edit markdown and see partial swaps on the open page — no export loop."
    cta_label: str = "Open get started"
    cta_href: str = "/docs/get-started/"


@dataclass(frozen=True, slots=True)
class SiteHomeFeatureConfig:
    eyebrow: str
    title: str
    body: str
    code: str
    action: SiteCtaConfig | None = None
    reverse: bool = False


@dataclass(frozen=True, slots=True)
class SiteHomeIdeasConfig:
    eyebrow: str
    title: str
    subtitle: str
    features: tuple[SiteHomeFeatureConfig, ...]


@dataclass(frozen=True, slots=True)
class SiteHomePipelineStepConfig:
    id: str
    label: str


@dataclass(frozen=True, slots=True)
class SiteHomeModeConfig:
    title: str
    subtitle: str
    body: str
    variant: str = ""


@dataclass(frozen=True, slots=True)
class SiteHomePipelineConfig:
    eyebrow: str
    title: str
    subtitle: str
    current_step: int
    steps: tuple[SiteHomePipelineStepConfig, ...]
    modes: tuple[SiteHomeModeConfig, ...]


@dataclass(frozen=True, slots=True)
class SiteHomeDeploymentOptionConfig:
    label: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class SiteHomeDeploymentFutureConfig:
    label: str
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SiteHomeDeploymentsConfig:
    eyebrow: str
    title: str
    subtitle: str
    options: tuple[SiteHomeDeploymentOptionConfig, ...]
    future: SiteHomeDeploymentFutureConfig | None = None


@dataclass(frozen=True, slots=True)
class SiteHomeSourceFormatConfig:
    label: str
    detail: str
    body: str


@dataclass(frozen=True, slots=True)
class SiteHomeSourcesConfig:
    eyebrow: str
    title: str
    subtitle: str
    formats: tuple[SiteHomeSourceFormatConfig, ...]
    note: str = ""


@dataclass(frozen=True, slots=True)
class SiteHomeExportsConfig:
    title: str
    subtitle: str
    action: SiteCtaConfig


@dataclass(frozen=True, slots=True)
class SiteHomeQuickStartConfig:
    eyebrow: str
    title: str
    commands: str
    caption: str


@dataclass(frozen=True, slots=True)
class SiteHomeWorkflowLinkConfig:
    label: str
    href: str


@dataclass(frozen=True, slots=True)
class SiteHomeWorkflowItemConfig:
    text: str
    link: SiteHomeWorkflowLinkConfig | None = None


@dataclass(frozen=True, slots=True)
class SiteHomeWorkflowsConfig:
    eyebrow: str
    title: str
    items: tuple[SiteHomeWorkflowItemConfig, ...]


@dataclass(frozen=True, slots=True)
class SiteHomeBrandConfig:
    eyebrow: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class SiteHomeStackRowConfig:
    mark: str
    name: str
    description: str
    href: str | None = None
    here: bool = False


@dataclass(frozen=True, slots=True)
class SiteHomeStackConfig:
    eyebrow: str
    title: str
    intro: str
    footer: str
    rows: tuple[SiteHomeStackRowConfig, ...]


@dataclass(frozen=True, slots=True)
class SiteHomeMetricsHeadConfig:
    eyebrow: str = ""
    title: str = ""
    subtitle: str = ""


@dataclass(frozen=True, slots=True)
class SiteHomeExploreConfig:
    title: str
    links: tuple[SiteCtaConfig, ...]


@dataclass(frozen=True, slots=True)
class SiteHomeCtaBandConfig:
    title: str
    body: str
    secondary: SiteCtaConfig


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
    metrics_head: SiteHomeMetricsHeadConfig | None = None
    visual: SiteHomeVisualConfig = field(default_factory=SiteHomeVisualConfig)
    ideas: SiteHomeIdeasConfig | None = None
    explore: SiteHomeExploreConfig | None = None
    pipeline: SiteHomePipelineConfig | None = None
    deployments: SiteHomeDeploymentsConfig | None = None
    sources: SiteHomeSourcesConfig | None = None
    exports: SiteHomeExportsConfig | None = None
    quick_start: SiteHomeQuickStartConfig | None = None
    workflows: SiteHomeWorkflowsConfig | None = None
    brand: SiteHomeBrandConfig | None = None
    stack: SiteHomeStackConfig | None = None
    cta: SiteHomeCtaBandConfig | None = None


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
class CatalogIdentityConfig:
    """Enterprise identity defaults for one configured docs site."""

    tenant: str = "default"
    workspace: str = "default"
    site: str = "default"

    def to_meta(self) -> dict[str, str]:
        return {
            "tenant": self.tenant,
            "workspace": self.workspace,
            "site": self.site,
        }


@dataclass(frozen=True, slots=True)
class SiteConfig:
    """Product branding and shell copy — variabilizes home and top navigation."""

    name: str = "Furatena"
    tagline: str = "Live documentation from markdown"
    description: str = (
        "Write markdown. Get a fast, searchable doc site that reloads while you work — "
        "and exports to GitHub Pages when you're ready to ship."
    )
    mark: str = "𐂛"
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
class PresentationConfig:
    """Site-global presentation-pack selection and explicit trust grants."""

    layout: str | None = None
    skin: str | None = None
    overrides: tuple[str, ...] = ()
    trusted_capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class DeliveryThemeConfig:
    """Theme identity selected by a delivery head."""

    id: str | None = None
    use: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryMountConfig:
    """Per-mount delivery overrides."""

    head: str | None = None
    theme: DeliveryThemeConfig = field(default_factory=DeliveryThemeConfig)


@dataclass(frozen=True, slots=True)
class DeliveryConfig:
    """Global and mount-specific rendering head / theme selection."""

    head: str = "live-shell"
    theme: DeliveryThemeConfig = field(default_factory=DeliveryThemeConfig)
    mounts: dict[str, DeliveryMountConfig] = field(default_factory=dict)


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
    presentation: PresentationConfig = field(default_factory=PresentationConfig)
    site: SiteConfig = field(default_factory=SiteConfig)
    catalog: CatalogNavConfig = field(default_factory=CatalogNavConfig)
    identity: CatalogIdentityConfig = field(default_factory=CatalogIdentityConfig)
    delivery: DeliveryConfig = field(default_factory=DeliveryConfig)
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


def _identity_value(*values: object, default: str) -> str:
    for value in values:
        text = _optional_str(value)
        if text is not None:
            return text
    return default


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


def _merge_home_raw(raw: object, *, app_root: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    merged = dict(raw)
    data_path = raw.get("data")
    if data_path:
        path = _resolve_path(app_root, str(data_path))
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            merged = {**loaded, **{k: v for k, v in raw.items() if k != "data"}}
    return merged


def _parse_home_features(raw: object) -> tuple[SiteHomeFeatureConfig, ...]:
    if not isinstance(raw, list):
        return ()
    features: list[SiteHomeFeatureConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        eyebrow = str(item.get("eyebrow") or "").strip()
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        code = str(item.get("code") or "").strip("\n")
        if not eyebrow or not title or not body:
            continue
        action_raw = item.get("action")
        action = None
        if isinstance(action_raw, dict):
            action = _parse_cta(
                action_raw, default=SiteCtaConfig(label="Learn more", href="/docs/")
            )
        features.append(
            SiteHomeFeatureConfig(
                eyebrow=eyebrow,
                title=title,
                body=body,
                code=code,
                action=action,
                reverse=bool(item.get("reverse")),
            )
        )
    return tuple(features)


def _parse_home_ideas(raw: object) -> SiteHomeIdeasConfig | None:
    if not isinstance(raw, dict):
        return None
    features = _parse_home_features(raw.get("features"))
    if not features:
        return None
    return SiteHomeIdeasConfig(
        eyebrow=str(raw.get("eyebrow") or "Catalog graph"),
        title=str(raw.get("title") or "Why Furatena"),
        subtitle=str(raw.get("subtitle") or ""),
        features=features,
    )


def _parse_home_pipeline_steps(raw: object) -> tuple[SiteHomePipelineStepConfig, ...]:
    if not isinstance(raw, list):
        return ()
    steps: list[SiteHomePipelineStepConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        if step_id and label:
            steps.append(SiteHomePipelineStepConfig(id=step_id, label=label))
    return tuple(steps)


def _parse_home_pipeline(raw: object) -> SiteHomePipelineConfig | None:
    if not isinstance(raw, dict):
        return None
    modes_raw = raw.get("modes")
    if not isinstance(modes_raw, list):
        return None
    modes: list[SiteHomeModeConfig] = []
    for item in modes_raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        subtitle = str(item.get("subtitle") or "").strip()
        body = str(item.get("body") or "").strip()
        if title and body:
            modes.append(
                SiteHomeModeConfig(
                    title=title,
                    subtitle=subtitle,
                    body=body,
                    variant=str(item.get("variant") or "").strip(),
                )
            )
    steps = _parse_home_pipeline_steps(raw.get("steps"))
    if not modes or not steps:
        return None
    current = raw.get("current_step", len(steps))
    try:
        current_step = int(current)
    except TypeError, ValueError:
        current_step = len(steps)
    return SiteHomePipelineConfig(
        eyebrow=str(raw.get("eyebrow") or "Delivery modes"),
        title=str(raw.get("title") or "The pipeline"),
        subtitle=str(raw.get("subtitle") or ""),
        current_step=current_step,
        steps=steps,
        modes=tuple(modes),
    )


def _parse_home_deployments(raw: object) -> SiteHomeDeploymentsConfig | None:
    if not isinstance(raw, dict):
        return None
    options_raw = raw.get("options")
    if not isinstance(options_raw, list):
        return None
    options: list[SiteHomeDeploymentOptionConfig] = []
    for item in options_raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        if label and title and body:
            options.append(SiteHomeDeploymentOptionConfig(label=label, title=title, body=body))
    if not options:
        return None
    future = None
    future_raw = raw.get("future")
    if isinstance(future_raw, dict):
        items_raw = future_raw.get("items")
        if isinstance(items_raw, list):
            items = tuple(str(item).strip() for item in items_raw if str(item).strip())
            if items:
                future = SiteHomeDeploymentFutureConfig(
                    label=str(future_raw.get("label") or "Opens the door to").strip(),
                    items=items,
                )
    return SiteHomeDeploymentsConfig(
        eyebrow=str(raw.get("eyebrow") or "Deployment choices").strip(),
        title=str(raw.get("title") or "Choose how your docs run").strip(),
        subtitle=str(raw.get("subtitle") or "").strip(),
        options=tuple(options),
        future=future,
    )


def _parse_home_sources(raw: object) -> SiteHomeSourcesConfig | None:
    if not isinstance(raw, dict):
        return None
    formats_raw = raw.get("formats")
    if not isinstance(formats_raw, list):
        return None
    formats: list[SiteHomeSourceFormatConfig] = []
    for item in formats_raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        detail = str(item.get("detail") or "").strip()
        body = str(item.get("body") or "").strip()
        if label and body:
            formats.append(SiteHomeSourceFormatConfig(label=label, detail=detail, body=body))
    if not formats:
        return None
    return SiteHomeSourcesConfig(
        eyebrow=str(raw.get("eyebrow") or "Source adapters").strip(),
        title=str(raw.get("title") or "Bring the docs you already have").strip(),
        subtitle=str(raw.get("subtitle") or "").strip(),
        formats=tuple(formats),
        note=str(raw.get("note") or "").strip(),
    )


def _parse_home_exports(raw: object) -> SiteHomeExportsConfig | None:
    if not isinstance(raw, dict):
        return None
    action_raw = raw.get("action")
    if not isinstance(action_raw, dict):
        return None
    return SiteHomeExportsConfig(
        title=str(raw.get("title") or "Agent-native exports"),
        subtitle=str(raw.get("subtitle") or ""),
        action=_parse_cta(action_raw, default=SiteCtaConfig(label="All exports", href="/develop/")),
    )


def _parse_home_quick_start(raw: object) -> SiteHomeQuickStartConfig | None:
    if not isinstance(raw, dict):
        return None
    commands = str(raw.get("commands") or "").strip("\n")
    if not commands:
        return None
    return SiteHomeQuickStartConfig(
        eyebrow=str(raw.get("eyebrow") or "Run locally"),
        title=str(raw.get("title") or "Quick start"),
        commands=commands,
        caption=str(raw.get("caption") or "").strip(),
    )


def _parse_home_workflows(raw: object) -> SiteHomeWorkflowsConfig | None:
    if not isinstance(raw, dict):
        return None
    items_raw = raw.get("items")
    if not isinstance(items_raw, list):
        return None
    items: list[SiteHomeWorkflowItemConfig] = []
    for item in items_raw:
        if isinstance(item, str):
            text = item.strip()
            if text:
                items.append(SiteHomeWorkflowItemConfig(text=text))
            continue
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        link_raw = item.get("link")
        link = None
        if isinstance(link_raw, dict):
            label = str(link_raw.get("label") or "").strip()
            href = str(link_raw.get("href") or "").strip()
            if label and href:
                link = SiteHomeWorkflowLinkConfig(label=label, href=href)
        items.append(SiteHomeWorkflowItemConfig(text=text, link=link))
    if not items:
        return None
    return SiteHomeWorkflowsConfig(
        eyebrow=str(raw.get("eyebrow") or "Authoring"),
        title=str(raw.get("title") or "Common workflows"),
        items=tuple(items),
    )


def _parse_home_brand(raw: object) -> SiteHomeBrandConfig | None:
    if not isinstance(raw, dict):
        return None
    body = str(raw.get("body") or "").strip()
    if not body:
        return None
    return SiteHomeBrandConfig(
        eyebrow=str(raw.get("eyebrow") or "Gold in the ledger"),
        title=str(raw.get("title") or "The mark"),
        body=body,
    )


def _parse_home_stack_rows(raw: object) -> tuple[SiteHomeStackRowConfig, ...]:
    if not isinstance(raw, list):
        return ()
    rows: list[SiteHomeStackRowConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mark = str(item.get("mark") or "").strip()
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not mark or not name:
            continue
        href = _optional_str(item.get("href"))
        rows.append(
            SiteHomeStackRowConfig(
                mark=mark,
                name=name,
                description=description,
                href=href,
                here=bool(item.get("here")),
            )
        )
    return tuple(rows)


def _parse_home_stack(raw: object) -> SiteHomeStackConfig | None:
    if not isinstance(raw, dict):
        return None
    rows = _parse_home_stack_rows(raw.get("rows"))
    if not rows:
        return None
    return SiteHomeStackConfig(
        eyebrow=str(raw.get("eyebrow") or "Bengal"),
        title=str(raw.get("title") or "The Bengal stack"),
        intro=str(raw.get("intro") or "").strip(),
        footer=str(raw.get("footer") or "").strip(),
        rows=rows,
    )


def _parse_home_cta_links(raw: object) -> tuple[SiteCtaConfig, ...]:
    if not isinstance(raw, list):
        return ()
    links: list[SiteCtaConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        link = _parse_cta(item, default=SiteCtaConfig(label="", href=""))
        if link.label and link.href:
            links.append(link)
    return tuple(links)


def _parse_home_metrics_head(raw: object) -> SiteHomeMetricsHeadConfig | None:
    if not isinstance(raw, dict):
        return None
    eyebrow = str(raw.get("eyebrow") or "").strip()
    title = str(raw.get("title") or "").strip()
    subtitle = str(raw.get("subtitle") or "").strip()
    if not (eyebrow or title or subtitle):
        return None
    return SiteHomeMetricsHeadConfig(eyebrow=eyebrow, title=title, subtitle=subtitle)


def _parse_home_explore(raw: object) -> SiteHomeExploreConfig | None:
    if not isinstance(raw, dict):
        return None
    links = _parse_home_cta_links(raw.get("links"))
    if not links:
        return None
    return SiteHomeExploreConfig(
        title=str(raw.get("title") or "Go deeper").strip(),
        links=links,
    )


def _parse_home_cta_band(
    raw: object, *, default_secondary: SiteCtaConfig
) -> SiteHomeCtaBandConfig | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    body = str(raw.get("body") or "").strip()
    if not title:
        return None
    secondary_raw = raw.get("secondary")
    secondary = (
        _parse_cta(secondary_raw, default=default_secondary)
        if isinstance(secondary_raw, dict)
        else default_secondary
    )
    return SiteHomeCtaBandConfig(title=title, body=body, secondary=secondary)


def _parse_home_config(raw: object, *, site_name: str, app_root: Path) -> SiteHomeConfig:
    defaults = SiteHomeConfig(
        hero_points=(),
        metrics=(
            SiteMetricConfig(
                "1", "markdown corpus", "Index pages from content/ mounts at serve time."
            ),
            SiteMetricConfig(
                "0", "export loop", "Author mode reloads the open page via htmx partial swaps."
            ),
            SiteMetricConfig(
                "∞", "graph edges", "Links, nav, search, and agents read the same catalog."
            ),
        ),
        visual=SiteHomeVisualConfig(
            aria_label=f"Preview of {site_name} documentation surfaces",
            cta_href="/docs/get-started/",
        ),
    )
    if not isinstance(raw, dict):
        return defaults
    merged = _merge_home_raw(raw, app_root=app_root)
    if "hero_points" in merged:
        hero_raw = merged.get("hero_points") or ()
        hero_points = tuple(str(item).strip() for item in hero_raw if str(item).strip())
    else:
        hero_points = defaults.hero_points
    metrics_raw = merged.get("metrics")
    metrics = _parse_metrics(metrics_raw) if metrics_raw is not None else defaults.metrics
    visual = _parse_home_visual(merged.get("visual"), defaults=defaults.visual)
    cta_secondary = _parse_cta(merged.get("cta_secondary"), default=defaults.cta_secondary)
    return SiteHomeConfig(
        aria_label=str(merged.get("aria_label") or f"{site_name} overview"),
        hero_points=hero_points,
        cta_primary=_parse_cta(merged.get("cta_primary"), default=defaults.cta_primary),
        cta_secondary=cta_secondary,
        metrics=metrics,
        metrics_head=_parse_home_metrics_head(merged.get("metrics_head")),
        visual=visual,
        ideas=_parse_home_ideas(merged.get("ideas")),
        explore=_parse_home_explore(merged.get("explore")),
        pipeline=_parse_home_pipeline(merged.get("pipeline")),
        deployments=_parse_home_deployments(merged.get("deployments")),
        sources=_parse_home_sources(merged.get("sources")),
        exports=_parse_home_exports(merged.get("exports")),
        quick_start=_parse_home_quick_start(merged.get("quick_start")),
        workflows=_parse_home_workflows(merged.get("workflows")),
        brand=_parse_home_brand(merged.get("brand")),
        stack=_parse_home_stack(merged.get("stack")),
        cta=_parse_home_cta_band(merged.get("cta"), default_secondary=cta_secondary),
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
        overview_href=str(
            overview_raw.get("href") or raw.get("overview_href") or defaults.overview_href
        ),
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
                    "How pages, navigation, search, and outputs fit together.",
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
                    "Preview locally, build static pages, check links, and deploy.",
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
                    "Product principles, roadmap, and project direction.",
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
            overview_blurb="Structured outputs for search, references, and AI tools.",
            links=(
                SiteNavLinkConfig(
                    "/api/", "Autodoc API", "Python modules indexed from the repo.", "code"
                ),
                SiteNavLinkConfig(
                    "/shared/", "Shared reference", "Cross-mount glossary and formats.", "globe"
                ),
                SiteNavLinkConfig(
                    "/releases/", "Releases", "Version notes and channel history.", "rocket"
                ),
                SiteNavLinkConfig(
                    "/develop/catalog/", "Catalog JSON", "Structured page metadata.", "file-code"
                ),
                SiteNavLinkConfig("/develop/llms/", "llms.txt", "AI-ready page index.", "article"),
                SiteNavLinkConfig(
                    "/develop/search/", "search.json", "Keyword search index.", "magnifying-glass"
                ),
                SiteNavLinkConfig(
                    "/develop/tools/", "tools.json", "Tool metadata for integrations.", "stack"
                ),
            ),
        ),
    )


def _parse_delivery_theme(raw: object) -> DeliveryThemeConfig:
    if not isinstance(raw, dict):
        return DeliveryThemeConfig()
    return DeliveryThemeConfig(
        id=_optional_str(raw.get("id")),
        use=_optional_str(raw.get("use")),
    )


def _parse_delivery_config(raw: object) -> DeliveryConfig:
    if not isinstance(raw, dict):
        return DeliveryConfig()
    mounts: dict[str, DeliveryMountConfig] = {}
    mounts_raw = raw.get("mounts")
    if isinstance(mounts_raw, dict):
        for mount_id, item in mounts_raw.items():
            if not isinstance(item, dict):
                continue
            mount_key = str(mount_id).strip()
            if not mount_key:
                continue
            mounts[mount_key] = DeliveryMountConfig(
                head=_optional_str(item.get("head")),
                theme=_parse_delivery_theme(item.get("theme")),
            )
    return DeliveryConfig(
        head=str(raw.get("head") or "live-shell"),
        theme=_parse_delivery_theme(raw.get("theme")),
        mounts=mounts,
    )


def _parse_identity_config(raw: dict[str, Any], *, site_raw: object) -> CatalogIdentityConfig:
    identity_raw = raw.get("identity")
    if not isinstance(identity_raw, dict):
        identity_raw = raw.get("enterprise") if isinstance(raw.get("enterprise"), dict) else {}
    site_identity = site_raw if isinstance(site_raw, dict) else {}
    return CatalogIdentityConfig(
        tenant=_identity_value(
            identity_raw.get("tenant"),
            identity_raw.get("tenant_id"),
            raw.get("tenant"),
            raw.get("tenant_id"),
            default="default",
        ),
        workspace=_identity_value(
            identity_raw.get("workspace"),
            identity_raw.get("workspace_id"),
            raw.get("workspace"),
            raw.get("workspace_id"),
            default="default",
        ),
        site=_identity_value(
            identity_raw.get("site"),
            identity_raw.get("site_id"),
            site_identity.get("id"),
            raw.get("site_id"),
            default="default",
        ),
    )


def _parse_site_config(raw: object, *, app_root: Path) -> SiteConfig:
    site_raw = raw if isinstance(raw, dict) else {}
    name = str(site_raw.get("name") or "Furatena").strip() or "Furatena"
    tagline = str(site_raw.get("tagline") or "Polished docs from Markdown").strip()
    description = str(
        site_raw.get("description")
        or (
            "Write locally, preview instantly, and publish a fast, searchable "
            "documentation site from the same Markdown source."
        )
    ).strip()
    mark = str(site_raw.get("mark") or "𐂛").strip() or "𐂛"
    home = _parse_home_config(site_raw.get("home"), site_name=name, app_root=app_root)
    nav_raw = site_raw.get("navigation")
    navigation = default_site_navigation(name)
    if isinstance(nav_raw, dict):
        navigation = SiteNavigationConfig(
            documentation=_parse_nav_section(
                nav_raw.get("documentation"), defaults=navigation.documentation
            ),
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
    overrides_raw = (
        theme_raw.get("overrides") if isinstance(theme_raw.get("overrides"), dict) else {}
    )
    use_raw = theme_raw.get("use")
    use = str(use_raw).strip() if use_raw else None
    has_local_skin = any(key in theme_raw for key in ("tokens", "styles")) or bool(overrides_raw)
    code = str(effects_raw.get("code") or "flat")
    cards = str(effects_raw.get("cards") or "flat")
    hero = str(effects_raw.get("hero") or "wash")
    theme = ThemeConfig(
        id=str(theme_raw.get("id") or "furatena"),
        use=use or (None if has_local_skin else "lagoon"),
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

    presentation_raw = raw.get("presentation") or {}
    if not isinstance(presentation_raw, dict):
        presentation_raw = {}
    presentation_overrides_raw = presentation_raw.get("overrides") or ()
    if not isinstance(presentation_overrides_raw, list | tuple):
        presentation_overrides_raw = ()
    presentation_trust_raw = presentation_raw.get("trusted_capabilities") or ()
    if not isinstance(presentation_trust_raw, list | tuple | set | frozenset):
        presentation_trust_raw = ()
    presentation = PresentationConfig(
        layout=_optional_str(presentation_raw.get("layout")),
        skin=_optional_str(presentation_raw.get("skin")),
        overrides=tuple(
            value for item in presentation_overrides_raw if (value := str(item).strip())
        ),
        trusted_capabilities=frozenset(
            value for item in presentation_trust_raw if (value := str(item).strip())
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

    site_raw = raw.get("site")
    site = _parse_site_config(site_raw, app_root=root)
    catalog = parse_catalog_nav(raw.get("catalog"))
    identity = _parse_identity_config(raw, site_raw=site_raw)
    delivery = _parse_delivery_config(raw.get("delivery"))

    return DocsConfig(
        root=root,
        shell=str(raw.get("shell") or "shell.html"),
        views=views,
        overrides=overrides,
        compose=compose,
        theme=theme,
        presentation=presentation,
        site=site,
        catalog=catalog,
        identity=identity,
        delivery=delivery,
        mounts_path=mounts_path,
        rewrites_path=rewrites_path,
        inventories_path=inventories_path,
        i18n=i18n,
        locales_dir=locales_dir,
    )
