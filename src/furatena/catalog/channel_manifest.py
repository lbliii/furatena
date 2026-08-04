"""Publication channel manifest for live, static, agent, and PDF outputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from furatena.catalog.access import AccessPermission, AccessSubject, accessible_nodes
from furatena.catalog.catalog_shards import catalog_shard_url
from furatena.catalog.deployment_manifest import DeploymentArtifact, DeploymentManifest
from furatena.catalog.identity import normalize_identity
from furatena.catalog.version_artifacts import public_versioned_mounts, versions_mount_url

_JSON_OUTPUTS = (
    ("catalog", "/catalog.json", "Catalog graph", "application/json"),
    ("search", "/search.json", "Search index", "application/json"),
    ("tools", "/tools.json", "Agent tool manifest", "application/json"),
    (
        "api-operations",
        "/catalog/api-operations.json",
        "API operation inventory",
        "application/json",
    ),
    ("meta", "/meta.json", "Metadata index", "application/json"),
    ("semantic", "/semantic.json", "Semantic search index", "application/json"),
    ("structure", "/structure.json", "Content structure index", "application/json"),
    ("surface", "/surface.json", "Surface manifest", "application/json"),
    ("channels", "/channels.json", "Publication channel manifest", "application/json"),
    ("versions", "/versions.json", "Edition discovery manifest", "application/json"),
    (
        "deployment-profiles",
        "/deployment-profiles.json",
        "Deployment profile manifest",
        "application/json",
    ),
)
_TEXT_OUTPUTS = (
    ("llms", "/llms.txt", "Compact LLM index", "text/plain"),
    ("llms-full", "/llms-full.txt", "Full LLM corpus", "text/plain"),
)


def channel_manifest(
    catalog: Any,
    *,
    config: Any | None = None,
    base_url: str = "",
    base_path: str = "",
    mode: str = "live",
    paths: list[str] | tuple[str, ...] | None = None,
    pdf_paths: list[str] | tuple[str, ...] | None = None,
    fingerprints: dict[str, str] | None = None,
    mount_status: dict[str, dict[str, Any]] | None = None,
    renderer_fingerprint: str = "",
) -> dict[str, Any]:
    """Return a stable manifest describing publication output channels."""
    base = _canonical_base(base_url=base_url, base_path=base_path)
    public_nodes = accessible_nodes(
        catalog,
        getattr(catalog, "nodes", ()),
        permission=AccessPermission.EXPORT,
    )
    all_nodes = list(getattr(catalog, "nodes", ()))
    artifact_paths = sorted(str(path) for path in (paths or ()))
    pdf_artifact_paths = sorted(str(path) for path in (pdf_paths or ()))
    route_fingerprints = dict(sorted((fingerprints or {}).items()))
    source_fingerprints = _source_fingerprints(catalog, mount_status=mount_status)
    fingerprints_payload = {
        "catalog": catalog_fingerprint(catalog),
        "source": _digest(source_fingerprints),
        "theme": _theme_fingerprint(config),
        "renderer": renderer_fingerprint,
        "routes": route_fingerprints,
    }
    identity = _identity(config)
    all_artifact_paths = sorted({*artifact_paths, *pdf_artifact_paths})
    return DeploymentManifest(
        target="channels",
        mode=mode,
        page_count=len(public_nodes),
        artifacts=tuple(DeploymentArtifact(path=path) for path in all_artifact_paths),
        fingerprints=fingerprints_payload,
        sync={"sources": source_fingerprints},
        extensions={
            "active_channel": getattr(catalog, "active_channel", "latest"),
            "site": _site(config),
            "identity": identity,
            "base_url": base,
            "protected_page_count": max(len(all_nodes) - len(public_nodes), 0),
            "sources": source_fingerprints,
            "channels": [
                _live_channel(base, enabled=mode == "live"),
                _static_channel(
                    base,
                    enabled=mode in {"static", "freeze"},
                    paths=artifact_paths,
                ),
                _agent_channel(
                    base,
                    catalog=catalog,
                    source_fingerprints=source_fingerprints,
                ),
                _pdf_channel(base, paths=pdf_artifact_paths),
            ],
        },
    ).to_dict()


def _live_channel(base: str, *, enabled: bool) -> dict[str, Any]:
    return {
        "id": "live",
        "label": "Live app",
        "kind": "html",
        "status": "available" if enabled else "exportable",
        "visibility": "public",
        "canonical_url": _url(base, "/"),
        "outputs": [_output("html", "/", "Documentation site", "text/html", base=base)],
    }


def _static_channel(base: str, *, enabled: bool, paths: list[str]) -> dict[str, Any]:
    channel = {
        "id": "static",
        "label": "Static site",
        "kind": "html",
        "status": "available" if enabled else "exportable",
        "visibility": "public",
        "canonical_url": _url(base, "/"),
        "outputs": [_output("html", "/", "Static documentation site", "text/html", base=base)],
    }
    if paths:
        channel["artifact_count"] = len(paths)
        channel["artifacts"] = paths
    return channel


def _agent_channel(
    base: str,
    *,
    catalog: Any,
    source_fingerprints: list[dict[str, Any]],
) -> dict[str, Any]:
    outputs = [
        *[
            _output(output_id, href, label, media_type, base=base, format="json")
            for output_id, href, label, media_type in _JSON_OUTPUTS
        ],
        *[
            _output(output_id, href, label, media_type, base=base, format="text")
            for output_id, href, label, media_type in _TEXT_OUTPUTS
        ],
        {
            "id": "mcp",
            "label": "MCP catalog server",
            "format": "mcp",
            "media_type": "application/json",
            "uri": "fura://catalog/nodes",
            "visibility": "public",
        },
    ]
    for source in source_fingerprints:
        mount_id = str(source.get("mount") or "")
        href = catalog_shard_url(mount_id)
        if href is None:
            continue
        can_access_mount = getattr(catalog, "can_access_mount", None)
        if callable(can_access_mount) and not can_access_mount(
            mount_id,
            AccessSubject.anonymous(),
            permission=AccessPermission.EXPORT,
        ):
            continue
        outputs.append(
            {
                **_output(
                    f"catalog-shard-{mount_id}",
                    href,
                    f"{source.get('label') or mount_id} catalog shard",
                    "application/json",
                    base=base,
                    format="json",
                ),
                "mount": mount_id,
                "fingerprint": str(source.get("fingerprint") or ""),
                "page_count": int(source.get("page_count") or 0),
            }
        )
    for mount in public_versioned_mounts(catalog):
        href = versions_mount_url(str(mount.id))
        if href is None:
            continue
        outputs.append(
            {
                **_output(
                    f"versions-mount-{mount.id}",
                    href,
                    f"{mount.label} Mike-compatible editions",
                    "application/json",
                    base=base,
                    format="json",
                ),
                "mount": mount.id,
            }
        )
    inventory_store = getattr(catalog, "inventory_store", None)
    if inventory_store is not None and inventory_store.specs:
        outputs.extend(
            (
                _output(
                    "inventories",
                    "/inventories.json",
                    "Reference inventory manifest",
                    "application/json",
                    base=base,
                    format="json",
                ),
                _output(
                    "objects-inventory",
                    "/objects.inv",
                    "Default Sphinx object inventory",
                    "application/octet-stream",
                    base=base,
                    format="inventory",
                ),
            )
        )
    return {
        "id": "agent",
        "label": "Agent exports",
        "kind": "machine",
        "status": "available",
        "visibility": "public",
        "canonical_url": _url(base, "/tools.json"),
        "outputs": outputs,
    }


def _pdf_channel(base: str, *, paths: list[str]) -> dict[str, Any]:
    status = "available" if paths else "planned"
    outputs = [
        {
            "id": Path(path).stem,
            "label": Path(path).stem.replace("-", " ").title(),
            "format": "pdf",
            "media_type": "application/pdf",
            "url": _url(base, f"/{path.lstrip('/')}"),
            "status": "available",
            "visibility": "public",
        }
        for path in paths
    ] or [
        {
            "id": "pdf",
            "label": "PDF bundle",
            "format": "pdf",
            "media_type": "application/pdf",
            "url": _url(base, "/pdf/"),
            "status": "planned",
            "visibility": "public",
        }
    ]
    return {
        "id": "pdf",
        "label": "PDF artifacts",
        "kind": "pdf",
        "status": status,
        "visibility": "public",
        "canonical_url": _url(base, f"/{paths[0].lstrip('/')}") if paths else _url(base, "/pdf/"),
        "outputs": outputs,
        **({} if paths else {"next_action": "Implement PDF rendering in the PDF export task."}),
    }


def _output(
    output_id: str,
    href: str,
    label: str,
    media_type: str,
    *,
    base: str,
    format: str = "html",
) -> dict[str, str]:
    return {
        "id": output_id,
        "label": label,
        "format": format,
        "media_type": media_type,
        "url": _url(base, href),
        "visibility": "public",
    }


def _source_fingerprints(
    catalog: Any, *, mount_status: dict[str, dict[str, Any]] | None
) -> list[dict[str, Any]]:
    mounts = list(getattr(catalog, "mounts", ()))
    nodes = list(getattr(catalog, "nodes", ()))
    records: list[dict[str, Any]] = []
    for mount in mounts:
        status = mount_status.get(mount.id, {}) if mount_status else {}
        mount_nodes = [node for node in nodes if getattr(node, "mount", "") == mount.id]
        fingerprint = status.get("content_fingerprint") or _digest(
            [
                {
                    "node_id": getattr(node, "node_id", ""),
                    "source_path": getattr(node, "source_path", ""),
                    "url": getattr(node, "url", ""),
                }
                for node in mount_nodes
            ]
        )
        editions_for = getattr(catalog, "discovered_editions_for", None)
        editions = editions_for(mount.id) if callable(editions_for) else ()
        records.append(
            {
                "mount": mount.id,
                "label": mount.label,
                "provider": status.get("provider")
                or getattr(getattr(mount, "source", None), "kind", "filesystem"),
                "fingerprint": fingerprint,
                "status": status.get("status") or "available",
                "page_count": len(mount_nodes),
                "editions": [
                    {
                        "id": edition.id,
                        "ref": edition.ref,
                        "resolved_ref": edition.resolved_ref,
                        "status": edition.status,
                        "prerelease": edition.prerelease,
                        "discovered_at": edition.discovered_at,
                    }
                    for edition in editions
                ],
            }
        )
    return records


def catalog_fingerprint(catalog: Any) -> str:
    """Return the stable fingerprint for a frozen catalog composition."""
    return _digest(
        {
            "active_channel": getattr(catalog, "active_channel", "latest"),
            "nodes": [
                {
                    "node_id": getattr(node, "node_id", ""),
                    "url": getattr(node, "url", ""),
                    "source_path": getattr(node, "source_path", ""),
                    "mount": getattr(node, "mount", ""),
                    "edition": getattr(node, "edition", ""),
                }
                for node in getattr(catalog, "nodes", ())
            ],
        }
    )


def _theme_fingerprint(config: Any | None) -> str:
    if config is None:
        return ""
    return _digest(_to_plain(getattr(config, "theme", None)))


def _identity(config: Any | None) -> dict[str, str]:
    raw = getattr(getattr(config, "identity", None), "to_meta", lambda: {})()
    return normalize_identity(raw)


def _site(config: Any | None) -> dict[str, str]:
    site = getattr(config, "site", None)
    return {
        "name": str(getattr(site, "name", "Furatena")),
        "description": str(getattr(site, "description", "")),
    }


def _canonical_base(*, base_url: str, base_path: str) -> str:
    origin = base_url.rstrip("/")
    prefix = _normalize_base_path(base_path)
    if origin:
        origin_path = urlsplit(origin).path.rstrip("/")
        if prefix and (origin_path == prefix or origin_path.endswith(f"{prefix}")):
            return origin
        return f"{origin}{prefix}"
    return prefix


def _url(base: str, path: str) -> str:
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{base}{normalized}" if base else normalized


def _normalize_base_path(value: str) -> str:
    text = str(value or "").strip()
    if text in {"", "/"}:
        return ""
    return "/" + text.strip("/")


def _digest(value: Any) -> str:
    payload = json.dumps(_to_plain(value), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {
            str(key): _to_plain(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_plain(item) for item in value]
    return value
