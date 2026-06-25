"""Theme asset and config validation for ``fura check``."""

from __future__ import annotations

from pathlib import Path

from furatena.catalog.config import DocsConfig
from furatena.catalog.docs_core import load_docs_core
from furatena.catalog.theme_pack import list_theme_packs, load_theme_pack, resolve_theme_paths
from furatena.catalog.theme_preset import validate_font_name, validate_measure_value, write_theme_preset
from furatena.catalog.vendor_paths import VENDOR_FILES, vendor_dir

_EFFECTS_CODE = frozenset({"flat", "subtle", "glow"})
_EFFECTS_CARDS = frozenset({"flat", "elevated"})
_EFFECTS_HERO = frozenset({"wash", "minimal"})

_REQUIRED_JS = (
    "fura-utils.js",
    "fura-toc.js",
    "fura-nav.js",
    "fura-theme.js",
    "fura-static-search.js",
    "docs-enhance.js",
)


def check_theme_assets(docs: DocsConfig) -> tuple[list[str], list[str]]:
    """Validate local theme files, scripts, and effect preset config."""
    errors: list[str] = []
    warnings: list[str] = []

    effects = docs.theme.effects
    for field, value, allowed in (
        ("code", effects.code, _EFFECTS_CODE),
        ("cards", effects.cards, _EFFECTS_CARDS),
        ("hero", effects.hero, _EFFECTS_HERO),
    ):
        if value not in allowed:
            errors.append(f"theme.effects.{field} invalid: {value!r}")

    measure = docs.theme.measure
    for field, value in (
        ("prose", measure.prose),
        ("reading", measure.reading),
        ("docs", measure.docs),
        ("container", measure.container),
    ):
        message = validate_measure_value(value, field=field)
        if message:
            errors.append(message)

    fonts = docs.theme.fonts
    for field, value in (("sans", fonts.sans), ("display", fonts.display)):
        message = validate_font_name(value, field=field)
        if message:
            errors.append(message)

    if docs.theme.use:
        try:
            load_theme_pack(docs.theme.use)
        except (LookupError, TypeError, ValueError) as exc:
            errors.append(f"theme.use invalid: {exc}")

    if docs.theme.id and load_docs_core(docs.theme.id) is None:
        errors.append(f"theme.id unknown or docs-core missing: {docs.theme.id!r}")

    try:
        skin = resolve_theme_paths(docs)
    except FileNotFoundError as exc:
        errors.append(str(exc))
        return sorted(errors), sorted(warnings)

    for name in _REQUIRED_JS:
        if not (skin.js_dir / name).is_file():
            errors.append(f"theme js/{name} missing")

    if skin.fonts_dir is None:
        warnings.append("theme fonts dir missing (typography may fall back to system fonts)")

    branding_dir = skin.app_assets_root / "branding"
    for rel in ("favicon.svg", "site.webmanifest"):
        if not (branding_dir / rel).is_file():
            warnings.append(f"theme/assets/branding/{rel} missing")

    css_dir = skin.docs_core.css_dir if skin.docs_core is not None else skin.app_assets_root / "css"
    bundled = css_dir / "style.css"
    if not bundled.is_file():
        errors.append("docs-core style.css missing (packaged bundle entry)")
    else:
        text = bundled.read_text(encoding="utf-8")
        for legacy in (
            "layouts/grid.css",
            "components/alerts.css",
            "components/tabs.css",
            "components/dropdowns.css",
            "components/blog.css",
            "components/widgets.css",
            "components/stale-banner.css",
        ):
            if legacy in text:
                warnings.append(f"packaged bundle still imports superseded module: {legacy}")

    cache_dir = docs.root / ".docs-cache"
    preset_path = write_theme_preset(docs.theme, cache_dir=cache_dir)
    if not preset_path.is_file():
        errors.append("failed to generate theme-preset.css")

    if docs.theme.use is None and not list_theme_packs():
        warnings.append("no furatena.themes entry points registered")

    vendor_root = Path(vendor_dir())
    for name in VENDOR_FILES:
        if not (vendor_root / name).is_file():
            errors.append(f"vendor asset missing: {name}")

    shell_template = docs.framework_templates_dir / "layouts" / "fura_shell.html"
    if shell_template.is_file():
        shell_source = shell_template.read_text(encoding="utf-8")
        if "unpkg.com" in shell_source:
            errors.append("fura_shell.html still references unpkg CDN scripts")
        if "/docs-vendor/htmx.min.js" not in shell_source:
            warnings.append("fura_shell.html does not reference vendored htmx script")

    if branding_dir := skin.app_assets_root / "branding":
        if branding_dir.is_dir() and not (branding_dir / "favicon.ico").is_file():
            warnings.append("theme/assets/branding/favicon.ico missing (browsers request /favicon.ico)")

    return sorted(errors), sorted(warnings)
