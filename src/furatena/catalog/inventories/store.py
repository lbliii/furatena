"""Load and query reference inventories."""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from furatena.catalog.inventories.dcp import catalog_doc_inventory_entries, catalog_inventory_entries
from furatena.catalog.inventories.models import InventoryEntry, InventorySpec
from furatena.catalog.inventories.sphinx import parse_objects_inv_bytes

@dataclass
class InventoryStore:
    """In-memory inventory entries keyed by ``domain:name``."""

    entries: dict[str, InventoryEntry] = field(default_factory=dict)
    specs: tuple[InventorySpec, ...] = ()
    role_domains: dict[str, str] = field(default_factory=dict)

    def lookup(self, domain: str, name: str) -> InventoryEntry | None:
        return self.entries.get(f"{domain}:{name}")

    def lookup_role(self, role_name: str, target: str) -> InventoryEntry | None:
        inventory_id = self.role_domains.get(role_name)
        if inventory_id:
            for entry in self.entries.values():
                if entry.inventory_id != inventory_id:
                    continue
                if entry.name == target:
                    return entry
        return self.lookup(role_name, target)

    def metadata(self) -> list[dict[str, Any]]:
        grouped: dict[str, int] = {}
        for entry in self.entries.values():
            grouped[entry.inventory_id] = grouped.get(entry.inventory_id, 0) + 1
        return [
            {
                "id": spec.id,
                "format": spec.format,
                "entry_count": grouped.get(spec.id, 0),
                "mount": spec.mount or None,
                "domain": spec.domain or None,
            }
            for spec in self.specs
        ]


def load_inventories_config(path: Path | None) -> tuple[tuple[InventorySpec, ...], dict[str, str]]:
    if path is None or not path.is_file():
        return (), {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    role_domains = {str(k): str(v) for k, v in (raw.get("role_domains") or {}).items()}
    specs = tuple(
        InventorySpec.from_dict(item)
        for item in (raw.get("inventories") or [])
        if isinstance(item, dict) and item.get("id")
    )
    return specs, role_domains


def _fetch_inventory_url(url: str, cache_path: Path) -> bytes:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.is_file():
        return cache_path.read_bytes()
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        data = response.read()
    cache_path.write_bytes(data)
    return data


def build_inventory_store(
    specs: tuple[InventorySpec, ...],
    *,
    role_domains: dict[str, str] | None = None,
    catalog_nodes: list | None = None,
    app_root: Path | None = None,
) -> InventoryStore:
    entries: dict[str, InventoryEntry] = {}
    for spec in specs:
        loaded: tuple[InventoryEntry, ...] = ()
        if spec.format == "sphinx-inventory":
            cache = Path(spec.cache) if spec.cache else None
            if cache and not cache.is_absolute() and app_root is not None:
                cache = app_root / cache
            if spec.url and cache is not None:
                raw = _fetch_inventory_url(spec.url, cache)
            elif cache is not None and cache.is_file():
                raw = cache.read_bytes()
            elif spec.url:
                with urllib.request.urlopen(spec.url, timeout=30) as response:  # noqa: S310
                    raw = response.read()
            else:
                continue
            loaded = parse_objects_inv_bytes(raw, inventory_id=spec.id)
        elif spec.format == "dcp-catalog" and catalog_nodes is not None:
            loaded = catalog_inventory_entries(
                catalog_nodes,
                inventory_id=spec.id,
                mount=spec.mount,
                domain=spec.domain or "py",
                slug_prefix=spec.slug_prefix,
            )
            loaded = loaded + catalog_doc_inventory_entries(
                catalog_nodes,
                inventory_id=spec.id,
                mount=spec.mount,
                domain="doc",
            )
        for entry in loaded:
            entries[entry.key] = entry
    return InventoryStore(
        entries=entries,
        specs=specs,
        role_domains=dict(role_domains or {}),
    )
