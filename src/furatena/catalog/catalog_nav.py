"""Catalog sidebar rail — auto-detected doc sections with optional docs.yaml overrides."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from furatena.catalog.config import CatalogNavConfig
    from furatena.catalog.models import DocNode


@dataclass(frozen=True, slots=True)
class CatalogSectionConfig:
    """One docs lane in the catalog icon rail."""

    id: str
    label: str | None = None
    icon: str | None = None
    mark: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogNavConfig:
    """Optional catalog navigation overrides from ``docs.yaml``."""

    sections: tuple[CatalogSectionConfig, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedCatalogSection:
    """Fully resolved section entry for nav_tree and catalog_rail_items."""

    id: str
    label: str
    icon: str | None = None
    mark: str | None = None
    sort_weight: float = 0.0


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
        sections.append(
            CatalogSectionConfig(
                id=section_id,
                label=str(label_raw).strip() if label_raw else None,
                icon=str(icon_raw).strip() if icon_raw else None,
                mark=str(mark_raw).strip() if mark_raw else None,
            )
        )
    return CatalogNavConfig(sections=tuple(sections))


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
        label = (
            index_node.title
            if index_node is not None
            else section_id.replace("-", " ").title()
        )
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
        for section_id in config_by_id:
            if section_id in discovered:
                ordered_ids.append(section_id)
                seen.add(section_id)

    remaining = sorted(
        (section_id for section_id in discovered if section_id not in seen),
        key=lambda sid: (discovered[sid].sort_weight, discovered[sid].label.lower()),
    )
    ordered_ids.extend(remaining)

    resolved: list[ResolvedCatalogSection] = []
    for index, section_id in enumerate(ordered_ids, start=1):
        base = discovered[section_id]
        override = config_by_id.get(section_id)
        label = override.label if override and override.label else base.label
        icon = (
            override.icon
            if override and override.icon
            else base.icon
        )
        mark = (
            override.mark
            if override and override.mark
            else base.mark or f"{index:02d}"
        )
        resolved.append(
            ResolvedCatalogSection(
                id=section_id,
                label=label,
                icon=icon,
                mark=mark,
                sort_weight=base.sort_weight,
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
