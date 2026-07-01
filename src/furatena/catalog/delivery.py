"""Mount-aware rendering head and theme selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from furatena.catalog.config import DeliveryThemeConfig, DocsConfig
from furatena.catalog.docs_core import load_docs_core
from furatena.catalog.rendering_heads import RENDERING_HEADS, RenderingHeadSpec
from furatena.catalog.theme_pack import load_theme_pack


@dataclass(frozen=True, slots=True)
class ResolvedDelivery:
    """Resolved delivery profile for one catalog mount or node."""

    mount: str
    head: RenderingHeadSpec
    theme_id: str
    theme_use: str | None
    source: str

    def to_json(self) -> dict[str, Any]:
        return {
            "mount": self.mount,
            "head": self.head.id,
            "head_output": self.head.output,
            "theme": {
                "id": self.theme_id,
                "use": self.theme_use,
            },
            "source": self.source,
        }


def rendering_head_ids() -> frozenset[str]:
    """Known rendering head ids."""
    return frozenset(head.id for head in RENDERING_HEADS)


def rendering_head_by_id(head_id: str) -> RenderingHeadSpec | None:
    """Return a rendering head spec by id."""
    return next((head for head in RENDERING_HEADS if head.id == head_id), None)


def resolve_delivery_for_mount(docs: DocsConfig, mount_id: str) -> ResolvedDelivery:
    """Resolve delivery head and theme for a mount."""
    override = docs.delivery.mounts.get(mount_id)
    head_id = (override.head if override and override.head else docs.delivery.head).strip()
    head = rendering_head_by_id(head_id) or rendering_head_by_id("live-shell")
    if head is None:  # Defensive: the built-in registry always includes live-shell.
        raise LookupError("rendering head registry missing live-shell")
    theme = _resolve_delivery_theme(
        docs,
        override.theme if override is not None else None,
    )
    source = "mount" if override and (override.head or _theme_has_override(override.theme)) else "global"
    return ResolvedDelivery(
        mount=mount_id,
        head=head,
        theme_id=theme.id or docs.theme.id,
        theme_use=theme.use if theme.use is not None else docs.theme.use,
        source=source,
    )


def resolve_delivery_for_node(docs: DocsConfig, node: Any) -> ResolvedDelivery:
    """Resolve delivery head and theme for a catalog node."""
    return resolve_delivery_for_mount(docs, str(getattr(node, "mount", "") or ""))


def delivery_surface_json(docs: DocsConfig, catalog: Any) -> dict[str, Any]:
    """Export mount-aware delivery selections for machine consumers."""
    mounts = getattr(catalog, "mounts", ())
    return {
        "default": resolve_delivery_for_mount(docs, _default_mount_id(mounts)).to_json(),
        "mounts": [
            resolve_delivery_for_mount(docs, str(getattr(mount, "id", ""))).to_json()
            for mount in mounts
        ],
    }


def check_delivery_config(docs: DocsConfig, catalog: Any | None = None) -> tuple[list[str], list[str]]:
    """Validate delivery head/theme configuration."""
    errors: list[str] = []
    warnings: list[str] = []
    known_heads = rendering_head_ids()
    catalog_mounts = {str(getattr(mount, "id", "")) for mount in getattr(catalog, "mounts", ())}

    if docs.delivery.head not in known_heads:
        errors.append(f"delivery.head unknown rendering head: {docs.delivery.head!r}")
    errors.extend(_check_theme("delivery.theme", docs.delivery.theme))

    for mount_id, override in sorted(docs.delivery.mounts.items()):
        if catalog_mounts and mount_id not in catalog_mounts:
            warnings.append(f"delivery.mounts.{mount_id} does not match a configured mount")
        if override.head and override.head not in known_heads:
            errors.append(f"delivery.mounts.{mount_id}.head unknown rendering head: {override.head!r}")
        errors.extend(_check_theme(f"delivery.mounts.{mount_id}.theme", override.theme))

    return sorted(errors), sorted(warnings)


def _resolve_delivery_theme(
    docs: DocsConfig,
    override: DeliveryThemeConfig | None,
) -> DeliveryThemeConfig:
    global_theme = docs.delivery.theme
    return DeliveryThemeConfig(
        id=(override.id if override and override.id else global_theme.id) or docs.theme.id,
        use=(override.use if override and override.use is not None else global_theme.use)
        if (override and override.use is not None) or global_theme.use is not None
        else docs.theme.use,
    )


def _theme_has_override(theme: DeliveryThemeConfig) -> bool:
    return theme.id is not None or theme.use is not None


def _check_theme(path: str, theme: DeliveryThemeConfig) -> list[str]:
    errors: list[str] = []
    if theme.id and load_docs_core(theme.id) is None:
        errors.append(f"{path}.id unknown or docs-core missing: {theme.id!r}")
    if theme.use:
        try:
            load_theme_pack(theme.use)
        except (LookupError, TypeError, ValueError) as exc:
            errors.append(f"{path}.use invalid: {exc}")
    return errors


def _default_mount_id(mounts: Any) -> str:
    mounts_tuple = tuple(mounts)
    default = next((mount for mount in mounts_tuple if getattr(mount, "default", False)), None)
    if default is not None:
        return str(getattr(default, "id", ""))
    if mounts_tuple:
        return str(getattr(mounts_tuple[0], "id", ""))
    return ""
