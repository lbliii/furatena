"""Internationalization for Furatena — content locales and UI chrome."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

_RTL_LOCALES: frozenset[str] = frozenset({"ar", "he", "fa", "ur", "yi", "dv", "ku", "ps", "sd"})


@dataclass(frozen=True, slots=True)
class DocsLanguage:
    """One configured documentation locale."""

    code: str
    name: str
    hreflang: str = ""
    rtl: bool | None = None
    weight: int = 0

    @property
    def direction(self) -> str:
        if self.rtl is not None:
            return "rtl" if self.rtl else "ltr"
        return "rtl" if self.code in _RTL_LOCALES else "ltr"


@dataclass(frozen=True, slots=True)
class DocsI18nConfig:
    """Declarative i18n settings from ``docs.yaml``."""

    default_language: str = "en"
    fallback_to_default: bool = True
    languages: tuple[DocsLanguage, ...] = (DocsLanguage("en", "English"),)
    strategy: str = "subdir"
    locale_content_dir: str = "_locale"

    @property
    def enabled(self) -> bool:
        return self.strategy != "none" and len(self.languages) > 1

    def language_codes(self) -> frozenset[str]:
        return frozenset(lang.code for lang in self.languages)

    def language(self, code: str) -> DocsLanguage | None:
        for lang in self.languages:
            if lang.code == code:
                return lang
        return None

    def sorted_languages(self) -> tuple[DocsLanguage, ...]:
        return tuple(sorted(self.languages, key=lambda item: (item.weight, item.code)))


def load_i18n_config(raw: dict[str, Any] | None) -> DocsI18nConfig:
    """Parse the ``i18n`` block from ``docs.yaml``."""
    if not raw or not isinstance(raw, dict):
        return DocsI18nConfig()

    default = str(raw.get("default_language") or "en").strip() or "en"
    strategy = str(raw.get("strategy") or "subdir").strip().lower() or "subdir"
    fallback = bool(raw.get("fallback_to_default", True))
    locale_content_dir = str(raw.get("locale_content_dir") or "_locale").strip() or "_locale"

    languages_raw = raw.get("languages") or [default]
    languages: list[DocsLanguage] = []
    seen: set[str] = set()
    for entry in languages_raw:
        if isinstance(entry, str):
            code = entry.strip()
            if not code or code in seen:
                continue
            seen.add(code)
            languages.append(DocsLanguage(code=code, name=code, hreflang=code))
            continue
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        rtl_raw = entry.get("rtl")
        rtl = bool(rtl_raw) if isinstance(rtl_raw, bool) else None
        languages.append(
            DocsLanguage(
                code=code,
                name=str(entry.get("name") or code),
                hreflang=str(entry.get("hreflang") or code),
                rtl=rtl,
                weight=int(entry.get("weight") or 0),
            )
        )

    if default not in seen:
        languages.insert(0, DocsLanguage(code=default, name=default, hreflang=default))

    if not languages:
        languages = [DocsLanguage(code=default, name=default, hreflang=default)]

    return DocsI18nConfig(
        default_language=default,
        fallback_to_default=fallback,
        languages=tuple(languages),
        strategy=strategy,
        locale_content_dir=locale_content_dir,
    )


def active_language_override() -> str | None:
    """Optional locale override from ``FURA_LANG``."""
    raw = os.environ.get("FURA_LANG", "").strip()
    return raw or None


def detect_lang_from_path(path: str, config: DocsI18nConfig) -> str:
    """Resolve locale from a request path (``/es/docs/...`` → ``es``)."""
    if not config.enabled:
        return config.default_language
    parts = path.strip("/").split("/")
    if parts and parts[0] in config.language_codes() and parts[0] != config.default_language:
        return parts[0]
    return config.default_language


def strip_locale_prefix(slug: str, config: DocsI18nConfig) -> str:
    """Remove a leading locale segment from a slug."""
    slug = slug.strip("/")
    if not slug or not config.enabled:
        return slug
    head, _, tail = slug.partition("/")
    if head in config.language_codes() and head != config.default_language:
        return tail
    return slug


def locale_slug(slug: str, lang: str, config: DocsI18nConfig) -> str:
    """Prefix a canonical slug with its locale when needed."""
    canonical = strip_locale_prefix(slug, config)
    if lang == config.default_language or not config.enabled:
        return canonical
    if not canonical:
        return lang
    return f"{lang}/{canonical}"


def locale_url(url: str, lang: str, config: DocsI18nConfig) -> str:
    """Prefix a URL with ``/{lang}`` for non-default locales."""
    if lang == config.default_language or not config.enabled:
        return url
    prefix = f"/{lang}"
    if url == "/":
        return f"{prefix}/"
    if url.startswith("/"):
        return f"{prefix}{url}"
    return f"{prefix}/{url}"


def resolve_page_lang(meta: dict[str, Any], *, config: DocsI18nConfig, slug: str) -> str:
    """Language for a page from front matter or slug prefix."""
    raw = meta.get("lang") or meta.get("locale")
    if raw:
        code = str(raw).strip()
        if code in config.language_codes():
            return code
    head = slug.strip("/").split("/", 1)[0]
    if head in config.language_codes() and head != config.default_language:
        return head
    return config.default_language


def resolve_translation_key(
    meta: dict[str, Any],
    *,
    slug: str,
    lang: str,
    config: DocsI18nConfig,
) -> str:
    """Stable key linking translated pages (Bengal-compatible front matter)."""
    raw = meta.get("translation_key")
    if raw:
        return str(raw).strip("/") or "index"
    canonical = strip_locale_prefix(slug, config)
    return canonical.strip("/") or "index"


def node_matches_language(node, lang: str | None, config: DocsI18nConfig) -> bool:
    """Whether a node belongs in nav/search for the active locale."""
    if not config.enabled or not lang:
        return True
    node_lang = getattr(node, "lang", config.default_language)
    return node_lang == lang


@dataclass(frozen=True, slots=True)
class LocalizedNodeMatch:
    """Result of resolving a page for a requested locale."""

    node: Any
    requested_lang: str
    fallback: bool = False
    requested_url: str = ""


def canonical_doc_slug(slug: str, config: DocsI18nConfig) -> str:
    """Strip locale prefix from a docs slug lookup."""
    return strip_locale_prefix(slug.strip("/"), config)


def _normalize_catalog_path(path: str) -> str:
    cleaned = path.strip()
    if not cleaned:
        return "/"
    return cleaned if cleaned.endswith("/") else f"{cleaned}/"


def resolve_localized_node(
    catalog,
    lookup_slug: str,
    *,
    requested_lang: str,
    config: DocsI18nConfig,
) -> LocalizedNodeMatch | None:
    """Resolve a path for ``requested_lang``, optionally falling back to default."""
    path = _normalize_catalog_path(
        lookup_slug if lookup_slug.startswith("/") else f"/{lookup_slug.strip('/')}/"
    )
    node = catalog.get(path)
    if node is not None and node.lang == requested_lang:
        return LocalizedNodeMatch(
            node=node,
            requested_lang=requested_lang,
            fallback=False,
            requested_url=node.url,
        )

    if not config.enabled or not config.fallback_to_default:
        return None
    if requested_lang == config.default_language:
        return None

    canonical = canonical_doc_slug(path.strip("/"), config)
    if not canonical:
        return None

    default_path = _normalize_catalog_path(canonical)
    fallback_node = catalog.get(default_path)
    if fallback_node is None:
        default_mount = getattr(catalog, "default_mount", None)
        mount_id = default_mount.id if default_mount is not None else None
        if mount_id is not None:
            fallback_node = catalog.get_by_slug(canonical, mount=mount_id)
    if fallback_node is None:
        return None

    requested_url = locale_url(fallback_node.url, requested_lang, config)
    return LocalizedNodeMatch(
        node=fallback_node,
        requested_lang=requested_lang,
        fallback=True,
        requested_url=requested_url,
    )


def collect_i18n_export_routes(catalog, config: DocsI18nConfig) -> set[str]:
    """Static export paths for untranslated pages served via fallback."""
    if not config.enabled or not config.fallback_to_default:
        return set()

    translation_index = (
        catalog.translation_index
        if hasattr(catalog, "translation_index")
        else build_translation_index(getattr(catalog, "nodes", ()))
    )
    routes: set[str] = set()
    for node in getattr(catalog, "nodes", ()):
        if getattr(node, "lang", config.default_language) != config.default_language:
            continue
        key = getattr(node, "translation_key", None)
        if not key:
            continue
        translations = translation_index.get(key, {})
        for lang in config.language_codes():
            if lang == config.default_language or lang in translations:
                continue
            routes.add(locale_url(node.url, lang, config))
    return routes


def collect_i18n_home_routes(config: DocsI18nConfig) -> set[str]:
    """Locale home paths (``/es/``) for static export when i18n is enabled."""
    if not config.enabled:
        return set()
    return {locale_url("/", lang, config) for lang in config.language_codes() if lang != config.default_language}


def fallback_context(
    match: LocalizedNodeMatch,
    *,
    config: DocsI18nConfig,
) -> dict[str, Any]:
    """Template flags when showing default-language content under a locale URL."""
    if not match.fallback:
        return {
            "i18n_fallback": False,
            "i18n_fallback_active": False,
        }
    default = config.language(config.default_language)
    requested = config.language(match.requested_lang)
    requested_name = requested.name if requested else match.requested_lang
    content_name = default.name if default else match.node.lang
    return {
        "i18n_fallback": True,
        "i18n_fallback_active": True,
        "i18n_requested_language": match.requested_lang,
        "i18n_requested_language_name": requested_name,
        "i18n_content_language": match.node.lang,
        "i18n_content_language_name": content_name,
        "i18n_fallback_url": match.requested_url,
        "i18n_fallback_message": (
            f"This page is not yet available in {requested_name}. "
            f"Showing the {content_name} version."
        ),
    }


def build_translation_index(nodes: tuple[Any, ...]) -> dict[str, dict[str, str]]:
    """Map ``translation_key`` → ``{lang: url}``."""
    index: dict[str, dict[str, str]] = {}
    for node in nodes:
        key = getattr(node, "translation_key", None)
        if not key:
            continue
        lang = getattr(node, "lang", "en")
        index.setdefault(key, {})[lang] = node.url
    return index


def alternate_links(
    node,
    *,
    translation_index: dict[str, dict[str, str]],
    config: DocsI18nConfig,
    base_url: str = "",
) -> list[dict[str, str]]:
    """hreflang alternates for the current page."""
    if not config.enabled or node is None:
        return []
    key = getattr(node, "translation_key", None)
    if not key:
        return []
    urls = translation_index.get(key, {})
    links: list[dict[str, str]] = []
    for lang in config.sorted_languages():
        href = urls.get(lang.code)
        if not href:
            continue
        absolute = f"{base_url.rstrip('/')}{href}" if base_url else href
        links.append({"hreflang": lang.hreflang or lang.code, "href": absolute})
    return links


def locale_context(
    config: DocsI18nConfig,
    *,
    active_lang: str,
    node=None,
    translation_index: dict[str, dict[str, str]] | None = None,
    fallback_url: str | None = None,
) -> dict[str, Any]:
    """Template context for locale switchers and ``<html lang>``."""
    translation_index = translation_index or {}
    active = config.language(active_lang) or DocsLanguage(active_lang, active_lang)
    translation_key = getattr(node, "translation_key", None) if node is not None else None
    urls = translation_index.get(translation_key or "", {}) if translation_key else {}

    languages: list[dict[str, Any]] = []
    for lang in config.sorted_languages():
        href = urls.get(lang.code)
        if href is None and fallback_url and lang.code == active_lang:
            href = fallback_url
        if href is None and lang.code == config.default_language and node is not None:
            href = getattr(node, "url", None) if getattr(node, "lang", None) == lang.code else None
        languages.append(
            {
                "code": lang.code,
                "name": lang.name,
                "hreflang": lang.hreflang or lang.code,
                "direction": lang.direction,
                "active": lang.code == active_lang,
                "href": href,
                "available": href is not None,
            }
        )

    return {
        "doc_languages": languages,
        "active_language": active_lang,
        "active_language_name": active.name,
        "html_lang": active_lang,
        "html_dir": active.direction,
        "i18n_enabled": config.enabled,
        "alternate_links": alternate_links(
            node,
            translation_index=translation_index,
            config=config,
        ),
    }


def supported_app_locales(config: DocsI18nConfig) -> tuple[str, ...]:
    """Locales passed to Chirp ``AppConfig.i18n_supported_locales``."""
    return tuple(lang.code for lang in config.sorted_languages())
