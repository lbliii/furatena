"""Inventory data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    """One resolvable object in an external or catalog inventory."""

    domain: str
    name: str
    objtype: str
    uri: str
    display_name: str
    priority: int = 0
    inventory_id: str = ""

    @property
    def key(self) -> str:
        return f"{self.domain}:{self.name}"


@dataclass(frozen=True, slots=True)
class InventorySpec:
    """Configuration for one loaded inventory."""

    id: str
    format: str
    url: str = ""
    cache: str = ""
    mount: str = ""
    domain: str = ""
    slug_prefix: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> InventorySpec:
        return cls(
            id=str(raw.get("id") or ""),
            format=str(raw.get("format") or ""),
            url=str(raw.get("url") or ""),
            cache=str(raw.get("cache") or ""),
            mount=str(raw.get("mount") or ""),
            domain=str(raw.get("domain") or ""),
            slug_prefix=str(raw.get("slug_prefix") or ""),
        )
