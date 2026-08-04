"""Verified compose-by-reference registry for immutable remote federation shards."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import threading
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from furatena.catalog.atomic_directory import AtomicDirectoryTransaction
from furatena.catalog.exceptions import CatalogError
from furatena.catalog.federation_artifacts import (
    MAX_PUBLISHED_OBJECT_BYTES,
    published_manifest_digest,
    validate_federation_hub_manifest,
    validate_published_object_payload,
    validate_published_shard_manifest,
)
from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)

MAX_HUB_MANIFEST_BYTES = 4 * 1024 * 1024


class RemoteShardError(CatalogError, RuntimeError):
    """Base failure for verified remote-shard composition."""

    code = "fura.remote_shard"
    exit_code = 4


class RemoteShardFetchError(RemoteShardError):
    code = "fura.remote_shard.fetch"


class RemoteShardVerificationError(RemoteShardError):
    code = "fura.remote_shard.verify"


class RemoteShardUnavailableError(RemoteShardError):
    code = "fura.remote_shard.unavailable"


@dataclass(frozen=True, slots=True)
class RemoteHTTPResponse:
    """One transport response returned before strict reader verification."""

    body: bytes
    final_url: str
    content_length: int | None = None


class HTTPSBodyTransport(Protocol):
    """Injectable HTTPS byte transport used by the strict fetch boundary."""

    def fetch(self, url: str, *, max_bytes: int, timeout_seconds: float) -> RemoteHTTPResponse: ...


class RemoteCryptographicVerifier(Protocol):
    """Required cryptographic authority boundary for hub and shard signatures."""

    def verify_hub(
        self, manifest: Mapping[str, Any], *, fetcher: StrictHTTPSFetcher
    ) -> Mapping[str, Any]: ...

    def verify_shard(
        self, manifest: Mapping[str, Any], *, fetcher: StrictHTTPSFetcher
    ) -> Mapping[str, Any]: ...


class StrictHTTPSFetcher:
    """Fetch bounded bytes from one credential-free immutable HTTPS origin."""

    def __init__(
        self,
        origin_url: str,
        *,
        transport: HTTPSBodyTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._origin = _origin(origin_url)
        self._timeout_seconds = float(timeout_seconds)
        if self._timeout_seconds <= 0:
            raise ValueError("Remote shard fetch timeout must be a positive number.")
        self._transport = transport or _UrllibHTTPSBodyTransport(self._origin)

    @property
    def origin(self) -> tuple[str, str, int]:
        return self._origin

    def validate_url(self, url: str) -> str:
        _validate_https_url(url, origin=self._origin)
        return url

    def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> bytes:
        self.validate_url(url)
        if max_bytes <= 0 or max_bytes > MAX_PUBLISHED_OBJECT_BYTES:
            raise RemoteShardFetchError(
                f"Remote fetch bound must be between 1 and {MAX_PUBLISHED_OBJECT_BYTES} bytes.",
                operation="remote_shard_fetch",
            )
        if expected_size is not None and (expected_size < 0 or expected_size > max_bytes):
            raise RemoteShardFetchError(
                "Remote object expected size exceeds its reader bound.",
                operation="remote_shard_fetch",
            )
        try:
            response = self._transport.fetch(
                url,
                max_bytes=max_bytes,
                timeout_seconds=self._timeout_seconds,
            )
        except RemoteShardError:
            raise
        except Exception as exc:
            raise RemoteShardFetchError(
                f"Remote HTTPS fetch failed for {url}: {exc}.",
                operation="remote_shard_fetch",
            ) from exc
        self.validate_url(response.final_url)
        if response.content_length is not None and response.content_length < 0:
            raise RemoteShardFetchError(
                "The remote object declares an invalid negative Content-Length header.",
                operation="remote_shard_fetch",
            )
        if response.content_length is not None and response.content_length > max_bytes:
            raise RemoteShardFetchError(
                f"Remote object declares {response.content_length} bytes above the {max_bytes} byte bound.",
                operation="remote_shard_fetch",
            )
        value = bytes(response.body)
        if len(value) > max_bytes:
            raise RemoteShardFetchError(
                f"Remote object exceeded the {max_bytes} byte bound during read.",
                operation="remote_shard_fetch",
            )
        if expected_size is not None and len(value) != expected_size:
            raise RemoteShardFetchError(
                f"Remote object size differs from the immutable inventory for {url}.",
                operation="remote_shard_fetch",
            )
        if expected_sha256 is not None and _digest_bytes(value) != expected_sha256:
            raise RemoteShardFetchError(
                f"Remote object digest differs from the immutable inventory for {url}.",
                operation="remote_shard_fetch",
            )
        return value


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: tuple[str, str, int]) -> None:
        self._origin = origin

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_https_url(newurl, origin=self._origin)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _UrllibHTTPSBodyTransport:
    def __init__(self, origin: tuple[str, str, int]) -> None:
        self._origin = origin
        self._opener = urllib.request.build_opener(_SameOriginRedirectHandler(origin))

    def fetch(self, url: str, *, max_bytes: int, timeout_seconds: float) -> RemoteHTTPResponse:
        request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with self._opener.open(request, timeout=timeout_seconds) as response:
            final_url = str(response.geturl())
            _validate_https_url(final_url, origin=self._origin)
            raw_length = response.headers.get("Content-Length")
            content_length = int(raw_length) if raw_length is not None else None
            if content_length is not None and content_length > max_bytes:
                return RemoteHTTPResponse(b"", final_url, content_length)
            body = response.read(max_bytes + 1)
        return RemoteHTTPResponse(body, final_url, content_length)


@dataclass(frozen=True, slots=True)
class RemoteObjectRef:
    logical_path: str
    role: str
    object_url: str
    content_encoding: str
    uncompressed_size: int
    encoded_size: int
    sha256: str
    encoded_sha256: str
    node_id: str | None


@dataclass(frozen=True, slots=True)
class RemoteShardGeneration:
    identity: str
    mount: str
    edition: str
    fingerprint: str
    artifact_base_url: str
    manifest: Mapping[str, Any]
    catalog: Mapping[str, Any]
    public_node_ids: frozenset[str]
    fragments: Mapping[str, RemoteObjectRef]
    presentations: Mapping[str, RemoteObjectRef]
    semantic: RemoteObjectRef


@dataclass(frozen=True, slots=True)
class RemoteMountGeneration:
    mount: str
    generation_id: str
    hub_payload_sha256: str
    latest: str
    stable: str
    editions: tuple[str, ...]
    shards: Mapping[str, RemoteShardGeneration]
    activated_at: str
    rollback_pin: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteNodePayload:
    node_id: str
    fragment: Mapping[str, Any]
    presentation: bytes
    shard_fingerprint: str


@dataclass(frozen=True, slots=True)
class RemoteMountRefresh:
    mount: str
    status: str
    generation_id: str | None
    retained_last_known_good: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RemoteRefreshReport:
    hub_payload_sha256: str
    mounts: tuple[RemoteMountRefresh, ...]


class RemoteShardMountRegistry:
    """Immutable, atomically swapped registry of verified remote mount generations."""

    def __init__(
        self,
        *,
        hub_url: str,
        state_root: Path,
        fetcher: StrictHTTPSFetcher,
        verifier: RemoteCryptographicVerifier,
        max_hub_bytes: int = MAX_HUB_MANIFEST_BYTES,
    ) -> None:
        self.hub_url = fetcher.validate_url(hub_url)
        self.state_root = state_root.expanduser().resolve()
        self.fetcher = fetcher
        self.verifier = verifier
        if max_hub_bytes <= 0 or max_hub_bytes > MAX_HUB_MANIFEST_BYTES:
            raise ValueError(
                f"Remote hub bound must be between 1 and {MAX_HUB_MANIFEST_BYTES} bytes."
            )
        self.max_hub_bytes = max_hub_bytes
        self._state_lock = threading.RLock()
        self._lock_table_lock = threading.Lock()
        self._mount_locks: dict[str, threading.Lock] = {}
        self._mounts: Mapping[str, RemoteMountGeneration] = MappingProxyType({})
        self._startup_errors: list[str] = []
        self._load_last_known_good()

    def mounts(self) -> tuple[str, ...]:
        with self._state_lock:
            return tuple(sorted(self._mounts))

    @property
    def startup_errors(self) -> tuple[str, ...]:
        return tuple(self._startup_errors)

    def generation(self, mount: str) -> RemoteMountGeneration:
        with self._state_lock:
            generation = self._mounts.get(mount)
        if generation is None:
            raise RemoteShardUnavailableError(
                f"Remote shard mount {mount!r} has no verified generation.",
                mount=mount,
                operation="remote_shard_read",
            )
        return generation

    def shard(self, mount: str, edition: str = "latest") -> RemoteShardGeneration:
        """Return one immutable verified shard generation by edition or channel alias."""
        return self._resolve_shard(self.generation(mount), edition)

    def refresh(self) -> RemoteRefreshReport:
        hub_bytes = self.fetcher.fetch(self.hub_url, max_bytes=self.max_hub_bytes)
        hub = _json_object(hub_bytes, label="remote hub manifest")
        errors = validate_federation_hub_manifest(hub)
        if len(hub.get("shards", {})) > int(hub.get("discovery", {}).get("max_shards") or 0):
            errors.append("shards: exceeds discovery.max_shards")
        if errors:
            raise RemoteShardVerificationError(
                f"The remote hub manifest failed contract validation: {'; '.join(errors[:6])}.",
                operation="remote_hub_validate",
            )
        hub_verification = self._verify_hub(hub)
        hub_digest = str(hub["integrity"]["payload_sha256"])
        outcomes: list[RemoteMountRefresh] = []
        for mount in sorted(hub["channels"]):
            outcomes.append(
                self._refresh_mount(
                    mount,
                    hub=hub,
                    hub_verification=hub_verification,
                )
            )
        advertised = set(hub["channels"])
        for mount in sorted(set(self.mounts()) - advertised):
            current = self._current(mount)
            if current is not None and current.rollback_pin is not None:
                outcomes.append(RemoteMountRefresh(mount, "pinned", current.generation_id, True))
            else:
                self._deactivate_mount(mount)
                outcomes.append(RemoteMountRefresh(mount, "removed", None, False))
        outcomes.sort(key=lambda item: item.mount)
        return RemoteRefreshReport(hub_digest, tuple(outcomes))

    def fetch_node(self, mount: str, edition: str, node_id: str) -> RemoteNodePayload:
        generation = self.generation(mount)
        shard = self._resolve_shard(generation, edition)
        fragment_ref = shard.fragments.get(node_id)
        presentation_ref = shard.presentations.get(node_id)
        if fragment_ref is None or presentation_ref is None:
            raise RemoteShardUnavailableError(
                f"Remote node {node_id!r} is not public in {shard.identity}.",
                mount=mount,
                operation="remote_shard_read",
            )
        fragment_bytes = self._fetch_object(shard, fragment_ref)
        presentation = self._fetch_object(shard, presentation_ref)
        fragment = _json_object(fragment_bytes, label=f"fragment {node_id}")
        return RemoteNodePayload(
            node_id=node_id,
            fragment=_freeze_json(fragment),
            presentation=presentation,
            shard_fingerprint=shard.fingerprint,
        )

    def fetch_presentation(self, mount: str, edition: str, node_id: str) -> bytes:
        """Fetch and verify one inert presentation without fetching semantic content."""
        generation = self.generation(mount)
        shard = self._resolve_shard(generation, edition)
        presentation_ref = shard.presentations.get(node_id)
        if presentation_ref is None:
            raise RemoteShardUnavailableError(
                f"Remote node {node_id!r} is not public in {shard.identity}.",
                mount=mount,
                operation="remote_shard_read",
            )
        return self._fetch_object(shard, presentation_ref)

    def fetch_semantic_index(self, mount: str, edition: str) -> Mapping[str, Any]:
        generation = self.generation(mount)
        shard = self._resolve_shard(generation, edition)
        decoded = self._fetch_object(shard, shard.semantic)
        value = _json_object(decoded, label=f"semantic index {shard.identity}")
        semantic_ids = {
            str(record.get("node_id"))
            for record in value.get("records", [])
            if isinstance(record, dict) and record.get("node_id")
        }
        if semantic_ids != shard.public_node_ids:
            raise RemoteShardVerificationError(
                f"Remote semantic index node set differs from public catalog for {shard.identity}.",
                mount=mount,
                operation="remote_shard_object_validate",
            )
        return _freeze_json(value)

    def rollback(self, mount: str, fingerprint: str) -> RemoteMountGeneration:
        lock = self._lock_for(mount)
        with lock, self._lease(mount):
            candidates: list[tuple[str, Path]] = []
            generations_root = self._mount_root(mount) / "generations"
            for path in generations_root.iterdir() if generations_root.is_dir() else ():
                if not path.is_dir():
                    continue
                try:
                    receipt = _read_json_file(path / "receipt.json")
                except RemoteShardError:
                    continue
                if fingerprint in {
                    str(item.get("fingerprint") or "")
                    for item in receipt.get("shards", {}).values()
                    if isinstance(item, dict)
                }:
                    candidates.append((str(receipt.get("activated_at") or ""), path))
            if not candidates:
                raise RemoteShardUnavailableError(
                    f"No verified generation for mount {mount!r} contains fingerprint {fingerprint}.",
                    mount=mount,
                    operation="remote_shard_rollback",
                )
            generation_path = max(candidates)[1]
            generation = self._read_generation(mount, generation_path.name)
            generation = RemoteMountGeneration(
                mount=generation.mount,
                generation_id=generation.generation_id,
                hub_payload_sha256=generation.hub_payload_sha256,
                latest=generation.latest,
                stable=generation.stable,
                editions=generation.editions,
                shards=generation.shards,
                activated_at=generation.activated_at,
                rollback_pin=fingerprint,
            )
            self._promote_selection(generation)
            self._install_generation(generation)
            return generation

    def unpin(self, mount: str) -> RemoteMountGeneration:
        """Clear a rollback pin while keeping its verified generation active."""
        lock = self._lock_for(mount)
        with lock, self._lease(mount):
            current = self.generation(mount)
            generation = RemoteMountGeneration(
                mount=current.mount,
                generation_id=current.generation_id,
                hub_payload_sha256=current.hub_payload_sha256,
                latest=current.latest,
                stable=current.stable,
                editions=current.editions,
                shards=current.shards,
                activated_at=current.activated_at,
            )
            self._promote_selection(generation)
            self._install_generation(generation)
            return generation

    def _refresh_mount(
        self,
        mount: str,
        *,
        hub: dict[str, Any],
        hub_verification: dict[str, Any],
    ) -> RemoteMountRefresh:
        lock = self._lock_for(mount)
        with lock, self._lease(mount):
            pinned = self._current(mount)
            if pinned is not None and pinned.rollback_pin is not None:
                return RemoteMountRefresh(
                    mount,
                    "pinned",
                    pinned.generation_id,
                    True,
                )
            try:
                generation = self._build_mount_generation(
                    mount,
                    hub=hub,
                    hub_verification=hub_verification,
                )
                previous = self._current(mount)
                status = (
                    "reused"
                    if previous and previous.generation_id == generation.generation_id
                    else "activated"
                )
                self._promote_selection(generation)
                self._install_generation(generation)
                return RemoteMountRefresh(mount, status, generation.generation_id, False)
            except RemoteShardError as exc:
                previous = self._current(mount)
                return RemoteMountRefresh(
                    mount,
                    "failed",
                    previous.generation_id if previous else None,
                    previous is not None,
                    str(exc),
                )
            except OSError as exc:
                previous = self._current(mount)
                return RemoteMountRefresh(
                    mount,
                    "failed",
                    previous.generation_id if previous else None,
                    previous is not None,
                    f"Remote mount state update failed: {exc}.",
                )

    def _build_mount_generation(
        self,
        mount: str,
        *,
        hub: dict[str, Any],
        hub_verification: dict[str, Any],
    ) -> RemoteMountGeneration:
        channel = hub["channels"][mount]
        identities = tuple(str(value) for value in channel["editions"])
        manifests: dict[str, dict[str, Any]] = {}
        catalogs: dict[str, dict[str, Any]] = {}
        shard_verifications: dict[str, dict[str, Any]] = {}
        for identity in identities:
            entry = hub["shards"][identity]
            manifest_bytes = self.fetcher.fetch(
                str(entry["artifact_url"]),
                max_bytes=int(entry["integrity"]["manifest_bytes"]),
                expected_size=int(entry["integrity"]["manifest_bytes"]),
                expected_sha256=str(entry["integrity"]["manifest_sha256"]),
            )
            manifest = _json_object(manifest_bytes, label=f"shard manifest {identity}")
            errors = validate_published_shard_manifest(manifest)
            errors.extend(
                validate_federation_hub_manifest(hub, published_manifests={identity: manifest})
            )
            if errors:
                raise RemoteShardVerificationError(
                    f"Remote shard {identity} failed manifest validation: {'; '.join(errors[:6])}.",
                    mount=mount,
                    operation="remote_shard_manifest_validate",
                )
            self._validate_shard_urls(manifest)
            shard_verifications[identity] = self._verify_shard(manifest, mount=mount)
            catalog_item = next(item for item in manifest["inventory"] if item["role"] == "catalog")
            catalog_bytes = self._fetch_decoded(manifest, catalog_item)
            role_errors = validate_published_object_payload(manifest, catalog_item, catalog_bytes)
            if role_errors:
                raise RemoteShardVerificationError(
                    f"Remote shard {identity} catalog failed validation: {'; '.join(role_errors[:6])}.",
                    mount=mount,
                    operation="remote_shard_catalog_validate",
                )
            catalogs[identity] = _json_object(catalog_bytes, label=f"catalog {identity}")
            manifests[identity] = manifest
        generation_id = _digest_json(
            {
                "hub_payload_sha256": hub["integrity"]["payload_sha256"],
                "mount": mount,
                "shards": {identity: manifests[identity]["fingerprint"] for identity in identities},
            }
        )
        generation_path = self._mount_root(mount) / "generations" / generation_id
        if not generation_path.is_dir():
            receipt = {
                "schema_version": 1,
                "mount": mount,
                "generation_id": generation_id,
                "hub_payload_sha256": hub["integrity"]["payload_sha256"],
                "activated_at": _now(),
                "hub_verification": hub_verification,
                "shard_verifications": shard_verifications,
                "shards": {
                    identity: {
                        "fingerprint": manifests[identity]["fingerprint"],
                        "manifest_sha256": published_manifest_digest(manifests[identity]),
                    }
                    for identity in identities
                },
            }
            transaction = AtomicDirectoryTransaction(
                generation_path, operation="remote-shard-generation"
            )
            staging = transaction.prepare()
            try:
                _write_json(staging / "hub.json", hub)
                _write_json(staging / "receipt.json", receipt)
                for identity in identities:
                    name = _identity_digest(identity)
                    _write_json(staging / "manifests" / f"{name}.json", manifests[identity])
                    _write_json(staging / "catalogs" / f"{name}.json", catalogs[identity])
                transaction.commit()
            finally:
                transaction.cleanup()
        return self._read_generation(mount, generation_id)

    def _read_generation(self, mount: str, generation_id: str) -> RemoteMountGeneration:
        root = self._mount_root(mount) / "generations" / generation_id
        hub = _read_json_file(root / "hub.json")
        receipt = _read_json_file(root / "receipt.json")
        if receipt.get("mount") != mount or receipt.get("generation_id") != generation_id:
            raise RemoteShardVerificationError(
                f"Stored remote generation receipt identity is invalid for mount {mount!r}.",
                mount=mount,
                operation="remote_shard_generation_load",
            )
        if not isinstance(receipt.get("hub_verification"), dict) or not isinstance(
            receipt.get("shard_verifications"), dict
        ):
            raise RemoteShardVerificationError(
                f"Stored remote generation receipt lacks cryptographic evidence for mount {mount!r}.",
                mount=mount,
                operation="remote_shard_generation_load",
            )
        errors = validate_federation_hub_manifest(hub)
        if errors or receipt.get("hub_payload_sha256") != hub.get("integrity", {}).get(
            "payload_sha256"
        ):
            raise RemoteShardVerificationError(
                f"Stored remote hub generation is invalid for mount {mount!r}.",
                mount=mount,
                operation="remote_shard_generation_load",
            )
        channel = hub["channels"].get(mount)
        if not isinstance(channel, dict):
            raise RemoteShardVerificationError(
                f"Stored remote generation is missing channel {mount!r}.",
                mount=mount,
                operation="remote_shard_generation_load",
            )
        shards: dict[str, RemoteShardGeneration] = {}
        for identity in channel["editions"]:
            name = _identity_digest(identity)
            manifest = _read_json_file(root / "manifests" / f"{name}.json")
            catalog = _read_json_file(root / "catalogs" / f"{name}.json")
            manifest_errors = validate_published_shard_manifest(manifest)
            manifest_errors.extend(
                validate_federation_hub_manifest(hub, published_manifests={identity: manifest})
            )
            catalog_item = next(
                (item for item in manifest.get("inventory", []) if item.get("role") == "catalog"),
                None,
            )
            catalog_bytes = _canonical_json(catalog)
            if catalog_item is None:
                manifest_errors.append("inventory: catalog object is missing")
            else:
                if _digest_bytes(catalog_bytes) != catalog_item["sha256"]:
                    manifest_errors.append("catalog: stored decoded digest mismatch")
                manifest_errors.extend(
                    validate_published_object_payload(manifest, catalog_item, catalog_bytes)
                )
            if manifest_errors:
                raise RemoteShardVerificationError(
                    f"Stored remote shard {identity} failed validation: {'; '.join(manifest_errors[:6])}.",
                    mount=mount,
                    operation="remote_shard_generation_load",
                )
            receipt_shard = receipt.get("shards", {}).get(identity)
            if not isinstance(receipt_shard, dict) or receipt_shard != {
                "fingerprint": manifest["fingerprint"],
                "manifest_sha256": published_manifest_digest(manifest),
            }:
                raise RemoteShardVerificationError(
                    f"Stored remote shard receipt differs from immutable manifest {identity}.",
                    mount=mount,
                    operation="remote_shard_generation_load",
                )
            if identity not in receipt["shard_verifications"]:
                raise RemoteShardVerificationError(
                    f"Stored remote shard receipt lacks cryptographic evidence for {identity}.",
                    mount=mount,
                    operation="remote_shard_generation_load",
                )
            shards[identity] = _shard_generation(manifest, catalog)
        computed_id = _digest_json(
            {
                "hub_payload_sha256": hub["integrity"]["payload_sha256"],
                "mount": mount,
                "shards": {
                    identity: shards[identity].fingerprint for identity in channel["editions"]
                },
            }
        )
        if computed_id != generation_id:
            raise RemoteShardVerificationError(
                f"Stored remote generation digest is invalid for mount {mount!r}.",
                mount=mount,
                operation="remote_shard_generation_load",
            )
        return RemoteMountGeneration(
            mount=mount,
            generation_id=generation_id,
            hub_payload_sha256=str(hub["integrity"]["payload_sha256"]),
            latest=str(channel["latest"]),
            stable=str(channel["stable"]),
            editions=tuple(str(value) for value in channel["editions"]),
            shards=MappingProxyType(shards),
            activated_at=str(receipt["activated_at"]),
        )

    def _fetch_object(self, shard: RemoteShardGeneration, item: RemoteObjectRef) -> bytes:
        manifest = dict(shard.manifest)
        raw_item = next(
            value for value in manifest["inventory"] if value["logical_path"] == item.logical_path
        )
        return self._fetch_decoded(manifest, raw_item)

    def _fetch_decoded(self, manifest: dict[str, Any], item: dict[str, Any]) -> bytes:
        url = str(manifest["artifact_base_url"]).rstrip("/") + "/" + str(item["object_url"])
        encoded = self.fetcher.fetch(
            url,
            max_bytes=int(item["encoded_size"]),
            expected_size=int(item["encoded_size"]),
            expected_sha256=str(item["encoded_sha256"]),
        )
        role = str(item["role"])
        layout = manifest["layout"]
        decoded_limit = int(
            layout["max_fragment_bytes"]
            if role == "fragment"
            else layout["max_presentation_bytes"]
            if role == "presentation"
            else layout["max_object_bytes"]
        )
        decoded = _decode(encoded, str(item["content_encoding"]), max_bytes=decoded_limit)
        if len(decoded) != int(item["uncompressed_size"]) or _digest_bytes(decoded) != str(
            item["sha256"]
        ):
            raise RemoteShardVerificationError(
                f"Decoded remote object integrity differs for {item['logical_path']}.",
                operation="remote_shard_object_validate",
            )
        errors = validate_published_object_payload(manifest, item, decoded)
        if errors:
            raise RemoteShardVerificationError(
                f"Remote object {item['logical_path']} failed validation: {'; '.join(errors[:6])}.",
                operation="remote_shard_object_validate",
            )
        return decoded

    def _verify_hub(self, hub: dict[str, Any]) -> dict[str, Any]:
        if str(hub["discovery"]["hub_manifest_url"]) != self.hub_url:
            raise RemoteShardVerificationError(
                "Remote hub discovery URL differs from the configured trusted hub URL.",
                operation="remote_hub_verify",
            )
        for record in hub["signatures"]:
            self.fetcher.validate_url(str(record["url"]))
        try:
            result = self.verifier.verify_hub(hub, fetcher=self.fetcher)
        except Exception as exc:
            raise RemoteShardVerificationError(
                f"Remote hub cryptographic verification failed: {exc}.",
                operation="remote_hub_verify",
            ) from exc
        return _receipt_mapping(result, label="hub verifier")

    def _verify_shard(self, manifest: dict[str, Any], *, mount: str) -> dict[str, Any]:
        try:
            result = self.verifier.verify_shard(manifest, fetcher=self.fetcher)
        except Exception as exc:
            raise RemoteShardVerificationError(
                f"Remote shard cryptographic verification failed: {exc}.",
                mount=mount,
                operation="remote_shard_verify",
            ) from exc
        return _receipt_mapping(result, label="shard verifier")

    def _validate_shard_urls(self, manifest: dict[str, Any]) -> None:
        base = str(manifest["artifact_base_url"])
        self.fetcher.validate_url(base)
        for item in manifest["inventory"]:
            self.fetcher.validate_url(base.rstrip("/") + "/" + str(item["object_url"]))
        for record in (*manifest["signatures"], *manifest["attestations"]):
            self.fetcher.validate_url(str(record["url"]))

    def _resolve_shard(
        self, generation: RemoteMountGeneration, edition: str
    ) -> RemoteShardGeneration:
        identity = (
            generation.latest
            if edition == "latest"
            else generation.stable
            if edition == "stable"
            else f"{generation.mount}:{edition}"
        )
        shard = generation.shards.get(identity)
        if shard is None:
            raise RemoteShardUnavailableError(
                f"Remote shard edition {edition!r} is unavailable for mount {generation.mount!r}.",
                mount=generation.mount,
                operation="remote_shard_read",
            )
        return shard

    def _promote_selection(self, generation: RemoteMountGeneration) -> None:
        target = self._mount_root(generation.mount) / "current"
        transaction = AtomicDirectoryTransaction(target, operation="remote-shard-select")
        staging = transaction.prepare()
        try:
            _write_json(
                staging / "selection.json",
                {
                    "schema_version": 1,
                    "mount": generation.mount,
                    "generation_id": generation.generation_id,
                    "rollback_pin": generation.rollback_pin,
                    "active": True,
                    "selected_at": _now(),
                },
            )
            transaction.commit()
        finally:
            transaction.cleanup()

    def _load_last_known_good(self) -> None:
        mounts_root = self.state_root / "mounts"
        if not mounts_root.is_dir():
            return
        loaded: dict[str, RemoteMountGeneration] = {}
        for mount_root in mounts_root.iterdir():
            selection_path = mount_root / "current" / "selection.json"
            if not selection_path.is_file():
                continue
            try:
                selection = _read_json_file(selection_path)
                mount = str(selection["mount"])
                if selection.get("active") is not True or mount != mount_root.name:
                    continue
                generation = self._read_generation(mount, str(selection["generation_id"]))
                rollback_pin = str(selection.get("rollback_pin") or "") or None
                if rollback_pin is not None:
                    generation = RemoteMountGeneration(
                        mount=generation.mount,
                        generation_id=generation.generation_id,
                        hub_payload_sha256=generation.hub_payload_sha256,
                        latest=generation.latest,
                        stable=generation.stable,
                        editions=generation.editions,
                        shards=generation.shards,
                        activated_at=generation.activated_at,
                        rollback_pin=rollback_pin,
                    )
                loaded[mount] = generation
            except (KeyError, OSError, RemoteShardError, ValueError) as exc:
                self._startup_errors.append(f"{selection_path}: {exc}")
        self._mounts = MappingProxyType(loaded)

    def _install_generation(self, generation: RemoteMountGeneration) -> None:
        # Free-threaded readers never observe a mutating dict: construct a new
        # mapping under the registry lock, then swap the immutable reference.
        with self._state_lock:
            updated = dict(self._mounts)
            updated[generation.mount] = generation
            self._mounts = MappingProxyType(updated)

    def _deactivate_mount(self, mount: str) -> None:
        lock = self._lock_for(mount)
        with lock, self._lease(mount):
            target = self._mount_root(mount) / "current"
            transaction = AtomicDirectoryTransaction(target, operation="remote-shard-select")
            staging = transaction.prepare()
            try:
                _write_json(
                    staging / "selection.json",
                    {
                        "schema_version": 1,
                        "mount": mount,
                        "active": False,
                        "selected_at": _now(),
                    },
                )
                transaction.commit()
            finally:
                transaction.cleanup()
            with self._state_lock:
                updated = dict(self._mounts)
                updated.pop(mount, None)
                self._mounts = MappingProxyType(updated)

    def _current(self, mount: str) -> RemoteMountGeneration | None:
        with self._state_lock:
            return self._mounts.get(mount)

    def _lock_for(self, mount: str) -> threading.Lock:
        with self._lock_table_lock:
            return self._mount_locks.setdefault(mount, threading.Lock())

    def _lease(self, mount: str) -> OperationLease:
        return OperationLease(
            self.state_root / "leases",
            f"remote-shard-{mount}",
            resource=mount,
            timeout_seconds=operation_timeout_seconds(),
            lease_seconds=operation_lease_seconds(),
        )

    def _mount_root(self, mount: str) -> Path:
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", mount) is None:
            raise RemoteShardVerificationError(
                f"Remote mount identity {mount!r} is unsafe for local state.",
                mount=mount,
                operation="remote_shard_state",
            )
        return self.state_root / "mounts" / mount


def _shard_generation(manifest: dict[str, Any], catalog: dict[str, Any]) -> RemoteShardGeneration:
    identity = str(manifest["identity"]["key"])
    public_node_ids = frozenset(
        str(page["node_id"])
        for page in catalog.get("pages", [])
        if isinstance(page, dict) and page.get("node_id")
    )
    refs = [_object_ref(item) for item in manifest["inventory"]]
    fragments = {item.node_id: item for item in refs if item.role == "fragment" and item.node_id}
    presentations = {
        item.node_id: item for item in refs if item.role == "presentation" and item.node_id
    }
    if set(fragments) != public_node_ids or set(presentations) != public_node_ids:
        raise RemoteShardVerificationError(
            f"Remote shard {identity} per-node inventory differs from its public catalog.",
            mount=str(manifest["identity"]["mount"]),
            operation="remote_shard_catalog_validate",
        )
    semantic = next(item for item in refs if item.role == "semantic")
    return RemoteShardGeneration(
        identity=identity,
        mount=str(manifest["identity"]["mount"]),
        edition=str(manifest["identity"]["edition"]),
        fingerprint=str(manifest["fingerprint"]),
        artifact_base_url=str(manifest["artifact_base_url"]),
        manifest=_freeze_json(manifest),
        catalog=_freeze_json(catalog),
        public_node_ids=public_node_ids,
        fragments=MappingProxyType(fragments),
        presentations=MappingProxyType(presentations),
        semantic=semantic,
    )


def _object_ref(item: dict[str, Any]) -> RemoteObjectRef:
    return RemoteObjectRef(
        logical_path=str(item["logical_path"]),
        role=str(item["role"]),
        object_url=str(item["object_url"]),
        content_encoding=str(item["content_encoding"]),
        uncompressed_size=int(item["uncompressed_size"]),
        encoded_size=int(item["encoded_size"]),
        sha256=str(item["sha256"]),
        encoded_sha256=str(item["encoded_sha256"]),
        node_id=str(item.get("node_id") or "") or None,
    )


def _decode(value: bytes, encoding: str, *, max_bytes: int) -> bytes:
    try:
        if encoding == "identity":
            decoded = value
        elif encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(value)) as stream:
                decoded = stream.read(max_bytes + 1)
        elif encoding == "zstd":
            from compression import zstd

            with zstd.open(io.BytesIO(value), "rb") as stream:
                decoded = stream.read(max_bytes + 1)
        else:
            raise ValueError(f"unsupported content encoding {encoding!r}")
    except (OSError, ValueError) as exc:
        raise RemoteShardVerificationError(
            f"Remote object decompression failed: {exc}.",
            operation="remote_shard_object_decode",
        ) from exc
    if len(decoded) > max_bytes:
        raise RemoteShardVerificationError(
            f"Remote object decompressed beyond the {max_bytes} byte bound.",
            operation="remote_shard_object_decode",
        )
    return decoded


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query or parsed.fragment)
    ):
        raise ValueError("remote shard origin must be an absolute HTTPS URL")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("remote shard origin has an invalid HTTPS port") from exc
    return ("https", parsed.hostname.casefold(), port)


def _validate_https_url(url: str, *, origin: tuple[str, str, int]) -> None:
    parsed = urlsplit(url)
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise RemoteShardFetchError(
            f"Remote URL has an invalid HTTPS port: {url}.",
            operation="remote_shard_url",
        ) from exc
    candidate = (
        parsed.scheme.casefold(),
        parsed.hostname.casefold() if parsed.hostname else "",
        port,
    )
    decoded_path = unquote(parsed.path)
    if (
        candidate != origin
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query or parsed.fragment)
        or not parsed.path.startswith("/")
        or "\\" in decoded_path
        or any(ord(character) <= 32 for character in url)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        raise RemoteShardFetchError(
            f"Remote URL is not a credential-free same-origin HTTPS path: {url}.",
            operation="remote_shard_url",
        )


def _json_object(value: bytes, *, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteShardVerificationError(
            f"{label} is not valid UTF-8 JSON: {exc}.",
            operation="remote_shard_json",
        ) from exc
    if not isinstance(decoded, dict):
        raise RemoteShardVerificationError(
            f"{label} must contain a JSON object.",
            operation="remote_shard_json",
        )
    return decoded


def _receipt_mapping(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    try:
        encoded = _canonical_json(dict(value))
        result = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise RemoteShardVerificationError(
            f"{label} returned a non-serializable verification receipt: {exc}.",
            operation="remote_shard_verify",
        ) from exc
    if not isinstance(result, dict):
        raise RemoteShardVerificationError(
            f"{label} must return a receipt object.",
            operation="remote_shard_verify",
        )
    if result.get("verified") is not True:
        raise RemoteShardVerificationError(
            f"{label} did not return explicit verified evidence.",
            operation="remote_shard_verify",
        )
    return result


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        return _json_object(path.read_bytes(), label=str(path))
    except OSError as exc:
        raise RemoteShardVerificationError(
            f"Stored remote generation file is unreadable: {path}: {exc}.",
            path=path,
            operation="remote_shard_generation_load",
        ) from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json(dict(value)) + b"\n")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _identity_digest(identity: str) -> str:
    return hashlib.sha256(identity.encode()).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest_json(value: Any) -> str:
    return _digest_bytes(_canonical_json(value))


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
