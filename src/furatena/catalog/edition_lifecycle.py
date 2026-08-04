"""Canonical lifecycle metadata for documentation editions.

Lifecycle narrows already-authorized retrieval sets.  It never grants access and
therefore remains separate from the catalog visibility policy.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

EDITION_STATUSES = frozenset({"current", "legacy", "deprecated", "preview", "eol"})
DEFAULT_RETRIEVAL_STATUSES = frozenset({"current", "legacy", "deprecated"})
EDITION_RANK_MULTIPLIERS: Mapping[str, float] = MappingProxyType(
    {"current": 1.0, "legacy": 0.9, "deprecated": 0.5, "preview": 0.8, "eol": 0.25}
)


@dataclass(frozen=True, slots=True)
class EditionLifecycle:
    """Immutable lifecycle facts for one mount/edition namespace."""

    mount: str
    edition: str
    status: str
    release_date: str | None = None
    end_of_life: str | None = None
    banner: str | None = None

    def __post_init__(self) -> None:
        if self.status not in EDITION_STATUSES:
            raise ValueError(
                f"Unsupported edition lifecycle status {self.status!r}; use current, legacy, "
                "deprecated, preview, or eol."
            )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "release_date": self.release_date,
            "end_of_life": self.end_of_life,
            "banner": self.banner,
        }


def lifecycle_lookup(
    mounts: Iterable[Any],
    discovered: Mapping[str, tuple[Any, ...]],
) -> Mapping[tuple[str, str], EditionLifecycle]:
    """Build the registry's one immutable, constant-time lifecycle lookup."""
    records: dict[tuple[str, str], EditionLifecycle] = {}
    for mount in mounts:
        mount_id = str(mount.id)
        snapshots = discovered.get(mount_id, ())
        if not snapshots:
            records[(mount_id, "latest")] = EditionLifecycle(
                mount=mount_id,
                edition="latest",
                status="current",
            )
            continue
        for snapshot in snapshots:
            records[(mount_id, str(snapshot.id))] = EditionLifecycle(
                mount=mount_id,
                edition=str(snapshot.id),
                status=str(snapshot.status),
                release_date=getattr(snapshot, "release_date", None),
                end_of_life=getattr(snapshot, "end_of_life", None),
                banner=getattr(snapshot, "banner", None),
            )
    return MappingProxyType(records)


def lifecycle_statuses(
    *,
    status: str | None = None,
    include_preview: bool = False,
    include_eol: bool = False,
) -> frozenset[str]:
    """Resolve one explicit status or the deterministic broad-retrieval default."""
    normalized = (status or "").strip().lower()
    if normalized:
        if normalized not in EDITION_STATUSES:
            expected = ", ".join(sorted(EDITION_STATUSES))
            raise ValueError(f"Edition lifecycle status must be one of: {expected}.")
        return frozenset({normalized})
    selected = set(DEFAULT_RETRIEVAL_STATUSES)
    if include_preview:
        selected.add("preview")
    if include_eol:
        selected.add("eol")
    return frozenset(selected)


def lifecycle_allows(
    lifecycle: EditionLifecycle,
    *,
    status: str | None = None,
    include_preview: bool = False,
    include_eol: bool = False,
) -> bool:
    return lifecycle.status in lifecycle_statuses(
        status=status,
        include_preview=include_preview,
        include_eol=include_eol,
    )


def lifecycle_rank_multiplier(status: str) -> float:
    """Return the stable retrieval multiplier for one canonical status."""
    return EDITION_RANK_MULTIPLIERS.get(status, 1.0)
