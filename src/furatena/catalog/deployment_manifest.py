"""Shared deployment manifest schema for freeze, static, channel, and PDF outputs.

Schema version 3 defines one stable envelope:

``manifest_type``
    Always ``furatena.deployment``.
``target`` / ``mode``
    The artifact producer (``freeze``, ``static``, ``channels``, or ``pdf``)
    and its publication mode.
``page_count`` / ``artifacts``
    Public page total and normalized artifact metadata.
``fingerprints`` / ``sync``
    Producer fingerprints and incremental/source synchronization state.

Target-specific compatibility fields may accompany this envelope. Readers use
this module so future durable manifest backends do not leak into producers.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEPLOYMENT_MANIFEST_SCHEMA_VERSION = 3
DEPLOYMENT_MANIFEST_TYPE = "furatena.deployment"


@dataclass(frozen=True, slots=True)
class DeploymentArtifact:
    """Normalized metadata for one deployable artifact."""

    path: str
    bytes: int | None = None
    media_type: str | None = None
    fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path}
        if self.bytes is not None:
            payload["bytes"] = self.bytes
        if self.media_type:
            payload["media_type"] = self.media_type
        if self.fingerprint:
            payload["fingerprint"] = self.fingerprint
        return payload

    @classmethod
    def from_value(cls, value: Any) -> DeploymentArtifact:
        if isinstance(value, Mapping):
            size = value.get("bytes")
            return cls(
                path=str(value.get("path") or ""),
                bytes=int(size) if isinstance(size, int | float) else None,
                media_type=str(value.get("media_type") or "") or None,
                fingerprint=str(value.get("fingerprint") or "") or None,
            )
        return cls(path=str(value))


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    """Versioned deployment envelope shared by every packaging target."""

    target: str
    mode: str
    page_count: int
    artifacts: tuple[DeploymentArtifact, ...] = ()
    fingerprints: Mapping[str, Any] = field(default_factory=dict)
    sync: Mapping[str, Any] = field(default_factory=dict)
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": DEPLOYMENT_MANIFEST_SCHEMA_VERSION,
            "manifest_type": DEPLOYMENT_MANIFEST_TYPE,
            "target": self.target,
            "mode": self.mode,
            "page_count": self.page_count,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "fingerprints": _plain(self.fingerprints),
            "sync": _plain(self.sync),
        }
        for key, value in self.extensions.items():
            if key not in payload:
                payload[str(key)] = _plain(value)
        return payload

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        target_hint: str = "",
    ) -> DeploymentManifest:
        target = str(payload.get("target") or target_hint or _legacy_target(payload))
        raw_artifacts = payload.get("artifacts")
        if not isinstance(raw_artifacts, list | tuple):
            raw_artifacts = payload.get("paths") or ()
        artifacts = tuple(
            artifact
            for item in raw_artifacts
            if (artifact := DeploymentArtifact.from_value(item)).path
        )
        raw_fingerprints = payload.get("fingerprints")
        fingerprints = dict(raw_fingerprints) if isinstance(raw_fingerprints, Mapping) else {}
        raw_sync = payload.get("sync")
        sync = dict(raw_sync) if isinstance(raw_sync, Mapping) else _legacy_sync(payload, target)
        common = {
            "schema_version",
            "manifest_type",
            "target",
            "mode",
            "page_count",
            "artifacts",
            "fingerprints",
            "sync",
        }
        return cls(
            target=target,
            mode=str(payload.get("mode") or target),
            page_count=int(payload.get("page_count") or 0),
            artifacts=artifacts,
            fingerprints=fingerprints,
            sync=sync,
            extensions={str(key): value for key, value in payload.items() if key not in common},
        )

    @property
    def artifact_paths(self) -> tuple[str, ...]:
        return tuple(artifact.path for artifact in self.artifacts)

    @property
    def route_fingerprints(self) -> dict[str, str]:
        routes = self.fingerprints.get("routes")
        if isinstance(routes, Mapping):
            return {str(key): str(value) for key, value in routes.items()}
        if self.target == "static":
            return {
                str(key): str(value)
                for key, value in self.fingerprints.items()
                if isinstance(value, str)
            }
        return {}

    @property
    def renderer_fingerprint(self) -> str | None:
        value = self.fingerprints.get("renderer")
        if value:
            return str(value)
        renderer = self.extensions.get("renderer")
        if isinstance(renderer, Mapping) and renderer.get("fingerprint"):
            return str(renderer["fingerprint"])
        return None


def read_deployment_manifest(
    path: Path,
    *,
    target_hint: str = "",
) -> DeploymentManifest | None:
    """Read schema v3 or normalize a legacy deployment manifest."""
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, TypeError, ValueError:
        return None
    if not isinstance(payload, Mapping):
        return None
    return DeploymentManifest.from_dict(payload, target_hint=target_hint)


def write_deployment_manifest(path: Path, manifest: DeploymentManifest) -> None:
    """Write a deployment manifest with deterministic JSON formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest.to_dict(), indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def collect_deployment_artifacts(
    root: Path,
    paths: Iterable[str | Path],
    *,
    fingerprints: Mapping[str, str] | None = None,
) -> tuple[DeploymentArtifact, ...]:
    """Describe artifact paths without requiring every advertised file to exist."""
    records: list[DeploymentArtifact] = []
    seen: set[str] = set()
    for value in sorted({Path(path).as_posix() for path in paths}):
        path = value.lstrip("/")
        if not path or path in seen:
            continue
        seen.add(path)
        absolute = root / path
        size = absolute.stat().st_size if absolute.is_file() else None
        media_type = _media_type(path)
        records.append(
            DeploymentArtifact(
                path=path,
                bytes=size,
                media_type=media_type,
                fingerprint=(fingerprints or {}).get(value),
            )
        )
    return tuple(records)


def _legacy_target(payload: Mapping[str, Any]) -> str:
    if "channels" in payload:
        return "channels"
    if "dirty_mounts" in payload or "mount_status" in payload:
        return "freeze"
    if "base_path" in payload or "sidecars" in payload:
        return "static"
    if "nodes" in payload:
        return "pdf"
    return "deployment"


def _media_type(path: str) -> str:
    return {
        ".css": "text/css",
        ".html": "text/html",
        ".inv": "application/octet-stream",
        ".js": "text/javascript",
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".xml": "application/xml",
    }.get(Path(path).suffix.lower(), "application/octet-stream")


def _legacy_sync(payload: Mapping[str, Any], target: str) -> dict[str, Any]:
    if target == "freeze":
        return {
            "dirty_mounts": list(payload.get("dirty_mounts") or ()),
            "mounts": list(payload.get("mounts") or ()),
            "mount_status": list(payload.get("mount_status") or ()),
            "renderer_changed": bool(
                (payload.get("renderer") or {}).get("changed")
                if isinstance(payload.get("renderer"), Mapping)
                else False
            ),
        }
    if target == "static":
        return {
            "incremental": bool(payload.get("incremental")),
            "skipped_count": int(payload.get("skipped_count") or 0),
        }
    return {}


def _plain(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    return value
