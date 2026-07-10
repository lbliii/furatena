"""Shared paths and identifiers for public per-mount catalog shards."""

from __future__ import annotations

import re
from pathlib import Path

_SAFE_MOUNT_ID = re.compile(r"[A-Za-z0-9_.-]+\Z")


def is_safe_mount_id(mount_id: str) -> bool:
    """Return whether a mount id is safe to use as one URL/file-name segment."""
    value = str(mount_id or "")
    return value not in {".", ".."} and bool(_SAFE_MOUNT_ID.fullmatch(value))


def catalog_shard_url(mount_id: str) -> str | None:
    """Return the public URL for a mount shard, or ``None`` for an unsafe id."""
    if not is_safe_mount_id(mount_id):
        return None
    return f"/catalog/mounts/{mount_id}.json"


def catalog_shard_path(mount_id: str) -> Path | None:
    """Return the static-export path for a mount shard, or ``None`` for an unsafe id."""
    if not is_safe_mount_id(mount_id):
        return None
    return Path("catalog") / "mounts" / f"{mount_id}.json"
