"""Machine-readable liveness, readiness, freshness, and artifact contracts."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from furatena.catalog.deployment_manifest import read_deployment_manifest
from furatena.catalog.runtime import ServeMode


def process_health(*, now: float | None = None) -> dict[str, Any]:
    """Return process liveness without consulting sources or artifacts."""
    observed_at = _now(now)
    return {
        "schema_version": 1,
        "kind": "health",
        "ok": True,
        "status": "healthy",
        "http_status": 200,
        "observed_at": _iso(observed_at),
        "process": {"pid": os.getpid(), "responsive": True},
    }


def operational_status(docs: Any, *, now: float | None = None) -> dict[str, Any]:
    """Build distinct operational signals from one consistent observation."""
    observed_at = _now(now)
    source_health = docs.catalog.source_health()
    frozen_root = docs.serve.frozen_dir or docs.config.root / "frozen"
    public_root = docs.config.root / "public"
    source_mtime = max(
        (_tree_mtime(mount.content_root) for mount in docs.catalog.mounts),
        default=0.0,
    )
    freeze = _artifact(
        "freeze",
        frozen_root,
        frozen_root / "freeze.manifest.json",
        observed_at,
        upstream_mtime=source_mtime,
        required=docs.serve.mode in {ServeMode.PREVIEW, ServeMode.HYBRID},
    )
    export = _artifact(
        "export",
        public_root,
        public_root / "export.manifest.json",
        observed_at,
        upstream_mtime=float(freeze.get("generated_at_epoch") or 0.0),
        required=False,
    )
    artifact_signals = (freeze, export)
    artifact_status = (
        "degraded"
        if any(item["exists"] and not item["ok"] for item in artifact_signals)
        else "available"
        if any(item["exists"] for item in artifact_signals)
        else "missing"
    )
    artifacts = {
        "schema_version": 1,
        "kind": "artifacts",
        "ok": freeze["ok"] if freeze["required"] else True,
        "status": artifact_status,
        "http_status": 200,
        "observed_at": _iso(observed_at),
        "freeze": freeze,
        "export": export,
    }
    freshness = _freshness(source_health, freeze, export, observed_at)
    readiness = _readiness(docs, source_health, freeze, observed_at)
    report = {
        "schema_version": 1,
        "kind": "operational_status",
        "observed_at": _iso(observed_at),
        "serve_mode": docs.serve.mode.value,
        "health": process_health(now=observed_at),
        "readiness": readiness,
        "freshness": freshness,
        "artifacts": artifacts,
    }
    from furatena.catalog.observability import emit_operational_status

    emit_operational_status(docs.observability, report)
    return report


def _readiness(
    docs: Any,
    source_health: dict[str, Any],
    freeze: dict[str, Any],
    observed_at: float,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for mount in source_health.get("mounts", []):
        mount_id = str(mount.get("id") or "unknown")
        source_ok = (mount.get("source") or {}).get("status") == "ok"
        index = mount.get("index") or {}
        index_ok = bool(index.get("loaded")) and index.get("status") == "ok"
        checks.extend(
            [
                _check(
                    f"source:{mount_id}",
                    source_ok,
                    "Source sync is current.",
                    "Restore source access or repair the provider sync error.",
                ),
                _check(
                    f"index:{mount_id}",
                    index_ok,
                    "Catalog shard is loaded.",
                    "Rebuild the mount index or restore its frozen shard.",
                ),
            ]
        )
    if docs.serve.mode in {ServeMode.PREVIEW, ServeMode.HYBRID}:
        checks.append(
            _check(
                "freeze:required",
                bool(freeze["ok"]),
                "Required frozen catalog artifact is available and current.",
                "Run `fura freeze` and restart with the refreshed frozen directory.",
            )
        )
    ok = bool(checks) and all(check["ok"] for check in checks)
    return {
        "schema_version": 1,
        "kind": "readiness",
        "ok": ok,
        "status": "ready" if ok else "not_ready",
        "http_status": 200 if ok else 503,
        "observed_at": _iso(observed_at),
        "checks": checks,
        "remediation": [check["remediation"] for check in checks if not check["ok"]],
    }


def _freshness(
    source_health: dict[str, Any],
    freeze: dict[str, Any],
    export: dict[str, Any],
    observed_at: float,
) -> dict[str, Any]:
    signals = [
        {
            "id": f"source:{mount.get('id') or 'unknown'}",
            "status": "fresh" if mount.get("status") == "healthy" else "degraded",
            "detail": f"source/index state is {mount.get('status') or 'unknown'}",
        }
        for mount in source_health.get("mounts", [])
    ]
    for artifact in (freeze, export):
        signals.append(
            {
                "id": str(artifact["id"]),
                "status": artifact["freshness"],
                "detail": artifact["detail"],
            }
        )
    statuses = {signal["status"] for signal in signals}
    if "degraded" in statuses:
        status = "degraded"
    elif "stale" in statuses:
        status = "stale"
    elif statuses <= {"fresh", "not_available"}:
        status = "fresh"
    else:
        status = "unknown"
    return {
        "schema_version": 1,
        "kind": "freshness",
        "ok": status == "fresh",
        "status": status,
        "http_status": 200,
        "observed_at": _iso(observed_at),
        "signals": signals,
        "remediation": _freshness_remediation(signals),
    }


def _artifact(
    artifact_id: str,
    root: Path,
    manifest_path: Path,
    observed_at: float,
    *,
    upstream_mtime: float,
    required: bool,
) -> dict[str, Any]:
    manifest = read_deployment_manifest(manifest_path, target_hint=artifact_id)
    exists = manifest_path.is_file()
    generated_at = _mtime(manifest_path)
    stale = bool(exists and upstream_mtime > generated_at + 1.0)
    valid = manifest is not None
    ok = exists and valid and not stale
    if not exists:
        freshness = "not_available"
        detail = f"{artifact_id} manifest is missing"
    elif not valid:
        freshness = "degraded"
        detail = f"{artifact_id} manifest is invalid"
    elif stale:
        freshness = "stale"
        detail = f"{artifact_id} is older than its upstream input"
    else:
        freshness = "fresh"
        detail = f"{artifact_id} artifact is current"
    return {
        "id": artifact_id,
        "path": str(root),
        "manifest_path": str(manifest_path),
        "required": required,
        "exists": exists,
        "valid": valid,
        "ok": ok,
        "freshness": freshness,
        "detail": detail,
        "generated_at": _iso(generated_at) if generated_at else None,
        "generated_at_epoch": generated_at or None,
        "age_seconds": round(max(observed_at - generated_at, 0.0), 3)
        if generated_at
        else None,
        "page_count": manifest.page_count if manifest is not None else None,
        "artifact_count": len(manifest.artifacts) if manifest is not None else 0,
        "file_count": _file_count(root),
    }


def _check(check_id: str, ok: bool, detail: str, remediation: str) -> dict[str, Any]:
    return {
        "id": check_id,
        "ok": ok,
        "status": "pass" if ok else "fail",
        "detail": detail if ok else remediation,
        "remediation": remediation,
    }


def _freshness_remediation(signals: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    if any(item["id"].startswith("source:") and item["status"] != "fresh" for item in signals):
        actions.append("Repair source sync/index failures before serving the affected mount.")
    if any(item["id"] == "freeze" and item["status"] == "stale" for item in signals):
        actions.append("Run `fura freeze` to refresh the catalog artifact.")
    if any(item["id"] == "export" and item["status"] == "stale" for item in signals):
        actions.append("Run `fura export --fresh` to refresh deployable output.")
    return actions


def _now(value: float | None) -> float:
    normalized = time.time() if value is None else float(value)
    if not (0 <= normalized < float("inf")):
        raise ValueError("operational status timestamp must be finite and non-negative")
    return normalized


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _tree_mtime(root: Path) -> float:
    if not root.is_dir():
        return 0.0
    return max((_mtime(path) for path in root.rglob("*") if path.is_file()), default=0.0)


def _file_count(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob("*") if path.is_file())
