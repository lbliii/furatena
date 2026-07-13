"""Immutable, renderer-independent Content IR shards for release editions."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, is_dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from furatena.catalog.atomic_directory import AtomicDirectoryTransaction
from furatena.catalog.exceptions import ExportError
from furatena.catalog.export import catalog_graph

if TYPE_CHECKING:
    from furatena.catalog.record_types import EdgeRecord, NamespaceRecord
    from furatena.catalog.registry import CatalogRegistry, MountConfig
    from furatena.catalog.sources.types import GitEditionSnapshot

EDITION_SHARD_MANIFEST_VERSION = 1
EDITION_DCP_SCHEMA_VERSION = 3
EDITION_CONTENT_IR_SCHEMA_VERSION = 3
EDITION_ADAPTER_CONTRACT_VERSION = 1

_VOLATILE_KEYS = frozenset(
    {
        "discovered_at",
        "generated_at",
        "last_indexed_at",
        "observed_at",
        "reconciled_at",
        "updated_at",
    }
)


@dataclass(frozen=True, slots=True)
class EditionShardStatus:
    """Outcome for one release edition during a catalog freeze."""

    mount: str
    edition: str
    status: str
    input_fingerprint: str
    fingerprint: str
    source_ref: str
    resolved_ref: str
    page_count: int
    path: Path

    def public_record(self) -> dict[str, Any]:
        return {
            "mount": self.mount,
            "edition": self.edition,
            "status": self.status,
            "input_fingerprint": self.input_fingerprint,
            "fingerprint": self.fingerprint,
            "source_ref": self.source_ref,
            "resolved_ref": self.resolved_ref,
            "page_count": self.page_count,
        }


class _EditionCatalogView:
    """Apply registry access policy while serializing one edition DocCatalog."""

    def __init__(self, registry: CatalogRegistry, shard: Any, edition: str) -> None:
        self.registry = registry
        self.shard = shard
        self.active_channel = edition
        self.nodes = shard.nodes

    def doc_nodes(self) -> list[Any]:
        return self.shard.doc_nodes()

    def backlinks_for(self, node: Any) -> list[dict[str, str]]:
        return self.shard.backlinks_for(node)

    def graph_edges(self) -> list[EdgeRecord]:
        return self.shard.graph_edges()

    def namespaces(self) -> list[NamespaceRecord]:
        return self.shard.namespaces()

    def inventories_metadata(self) -> list[dict[str, Any]]:
        return self.registry.inventories_metadata()

    def mount_access_policy(self, mount_id: str) -> Any:
        return self.registry.mount_access_policy(mount_id)

    def node_access_policy(self, node: Any) -> Any:
        return self.registry.node_access_policy(node)


def freeze_edition_shards(
    registry: CatalogRegistry,
    out_dir: Path,
    *,
    dcp_schema_version: int = EDITION_DCP_SCHEMA_VERSION,
    content_ir_schema_version: int = EDITION_CONTENT_IR_SCHEMA_VERSION,
    adapter_contract_version: int = EDITION_ADAPTER_CONTRACT_VERSION,
) -> tuple[EditionShardStatus, ...]:
    """Freeze or verify every discovered immutable release edition."""
    outcomes: list[EditionShardStatus] = []
    for mount in registry.mounts:
        for snapshot in registry.discovered_editions_for(mount.id):
            if snapshot.id == "latest":
                continue
            outcomes.append(
                freeze_edition_shard(
                    registry,
                    mount,
                    snapshot,
                    out_dir,
                    dcp_schema_version=dcp_schema_version,
                    content_ir_schema_version=content_ir_schema_version,
                    adapter_contract_version=adapter_contract_version,
                )
            )
    return tuple(outcomes)


def freeze_edition_shard(
    registry: CatalogRegistry,
    mount: MountConfig,
    snapshot: GitEditionSnapshot,
    out_dir: Path,
    *,
    dcp_schema_version: int = EDITION_DCP_SCHEMA_VERSION,
    content_ir_schema_version: int = EDITION_CONTENT_IR_SCHEMA_VERSION,
    adapter_contract_version: int = EDITION_ADAPTER_CONTRACT_VERSION,
) -> EditionShardStatus:
    """Freeze one release snapshot, or reuse it after full integrity verification."""
    target = out_dir / "mounts" / mount.id / snapshot.id
    input_payload = _input_payload(
        registry,
        mount,
        snapshot,
        dcp_schema_version=dcp_schema_version,
        content_ir_schema_version=content_ir_schema_version,
        adapter_contract_version=adapter_contract_version,
    )
    input_fingerprint = _digest_json(input_payload)
    existing = _read_manifest(target)
    existing_ref = str(((existing or {}).get("source") or {}).get("resolved_ref") or "")
    if existing_ref and existing_ref != snapshot.resolved_ref:
        raise ExportError(
            f"release tag moved for {mount.id}:{snapshot.id}: recorded {existing_ref}, "
            f"resolved {snapshot.resolved_ref}; immutable edition shards cannot be overwritten",
            path=target,
            mount=mount.id,
            operation="freeze_edition_integrity",
        )
    verified = _verify_existing_shard(target, input_fingerprint=input_fingerprint)
    if verified is not None:
        return _status_from_manifest(target, verified, status="reused")

    transaction = AtomicDirectoryTransaction(target, operation="edition-freeze")
    staging = transaction.prepare()
    try:
        shutil.rmtree(staging)
        staging.mkdir(parents=True)
        manifest = _build_shard(
            registry,
            mount,
            snapshot,
            staging,
            input_payload=input_payload,
            input_fingerprint=input_fingerprint,
            dcp_schema_version=dcp_schema_version,
            content_ir_schema_version=content_ir_schema_version,
            adapter_contract_version=adapter_contract_version,
        )
        transaction.commit()
    finally:
        transaction.cleanup()
    return _status_from_manifest(target, manifest, status="frozen")


def _input_payload(
    registry: CatalogRegistry,
    mount: MountConfig,
    snapshot: GitEditionSnapshot,
    *,
    dcp_schema_version: int,
    content_ir_schema_version: int,
    adapter_contract_version: int,
) -> dict[str, Any]:
    git = mount.source.git
    autodoc_digest = None
    if mount.default and registry.autodoc_enabled and registry.autodoc_config is not None:
        autodoc_digest = _file_digest(registry.autodoc_config)
    return {
        "mount": mount.id,
        "edition": snapshot.id,
        "source": {
            "provider": mount.source.provider,
            "repo": git.repo if git is not None else None,
            "ref": snapshot.ref,
            "resolved_ref": snapshot.resolved_ref,
            "path": git.path if git is not None else "",
            "source_url": git.source_url if git is not None else None,
        },
        "semantic_config": {
            "extensions": sorted(mount.source.extensions),
            "index_files": sorted(mount.source.index_files),
            "format_map": dict(sorted(mount.source.format_map.items())),
            "default_format": mount.source.default_format,
            "access": mount.access.to_meta(),
            "autodoc": autodoc_digest,
            "identity": dict(sorted(registry.catalog_identity.items())),
            "i18n": _plain(registry.i18n_config),
        },
        "contracts": {
            "manifest": EDITION_SHARD_MANIFEST_VERSION,
            "dcp": dcp_schema_version,
            "content_ir": content_ir_schema_version,
            "adapter": adapter_contract_version,
        },
    }


def _build_shard(
    registry: CatalogRegistry,
    mount: MountConfig,
    snapshot: GitEditionSnapshot,
    target: Path,
    *,
    input_payload: dict[str, Any],
    input_fingerprint: str,
    dcp_schema_version: int,
    content_ir_schema_version: int,
    adapter_contract_version: int,
) -> dict[str, Any]:
    git = mount.source.git
    source = mount.source.with_git_sync_state(
        resolved_ref=snapshot.resolved_ref,
        source_url=git.source_url if git is not None else None,
    )
    edition_mount = replace(mount, content_root=snapshot.content_root, source=source)
    shard = registry._build_live_shard(
        edition_mount,
        cached_autodoc=None,
        edition=snapshot.id,
    )
    view = _EditionCatalogView(registry, shard, snapshot.id)
    graph = _immutable_value(catalog_graph(view, schema_version=dcp_schema_version))
    graph["mount"] = mount.id
    from furatena.catalog.dcp_validate import validate_catalog_payload

    dcp_errors = validate_catalog_payload(graph)
    if dcp_errors:
        preview = "; ".join(dcp_errors[:4])
        raise ExportError(
            f"edition shard {mount.id}:{snapshot.id} failed DCP validation: {preview}",
            path=target / "catalog.json",
            mount=mount.id,
            operation="freeze_edition_validate",
        )
    _write_json(target / "catalog.json", graph)

    nodes_by_id = {node.node_id: node for node in shard.nodes}
    for page in graph.get("pages", []):
        if not isinstance(page, dict):
            continue
        slug = str(page.get("slug") or "index")
        content_path = _safe_record_path(target / "content", slug)
        _write_json(content_path, page)
        node = nodes_by_id.get(str(page.get("node_id") or ""))
        if node is not None and node.ast_json:
            ast_path = _safe_record_path(target / "ast", slug)
            ast_path.parent.mkdir(parents=True, exist_ok=True)
            ast_path.write_text(node.ast_json.rstrip() + "\n", encoding="utf-8")

    artifacts = _artifact_digests(target)
    result_fingerprint = _digest_json(
        {"input_fingerprint": input_fingerprint, "artifacts": artifacts}
    )
    manifest = {
        "schema_version": EDITION_SHARD_MANIFEST_VERSION,
        "mount": mount.id,
        "edition": snapshot.id,
        "source": input_payload["source"],
        "contracts": {
            "dcp": dcp_schema_version,
            "content_ir": content_ir_schema_version,
            "adapter": adapter_contract_version,
        },
        "input_fingerprint": input_fingerprint,
        "fingerprint": result_fingerprint,
        "page_count": int(graph.get("page_count") or 0),
        "artifacts": artifacts,
    }
    _write_json(target / "fingerprint.json", manifest)
    return manifest


def _verify_existing_shard(target: Path, *, input_fingerprint: str) -> dict[str, Any] | None:
    manifest = _read_manifest(target)
    if manifest is None or manifest.get("input_fingerprint") != input_fingerprint:
        return None
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return None
    actual_paths = {
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file() and path.name != "fingerprint.json"
    }
    if actual_paths != set(artifacts):
        return None
    for relative, expected in artifacts.items():
        path = target / str(relative)
        if not path.is_file() or _file_digest(path) != str(expected):
            return None
    expected_result = _digest_json({"input_fingerprint": input_fingerprint, "artifacts": artifacts})
    if manifest.get("fingerprint") != expected_result:
        return None
    return manifest


def _status_from_manifest(
    target: Path,
    manifest: dict[str, Any],
    *,
    status: str,
) -> EditionShardStatus:
    source = manifest.get("source") or {}
    return EditionShardStatus(
        mount=str(manifest["mount"]),
        edition=str(manifest["edition"]),
        status=status,
        input_fingerprint=str(manifest["input_fingerprint"]),
        fingerprint=str(manifest["fingerprint"]),
        source_ref=str(source.get("ref") or ""),
        resolved_ref=str(source.get("resolved_ref") or ""),
        page_count=int(manifest.get("page_count") or 0),
        path=target,
    )


def _read_manifest(target: Path) -> dict[str, Any] | None:
    path = target / "fingerprint.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _artifact_digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _file_digest(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "fingerprint.json"
    }


def _safe_record_path(root: Path, slug: str) -> Path:
    target = (root / f"{slug}.json").resolve()
    resolved_root = root.resolve()
    if not target.is_relative_to(resolved_root):
        raise ExportError(
            f"unsafe edition shard slug: {slug!r}",
            path=target,
            operation="freeze_edition_path",
        )
    return target


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_digest(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_json(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _immutable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _immutable_value(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_immutable_value(item) for item in value]
    return value


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value
