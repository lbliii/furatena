"""Incremental freeze helpers — skip unchanged mount shards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

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


def mount_source_statuses(
    registry: CatalogRegistry,
    out_dir: Path,
    *,
    renderer_fingerprint: str,
    renderer_changed: bool,
    full_rebuild: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return per-mount source sync state for the next freeze."""
    statuses: dict[str, dict[str, Any]] = {}
    health_by_mount = {
        item["id"]: item
        for item in getattr(registry, "source_health", lambda: {"mounts": []})().get("mounts", [])
        if isinstance(item, dict) and item.get("id")
    }
    for mount in registry.mounts:
        mount_dir = out_dir / "mounts" / mount.id
        shard = registry._shards.get(mount.id)
        health = health_by_mount.get(mount.id, {})
        current = mount_content_fingerprint(shard, mount.content_root) if shard is not None else None
        previous = read_mount_fingerprint(mount_dir)
        drift_reasons: list[str] = []
        if full_rebuild:
            drift_reasons.append("full_rebuild")
        if renderer_changed:
            drift_reasons.append("renderer")
        if shard is None:
            drift_reasons.append("source_unavailable")
        elif previous is None:
            drift_reasons.append("missing_source_fingerprint")
        elif previous != current:
            drift_reasons.append("content")

        git = mount.source.git
        statuses[mount.id] = {
            "mount": mount.id,
            "provider": _mount_provider(shard),
            "source_repo": git.repo if git is not None else None,
            "source_ref": (git.resolved_ref or git.ref) if git is not None else None,
            "source_url": git.source_url if git is not None else None,
            "status": "failed" if shard is None else "pending" if drift_reasons else "skipped",
            "dirty": bool(drift_reasons) and shard is not None,
            "drift_reasons": drift_reasons,
            "content_fingerprint": current,
            "previous_content_fingerprint": previous,
            "renderer_fingerprint": renderer_fingerprint,
            "renderer_changed": renderer_changed,
            "page_count": len(shard.nodes) if shard is not None else 0,
            "source_root": mount.content_root.as_posix(),
            "url_prefix": mount.url_prefix,
            "health_status": health.get("status"),
            "source_error": (health.get("source") or {}).get("error") if health else None,
            "index_error": (health.get("index") or {}).get("error") if health else None,
        }
    return statuses


def _mount_provider(shard) -> str:
    for node in getattr(shard, "nodes", ()):
        provider = (getattr(node, "meta", {}) or {}).get("source_provider")
        if provider not in (None, ""):
            return str(provider)
    return "filesystem"


def set_mount_freeze_status(
    status: dict[str, Any],
    freeze_status: str,
    *,
    error: str | None = None,
) -> None:
    """Record the outcome for one mount without dropping pre-freeze drift context."""
    status["status"] = freeze_status
    if freeze_status in {"frozen", "skipped"}:
        status["dirty"] = False
    if error:
        status["error"] = error


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
        shard = registry._shards.get(mount.id)
        if shard is None:
            continue
        fingerprint = mount_content_fingerprint(shard, mount.content_root)
        if read_mount_fingerprint(mount_dir) != fingerprint:
            dirty.append(mount.id)
    return dirty


def write_freeze_manifest(
    out_dir: Path,
    *,
    dirty_mounts: list[str],
    total_pages: int,
    mount_statuses: dict[str, dict[str, Any]] | None = None,
    renderer_fingerprint: str | None = None,
    renderer_changed: bool = False,
) -> None:
    payload = {
        "schema_version": 2,
        "dirty_mounts": dirty_mounts,
        "page_count": total_pages,
        "mounts": [mount_dir.name for mount_dir in sorted((out_dir / "mounts").glob("*")) if mount_dir.is_dir()],
    }
    if renderer_fingerprint is not None:
        payload["renderer"] = {
            "fingerprint": renderer_fingerprint,
            "changed": renderer_changed,
        }
    if mount_statuses is not None:
        payload["mount_status"] = [
            _public_status(status)
            for _mount_id, status in sorted(mount_statuses.items())
        ]
    (out_dir / "freeze.manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in status.items() if key not in {"dirty", "source_root"}}
