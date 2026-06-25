"""Serve and export reference inventory (objects.inv) bytes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from furatena.catalog.inventories.sphinx import write_objects_inv_bytes

if TYPE_CHECKING:
    from furatena.catalog.inventories.store import InventoryStore
    from furatena.catalog.registry import CatalogRegistry


def inventory_entries_for_id(store: InventoryStore | None, inventory_id: str) -> tuple:
    if store is None:
        return ()
    return tuple(
        entry
        for entry in store.entries.values()
        if entry.inventory_id == inventory_id
    )


def inventory_bytes(
    catalog,
    inventory_id: str,
    *,
    frozen_dir: Path | None = None,
) -> bytes | None:
    """Return ``objects.inv`` bytes for *inventory_id*, or ``None`` when missing."""
    store = getattr(catalog, "inventory_store", None)
    entries = inventory_entries_for_id(store, inventory_id)
    if entries:
        active = getattr(catalog, "active_channel", "latest")
        return write_objects_inv_bytes(
            entries,
            project=inventory_id,
            version=active,
        )

    if frozen_dir is not None:
        frozen_path = frozen_dir / "inventories" / f"{inventory_id}.inv"
        if frozen_path.is_file():
            return frozen_path.read_bytes()
    return None


def inventories_json(catalog, *, base_url: str = "", frozen_dir: Path | None = None) -> dict:
    """Machine-readable index of reference inventories for external clients."""
    origin = base_url.rstrip("/")
    store = getattr(catalog, "inventory_store", None)
    specs = store.specs if store is not None else ()
    items: list[dict] = []
    for spec in specs:
        url_path = f"/inventories/{spec.id}/objects.inv"
        items.append(
            {
                "id": spec.id,
                "format": spec.format,
                "url": f"{origin}{url_path}" if origin else url_path,
                "mount": spec.mount or None,
                "domain": spec.domain or None,
            }
        )
    default_id = specs[0].id if specs else "local-catalog"
    objects_path = "/objects.inv"
    payload: dict = {
        "schema_version": 1,
        "default_inventory": default_id,
        "objects_inv_url": f"{origin}{objects_path}" if origin else objects_path,
        "inventories": items,
    }
    if frozen_dir is not None:
        frozen_inv = frozen_dir / "inventories"
        if frozen_inv.is_dir():
            payload["frozen_paths"] = sorted(
                str(path.relative_to(frozen_dir))
                for path in frozen_inv.glob("*.inv")
            )
    return payload
