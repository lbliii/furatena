"""Theme asset and config validation for ``fura check``."""

from __future__ import annotations

import re
from pathlib import Path

from furatena.catalog.config import DocsConfig
from furatena.catalog.docs_core import load_docs_core
from furatena.catalog.theme_pack import list_theme_packs, load_theme_pack, resolve_theme_paths
from furatena.catalog.theme_preset import (
    validate_font_name,
    validate_measure_value,
    write_theme_preset,
)
from furatena.catalog.vendor_paths import VENDOR_FILES, vendor_dir

_EFFECTS_CODE = frozenset({"flat", "subtle", "glow"})
_EFFECTS_CARDS = frozenset({"flat", "elevated"})
_EFFECTS_HERO = frozenset({"wash", "minimal"})

_REQUIRED_JS = (
    "fura-utils.js",
    "fura-toc.js",
    "fura-nav.js",
    "fura-theme.js",
    "fura-mermaid.js",
    "fura-static-search.js",
    "docs-enhance.js",
)

_CSS_IMPORT_RE = re.compile(r"@import\s+url\(['\"]?(?P<path>[^'\")]+)")
_CSS_CLASS_RE = re.compile(r"(?<![\w-])\.([A-Za-z_][A-Za-z0-9_-]*)")
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_TEMPLATE_CLASS_ATTR_RE = re.compile(
    r"\bclass\s*=\s*(?P<quote>['\"])(?P<classes>.*?)(?P=quote)",
    re.DOTALL,
)
_LOCAL_TEMPLATE_CLASS_RE = re.compile(r"(?:chirp-theme-[A-Za-z0-9_-]+|visually-hidden)")
_UNDOCUMENTED_SAFE_RE = re.compile(r"\|\s*safe\b(?!\s*\()")


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

    errors.extend(check_safe_filter_reasons(docs))

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
        imports = {match.group("path") for match in _CSS_IMPORT_RE.finditer(text)}
        for legacy in (
            "layouts/grid.css",
            "components/alerts.css",
            "components/tabs.css",
            "components/dropdowns.css",
            "components/blog.css",
            "components/widgets.css",
            "components/stale-banner.css",
        ):
            if legacy in imports:
                warnings.append(f"packaged bundle still imports superseded module: {legacy}")

    css_entries = (bundled, skin.tokens, skin.styles, skin.directives)
    css_classes = _effective_css_classes(css_entries)
    template_roots = tuple(
        path
        for path in (
            docs.templates_dir,
            skin.templates,
            docs.theme_dir,
            docs.framework_templates_dir,
        )
        if path is not None and path.is_dir()
    )
    for class_name, source_paths in _local_template_class_usages(template_roots).items():
        if class_name in css_classes:
            continue
        sources = ", ".join(_display_path(path, docs.root) for path in source_paths)
        errors.append(
            f"theme local CSS class .{class_name} has no selector in resolved theme CSS "
            f"(used by {sources})"
        )

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

    if (
        (branding_dir := skin.app_assets_root / "branding").is_dir()
        and not (branding_dir / "favicon.ico").is_file()
        and not (branding_dir / "favicon.svg").is_file()
    ):
        warnings.append("theme/assets/branding/favicon.ico or favicon.svg missing")

    return sorted(errors), sorted(warnings)


def _effective_css_classes(entrypoints: tuple[Path, ...]) -> frozenset[str]:
    """Return class selectors reachable from the effective CSS entrypoints."""
    classes: set[str] = set()
    pending = [path.resolve() for path in entrypoints if path.is_file()]
    visited: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        source = path.read_text(encoding="utf-8")
        contract_source = _CSS_COMMENT_RE.sub("", source)
        classes.update(_CSS_CLASS_RE.findall(contract_source))
        for match in _CSS_IMPORT_RE.finditer(contract_source):
            raw = match.group("path").strip()
            if not raw or raw.startswith(("/", "#", "data:")) or "://" in raw:
                continue
            relative = raw.split("?", 1)[0].split("#", 1)[0]
            imported = (path.parent / relative).resolve()
            if imported.suffix == ".css" and imported.is_file():
                pending.append(imported)
    return frozenset(classes)


def _local_template_class_usages(
    template_roots: tuple[Path, ...],
) -> dict[str, tuple[Path, ...]]:
    """Map literal theme-owned classes to deduplicated physical templates."""
    usages: dict[str, set[Path]] = {}
    visited: set[Path] = set()
    for root in template_roots:
        for candidate in root.rglob("*.html"):
            path = candidate.resolve()
            if path in visited or not path.is_file():
                continue
            visited.add(path)
            source = path.read_text(encoding="utf-8")
            for attr in _TEMPLATE_CLASS_ATTR_RE.finditer(source):
                for raw_token in attr.group("classes").split():
                    if "{" in raw_token or "}" in raw_token:
                        continue
                    match = _LOCAL_TEMPLATE_CLASS_RE.fullmatch(raw_token)
                    if match is None:
                        continue
                    class_name = match.group(0)
                    usages.setdefault(class_name, set()).add(path)
    return {class_name: tuple(sorted(paths)) for class_name, paths in sorted(usages.items())}


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def check_safe_filter_reasons(docs: DocsConfig) -> list[str]:
    """Reject template ``safe`` filters that omit a reviewable reason."""
    roots = {
        docs.framework_templates_dir.resolve(),
        docs.templates_dir.resolve(),
        docs.theme_dir.resolve(),
    }
    errors: list[str] = []
    seen: set[Path] = set()
    for root in sorted(roots, key=str):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.html")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _UNDOCUMENTED_SAFE_RE.search(line):
                    errors.append(
                        f"{path}:{line_number}: undocumented |safe filter; "
                        'use escaping, a trusted producer type, or safe(reason="...")'
                    )
    return errors
