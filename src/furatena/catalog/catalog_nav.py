"""Catalog sidebar rail — auto-detected doc sections with optional docs.yaml overrides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from furatena.catalog.config import CatalogNavConfig
    from furatena.catalog.models import DocNode


@dataclass(frozen=True, slots=True)
class CatalogSectionConfig:
    """One physical section or logical journey in the catalog icon rail."""

    id: str
    label: str | None = None
    icon: str | None = None
    mark: str | None = None
    sections: tuple[str, ...] = ()
    pages: tuple[str, ...] = ()
    href: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogNavConfig:
    """Optional catalog navigation overrides from ``docs.yaml``."""

    sections: tuple[CatalogSectionConfig, ...] = ()
    append_unlisted: bool = True


@dataclass(frozen=True, slots=True)
class ResolvedCatalogSection:
    """Fully resolved section entry for nav_tree and catalog_rail_items."""

    id: str
    label: str
    icon: str | None = None
    mark: str | None = None
    sort_weight: float = 0.0
    section_ids: tuple[str, ...] = ()
    page_slugs: tuple[str, ...] = ()
    href: str | None = None


# Hints when front matter does not set ``icon`` / explicit config omits them.
_DEFAULT_SECTION_ICONS: dict[str, str] = {
    "get-started": "book-open",
    "concepts": "layers",
    "authoring": "pencil",
    "theming": "palette",
    "operations": "rocket",
    "about": "info",
    "build-apps": "hammer",
    "tutorials": "graduation-cap",
    "examples": "stack",
    "quality": "check-circle",
    "reference": "code",
    "api": "code",
    "releases": "rocket",
}

_DEFAULT_SECTION_MARKS: dict[str, str] = {
    "get-started": "01",
    "about": "02",
    "build-apps": "03",
    "tutorials": "04",
    "examples": "05",
    "quality": "06",
    "reference": "07",
    "api": "08",
    "releases": "09",
}


def parse_catalog_nav(raw: object) -> CatalogNavConfig:
    """Parse ``catalog:`` block from ``docs.yaml``."""
    if not isinstance(raw, dict):
        return CatalogNavConfig()
    sections_raw = raw.get("sections")
    if not isinstance(sections_raw, list):
        return CatalogNavConfig()
    sections: list[CatalogSectionConfig] = []
    for item in sections_raw:
        if isinstance(item, str):
            section_id = item.strip()
            if section_id:
                sections.append(CatalogSectionConfig(id=section_id))
            continue
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("id") or "").strip()
        if not section_id:
            continue
        label_raw = item.get("label")
        icon_raw = item.get("icon")
        mark_raw = item.get("mark")
        sections_raw = item.get("sections")
        pages_raw = item.get("pages")
        href_raw = item.get("href")
        sections.append(
            CatalogSectionConfig(
                id=section_id,
                label=str(label_raw).strip() if label_raw else None,
                icon=str(icon_raw).strip() if icon_raw else None,
                mark=str(mark_raw).strip() if mark_raw else None,
                sections=_string_tuple(sections_raw),
                pages=_string_tuple(pages_raw),
                href=str(href_raw).strip() if href_raw else None,
            )
        )
    return CatalogNavConfig(
        sections=tuple(sections),
        append_unlisted=raw.get("append_unlisted", True) is not False,
    )


def resolve_doc_sections(
    *,
    sections_map: dict[str, list[DocNode]],
    slug_prefix: str,
    nav_config: CatalogNavConfig | None,
    get_index_node,
) -> tuple[ResolvedCatalogSection, ...]:
    """Discover docs sections from the graph, with optional YAML order and metadata."""
    discovered: dict[str, ResolvedCatalogSection] = {}
    for section_id, pages in sections_map.items():
        if not section_id or section_id in {"docs", "root"}:
            continue
        index_slug = _section_index_slug(slug_prefix, section_id)
        index_node = get_index_node(index_slug)
        label = index_node.title if index_node is not None else section_id.replace("-", " ").title()
        sort_weight = (
            float(index_node.weight)
            if index_node is not None
            else min(float(page.weight) for page in pages)
        )
        icon = _node_icon(index_node) or _DEFAULT_SECTION_ICONS.get(section_id)
        discovered[section_id] = ResolvedCatalogSection(
            id=section_id,
            label=label,
            icon=icon,
            mark=_DEFAULT_SECTION_MARKS.get(section_id),
            sort_weight=sort_weight,
        )

    if not discovered:
        return ()

    config_by_id: dict[str, CatalogSectionConfig] = {}
    if nav_config is not None:
        for item in nav_config.sections:
            config_by_id[item.id] = item

    ordered_ids: list[str] = []
    seen: set[str] = set()

    if config_by_id:
        for item in config_by_id.values():
            member_ids = item.sections or (item.id,)
            available_ids = tuple(sid for sid in member_ids if sid in discovered)
            if not available_ids and not item.pages:
                continue
            ordered_ids.append(item.id)
            seen.update(available_ids)

    if nav_config is None or nav_config.append_unlisted:
        remaining = sorted(
            (section_id for section_id in discovered if section_id not in seen),
            key=lambda sid: (discovered[sid].sort_weight, discovered[sid].label.lower()),
        )
        ordered_ids.extend(remaining)

    resolved: list[ResolvedCatalogSection] = []
    for index, section_id in enumerate(ordered_ids, start=1):
        override = config_by_id.get(section_id)
        member_ids = (
            tuple(sid for sid in override.sections if sid in discovered)
            if override and override.sections
            else ((section_id,) if section_id in discovered else ())
        )
        base = discovered[member_ids[0]] if member_ids else None
        label = (
            override.label
            if override and override.label
            else base.label
            if base is not None
            else section_id.replace("-", " ").title()
        )
        icon = (
            override.icon
            if override and override.icon
            else base.icon
            if base is not None
            else _DEFAULT_SECTION_ICONS.get(section_id)
        )
        mark = (
            override.mark
            if override and override.mark
            else (base.mark if base is not None else None) or f"{index:02d}"
        )
        resolved.append(
            ResolvedCatalogSection(
                id=section_id,
                label=label,
                icon=icon,
                mark=mark,
                sort_weight=base.sort_weight if base is not None else float(index),
                section_ids=member_ids,
                page_slugs=override.pages if override else (),
                href=override.href if override else None,
            )
        )
    return tuple(resolved)


def section_index_slug(slug_prefix: str, section_id: str) -> str:
    """Catalog slug for a docs section ``_index.md``."""
    return _section_index_slug(slug_prefix, section_id)


def _section_index_slug(slug_prefix: str, section_id: str) -> str:
    prefix = slug_prefix.strip("/")
    slug = f"docs/{section_id}"
    if prefix:
        slug = f"{prefix}/{slug}"
    return slug.strip("/")


def _node_icon(node: DocNode | None) -> str | None:
    if node is None:
        return None
    meta = node.meta or {}
    icon = meta.get("icon")
    if icon:
        return str(icon).strip() or None
    return None


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(text for item in value if (text := str(item).strip()))
