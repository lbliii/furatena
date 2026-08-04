"""Verified compose-by-reference registry for immutable remote federation shards."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import threading
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from furatena.catalog.atomic_directory import AtomicDirectoryTransaction
from furatena.catalog.edition_lifecycle import lifecycle_statuses
from furatena.catalog.exceptions import CatalogError
from furatena.catalog.federated_search import (
    FederatedSearchHit,
    FederatedSearchResult,
    FederatedShardSearchHit,
    FederatedShardSearchIndex,
    rank_merge_federated_hits,
)
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
MAX_SEARCH_CACHE_BYTES = 64 * 1024 * 1024
MAX_SEARCH_CACHE_ENTRIES = 256
MAX_SEARCH_FANOUT = 256
MAX_SEARCH_WORKERS = 16
DEFAULT_RESIDENT_SHARD_ENTRIES = 32
DEFAULT_RESIDENT_SHARD_BYTES = 128 * 1024 * 1024


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
    search: RemoteObjectRef
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


@dataclass(slots=True)
class _CatalogFlight:
    completed: threading.Event
    value: Mapping[str, Any] | None = None
    error: _CatalogFailure | None = None


@dataclass(frozen=True, slots=True)
class _CatalogFailure:
    error_type: type[BaseException]
    summary: str
    context: tuple[tuple[str, str], ...] = ()

    @classmethod
    def capture(cls, error: BaseException) -> _CatalogFailure:
        context = error.context if isinstance(error, CatalogError) else {}
        return cls(type(error), str(error), tuple(sorted(context.items())))

    def recreate(self) -> BaseException:
        try:
            if issubclass(self.error_type, CatalogError):
                return self.error_type(self.summary, **dict(self.context))
            return self.error_type(self.summary)
        except Exception:
            return RemoteShardFetchError(
                f"A coalesced remote catalog load failed: {self.summary}.",
                operation="remote_shard_catalog_load",
            )


class _LazyCatalog(Mapping[str, Any]):
    """Mapping facade that resolves one immutable catalog through residency."""

    __slots__ = ("_loader",)

    def __init__(self, loader: Callable[[], Mapping[str, Any]]) -> None:
        self._loader = loader

    def __getitem__(self, key: str) -> Any:
        return self._loader()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._loader())

    def __len__(self) -> int:
        return len(self._loader())


@dataclass(frozen=True, slots=True)
class _CatalogLocation:
    key: str
    mount: str
    identity: str
    manifest: Mapping[str, Any]
    item: Mapping[str, Any]
    warm_path: Path
    legacy_path: Path


class _CatalogResidency:
    """Free-threaded bounded LRU for decoded immutable catalog mappings."""

    def __init__(
        self,
        loader: Callable[[_CatalogLocation], tuple[Mapping[str, Any], int, str]],
        *,
        max_entries: int,
        max_bytes: int,
    ) -> None:
        if max_entries <= 0 or max_bytes <= 0:
            raise ValueError("Remote resident shard bounds must both be positive integers.")
        self._loader = loader
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._cache: OrderedDict[str, tuple[Mapping[str, Any], int]] = OrderedDict()
        self._flights: dict[str, _CatalogFlight] = {}
        self._locations: dict[str, _CatalogLocation] = {}
        self._lock = threading.Lock()
        self._hot_hits = 0
        self._warm_loads = 0
        self._cold_loads = 0
        self._evictions = 0
        self._coalesced = 0
        self._failures = 0
        self._resident_bytes = 0

    def register(self, location: _CatalogLocation) -> _LazyCatalog:
        with self._lock:
            self._locations[location.key] = location
        return _LazyCatalog(lambda: self.load(location))

    def load(self, location: _CatalogLocation) -> Mapping[str, Any]:
        with self._lock:
            cached = self._cache.get(location.key)
            if cached is not None:
                self._hot_hits += 1
                self._cache.move_to_end(location.key)
                return cached[0]
            flight = self._flights.get(location.key)
            owner = flight is None
            if flight is None:
                flight = _CatalogFlight(threading.Event())
                self._flights[location.key] = flight
            else:
                self._coalesced += 1
        if not owner:
            flight.completed.wait()
            if flight.error is not None:
                raise flight.error.recreate()
            assert flight.value is not None
            return flight.value
        try:
            value, size, tier = self._loader(location)
        except BaseException as exc:
            with self._lock:
                self._failures += 1
                flight.error = _CatalogFailure.capture(exc)
                self._flights.pop(location.key, None)
                flight.completed.set()
            raise
        with self._lock:
            flight.value = value
            if tier == "warm":
                self._warm_loads += 1
            else:
                self._cold_loads += 1
            registered = self._locations.get(location.key) is location
            if size <= self._max_bytes and registered:
                while self._cache and (
                    len(self._cache) >= self._max_entries
                    or self._resident_bytes + size > self._max_bytes
                ):
                    _key, (_value, evicted_size) = self._cache.popitem(last=False)
                    self._resident_bytes -= evicted_size
                    self._evictions += 1
                self._cache[location.key] = (value, size)
                self._resident_bytes += size
            self._flights.pop(location.key, None)
            flight.completed.set()
        return value

    def discard_mount(self, mount: str) -> None:
        self.retain_mount(mount, frozenset())

    def retain_mount(self, mount: str, valid_keys: frozenset[str]) -> None:
        with self._lock:
            keys = {
                key
                for key, item in self._locations.items()
                if item.mount == mount and key not in valid_keys
            }
            for key in keys:
                cached = self._cache.pop(key, None)
                if cached is not None:
                    self._resident_bytes -= cached[1]
                self._locations.pop(key, None)

    def status(self) -> dict[str, Any]:
        with self._lock:
            hot = set(self._cache)
            locations = tuple(self._locations.values())
            snapshot: dict[str, Any] = {
                "schema_version": 1,
                "resident_entries": len(self._cache),
                "resident_bytes": self._resident_bytes,
                "max_resident_entries": self._max_entries,
                "max_resident_bytes": self._max_bytes,
                "in_flight": len(self._flights),
                "hot_hits": self._hot_hits,
                "warm_loads": self._warm_loads,
                "cold_loads": self._cold_loads,
                "evictions": self._evictions,
                "coalesced_loads": self._coalesced,
                "load_failures": self._failures,
            }
        # Filesystem probes are status-only I/O and never hold the residency
        # lock needed by active readers.
        shards = [
            {
                "identity": item.identity,
                "mount": item.mount,
                "tier": (
                    "hot"
                    if item.key in hot
                    else "warm"
                    if item.warm_path.is_file() or item.legacy_path.is_file()
                    else "cold"
                ),
            }
            for item in locations
        ]
        snapshot["shards"] = sorted(shards, key=lambda item: str(item["identity"]))
        return snapshot


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
        max_search_cache_bytes: int = MAX_SEARCH_CACHE_BYTES,
        max_search_cache_entries: int = MAX_SEARCH_CACHE_ENTRIES,
        max_search_fanout: int = MAX_SEARCH_FANOUT,
        max_search_workers: int = MAX_SEARCH_WORKERS,
        resident_shard_entries: int = DEFAULT_RESIDENT_SHARD_ENTRIES,
        resident_shard_bytes: int = DEFAULT_RESIDENT_SHARD_BYTES,
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
        if not 0 <= max_search_cache_bytes <= MAX_SEARCH_CACHE_BYTES:
            raise ValueError(
                f"Remote search cache byte bound must be between 0 and {MAX_SEARCH_CACHE_BYTES}."
            )
        if not 0 <= max_search_cache_entries <= MAX_SEARCH_CACHE_ENTRIES:
            raise ValueError(
                f"Remote search cache entry bound must be between 0 and {MAX_SEARCH_CACHE_ENTRIES}."
            )
        if not 1 <= max_search_fanout <= MAX_SEARCH_FANOUT:
            raise ValueError(f"Remote search fan-out must be between 1 and {MAX_SEARCH_FANOUT}.")
        if not 1 <= max_search_workers <= MAX_SEARCH_WORKERS:
            raise ValueError(f"Remote search workers must be between 1 and {MAX_SEARCH_WORKERS}.")
        self.max_search_cache_bytes = max_search_cache_bytes
        self.max_search_cache_entries = max_search_cache_entries
        self.max_search_fanout = max_search_fanout
        self.max_search_workers = max_search_workers
        self._state_lock = threading.RLock()
        self._lock_table_lock = threading.Lock()
        self._mount_locks: dict[str, threading.Lock] = {}
        self._mounts: Mapping[str, RemoteMountGeneration] = MappingProxyType({})
        self._startup_errors: list[str] = []
        self._search_cache_lock = threading.RLock()
        self._search_cache: OrderedDict[str, tuple[FederatedShardSearchIndex, int]] = OrderedDict()
        self._search_cache_bytes = 0
        self._search_flights: dict[str, Future[FederatedShardSearchIndex]] = {}
        self._catalog_residency = _CatalogResidency(
            self._load_catalog_location,
            max_entries=resident_shard_entries,
            max_bytes=resident_shard_bytes,
        )
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

    def residency_status(self) -> dict[str, Any]:
        """Return bounded shard-residency and churn diagnostics for operators."""
        return self._catalog_residency.status()

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
        return self.fetch_presentation_from(shard, node_id)

    def fetch_presentation_from(self, shard: RemoteShardGeneration, node_id: str) -> bytes:
        """Fetch from an already resolved immutable generation without refresh mixing."""
        presentation_ref = shard.presentations.get(node_id)
        if presentation_ref is None:
            raise RemoteShardUnavailableError(
                f"Remote node {node_id!r} is not public in {shard.identity}.",
                mount=shard.mount,
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

    def search(
        self,
        query: str,
        *,
        mounts: Iterable[str] | None = None,
        edition: str = "latest",
        limit: int = 12,
        status: str | None = None,
        include_preview: bool = False,
        include_eol: bool = False,
    ) -> FederatedSearchResult:
        """Fan a query out only after mount, edition, and lifecycle selection."""
        if limit <= 0 or not query.strip():
            return FederatedSearchResult((), (), ())
        selected_mounts = frozenset(mounts) if mounts is not None else None
        selected_statuses = lifecycle_statuses(
            status=status,
            include_preview=include_preview,
            include_eol=include_eol,
        )
        with self._state_lock:
            generations = tuple(
                generation
                for mount, generation in sorted(self._mounts.items())
                if selected_mounts is None or mount in selected_mounts
            )
        candidates: list[RemoteShardGeneration] = []
        skipped: list[str] = []
        for generation in generations:
            try:
                shard = self._resolve_shard(generation, edition)
            except RemoteShardUnavailableError:
                continue
            lifecycle = str(shard.manifest["lifecycle"]["status"])
            if lifecycle not in selected_statuses:
                skipped.append(shard.identity)
                continue
            candidates.append(shard)
        if len(candidates) > self.max_search_fanout:
            raise RemoteShardUnavailableError(
                f"Remote search selected {len(candidates)} shards above the "
                f"{self.max_search_fanout} shard fan-out bound; scope the query by mount.",
                operation="remote_shard_search_scope",
            )
        per_shard_limit = min(max(limit * 2, 16), 100)
        shard_hits: list[tuple[RemoteShardGeneration, tuple[FederatedShardSearchHit, ...]]] = []
        failures: list[tuple[str, Exception]] = []
        if len(candidates) == 1:
            shard = candidates[0]
            try:
                shard_hits.append((shard, self._search_shard(shard, query, per_shard_limit)))
            except Exception as exc:
                failures.append((shard.identity, exc))
        elif candidates:
            worker_count = min(self.max_search_workers, len(candidates))
            # Under PYTHON_GIL=0 each worker owns its fetch/decode/query stack;
            # the only shared mutation is the explicitly locked bounded cache.
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(self._search_shard, shard, query, per_shard_limit): shard
                    for shard in candidates
                }
                for future in as_completed(futures):
                    shard = futures[future]
                    try:
                        shard_hits.append((shard, future.result()))
                    except Exception as exc:
                        failures.append((shard.identity, exc))
        if failures:
            identities = ", ".join(identity for identity, _ in sorted(failures))
            raise RemoteShardUnavailableError(
                f"Remote search could not verify/query shard indexes: {identities}.",
                operation="remote_shard_search",
            ) from failures[0][1]
        merged = [
            FederatedSearchHit(
                node_id=hit.node_id,
                title=hit.title,
                snippet=hit.snippet,
                mount=shard.mount,
                edition=shard.edition,
                shard_fingerprint=shard.fingerprint,
                score=hit.score,
                keyword_score=hit.keyword_score,
                tfidf_score=hit.tfidf_score,
            )
            for shard, hits in shard_hits
            for hit in hits
        ]
        return FederatedSearchResult(
            hits=rank_merge_federated_hits(merged, limit=limit),
            searched_shards=tuple(sorted(shard.identity for shard in candidates)),
            skipped_shards=tuple(sorted(skipped)),
        )

    def search_cache_stats(self) -> Mapping[str, int]:
        """Return lock-consistent decoded-index cache accounting."""
        with self._search_cache_lock:
            return MappingProxyType(
                {"entries": len(self._search_cache), "bytes": self._search_cache_bytes}
            )

    def _search_shard(
        self, shard: RemoteShardGeneration, query: str, limit: int
    ) -> tuple[FederatedShardSearchHit, ...]:
        index = self._cached_search_index(shard)
        return index.search(query, limit=limit)

    def _cached_search_index(self, shard: RemoteShardGeneration) -> FederatedShardSearchIndex:
        with self._search_cache_lock:
            cached = self._search_cache.get(shard.fingerprint)
            if cached is not None:
                self._search_cache.move_to_end(shard.fingerprint)
                return cached[0]
            flight = self._search_flights.get(shard.fingerprint)
            owner = flight is None
            if flight is None:
                flight = Future()
                self._search_flights[shard.fingerprint] = flight
        if not owner:
            return flight.result()
        try:
            index = self._load_search_index(shard)
        except Exception as exc:
            flight.set_exception(exc)
            raise
        else:
            flight.set_result(index)
            return index
        finally:
            with self._search_cache_lock:
                if self._search_flights.get(shard.fingerprint) is flight:
                    self._search_flights.pop(shard.fingerprint, None)

    def _load_search_index(self, shard: RemoteShardGeneration) -> FederatedShardSearchIndex:
        decoded = self._fetch_object(shard, shard.search)
        value = _json_object(decoded, label=f"search index {shard.identity}")
        try:
            index = FederatedShardSearchIndex(value)
        except ValueError as exc:
            raise RemoteShardVerificationError(
                f"Remote search index contract is invalid for {shard.identity}: {exc}.",
                mount=shard.mount,
                operation="remote_shard_object_validate",
            ) from exc
        if (
            index.mount != shard.mount
            or index.edition != shard.edition
            or {document.node_id for document in index.documents} != shard.public_node_ids
        ):
            raise RemoteShardVerificationError(
                f"Remote search index identity/node set differs from public catalog for "
                f"{shard.identity}.",
                mount=shard.mount,
                operation="remote_shard_object_validate",
            )
        self._store_search_index(shard.fingerprint, index, index.resident_bytes)
        return index

    def _store_search_index(
        self, fingerprint: str, index: FederatedShardSearchIndex, size: int
    ) -> None:
        if (
            not self.max_search_cache_entries
            or not self.max_search_cache_bytes
            or size > self.max_search_cache_bytes
        ):
            return
        with self._search_cache_lock:
            previous = self._search_cache.pop(fingerprint, None)
            if previous is not None:
                self._search_cache_bytes -= previous[1]
            self._search_cache[fingerprint] = (index, size)
            self._search_cache_bytes += size
            while (
                len(self._search_cache) > self.max_search_cache_entries
                or self._search_cache_bytes > self.max_search_cache_bytes
            ):
                _, (_, evicted_size) = self._search_cache.popitem(last=False)
                self._search_cache_bytes -= evicted_size

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
            manifests[identity] = manifest
        generation_shards = {
            identity: {
                "fingerprint": str(manifests[identity]["fingerprint"]),
                "manifest_sha256": published_manifest_digest(manifests[identity]),
            }
            for identity in identities
        }
        generation_id = _mount_generation_id(mount, channel, generation_shards)
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
                "shards": generation_shards,
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
            manifest_errors = validate_published_shard_manifest(manifest)
            manifest_errors.extend(
                validate_federation_hub_manifest(hub, published_manifests={identity: manifest})
            )
            catalog_item = next(
                (item for item in manifest.get("inventory", []) if item.get("role") == "catalog"),
                None,
            )
            if catalog_item is None:
                manifest_errors.append("inventory: catalog object is missing")
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
            assert catalog_item is not None
            catalog_key = f"{identity}:{manifest['fingerprint']}"
            location = _CatalogLocation(
                key=catalog_key,
                mount=mount,
                identity=str(identity),
                manifest=_freeze_json(manifest),
                item=_freeze_json(catalog_item),
                warm_path=(
                    self.state_root / "object-cache" / "sha256" / f"{catalog_item['sha256']}.json"
                ),
                legacy_path=root / "catalogs" / f"{name}.json",
            )
            shards[identity] = _shard_generation(
                manifest,
                self._catalog_residency.register(location),
            )
        computed_id = _mount_generation_id(
            mount,
            channel,
            {
                identity: {
                    "fingerprint": str(receipt["shards"][identity]["fingerprint"]),
                    "manifest_sha256": str(receipt["shards"][identity]["manifest_sha256"]),
                }
                for identity in channel["editions"]
            },
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

    def _load_catalog_location(
        self, location: _CatalogLocation
    ) -> tuple[Mapping[str, Any], int, str]:
        manifest = dict(location.manifest)
        item = dict(location.item)
        decoded: bytes | None = None
        tier = "cold"
        for path in (location.warm_path, location.legacy_path):
            try:
                candidate = path.read_bytes()
            except OSError:
                continue
            if path == location.legacy_path:
                try:
                    candidate = _canonical_json(
                        _json_object(candidate, label=f"legacy catalog {location.identity}")
                    )
                except RemoteShardVerificationError:
                    continue
            if (
                len(candidate) == int(item["uncompressed_size"])
                and _digest_bytes(candidate) == str(item["sha256"])
                and not validate_published_object_payload(manifest, item, candidate)
            ):
                decoded = candidate
                tier = "warm"
                break
        if decoded is None:
            decoded = self._fetch_decoded(manifest, item)
            location.warm_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = location.warm_path.with_name(
                f".{location.warm_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary.write_bytes(decoded)
                temporary.replace(location.warm_path)
            finally:
                with suppress(FileNotFoundError):
                    temporary.unlink()
        catalog = _json_object(decoded, label=f"catalog {location.identity}")
        frozen = _freeze_json(catalog)
        _validate_catalog_identity(manifest, frozen)
        return frozen, _deep_size(frozen), tier

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
        self._catalog_residency.retain_mount(
            generation.mount,
            frozenset(
                f"{shard.identity}:{shard.fingerprint}" for shard in generation.shards.values()
            ),
        )

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
            self._catalog_residency.discard_mount(mount)

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


def _shard_generation(
    manifest: dict[str, Any], catalog: Mapping[str, Any]
) -> RemoteShardGeneration:
    identity = str(manifest["identity"]["key"])
    refs = [_object_ref(item) for item in manifest["inventory"]]
    fragments = {item.node_id: item for item in refs if item.role == "fragment" and item.node_id}
    presentations = {
        item.node_id: item for item in refs if item.role == "presentation" and item.node_id
    }
    public_node_ids = frozenset(str(value) for value in fragments)
    if set(presentations) != public_node_ids:
        raise RemoteShardVerificationError(
            f"Remote shard {identity} per-node inventory differs from its public catalog.",
            mount=str(manifest["identity"]["mount"]),
            operation="remote_shard_catalog_validate",
        )
    search = next(item for item in refs if item.role == "search")
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
        search=search,
        semantic=semantic,
    )


def _validate_catalog_identity(manifest: Mapping[str, Any], catalog: Mapping[str, Any]) -> None:
    identity = str(manifest["identity"]["key"])
    catalog_ids = {
        str(page["node_id"])
        for page in catalog.get("pages", ())
        if isinstance(page, Mapping) and page.get("node_id")
    }
    inventory_ids = {
        str(item["node_id"])
        for item in manifest["inventory"]
        if isinstance(item, Mapping) and item.get("role") == "fragment" and item.get("node_id")
    }
    if catalog_ids != inventory_ids:
        raise RemoteShardVerificationError(
            f"Remote shard {identity} catalog node set differs from its public inventory.",
            mount=str(manifest["identity"]["mount"]),
            operation="remote_shard_catalog_validate",
        )


def _deep_size(value: Any, seen: set[int] | None = None) -> int:
    """Return a cycle-safe resident-size estimate for frozen JSON containers."""
    import sys

    visited = seen if seen is not None else set()
    identity = id(value)
    if identity in visited:
        return 0
    visited.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, Mapping):
        return size + sum(
            _deep_size(key, visited) + _deep_size(item, visited) for key, item in value.items()
        )
    if isinstance(value, tuple):
        return size + sum(_deep_size(item, visited) for item in value)
    return size


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


def _mount_generation_id(
    mount: str,
    channel: Mapping[str, Any],
    shards: Mapping[str, Mapping[str, str]],
) -> str:
    """Hash only state that changes the selected mount's readable projection."""
    identities = tuple(str(value) for value in channel["editions"])
    return _digest_json(
        {
            "mount": mount,
            "channel": {
                "latest": str(channel["latest"]),
                "stable": str(channel["stable"]),
                "editions": identities,
            },
            "shards": {
                identity: {
                    "fingerprint": shards[identity]["fingerprint"],
                    "manifest_sha256": shards[identity]["manifest_sha256"],
                }
                for identity in identities
            },
        }
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest_json(value: Any) -> str:
    return _digest_bytes(_canonical_json(value))


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
