"""Deterministic packaging and manifest-last publication of federation shards."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from furatena.catalog.exceptions import CatalogError
from furatena.catalog.federation_artifacts import (
    MAX_PRESENTATION_BYTES,
    MAX_PUBLISHED_INVENTORY_ENTRIES,
    MAX_PUBLISHED_OBJECT_BYTES,
    inventory_digest,
    published_artifact_fingerprint,
    published_manifest_digest,
    validate_inert_presentation_html,
    validate_published_shard_manifest,
)

_CHUNK_BYTES = 1024 * 1024
_MAX_FRAGMENT_BYTES = 4 * 1024 * 1024
_COMPRESSION_THRESHOLD = 1024


class ShardPublishError(CatalogError, RuntimeError):
    """Base failure for the remote shard publication boundary."""

    code = "fura.publish_shard"
    exit_code = 4


class ShardPublishAuthError(ShardPublishError, PermissionError):
    code = "fura.publish_shard.auth"


class ShardPublishPartialError(ShardPublishError):
    code = "fura.publish_shard.partial"


class ShardPublishConflictError(ShardPublishError):
    code = "fura.publish_shard.conflict"


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Metadata returned by a provider-neutral object backend."""

    size: int
    sha256: str | None = None


class ObjectBackend(Protocol):
    """Minimum hierarchical object-store contract used by the publisher."""

    def inspect(self, key: str) -> StoredObject | None: ...

    def put(
        self,
        key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        create_only: bool,
    ) -> None: ...

    def open(self, key: str) -> AbstractContextManager[BinaryIO]: ...


@dataclass(frozen=True, slots=True)
class PublishShardOptions:
    source_shard: Path
    staging_dir: Path
    public_base_url: str
    verification: dict[str, Any]
    repository_url: str | None = None
    lifecycle_status: str = "legacy"
    release_date: str | None = None
    end_of_life: str | None = None
    retention_days: int = 365
    pinned_by: tuple[str, ...] = ("hub:public-docs",)


@dataclass(frozen=True, slots=True)
class PublishShardResult:
    status: str
    fingerprint: str
    manifest: dict[str, Any]
    hub_entry: dict[str, Any]
    uploaded_objects: int
    reused_objects: int


def publish_shard(options: PublishShardOptions, backend: ObjectBackend) -> PublishShardResult:
    """Package a frozen release shard and commit its manifest only after verification."""
    artifact_root, manifest = build_published_shard(options)
    fingerprint = str(manifest["fingerprint"])
    prefix = f"sha256/{fingerprint}"
    manifest_path = artifact_root / "manifest.json"
    manifest_key = f"{prefix}/manifest.json"
    manifest_sha = _file_digest(manifest_path)
    manifest_size = manifest_path.stat().st_size

    existing_manifest = backend.inspect(manifest_key)
    if existing_manifest is not None:
        _verify_remote(backend, manifest_key, manifest_sha, manifest_size, trust_metadata=True)
        for item in manifest["inventory"]:
            _verify_remote(
                backend,
                f"{prefix}/{item['object_url']}",
                str(item["encoded_sha256"]),
                int(item["encoded_size"]),
                trust_metadata=True,
            )
        return PublishShardResult(
            status="up_to_date",
            fingerprint=fingerprint,
            manifest=manifest,
            hub_entry=hub_entry(manifest, manifest_size=manifest_size),
            uploaded_objects=0,
            reused_objects=len(manifest["inventory"]),
        )

    uploaded = 0
    reused = 0
    for item in manifest["inventory"]:
        relative = str(item["object_url"])
        key = f"{prefix}/{relative}"
        source = artifact_root / relative
        expected_sha = str(item["encoded_sha256"])
        expected_size = int(item["encoded_size"])
        remote = backend.inspect(key)
        if remote is None:
            try:
                backend.put(
                    key,
                    source,
                    media_type=str(item["media_type"]),
                    sha256=expected_sha,
                    create_only=True,
                )
                uploaded += 1
            except ShardPublishConflictError:
                reused += 1
        else:
            reused += 1
        _verify_remote(backend, key, expected_sha, expected_size)

    # A concurrent identical publisher wins safely; different bytes fail read-back.
    with suppress(ShardPublishConflictError):
        backend.put(
            manifest_key,
            manifest_path,
            media_type="application/json",
            sha256=manifest_sha,
            create_only=True,
        )
    _verify_remote(backend, manifest_key, manifest_sha, manifest_size)
    return PublishShardResult(
        status="published",
        fingerprint=fingerprint,
        manifest=manifest,
        hub_entry=hub_entry(manifest, manifest_size=manifest_size),
        uploaded_objects=uploaded,
        reused_objects=reused,
    )


def build_published_shard(options: PublishShardOptions) -> tuple[Path, dict[str, Any]]:
    """Build and fully validate one local v1 object set."""
    source = options.source_shard.resolve()
    fingerprint_path = source / "fingerprint.json"
    catalog_path = source / "catalog.json"
    if not fingerprint_path.is_file() or not catalog_path.is_file():
        raise ShardPublishError(
            "The selected frozen edition must contain fingerprint.json and catalog.json.",
            path=source,
            operation="publish_shard_package",
        )
    source_manifest = _read_json(fingerprint_path)
    catalog = _read_json(catalog_path)
    mount = str(source_manifest.get("mount") or "")
    edition = str(source_manifest.get("edition") or "")
    if not mount or not edition:
        raise ShardPublishError(
            "The frozen edition fingerprint must declare both mount and edition identity.",
            path=fingerprint_path,
            operation="publish_shard_package",
        )

    artifact_root = options.staging_dir.resolve() / "artifact"
    if artifact_root.exists():
        raise ShardPublishConflictError(
            "The dedicated shard staging directory already exists and will not be replaced.",
            path=artifact_root,
            operation="publish_shard_stage",
        )
    (artifact_root / "objects" / "sha256").mkdir(parents=True)
    objects: list[dict[str, Any]] = []
    objects.append(_write_object(artifact_root, "catalog/catalog.json", "catalog", catalog))

    pages = catalog.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ShardPublishError(
            "A published shard must contain at least one public catalog page.",
            path=catalog_path,
            operation="publish_shard_package",
        )
    page_records = sorted(
        (page for page in pages if isinstance(page, dict)),
        key=lambda value: str(value.get("node_id") or ""),
    )
    expected_presentations: set[Path] = set()
    for page in page_records:
        node_id = str(page.get("node_id") or "")
        digest = hashlib.sha256(node_id.encode()).hexdigest()
        slug = str(page.get("slug") or "index")
        objects.append(
            _write_object(
                artifact_root,
                f"fragments/nodes/{digest}.json",
                "fragment",
                page,
                node_id=node_id,
                max_bytes=_MAX_FRAGMENT_BYTES,
            )
        )
        presentation_path = _safe_presentation_path(source / "pages", slug)
        expected_presentations.add(presentation_path)
        presentation = _read_presentation(presentation_path)
        presentation_errors = validate_inert_presentation_html(presentation)
        if presentation_errors:
            raise ShardPublishError(
                f"The frozen presentation for {node_id} is not inert: "
                + "; ".join(presentation_errors[:4])
                + ".",
                path=presentation_path,
                operation="publish_shard_presentation",
            )
        objects.append(
            _write_bytes_object(
                artifact_root,
                f"presentations/nodes/{digest}.html",
                "presentation",
                presentation,
                extension="html",
                media_type="text/html; charset=utf-8",
                node_id=node_id,
                max_bytes=MAX_PRESENTATION_BYTES,
            )
        )
    actual_presentations = {
        path.resolve() for path in (source / "pages").rglob("*.html") if path.is_file()
    }
    if actual_presentations != expected_presentations:
        missing = sorted(str(path) for path in expected_presentations - actual_presentations)
        extra = sorted(str(path) for path in actual_presentations - expected_presentations)
        raise ShardPublishError(
            f"The frozen presentation node set differs from the public catalog "
            f"(missing={missing}, extra={extra}).",
            path=source / "pages",
            operation="publish_shard_presentation",
        )
    search = {
        "schema_version": 1,
        "mount": mount,
        "edition": edition,
        "documents": [
            {
                "node_id": str(page.get("node_id") or ""),
                "title": str(page.get("title") or ""),
                "text": _page_text(page),
            }
            for page in page_records
        ],
    }
    semantic = {
        "schema_version": 1,
        "mount": mount,
        "edition": edition,
        "records": [
            {
                "node_id": str(page.get("node_id") or ""),
                "sections": page.get("sections") if isinstance(page.get("sections"), list) else [],
            }
            for page in page_records
        ],
    }
    objects.extend(
        (
            _write_object(artifact_root, "indexes/search.json", "search", search),
            _write_object(artifact_root, "indexes/semantic.json", "semantic", semantic),
        )
    )
    objects.sort(key=lambda item: str(item["logical_path"]))
    if len(objects) > MAX_PUBLISHED_INVENTORY_ENTRIES:
        raise ShardPublishError(
            "The published shard exceeds the v1 maximum inventory entry limit.",
            path=source,
            operation="publish_shard_package",
        )

    source_info = source_manifest.get("source") or {}
    source_repository = str(source_info.get("repo") or "")
    if options.repository_url is not None and options.repository_url != source_repository:
        raise ShardPublishConflictError(
            "The asserted repository URL does not match frozen shard provenance.",
            path=fingerprint_path,
            operation="publish_shard_provenance",
        )
    contracts = source_manifest.get("contracts") or {}
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "manifest_type": "furatena-published-shard",
        "audience": "public",
        "identity": {"key": f"{mount}:{edition}", "mount": mount, "edition": edition},
        "source_shard_fingerprint": str(source_manifest.get("fingerprint") or ""),
        "fingerprint": "0" * 64,
        "artifact_base_url": "https://invalid.example/sha256/" + "0" * 64 + "/",
        "contracts": {
            "artifact": 1,
            "dcp": int(contracts.get("dcp") or 3),
            "content_ir": int(contracts.get("content_ir") or 3),
            "adapter": int(contracts.get("adapter") or 1),
            "reader_window": {
                "dcp_min": 2,
                "dcp_max": 3,
                "content_ir_min": 3,
                "content_ir_max": 3,
            },
        },
        "provenance": {
            "provider": "git",
            "repository": source_repository,
            "ref": str(source_info.get("ref") or edition),
            "resolved_ref": str(source_info.get("resolved_ref") or ""),
            "source_path": str(source_info.get("path") or "."),
        },
        "lifecycle": {
            "status": options.lifecycle_status,
            "release_date": options.release_date,
            "end_of_life": options.end_of_life,
        },
        "layout": {
            "style": "object-set-v1",
            "manifest_path": "manifest.json",
            "object_prefix": "objects/sha256/",
            "fragment_prefix": "fragments/",
            "presentation_prefix": "presentations/",
            "lookup": "node-id-map",
            "max_object_bytes": MAX_PUBLISHED_OBJECT_BYTES,
            "max_fragment_bytes": _MAX_FRAGMENT_BYTES,
            "max_presentation_bytes": MAX_PRESENTATION_BYTES,
            "max_inventory_entries": MAX_PUBLISHED_INVENTORY_ENTRIES,
        },
        "compression": {
            "default": "zstd",
            "threshold_bytes": _COMPRESSION_THRESHOLD,
            "allowed": ["identity", "zstd"],
        },
        "inventory": objects,
        "totals": {
            "object_count": len(objects),
            "fragment_count": len(page_records),
            "presentation_count": len(page_records),
            "uncompressed_bytes": sum(int(item["uncompressed_size"]) for item in objects),
            "encoded_bytes": sum(int(item["encoded_size"]) for item in objects),
        },
        "integrity": {
            "algorithm": "sha256",
            "inventory_sha256": inventory_digest(objects),
            "verification": "eager-manifest-lazy-objects",
        },
        "signatures": [],
        "attestations": [],
        "retention": {
            "class": "moving" if options.lifecycle_status == "current" else "release",
            "immutable_objects": True,
            "minimum_days": options.retention_days,
            "retain_until": None,
            "pinned_by": sorted(options.pinned_by),
        },
    }
    artifact_fingerprint = published_artifact_fingerprint(manifest)
    manifest["fingerprint"] = artifact_fingerprint
    manifest["artifact_base_url"] = (
        options.public_base_url.rstrip("/") + f"/sha256/{artifact_fingerprint}/"
    )
    manifest["signatures"] = _verification_records(
        options.verification.get("signatures"), artifact_fingerprint
    )
    manifest["attestations"] = _verification_records(
        options.verification.get("attestations"), artifact_fingerprint
    )
    _write_json(artifact_root / "manifest.json", manifest)
    errors = validate_published_shard_manifest(manifest, artifact_root=artifact_root)
    if errors:
        raise ShardPublishError(
            f"The published shard does not satisfy the v1 validation contract: {'; '.join(errors[:6])}.",
            path=artifact_root / "manifest.json",
            operation="publish_shard_validate",
        )
    return artifact_root, manifest


def hub_entry(manifest: dict[str, Any], *, manifest_size: int) -> dict[str, Any]:
    """Return the exact v1 hub ``shards[key]`` value for a published manifest."""
    return {
        "mount": manifest["identity"]["mount"],
        "edition": manifest["identity"]["edition"],
        "audience": manifest["audience"],
        "source_shard_fingerprint": manifest["source_shard_fingerprint"],
        "fingerprint": manifest["fingerprint"],
        "artifact_url": manifest["artifact_base_url"] + "manifest.json",
        "lifecycle": manifest["lifecycle"],
        "integrity": {
            "algorithm": "sha256",
            "manifest_sha256": published_manifest_digest(manifest),
            "manifest_bytes": manifest_size,
        },
        "contracts": {
            name: manifest["contracts"][name] for name in ("artifact", "dcp", "content_ir")
        },
        "provenance": manifest["provenance"],
        "retention": {
            "class": manifest["retention"]["class"],
            "minimum_days": manifest["retention"]["minimum_days"],
            "pinned": bool(manifest["retention"]["pinned_by"]),
        },
    }


def _write_object(
    root: Path,
    logical_path: str,
    role: str,
    payload: dict[str, Any],
    *,
    node_id: str | None = None,
    max_bytes: int = MAX_PUBLISHED_OBJECT_BYTES,
) -> dict[str, Any]:
    decoded = _canonical_json(payload)
    return _write_bytes_object(
        root,
        logical_path,
        role,
        decoded,
        extension="json",
        media_type="application/json",
        node_id=node_id,
        max_bytes=max_bytes,
    )


def _write_bytes_object(
    root: Path,
    logical_path: str,
    role: str,
    decoded: bytes,
    *,
    extension: str,
    media_type: str,
    node_id: str | None = None,
    max_bytes: int = MAX_PUBLISHED_OBJECT_BYTES,
) -> dict[str, Any]:
    if len(decoded) > max_bytes:
        raise ShardPublishError(
            f"{logical_path} exceeds the v1 object size limit",
            operation="publish_shard_package",
        )
    decoded_sha = hashlib.sha256(decoded).hexdigest()
    encoded = decoded
    encoding = "identity"
    suffix = f".{extension}"
    if len(decoded) >= _COMPRESSION_THRESHOLD:
        from compression import zstd

        encoded = zstd.compress(decoded)
        encoding = "zstd"
        suffix = f".{extension}.zst"
    encoded_sha = hashlib.sha256(encoded).hexdigest()
    object_url = f"objects/sha256/{encoded_sha}{suffix}"
    target = root / object_url
    if not target.exists():
        target.write_bytes(encoded)
    record = {
        "logical_path": logical_path,
        "role": role,
        "object_url": object_url,
        "media_type": media_type,
        "content_encoding": encoding,
        "uncompressed_size": len(decoded),
        "encoded_size": len(encoded),
        "sha256": decoded_sha,
        "encoded_sha256": encoded_sha,
        "required": True,
    }
    if node_id is not None:
        record["node_id"] = node_id
    return record


def _safe_presentation_path(root: Path, slug: str) -> Path:
    target = (root / f"{slug}.html").resolve()
    if not target.is_relative_to(root.resolve()):
        raise ShardPublishError(
            f"The catalog contains an unsafe presentation slug that escapes its root: {slug!r}.",
            path=target,
            operation="publish_shard_presentation",
        )
    return target


def _read_presentation(path: Path) -> bytes:
    if not path.is_file():
        raise ShardPublishError(
            "The public catalog node is missing its frozen presentation HTML file.",
            path=path,
            operation="publish_shard_presentation",
        )
    if path.stat().st_size > MAX_PRESENTATION_BYTES:
        raise ShardPublishError(
            "The frozen presentation exceeds the 16 MiB decoded presentation limit.",
            path=path,
            operation="publish_shard_presentation",
        )
    with path.open("rb") as stream:
        value = stream.read(MAX_PRESENTATION_BYTES + 1)
    if len(value) > MAX_PRESENTATION_BYTES:
        raise ShardPublishError(
            "The frozen presentation grew beyond the 16 MiB limit during its bounded read.",
            path=path,
            operation="publish_shard_presentation",
        )
    return value


def _verification_records(value: Any, fingerprint: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    records = []
    for item in value:
        if isinstance(item, dict):
            record = dict(item)
            supplied = record.get("subject_sha256")
            if supplied not in (None, fingerprint):
                raise ShardPublishConflictError(
                    "verification metadata subject does not match the packaged shard fingerprint",
                    operation="publish_shard_verification",
                )
            record["subject_sha256"] = fingerprint
            records.append(record)
    if records and "kind" in records[0]:
        records.sort(key=lambda item: (str(item.get("kind") or ""), str(item.get("url") or "")))
    else:
        records.sort(key=lambda item: str(item.get("url") or ""))
    return records


def _verify_remote(
    backend: ObjectBackend,
    key: str,
    expected_sha: str,
    expected_size: int,
    *,
    trust_metadata: bool = False,
) -> None:
    remote = backend.inspect(key)
    if remote is None:
        raise ShardPublishPartialError(
            f"The committed shard object is missing from remote storage: {key}.",
            operation="publish_shard_verify",
        )
    if remote.size != expected_size:
        raise ShardPublishConflictError(
            f"Remote object size differs from the immutable inventory for {key}.",
            operation="publish_shard_verify",
        )
    if remote.sha256 is not None and remote.sha256 != expected_sha:
        raise ShardPublishConflictError(
            f"Remote object digest metadata differs from the immutable inventory for {key}.",
            operation="publish_shard_verify",
        )
    if trust_metadata and remote.sha256 == expected_sha:
        return
    digest = hashlib.sha256()
    size = 0
    try:
        with backend.open(key) as stream:
            while chunk := stream.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > expected_size:
                    raise ShardPublishConflictError(
                        f"remote object differs from expected bytes: {key}",
                        operation="publish_shard_verify",
                    )
                digest.update(chunk)
    except ShardPublishError:
        raise
    except Exception as exc:
        raise ShardPublishPartialError(
            f"could not read back uploaded object {key}: {exc}",
            operation="publish_shard_verify",
        ) from exc
    if size != expected_size or digest.hexdigest() != expected_sha:
        raise ShardPublishConflictError(
            f"remote object differs from expected bytes: {key}",
            operation="publish_shard_verify",
        )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShardPublishError(
            f"cannot read required JSON: {exc}", path=path, operation="publish_shard_package"
        ) from exc
    if not isinstance(value, dict):
        raise ShardPublishError(
            "required JSON must contain an object", path=path, operation="publish_shard_package"
        )
    return value


def _page_text(page: dict[str, Any]) -> str:
    parts = [str(page.get("title") or "")]
    sections = page.get("sections")
    if isinstance(sections, list):
        parts.extend(
            str(section.get("text") or "") for section in sections if isinstance(section, dict)
        )
    return " ".join(part for part in parts if part).strip()


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _write_json(path: Path, payload: Any) -> None:
    path.write_bytes(_canonical_json(payload))


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
