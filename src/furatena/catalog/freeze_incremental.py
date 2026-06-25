"""Incremental freeze helpers — skip unchanged mount shards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from furatena.catalog.registry import CatalogRegistry


def mount_content_fingerprint(shard, content_root: Path) -> str:
    """Hash indexed source files for a mount shard."""
    digest = hashlib.sha256()
    for node in sorted(shard.nodes, key=lambda item: item.slug):
        rel = (node.source_path or "").strip()
        if not rel:
            continue
        path = content_root / rel
        if not path.is_file():
            continue
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def read_mount_fingerprint(mount_dir: Path) -> str | None:
    path = mount_dir / "freeze.fingerprint"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def write_mount_fingerprint(mount_dir: Path, fingerprint: str) -> None:
    mount_dir.mkdir(parents=True, exist_ok=True)
    (mount_dir / "freeze.fingerprint").write_text(fingerprint + "\n", encoding="utf-8")


def dirty_mount_ids(
    registry: CatalogRegistry,
    out_dir: Path,
    *,
    renderer_changed: bool,
) -> list[str]:
    """Return mount ids that need re-freezing."""
    if renderer_changed:
        return [mount.id for mount in registry.mounts]

    dirty: list[str] = []
    for mount in registry.mounts:
        mount_dir = out_dir / "mounts" / mount.id
        shard = registry._shards[mount.id]
        fingerprint = mount_content_fingerprint(shard, mount.content_root)
        if read_mount_fingerprint(mount_dir) != fingerprint:
            dirty.append(mount.id)
    return dirty


def write_freeze_manifest(out_dir: Path, *, dirty_mounts: list[str], total_pages: int) -> None:
    payload = {
        "schema_version": 1,
        "dirty_mounts": dirty_mounts,
        "page_count": total_pages,
        "mounts": [mount_dir.name for mount_dir in sorted((out_dir / "mounts").glob("*")) if mount_dir.is_dir()],
    }
    (out_dir / "freeze.manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
