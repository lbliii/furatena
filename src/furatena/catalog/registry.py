"""Catalog registry — federated mounts over DocCatalog shards."""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event, Lock, RLock
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from patitas.nodes import Document

    from furatena.catalog.edition_projection import EditionPageResolution, EditionProjection
    from furatena.catalog.inventories import InventoryStore
    from furatena.catalog.remote_shards import RemoteRefreshReport, RemoteShardMountRegistry

import yaml

from furatena.catalog.access import (
    ACCESS_EVALUATOR,
    AccessDecision,
    AccessPermission,
    AccessPolicy,
    AccessSubject,
)
from furatena.catalog.catalog_nav import CatalogNavConfig
from furatena.catalog.edition_lifecycle import EditionLifecycle
from furatena.catalog.graph import build_federated_backlinks, normalize_internal_url
from furatena.catalog.graph_schema import build_graph_edges
from furatena.catalog.i18n import DocsI18nConfig, build_translation_index
from furatena.catalog.identity import (
    identity_namespace_parts,
    identity_route_prefix,
    normalize_identity,
    scope_url,
    scoped_frozen_dir,
    strip_identity_route,
)
from furatena.catalog.loader import DocCatalog
from furatena.catalog.models import DocNode
from furatena.catalog.record_types import CatalogGraphRecord, EdgeRecord, NamespaceRecord
from furatena.catalog.runtime import ServeMode
from furatena.catalog.search import SearchHit, search_nodes
from furatena.catalog.source_sync_state import SourceSyncStateStore
from furatena.catalog.sources.git import sync_git_source
from furatena.catalog.sources.types import (
    GitEditionPolicy,
    GitEditionSnapshot,
    MountSourceConfig,
)
from furatena.catalog.versions import DocChannel, active_channel_id
from furatena.catalog.watch import SourceWatcher
from furatena.catalog.workers import resolve_workers

_REMOTE_RESIDENT_SHARD_ENTRIES = 32
_REMOTE_RESIDENT_SHARD_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MountConfig:
    """One documentation mount in a federated portal."""

    id: str
    label: str
    content_root: Path
    url_prefix: str = ""
    default: bool = False
    source: MountSourceConfig = field(default_factory=MountSourceConfig)
    access: AccessPolicy = field(default_factory=AccessPolicy)
    editions: GitEditionPolicy | None = None


@dataclass(frozen=True, slots=True)
class _CatalogReadGeneration:
    """One immutable publication unit for free-threaded catalog readers."""

    id: int
    edition: str
    shards: Mapping[str, DocCatalog]
    backlinks: Mapping[str, tuple[Mapping[str, str], ...]]
    translation_index: Mapping[str, Mapping[str, str]]
    inventory_store: InventoryStore | None
    edges: tuple[EdgeRecord, ...] | None
    namespaces: tuple[NamespaceRecord, ...] | None


@dataclass(frozen=True, slots=True)
class FederatedCatalogSearchHit:
    """A remote shard result resolved to the active catalog generation."""

    node: DocNode
    score: float
    snippet: str
    keyword_score: float
    tfidf_score: float


class _CatalogReadSnapshotContext:
    """Context manager that resets a read pin without rewriting exceptions."""

    def __init__(self, registry: CatalogRegistry) -> None:
        self._registry = registry
        self._token: Any = None

    def __enter__(self) -> None:
        self._token = self._registry._pin_read_generation()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        _ = exc_type, exc, traceback
        self._registry._read_generation_context.reset(self._token)
        return False


@dataclass(slots=True)
class _RemoteCatalogFlight:
    completed: Event
    mount_epoch: int
    value: DocCatalog | None = None
    error: BaseException | None = None


class _RemoteCatalogResidency:
    """Bounded free-threaded LRU for composed remote catalog graphs."""

    def __init__(self, *, max_entries: int, max_bytes: int) -> None:
        if max_entries <= 0 or max_bytes <= 0:
            raise ValueError("Remote composed shard bounds must both be positive integers.")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._cache: OrderedDict[str, tuple[DocCatalog, int, str]] = OrderedDict()
        self._flights: dict[str, _RemoteCatalogFlight] = {}
        self._mount_epochs: dict[str, int] = {}
        self._lock = Lock()
        self._resident_bytes = 0
        self._loads = 0
        self._hits = 0
        self._evictions = 0
        self._coalesced = 0

    def load(
        self,
        key: str,
        *,
        mount: str,
        loader: Callable[[], DocCatalog],
    ) -> DocCatalog:
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._hits += 1
                self._cache.move_to_end(key)
                return cached[0]
            flight = self._flights.get(key)
            owner = flight is None
            if flight is None:
                flight = _RemoteCatalogFlight(
                    Event(),
                    self._mount_epochs.get(mount, 0),
                )
                self._flights[key] = flight
            else:
                self._coalesced += 1
        if not owner:
            flight.completed.wait()
            if flight.error is not None:
                raise RuntimeError(
                    f"Coalesced remote catalog composition failed: {flight.error}. "
                    "Retry the mount request after checking source health."
                ) from flight.error
            assert flight.value is not None
            return flight.value
        try:
            catalog = loader()
            size = _doc_catalog_resident_size(catalog)
        except BaseException as exc:
            with self._lock:
                flight.error = exc
                self._flights.pop(key, None)
                flight.completed.set()
            raise
        with self._lock:
            self._loads += 1
            flight.value = catalog
            current_epoch = self._mount_epochs.get(mount, 0)
            if size <= self.max_bytes and flight.mount_epoch == current_epoch:
                while self._cache and (
                    len(self._cache) >= self.max_entries
                    or self._resident_bytes + size > self.max_bytes
                ):
                    _old_key, (_old_catalog, old_size, _old_mount) = self._cache.popitem(last=False)
                    self._resident_bytes -= old_size
                    self._evictions += 1
                self._cache[key] = (catalog, size, mount)
                self._resident_bytes += size
            self._flights.pop(key, None)
            flight.completed.set()
        return catalog

    def discard_mount(self, mount: str) -> None:
        with self._lock:
            self._mount_epochs[mount] = self._mount_epochs.get(mount, 0) + 1
            keys = [key for key, item in self._cache.items() if item[2] == mount]
            for key in keys:
                _catalog, size, _mount = self._cache.pop(key)
                self._resident_bytes -= size

    def status(self) -> dict[str, int]:
        with self._lock:
            return {
                "resident_entries": len(self._cache),
                "resident_bytes": self._resident_bytes,
                "max_resident_entries": self.max_entries,
                "max_resident_bytes": self.max_bytes,
                "in_flight": len(self._flights),
                "loads": self._loads,
                "hits": self._hits,
                "evictions": self._evictions,
                "coalesced_loads": self._coalesced,
            }


def _last_known_good_mount(mount: MountConfig, state: dict[str, Any]) -> MountConfig:
    last_known_good = state.get("last_known_good") or {}
    content_root = Path(str(last_known_good.get("content_root") or ""))
    resolved_ref = str(last_known_good.get("resolved_ref") or "") or None
    if not content_root.is_dir() or resolved_ref is None:
        return mount
    source_url = str(last_known_good.get("source_url") or "") or None
    return replace(
        mount,
        content_root=content_root,
        source=mount.source.with_git_sync_state(
            resolved_ref=resolved_ref,
            source_url=source_url,
        ),
    )


def _state_editions(state: dict[str, Any]) -> tuple[GitEditionSnapshot, ...]:
    last_known_good = state.get("last_known_good") or {}
    return tuple(
        GitEditionSnapshot.from_mapping(item)
        for item in last_known_good.get("editions") or []
        if isinstance(item, dict)
    )


def _frozen_editions(
    frozen_root: Path,
    mounts: tuple[MountConfig, ...],
) -> dict[str, tuple[GitEditionSnapshot, ...]]:
    """Restore immutable edition discovery state persisted in ``channels.json``."""
    path = frozen_root / "channels.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, TypeError, ValueError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    sync = payload.get("sync")
    sources = sync.get("sources") if isinstance(sync, Mapping) else None
    if not isinstance(sources, list):
        return {}

    configured = {mount.id for mount in mounts}
    restored: dict[str, tuple[GitEditionSnapshot, ...]] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        mount_id = str(source.get("mount") or "")
        editions = source.get("editions")
        if mount_id not in configured or not isinstance(editions, list):
            continue
        snapshots: list[GitEditionSnapshot] = []
        for edition in editions:
            if not isinstance(edition, Mapping):
                continue
            edition_id = str(edition.get("id") or "")
            ref = str(edition.get("ref") or "")
            resolved_ref = str(edition.get("resolved_ref") or "")
            if not edition_id or not ref or not resolved_ref:
                continue
            content_root = frozen_root / "mounts" / mount_id
            if edition_id != "latest":
                content_root /= edition_id
            snapshots.append(
                GitEditionSnapshot(
                    id=edition_id,
                    ref=ref,
                    resolved_ref=resolved_ref,
                    content_root=content_root,
                    status=str(edition.get("status") or ""),
                    prerelease=bool(edition.get("prerelease")),
                    discovered_at=str(edition.get("discovered_at") or ""),
                    release_date=str(edition.get("release_date") or "") or None,
                    end_of_life=str(edition.get("end_of_life") or "") or None,
                    banner=str(edition.get("banner") or "") or None,
                )
            )
        if snapshots:
            restored[mount_id] = tuple(snapshots)
    return restored


def _normalize_prefix(prefix: str) -> str:
    if not prefix or prefix == "/":
        return ""
    return prefix if prefix.endswith("/") else f"{prefix}/"


def _exception_record(exc: Exception) -> dict[str, Any]:
    from furatena.catalog.exceptions import CatalogError

    if isinstance(exc, CatalogError):
        legacy_type = next(
            (
                item.__name__
                for item in (FileNotFoundError, PermissionError, ValueError, RuntimeError)
                if isinstance(exc, item)
            ),
            "Exception",
        )
        return {
            "type": legacy_type,
            "message": str(exc),
            "domain_type": exc.__class__.__name__,
            "code": exc.code,
            "context": exc.context,
        }
    return {"type": exc.__class__.__name__, "message": str(exc)}


def _count_source_files(root: Path, extensions: tuple[str, ...]) -> int:
    if not root.is_dir():
        return 0
    normalized = tuple(ext if ext.startswith(".") else f".{ext}" for ext in extensions)
    return sum(
        1
        for path in root.rglob("*")
        if path.is_file() and (not normalized or path.suffix in normalized)
    )


def load_mounts(config_path: Path, *, repo_root: Path) -> tuple[MountConfig, ...]:
    """Load mount definitions from ``mounts.yaml``."""
    if not config_path.is_file():
        return (
            MountConfig(
                id="chirp",
                label="Furatena Documentation",
                content_root=repo_root / "site" / "content",
                default=True,
            ),
        )

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    mounts_raw = raw.get("mounts") or []
    mounts: list[MountConfig] = []
    for item in mounts_raw:
        if not isinstance(item, dict):
            continue
        mount_id = str(item.get("id") or "").strip()
        if not mount_id:
            continue
        source_config = MountSourceConfig.from_mount_dict(item)
        edition_policy = GitEditionPolicy.from_mount_dict(
            item,
            mount_id=mount_id,
            git_backed=source_config.git is not None,
        )
        content_raw = str(item.get("content_root") or "").strip()
        if content_raw:
            content_root = Path(content_raw)
        elif source_config.git is not None:
            content_root = config_path.parent / ".docs-cache" / "sources" / mount_id / "repo"
            if source_config.git.path:
                content_root = content_root / source_config.git.path
        else:
            content_root = Path(content_raw)
        if not content_root.is_absolute():
            content_root = config_path.parent / content_root
        content_root = content_root.resolve()
        mounts.append(
            MountConfig(
                id=mount_id,
                label=str(item.get("label") or mount_id),
                content_root=content_root,
                url_prefix=_normalize_prefix(str(item.get("url_prefix") or "")),
                default=bool(item.get("default")),
                source=source_config,
                access=AccessPolicy.from_mount_dict(item),
                editions=edition_policy,
            )
        )
    if not mounts:
        return load_mounts(Path("__missing__"), repo_root=repo_root)
    return tuple(mounts)


class CatalogRegistry:
    """Federated documentation graph composed of per-mount catalog shards."""

    def __init__(
        self,
        mounts: tuple[MountConfig, ...],
        *,
        repo_root: Path,
        app_root: Path | None = None,
        rewrites_path: Path | None = None,
        inventories_path: Path | None = None,
        autodoc_config: Path | None = None,
        autodoc: bool = True,
        auto_reload: bool = False,
        channel: str | None = None,
        frozen_dir: Path | None = None,
        lazy_html: bool = False,
        serve_mode: ServeMode = ServeMode.AUTHOR,
        include_private: bool = False,
        workers: int | None = None,
        i18n_config: DocsI18nConfig | None = None,
        catalog_nav: CatalogNavConfig | None = None,
        site_mark: str = "𐂛",
        catalog_identity: dict[str, str] | None = None,
        source_sync_state: SourceSyncStateStore | None = None,
        state_root: Path | None = None,
        remote_shards: RemoteShardMountRegistry | None = None,
        remote_resident_shard_entries: int = _REMOTE_RESIDENT_SHARD_ENTRIES,
        remote_resident_shard_bytes: int = _REMOTE_RESIDENT_SHARD_BYTES,
    ) -> None:
        self.repo_root = repo_root
        self.app_root = app_root or repo_root
        self.rewrites_path = rewrites_path
        self.inventories_path = inventories_path
        self.autodoc_config = autodoc_config
        self.autodoc_enabled = autodoc
        self.auto_reload = auto_reload
        self._default_channel = channel or active_channel_id()
        self._edition_context: ContextVar[str] = ContextVar(
            f"furatena_edition_{id(self)}", default=self._default_channel
        )
        self._read_generation_context: ContextVar[_CatalogReadGeneration | None] = ContextVar(
            f"furatena_read_generation_{id(self)}", default=None
        )
        self.i18n_config = i18n_config or DocsI18nConfig()
        self.catalog_nav = catalog_nav
        self.site_mark = site_mark
        self.catalog_identity = normalize_identity(catalog_identity)
        source_state_root = state_root or self.app_root / ".docs-cache" / "source-sync-state"
        namespace = self.identity_cache_namespace()
        self.source_sync_state = source_sync_state or SourceSyncStateStore(
            source_state_root / namespace if namespace else source_state_root
        )
        self.remote_shards = remote_shards
        self._remote_residency = _RemoteCatalogResidency(
            max_entries=remote_resident_shard_entries,
            max_bytes=remote_resident_shard_bytes,
        )
        self.frozen_dir = frozen_dir
        self.scoped_frozen_dir = (
            scoped_frozen_dir(frozen_dir, self.catalog_identity) if frozen_dir is not None else None
        )
        self.lazy_html = lazy_html
        self.serve_mode = serve_mode
        self.include_private = include_private
        self._workers = resolve_workers(workers)
        self._federated_slug_urls: dict[str, str] = {}
        self._source_sync_status: dict[str, dict[str, Any]] = {}
        self._shard_status: dict[str, dict[str, Any]] = {}
        self._discovered_editions: dict[str, tuple[GitEditionSnapshot, ...]] = {}
        self.mounts = (
            mounts if serve_mode == ServeMode.PREVIEW else self._sync_mount_sources(mounts)
        )
        self._mount_by_id = {mount.id: mount for mount in self.mounts}
        if serve_mode == ServeMode.PREVIEW and self.scoped_frozen_dir is not None:
            self._discovered_editions.update(_frozen_editions(self.scoped_frozen_dir, self.mounts))
        from furatena.catalog.edition_lifecycle import lifecycle_lookup

        self._edition_lifecycle = lifecycle_lookup(self.mounts, self._discovered_editions)
        self._html_cache: dict[str, str] = {}
        self._html_cache_lock = Lock()
        self._publication_lock = RLock()
        self._shards: dict[str, DocCatalog] = {}
        self._edition_shards: dict[str, dict[str, DocCatalog]] = {}
        self._edition_shards_lock = RLock()
        self._edition_projection_cache: EditionProjection | None = None
        self._edition_projection_lock = Lock()
        self._mount_for_url: list[tuple[str, MountConfig]] = []
        self._mount_for_route_segment: dict[str, MountConfig] = {}
        self._ambiguous_route_segments: set[str] = set()
        self._edges: list[EdgeRecord] | None = None
        self._namespaces: list[NamespaceRecord] | None = None
        self._query_graph_cache: dict[
            tuple[str, bool, AccessSubject | None], CatalogGraphRecord
        ] = {}
        self._query_graph_lock = Lock()
        self._generation = 0
        self._generation_lock = Lock()
        self._read_generation: _CatalogReadGeneration | None = None
        self._remote_mount_generations: dict[str, str] = {}
        self._federated_backlinks: dict[str, list[dict[str, str]]] = {}
        self._translation_index: dict[str, dict[str, str]] | None = None
        self._inventory_store = None
        self._watcher: SourceWatcher | None = None
        from furatena.catalog.rewrites import load_rewrite_table, set_rewrite_table

        set_rewrite_table(load_rewrite_table(rewrites_path))
        self._federated_slug_urls = self._prescan_federated_slugs()
        self._load_shards()
        self._finalize_federated()
        if auto_reload:
            self._start_watcher()

    def _sync_mount_sources(self, mounts: tuple[MountConfig, ...]) -> tuple[MountConfig, ...]:
        resolved: list[MountConfig] = []
        for mount in mounts:
            if mount.source.git is None:
                self._discovered_editions[mount.id] = ()
                self._record_source_sync(
                    mount,
                    "ok",
                    stage="source",
                    provider=mount.source.provider,
                )
                resolved.append(mount)
                continue
            can_attempt, blocked_reason = self.source_sync_state.can_attempt(mount.id)
            if not can_attempt:
                sync_state = self.source_sync_state.load(mount.id) or {}
                self._discovered_editions[mount.id] = _state_editions(sync_state)
                self._record_source_sync(
                    mount,
                    "quarantined" if blocked_reason == "quarantined" else "retry_wait",
                    stage="sync",
                    provider="git",
                    sync_state=sync_state,
                )
                resolved.append(_last_known_good_mount(mount, sync_state))
                continue
            self.source_sync_state.begin(
                mount.id,
                provider="git",
                source_repo=mount.source.git.repo,
                requested_ref=mount.source.git.ref,
            )
            previous_state = self.source_sync_state.load(mount.id) or {}
            previous_editions = _state_editions(previous_state)
            try:
                sync = sync_git_source(
                    mount.source.git,
                    mount_id=mount.id,
                    app_root=self.app_root,
                    cache_namespace=self.identity_cache_namespace(),
                    validate=lambda content_root, candidate=mount: self._validate_sync_candidate(
                        candidate,
                        content_root,
                    ),
                    edition_policy=mount.editions,
                    previous_editions=previous_editions,
                )
            except Exception as exc:
                sync_state = self.source_sync_state.record_failure(
                    mount.id,
                    _exception_record(exc),
                )
                self._discovered_editions[mount.id] = _state_editions(sync_state)
                self._record_source_sync(
                    mount,
                    "failed",
                    stage="sync",
                    provider="git",
                    error=exc,
                    sync_state=sync_state,
                )
                resolved.append(_last_known_good_mount(mount, sync_state))
                continue
            sync_state = self.source_sync_state.reconcile(
                mount.id,
                provider="git",
                resolved_ref=sync.resolved_ref,
                content_root=sync.content_root,
                source_url=sync.source_url,
                editions=sync.editions,
                edition_policy=mount.editions,
            )
            self._discovered_editions[mount.id] = sync.editions
            self._record_source_sync(
                mount,
                "ok",
                stage="sync",
                provider="git",
                resolved_ref=sync.resolved_ref,
                source_url=sync.source_url,
                sync_state=sync_state,
            )
            resolved.append(
                replace(
                    mount,
                    content_root=sync.content_root,
                    source=mount.source.with_git_sync_state(
                        resolved_ref=sync.resolved_ref,
                        source_url=sync.source_url,
                    ),
                )
            )
        return tuple(resolved)

    def _validate_sync_candidate(self, mount: MountConfig, content_root: Path) -> None:
        """Build a staged shard completely before promoting a synced snapshot."""
        self._build_live_shard(replace(mount, content_root=content_root), cached_autodoc=None)

    def _record_source_sync(
        self,
        mount: MountConfig,
        status: str,
        *,
        stage: str,
        provider: str,
        resolved_ref: str | None = None,
        source_url: str | None = None,
        error: Exception | None = None,
        sync_state: dict[str, Any] | None = None,
    ) -> None:
        git = mount.source.git
        payload: dict[str, Any] = {
            "status": status,
            "stage": stage,
            "provider": provider,
            "source_repo": git.repo if git is not None else None,
            "source_ref": resolved_ref or (git.ref if git is not None else None),
            "source_url": source_url or (git.source_url if git is not None else None),
            "sync_state": sync_state,
            "editions": [
                edition.to_dict() for edition in self._discovered_editions.get(mount.id, ())
            ],
            "repair_actions": list((sync_state or {}).get("repair_actions") or []),
        }
        if error is not None:
            payload["error"] = _exception_record(error)
        self._source_sync_status[mount.id] = payload

    def _record_shard_status(
        self,
        mount: MountConfig,
        status: str,
        *,
        stage: str,
        loaded_from: str | None = None,
        loaded: bool | None = None,
        error: Exception | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "stage": stage,
            "loaded": status == "ok" if loaded is None else loaded,
            "loaded_from": loaded_from,
        }
        if error is not None:
            payload["error"] = _exception_record(error)
        self._shard_status[mount.id] = payload

    def _build_live_shard(
        self,
        mount: MountConfig,
        *,
        cached_autodoc: list[DocNode] | None,
        edition: str | None = None,
    ) -> DocCatalog:
        shard = DocCatalog(
            mount.content_root,
            auto_reload=self.auto_reload,
            autodoc_config=self.autodoc_config if mount.default else None,
            repo_root=self.repo_root,
            channel=self.active_channel,
            autodoc=self.autodoc_enabled and mount.default,
            mount=mount.id,
            url_prefix=mount.url_prefix,
            edition=edition or self.active_channel,
            cached_autodoc_nodes=cached_autodoc if mount.default else None,
            source_config=mount.source,
            federated_slug_urls=self._federated_slug_urls,
            workers=self._workers,
            i18n_config=self.i18n_config,
            catalog_nav=self.catalog_nav if mount.default else None,
            include_private=self.include_private,
            identity_meta=self.catalog_identity,
        )
        if cached_autodoc is not None and mount.default and self.frozen_dir is not None:
            assert self.frozen_root is not None
            shard._frozen_shard_dir = self.frozen_root / "mounts" / mount.id
        shard._federated_slug_urls = self._federated_slug_urls
        return shard

    def _build_remote_shard(self, mount: MountConfig, *, edition: str = "latest") -> DocCatalog:
        if self.remote_shards is None:
            from furatena.catalog.exceptions import CatalogConfigError

            raise CatalogConfigError(
                f"remote shard mount {mount.id!r} requires an injected RemoteShardMountRegistry"
            )
        remote_registry = self.remote_shards
        remote = remote_registry.shard(mount.id, edition)
        key = f"{remote.identity}:{remote.fingerprint}:route={edition}"

        def compose() -> DocCatalog:
            shard = DocCatalog.from_remote(
                remote.catalog,
                mount=mount.id,
                edition=edition,
                presentation_loader=lambda node_id: remote_registry.fetch_presentation_from(
                    remote, node_id
                ),
                content_root=mount.content_root,
                catalog_nav=self.catalog_nav if mount.default else None,
            )
            shard._federated_slug_urls = self._federated_slug_urls
            return shard

        shard = self._remote_residency.load(
            key,
            mount=mount.id,
            loader=compose,
        )
        shard._renderer.attach_reference_context(
            catalog=self,
            inventory_store=self._inventory_store,
        )
        return shard

    def _load_shards(self) -> None:
        from furatena.catalog.autodoc_cache import load_cached_autodoc_nodes

        self._shards = {}
        self._mount_for_url = []
        self._mount_for_route_segment = {}
        self._ambiguous_route_segments = set()
        use_frozen = (
            self.serve_mode in {ServeMode.HYBRID, ServeMode.PREVIEW}
            and self.frozen_root is not None
        )
        cached_autodoc = None
        if use_frozen and self.autodoc_enabled:
            default_mount_id = next(
                (mount.id for mount in self.mounts if mount.default), self.mounts[0].id
            )
            cached_autodoc = load_cached_autodoc_nodes(
                config_path=self.autodoc_config,
                repo_root=self.repo_root,
                frozen_dir=self.frozen_root,
                mount=default_mount_id,
            )

        live_mount_jobs: list[tuple[MountConfig, Path | None]] = []
        for mount in self.mounts:
            if mount.source.provider == "remote-shard":
                if self.remote_shards is not None and mount.id in self.remote_shards.mounts():
                    self._remote_mount_generations[mount.id] = self.remote_shards.generation(
                        mount.id
                    ).generation_id
                    self._record_shard_status(
                        mount,
                        "ok",
                        stage="remote_deferred",
                        loaded_from="remote-shard",
                        loaded=False,
                    )
                else:
                    self._record_shard_status(
                        mount,
                        "failed",
                        stage="remote_deferred",
                        loaded=False,
                        error=RuntimeError(
                            f"remote shard mount {mount.id!r} has no verified generation"
                        ),
                    )
                continue
            shard_frozen = None
            if use_frozen and self.frozen_root is not None:
                candidate = self.frozen_root / "mounts" / mount.id
                if (candidate / "catalog.json").is_file():
                    shard_frozen = candidate
            if shard_frozen is not None and self.serve_mode == ServeMode.HYBRID:
                try:
                    shard = DocCatalog.from_frozen(
                        shard_frozen,
                        content_root=mount.content_root,
                        mount=mount.id,
                        lazy_html=self.lazy_html,
                        catalog_nav=self.catalog_nav if mount.default else None,
                    )
                except Exception as exc:
                    self._record_shard_status(mount, "failed", stage="frozen_load", error=exc)
                    live_mount_jobs.append((mount, shard_frozen))
                    continue
                try:
                    shard.enable_author_overlay(
                        autodoc_config=self.autodoc_config if mount.default else None,
                        repo_root=self.repo_root,
                        cached_autodoc_nodes=cached_autodoc if mount.default else None,
                    )
                except Exception as exc:
                    self._record_shard_status(
                        mount,
                        "ok",
                        stage="hybrid_overlay",
                        loaded_from="frozen",
                        error=exc,
                    )
                else:
                    self._record_shard_status(
                        mount,
                        "ok",
                        stage="hybrid_overlay",
                        loaded_from="frozen+overlay",
                    )
                shard._federated_slug_urls = self._federated_slug_urls
                self._shards[mount.id] = shard
            elif shard_frozen is not None:
                try:
                    shard = DocCatalog.from_frozen(
                        shard_frozen,
                        content_root=mount.content_root,
                        mount=mount.id,
                        lazy_html=self.lazy_html,
                        catalog_nav=self.catalog_nav if mount.default else None,
                    )
                except Exception as exc:
                    self._record_shard_status(mount, "failed", stage="frozen_load", error=exc)
                    continue
                shard._federated_slug_urls = self._federated_slug_urls
                self._shards[mount.id] = shard
                self._record_shard_status(mount, "ok", stage="frozen_load", loaded_from="frozen")
            else:
                live_mount_jobs.append((mount, shard_frozen))

        if live_mount_jobs:
            if self._workers > 1 and len(live_mount_jobs) > 1:
                with ThreadPoolExecutor(max_workers=self._workers) as pool:
                    futures = {
                        pool.submit(
                            self._build_live_shard,
                            mount,
                            cached_autodoc=cached_autodoc,
                        ): mount
                        for mount, shard_frozen in live_mount_jobs
                    }
                    for future in as_completed(futures):
                        mount = futures[future]
                        try:
                            self._shards[mount.id] = future.result()
                        except Exception as exc:
                            self._record_shard_status(
                                mount, "failed", stage="live_index", error=exc
                            )
                        else:
                            self._record_shard_status(
                                mount, "ok", stage="live_index", loaded_from="live"
                            )
            else:
                for mount, _shard_frozen in live_mount_jobs:
                    try:
                        self._shards[mount.id] = self._build_live_shard(
                            mount,
                            cached_autodoc=cached_autodoc,
                        )
                    except Exception as exc:
                        self._record_shard_status(mount, "failed", stage="live_index", error=exc)
                    else:
                        self._record_shard_status(
                            mount, "ok", stage="live_index", loaded_from="live"
                        )

        for mount in self.mounts:
            prefix = mount.url_prefix or "/"
            self._mount_for_url.append((prefix, mount))
            parts = prefix.strip("/").split("/")
            if len(parts) == 1 and parts[0]:
                segment = parts[0]
                existing = self._mount_for_route_segment.get(segment)
                if existing is None and segment not in self._ambiguous_route_segments:
                    self._mount_for_route_segment[segment] = mount
                elif existing is not mount:
                    self._mount_for_route_segment.pop(segment, None)
                    self._ambiguous_route_segments.add(segment)
        self._mount_for_url.sort(key=lambda item: len(item[0]), reverse=True)
        self._edges = None
        self._namespaces = None
        self._query_graph_cache.clear()

    def _prescan_federated_slugs(self) -> dict[str, str]:
        from furatena.catalog.sources import FilesystemScanner

        urls: dict[str, str] = {}
        for mount in self.mounts:
            if mount.source.provider == "remote-shard":
                continue
            if not mount.content_root.is_dir():
                continue
            scanner = FilesystemScanner(mount.source)
            for page in scanner.scan(
                mount.content_root,
                url_prefix=mount.url_prefix,
                include_private=self.include_private,
            ):
                urls[f"{mount.id}:{page.slug}"] = page.url
                if mount.default:
                    urls.setdefault(page.slug, page.url)
        return urls

    def source_health(self, *, mount: str | None = None) -> dict[str, Any]:
        """Per-mount source/index health for admin UI, CI, and MCP callers."""
        mount_filter = mount.strip() if mount else None
        mounts: list[dict[str, Any]] = []
        residency = self.remote_residency_status()
        if mount_filter:
            residency = dict(residency)
            residency["objects"] = dict(residency["objects"])
            residency["objects"]["shards"] = [
                item for item in residency["objects"]["shards"] if item["mount"] == mount_filter
            ]
        for item in self.mounts:
            if mount_filter and item.id != mount_filter:
                continue
            extensions = tuple(sorted(item.source.tracked_extensions()))
            shard = self._shards.get(item.id)
            sync = self._source_sync_status.get(
                item.id,
                {
                    "status": "ok",
                    "stage": "source",
                    "provider": item.source.provider,
                    "source_repo": None,
                    "source_ref": None,
                    "source_url": None,
                },
            )
            index = self._shard_status.get(
                item.id,
                {
                    "status": "ok" if shard is not None else "failed",
                    "stage": "index",
                    "loaded": shard is not None,
                    "loaded_from": "live" if shard is not None else None,
                },
            )
            loaded = bool(index.get("loaded"))
            remotely_available = (
                item.source.provider == "remote-shard"
                and self.remote_shards is not None
                and item.id in self.remote_shards.mounts()
            )
            available = shard is not None or remotely_available
            has_error = "error" in sync or "error" in index
            if not available:
                status = "unavailable"
            elif has_error or sync.get("status") != "ok" or index.get("status") == "failed":
                status = "degraded"
            else:
                status = "healthy"
            mounts.append(
                {
                    "id": item.id,
                    "label": item.label,
                    "status": status,
                    "default": item.default,
                    "url_prefix": item.url_prefix,
                    "provider": sync.get("provider") or item.source.provider,
                    "content_root": str(item.content_root),
                    "exists": item.content_root.is_dir(),
                    "loaded": loaded,
                    "loaded_from": index.get("loaded_from"),
                    "tracked_extensions": list(extensions),
                    "file_count": _count_source_files(item.content_root, extensions),
                    "page_count": len(shard.nodes) if shard is not None else 0,
                    "residency": {
                        "shards": [
                            entry
                            for entry in residency["objects"]["shards"]
                            if entry["mount"] == item.id
                        ],
                    }
                    if item.source.provider == "remote-shard"
                    else None,
                    "channels": [
                        {"id": ch.id, "label": ch.label, "default": ch.default}
                        for ch in self.channels_for(item.id)
                    ],
                    "editions": [
                        edition.to_dict() for edition in self.discovered_editions_for(item.id)
                    ],
                    "source": {
                        "status": sync.get("status"),
                        "stage": sync.get("stage"),
                        "repo": sync.get("source_repo"),
                        "ref": sync.get("source_ref"),
                        "url": sync.get("source_url"),
                        "sync_state": sync.get("sync_state"),
                        "repair_actions": sync.get("repair_actions", []),
                        **({"error": sync["error"]} if "error" in sync else {}),
                    },
                    "index": {
                        "status": index.get("status"),
                        "stage": index.get("stage"),
                        "loaded": loaded,
                        "loaded_from": index.get("loaded_from"),
                        **({"error": index["error"]} if "error" in index else {}),
                    },
                }
            )
        return {
            "schema_version": 1,
            "ok": all(item["status"] == "healthy" for item in mounts),
            "mount_count": len(mounts),
            "active_channel": self.active_channel,
            "serve_mode": self.serve_mode.value,
            "mounts": mounts,
            "remote_residency": residency,
        }

    def remote_residency_status(self) -> dict[str, Any]:
        """Bounded-cardinality object and composed-graph residency diagnostics."""
        objects = (
            self.remote_shards.residency_status()
            if self.remote_shards is not None
            else {
                "schema_version": 1,
                "resident_entries": 0,
                "resident_bytes": 0,
                "max_resident_entries": 0,
                "max_resident_bytes": 0,
                "in_flight": 0,
                "hot_hits": 0,
                "warm_loads": 0,
                "cold_loads": 0,
                "evictions": 0,
                "coalesced_loads": 0,
                "load_failures": 0,
                "shards": [],
            }
        )
        return {
            "schema_version": 1,
            "counter_scope": "process_lifetime",
            "refresh_behavior": "counters remain monotonic; stale identity residency is pruned",
            "objects": objects,
            "composed": self._remote_residency.status(),
        }

    def mount_access_policy(self, mount_id: str) -> AccessPolicy | None:
        """Return the access policy declared for a mount."""
        mount = next((item for item in self.mounts if item.id == mount_id), None)
        return mount.access if mount is not None else None

    def node_access_policy(self, node: DocNode) -> AccessPolicy:
        """Return the page-level access policy for a catalog node."""
        return AccessPolicy.from_page_meta(getattr(node, "meta", {}) or {})

    def can_access_mount(
        self,
        mount: MountConfig | str,
        subject: AccessSubject | None = None,
        *,
        permission: AccessPermission | str = AccessPermission.READ,
    ) -> bool:
        """Return whether a subject can use a mount-level surface."""
        return ACCESS_EVALUATOR.mount_decision(
            self,
            mount,
            subject,
            permission=permission,
        ).allowed

    def access_decision_for_node(
        self,
        node: DocNode,
        subject: AccessSubject | None = None,
        *,
        permission: AccessPermission | str = AccessPermission.READ,
    ) -> AccessDecision:
        """Evaluate mount and page policy for a catalog node."""
        return ACCESS_EVALUATOR.node_decision(
            self,
            node,
            subject,
            permission=permission,
        )

    def can_access_node(
        self,
        node: DocNode,
        subject: AccessSubject | None = None,
        *,
        permission: AccessPermission | str = AccessPermission.READ,
    ) -> bool:
        """Return whether a subject can use a page-level surface."""
        return self.access_decision_for_node(node, subject, permission=permission).allowed

    @property
    def frozen_root(self) -> Path | None:
        """Identity-scoped frozen root, or None when no frozen dir is configured."""
        return self.scoped_frozen_dir

    @property
    def active_channel(self) -> str:
        """Edition selected for the current request context."""
        return self._edition_context.get()

    @contextmanager
    def use_edition(self, edition: str) -> Iterator[None]:
        """Scope catalog reads to one edition without cross-request shared mutation."""
        token = self._edition_context.set(edition)
        try:
            with self.read_snapshot():
                yield
        finally:
            self._edition_context.reset(token)

    def _pin_read_generation(self) -> Any:
        pinned = self._read_generation_context.get()
        if pinned is not None and pinned.edition == self.active_channel:
            return self._read_generation_context.set(pinned)
        with self._publication_lock:
            generation = self._read_generation
            if generation is None:
                raise RuntimeError("The catalog read generation has not been initialized yet.")
            if generation.edition != self.active_channel:
                with self._edition_shards_lock:
                    shards = self._edition_shards.get(self.active_channel)
                    if shards is None:
                        shards = self._load_edition_shards(
                            self.active_channel,
                            include_remote=False,
                        )
                        self._edition_shards[self.active_channel] = shards
                nodes = tuple(node for shard in shards.values() for node in shard.nodes)
                generation = _CatalogReadGeneration(
                    id=generation.id,
                    edition=self.active_channel,
                    shards=MappingProxyType(dict(shards)),
                    backlinks=_freeze_backlinks(
                        build_federated_backlinks(list(nodes), catalog=self)
                    ),
                    translation_index=_freeze_translation_index(build_translation_index(nodes)),
                    inventory_store=self._inventory_store,
                    edges=None,
                    namespaces=None,
                )
            return self._read_generation_context.set(generation)

    def read_snapshot(self) -> _CatalogReadSnapshotContext:
        """Pin one immutable composed shard generation for a complete request read."""
        return _CatalogReadSnapshotContext(self)

    def edition_ids_for(self, mount_id: str) -> tuple[str, ...]:
        """Public edition ids discovered for one mount, including ``latest``."""
        mount = next((item for item in self.mounts if item.id == mount_id), None)
        if mount is not None and mount.source.provider == "remote-shard":
            if self.remote_shards is None:
                return ()
            try:
                generation = self.remote_shards.generation(mount_id)
            except Exception:
                return ()
            editions = tuple(dict.fromkeys(shard.edition for shard in generation.shards.values()))
            return tuple(dict.fromkeys(("latest", *editions)))
        ids = tuple(snapshot.id for snapshot in self.discovered_editions_for(mount_id))
        return ids or ("latest",)

    def edition_aliases_for(self, mount_id: str) -> dict[str, str]:
        """Configured alias-to-edition mapping for one mount."""
        mount = next((item for item in self.mounts if item.id == mount_id), None)
        if mount is None or mount.editions is None:
            return {}
        return dict(mount.editions.aliases)

    def has_edition(self, mount_id: str, edition: str) -> bool:
        return edition in self.edition_ids_for(mount_id)

    def edition_route_segments(self) -> tuple[str, ...]:
        """All concrete edition and alias segments needed by the live router."""
        from furatena.catalog.edition_routing import edition_segment

        segments: set[str] = set()
        for mount in self.mounts:
            segments.update(self.edition_aliases_for(mount.id))
            segments.update(
                edition_segment(edition)
                for edition in self.edition_ids_for(mount.id)
                if edition != "latest"
            )
        return tuple(sorted(segment for segment in segments if segment))

    def _active_shards(self) -> Mapping[str, DocCatalog]:
        with self._publication_lock:
            edition = self.active_channel
            pinned = self._read_generation_context.get()
            if pinned is not None and pinned.edition == edition:
                shards = dict(pinned.shards)
                for mount in self.mounts:
                    if mount.id not in shards:
                        shard = self._active_shard(mount.id)
                        if shard is not None:
                            shards[mount.id] = shard
                return shards
            if edition == self._default_channel or edition == "latest":
                shards = dict(self._shards)
                for mount in self.mounts:
                    if mount.source.provider == "remote-shard":
                        shard = self._active_shard(mount.id)
                        if shard is not None:
                            shards[mount.id] = shard
                return shards
            with self._edition_shards_lock:
                cached = self._edition_shards.get(edition)
                if cached is not None:
                    shards = dict(cached)
                    for mount in self.mounts:
                        if mount.source.provider == "remote-shard":
                            shard = self._active_shard(mount.id)
                            if shard is not None:
                                shards[mount.id] = shard
                    return shards
                shards = self._load_edition_shards(edition)
                self._edition_shards[edition] = shards
                return shards

    def _active_shard(self, mount_id: str) -> DocCatalog | None:
        """Resolve mount and edition before materializing one catalog shard."""
        mount = self._mount_by_id.get(mount_id)
        if mount is None:
            return None
        edition = self.active_channel
        pinned = self._read_generation_context.get()
        if pinned is not None and pinned.edition == edition:
            shard = pinned.shards.get(mount_id)
            if shard is not None:
                return shard
        if mount.source.provider == "remote-shard":
            try:
                shard = self._build_remote_shard(mount, edition=edition)
            except Exception as exc:
                self._record_shard_status(
                    mount,
                    "failed",
                    stage="remote_resident_load",
                    loaded=False,
                    error=exc,
                )
                return None
            self._record_shard_status(
                mount,
                "ok",
                stage="remote_resident",
                loaded_from="remote-shard",
                loaded=True,
            )
            self._pin_remote_shard_for_request(mount_id, shard)
            return shard
        if edition == self._default_channel or edition == "latest":
            return self._shards.get(mount_id)
        with self._edition_shards_lock:
            cached = self._edition_shards.get(edition)
            if cached is None:
                cached = self._load_edition_shards(edition, include_remote=False)
                self._edition_shards[edition] = cached
            return cached.get(mount_id)

    def _pin_remote_shard_for_request(self, mount_id: str, shard: DocCatalog) -> None:
        """Add one lazy remote shard to the current immutable request generation."""
        pinned = self._read_generation_context.get()
        if (
            pinned is None
            or pinned.edition != self.active_channel
            or pinned.shards.get(mount_id) is shard
        ):
            return
        shards = dict(pinned.shards)
        shards[mount_id] = shard
        self._read_generation_context.set(
            replace(
                pinned,
                shards=MappingProxyType(shards),
                edges=None,
                namespaces=None,
            )
        )

    def _load_edition_shards(
        self, edition: str, *, include_remote: bool = True
    ) -> dict[str, DocCatalog]:
        from furatena.catalog.edition_routing import edition_path

        shards: dict[str, DocCatalog] = {}
        for mount in self.mounts:
            if mount.source.provider == "remote-shard":
                if not include_remote:
                    continue
                try:
                    shard = self._build_remote_shard(mount, edition=edition)
                except Exception as exc:
                    self._record_shard_status(
                        mount, "failed", stage="remote_edition_compose", error=exc
                    )
                    continue
                shard._renderer.attach_reference_context(
                    catalog=self,
                    inventory_store=self._inventory_store,
                )
                shards[mount.id] = shard
                continue
            snapshot = next(
                (item for item in self.discovered_editions_for(mount.id) if item.id == edition),
                None,
            )
            if snapshot is None:
                continue
            candidate = (
                self.frozen_root / "mounts" / mount.id / edition
                if self.frozen_root is not None
                else None
            )
            if candidate is not None and (candidate / "catalog.json").is_file():
                shard = DocCatalog.from_frozen(
                    candidate,
                    content_root=snapshot.content_root,
                    mount=mount.id,
                    edition=edition,
                    lazy_html=False,
                    catalog_nav=self.catalog_nav if mount.default else None,
                )
            else:
                git = mount.source.git
                source = mount.source.with_git_sync_state(
                    resolved_ref=snapshot.resolved_ref,
                    source_url=git.source_url if git is not None else None,
                )
                edition_mount = replace(
                    mount,
                    content_root=snapshot.content_root,
                    url_prefix=edition_path(mount.url_prefix or "/", edition),
                    source=source,
                )
                shard = self._build_live_shard(
                    edition_mount,
                    cached_autodoc=None,
                    edition=edition,
                )
            shard._renderer.attach_reference_context(
                catalog=self,
                inventory_store=self._inventory_store,
            )
            shards[mount.id] = shard
        return shards

    def refresh_remote_shards(self) -> RemoteRefreshReport:
        """Refresh verified mounts, then atomically publish composed catalog shards."""
        if self.remote_shards is None:
            from furatena.catalog.exceptions import CatalogConfigError

            raise CatalogConfigError(
                "Remote shard refresh requires a configured RemoteShardMountRegistry instance."
            )
        report = self.remote_shards.refresh()
        remote_mounts = tuple(
            mount for mount in self.mounts if mount.source.provider == "remote-shard"
        )
        with self._publication_lock, self._edition_shards_lock:
            available_mounts = set(self.remote_shards.mounts())
            next_generations = {
                mount.id: self.remote_shards.generation(mount.id).generation_id
                for mount in remote_mounts
                if mount.id in available_mounts
            }
            changed_mounts = {
                mount.id
                for mount in remote_mounts
                if self._remote_mount_generations.get(mount.id) != next_generations.get(mount.id)
            }
            for mount in remote_mounts:
                if mount.id in changed_mounts:
                    self._remote_residency.discard_mount(mount.id)
                if mount.id in available_mounts:
                    if mount.id in changed_mounts:
                        self._record_shard_status(
                            mount,
                            "ok",
                            stage="remote_deferred",
                            loaded_from="remote-shard",
                            loaded=False,
                        )
                else:
                    self._record_shard_status(
                        mount,
                        "failed",
                        stage="remote_deferred",
                        loaded=False,
                        error=RuntimeError(
                            f"remote shard mount {mount.id!r} has no verified generation"
                        ),
                    )
            if not changed_mounts:
                return report
            # Publish the shard map and every derived graph/cache reference as
            # one lock-owned generation. Existing maps remain immutable.
            next_shards = {
                mount_id: shard
                for mount_id, shard in self._shards.items()
                if mount_id not in changed_mounts
            }
            self._shards = next_shards
            self._edition_shards = {
                edition: {
                    mount_id: shard
                    for mount_id, shard in shards.items()
                    if mount_id not in changed_mounts
                }
                for edition, shards in self._edition_shards.items()
            }
            self._remote_mount_generations = next_generations
            self._edges = None
            self._namespaces = None
            with self._query_graph_lock:
                self._query_graph_cache.clear()
            self._finalize_federated()
        return report

    def query_graph_snapshot(
        self,
        *,
        include_private: bool = False,
        subject: AccessSubject | None = None,
    ) -> CatalogGraphRecord:
        """Return one access-scoped graph serialization per catalog generation."""
        pinned = self._read_generation_context.get()
        if pinned is not None:
            from furatena.catalog.export import catalog_graph

            return catalog_graph(cast(Any, self), include_private=include_private, subject=subject)
        with self._publication_lock:
            key = (self.active_channel, include_private, subject)
            with self._query_graph_lock:
                cached = self._query_graph_cache.get(key)
                if cached is not None:
                    return cached
                from furatena.catalog.export import catalog_graph

                graph = catalog_graph(
                    cast(Any, self), include_private=include_private, subject=subject
                )
                self._query_graph_cache[key] = graph
                return graph

    @property
    def route_prefix(self) -> str:
        """Tenant/workspace/site route prefix for this registry."""
        return identity_route_prefix(self.catalog_identity)

    def identity_cache_namespace(self) -> str:
        """Stable cache namespace used for identity-scoped source caches."""
        if not self.route_prefix:
            return ""
        return "/".join(identity_namespace_parts(self.catalog_identity))

    def strip_identity_route(self, path: str) -> str:
        """Strip this registry's tenant route prefix from a request path."""
        return strip_identity_route(path, self.catalog_identity)

    def scoped_url(self, path: str) -> str:
        """Prefix a catalog URL with this registry's tenant route namespace."""
        return scope_url(path, self.catalog_identity)

    @staticmethod
    def _edition_matches(node: DocNode | None, edition: str | None) -> bool:
        if node is None:
            return False
        if not edition:
            return True
        return node.edition == edition

    def default_mount_sections(self) -> tuple[str, ...]:
        """Top-level URL segments owned by the default mount (for route registration)."""
        default_id = self.default_mount.id
        explicit_prefixes = tuple(
            mount.url_prefix.rstrip("/") for mount in self.mounts if mount.url_prefix
        )
        sections: set[str] = set()
        for node in self.nodes:
            if node.mount != default_id and any(
                node.url == prefix or node.url.startswith(f"{prefix}/")
                for prefix in explicit_prefixes
            ):
                continue
            parts = node.url.strip("/").split("/")
            if parts and parts[0]:
                sections.add(parts[0])
        return tuple(sorted(sections))

    def get_path(self, path: str) -> DocNode | None:
        """Resolve a request path, tolerating a missing trailing slash."""
        node = self.get(path)
        if node is None and path != "/" and not path.endswith("/"):
            node = self.get(f"{path}/")
        return node

    def resolve_link(
        self,
        target: str,
        *,
        source_mount: str | None = None,
        edition: str | None = None,
    ) -> DocNode | None:
        """Resolve an author-time slug/path target with mount-aware disambiguation."""
        from furatena.catalog.references.resolver import _split_qualified_target

        target = target.strip()
        if not target:
            return None

        if target.startswith("/"):
            node = self.get_path(target)
            return node if self._edition_matches(node, edition) else None

        mount_hint, edition_hint, slug = _split_qualified_target(target, self)
        effective_edition = edition or edition_hint

        if mount_hint and mount_hint in self._mount_by_id:
            return self._resolve_link_slug(slug, mount=mount_hint, edition=effective_edition)

        mounts_to_try: list[str] = []
        if source_mount and source_mount in self._mount_by_id:
            mounts_to_try.append(source_mount)
        default_id = self.default_mount.id
        if default_id not in mounts_to_try:
            mounts_to_try.append(default_id)

        for mount_id in mounts_to_try:
            node = self._resolve_link_slug(slug, mount=mount_id, edition=effective_edition)
            if node is not None:
                return node
        return None

    def _resolve_link_slug(
        self,
        slug: str,
        *,
        mount: str,
        edition: str | None,
    ) -> DocNode | None:
        from furatena.catalog.references.resolver import _slug_variants

        for variant in _slug_variants(slug):
            url = self._federated_slug_urls.get(f"{mount}:{variant}")
            if url:
                node = self.get(url)
                if self._edition_matches(node, edition):
                    return node
            node = self.get_by_slug(variant, mount=mount)
            if self._edition_matches(node, edition):
                return node
        return None

    def _finalize_federated(self) -> None:
        from furatena.catalog.inventories import build_inventory_store, load_inventories_config

        with self._publication_lock:
            # Remote identities stay descriptor-only until a request proves a
            # mount is relevant. Cross-shard derived state is reconciled by
            # the separate incremental-link work rather than defeating lazy
            # residency here.
            nodes = [node for shard in self._shards.values() for node in shard.nodes]
            self._federated_backlinks = build_federated_backlinks(nodes, catalog=self)
            self._translation_index = build_translation_index(tuple(nodes))
            specs, role_domains = load_inventories_config(self.inventories_path)
            self._inventory_store = build_inventory_store(
                specs,
                role_domains=role_domains,
                catalog_nodes=nodes,
                app_root=self.app_root,
            )
            for shard in self._shards.values():
                shard._renderer.attach_reference_context(
                    catalog=self,
                    inventory_store=self._inventory_store,
                )
            with self._generation_lock:
                self._generation += 1
                generation_id = self._generation
            self._read_generation = _CatalogReadGeneration(
                id=generation_id,
                edition=self._default_channel,
                shards=MappingProxyType(dict(self._shards)),
                backlinks=_freeze_backlinks(self._federated_backlinks),
                translation_index=_freeze_translation_index(self._translation_index or {}),
                inventory_store=self._inventory_store,
                edges=tuple(self._edges) if self._edges is not None else None,
                namespaces=tuple(self._namespaces) if self._namespaces is not None else None,
            )

    @property
    def generation(self) -> int:
        """Monotonic generation incremented after federated catalog publication."""
        pinned = self._read_generation_context.get()
        if pinned is not None:
            return pinned.id
        with self._generation_lock:
            return self._generation

    @property
    def inventory_store(self) -> InventoryStore | None:
        pinned = self._read_generation_context.get()
        return pinned.inventory_store if pinned is not None else self._inventory_store

    def inventories_metadata(self) -> list[dict[str, Any]]:
        store = self.inventory_store
        if store is None:
            return []
        return store.metadata()

    def _start_watcher(self) -> None:
        roots = tuple(
            mount.content_root
            for mount in self.mounts
            if mount.source.provider != "remote-shard" and mount.content_root.is_dir()
        )
        if not roots:
            return
        extensions: set[str] = set()
        for mount in self.mounts:
            if mount.source.provider == "remote-shard":
                continue
            extensions.update(mount.source.tracked_extensions())
        self._watcher = SourceWatcher(roots, extensions=frozenset(extensions))
        self._watcher.start()
        for shard in self._shards.values():
            shard.attach_watcher(self._watcher)

    def refresh_if_stale(self) -> bool:
        changed = False
        for shard in self._shards.values():
            if shard.refresh_if_stale():
                changed = True
        if changed:
            self._edges = None
            self._namespaces = None
            with self._edition_projection_lock:
                self._edition_projection_cache = None
            with self._query_graph_lock:
                self._query_graph_cache.clear()
            self._finalize_federated()
        return changed

    def invalidation_hints(self, slug: str) -> tuple[str, ...]:
        """htmx swap targets for the shard that owns ``slug``."""
        slug = slug.strip("/")
        node = self.get_by_slug(slug)
        if node is not None:
            return self._shard_for_node(node).invalidation_hints(slug)
        for shard in self._shards.values():
            hints = shard.invalidation_hints(slug)
            if hints:
                return hints
        return ()

    def clear_invalidation_hints(self, slug: str) -> None:
        """Clear pending reload hints after a selective author refresh."""
        slug = slug.strip("/")
        node = self.get_by_slug(slug)
        if node is not None:
            self._shard_for_node(node).clear_invalidation_hints(slug)
            return
        for shard in self._shards.values():
            shard.clear_invalidation_hints(slug)

    def author_stale_entries(self, slug: str | None = None) -> list[dict[str, object]]:
        """Slugs with pending invalidation hints for author-mode polling."""
        normalized = slug.strip("/") if slug else None
        entries: list[dict[str, object]] = []
        for mount in self.mounts:
            shard = self._shards.get(mount.id)
            if shard is None:
                continue
            for entry_slug, hints in shard.stale_invalidation_entries():
                if normalized is not None and entry_slug != normalized:
                    continue
                entries.append(
                    {
                        "slug": entry_slug,
                        "mount": mount.id,
                        "hints": list(hints),
                    }
                )
        return entries

    def _resolve_mount(self, url: str) -> MountConfig:
        url = self.strip_identity_route(url)
        if self.active_channel != "latest":
            from furatena.catalog.edition_routing import edition_segment, strip_edition_path

            url = strip_edition_path(url, edition_segment(self.active_channel))
        route_segment = url.strip("/").split("/", 1)[0]
        direct = self._mount_for_route_segment.get(route_segment)
        if direct is not None:
            return direct
        normalized = url if url.endswith("/") or url == "/" else f"{url}/"
        for prefix, mount in self._mount_for_url:
            if prefix == "/":
                if mount.default:
                    return mount
                continue
            if normalized == prefix or normalized.startswith(prefix):
                return mount
        default = next((m for m in self.mounts if m.default), self.mounts[0])
        return default

    def _shard_for_node(self, node: DocNode) -> DocCatalog:
        shard = self._active_shard(node.mount)
        if shard is None:
            raise KeyError(f"Catalog shard {node.mount!r} is unavailable.")
        return shard

    @property
    def default_mount(self) -> MountConfig:
        return next((m for m in self.mounts if m.default), self.mounts[0])

    @property
    def channels(self) -> tuple[DocChannel, ...]:
        return self.channels_for(self.default_mount.id)

    def channels_for(self, mount_id: str | None = None) -> tuple[DocChannel, ...]:
        """Release/version channels for one mount shard."""
        target_mount = mount_id or self.default_mount.id
        return tuple(
            DocChannel(
                id=edition,
                label="Latest" if edition == "latest" else f"v{edition}",
                default=edition == "latest",
            )
            for edition in self.edition_ids_for(target_mount)
        )

    def discovered_editions_for(self, mount_id: str) -> tuple[GitEditionSnapshot, ...]:
        """Return source-sync edition provenance without expanding mount config."""
        return self._discovered_editions.get(mount_id, ())

    def edition_lifecycle_for(self, mount_id: str, edition: str | None = None) -> EditionLifecycle:
        """Return lifecycle facts; unknown namespaces fail closed."""
        edition_id = (edition or self.active_channel).strip() or "latest"
        try:
            return self._edition_lifecycle[(mount_id, edition_id)]
        except KeyError as exc:
            raise KeyError(
                f"Unknown edition lifecycle namespace {mount_id}:{edition_id}; "
                "choose a discovered edition."
            ) from exc

    def edition_status_for(self, mount_id: str, edition: str | None = None) -> str:
        return self.edition_lifecycle_for(mount_id, edition).status

    @property
    def nodes(self) -> tuple[DocNode, ...]:
        items: list[DocNode] = []
        for shard in self._active_shards().values():
            items.extend(shard.nodes)
        return tuple(items)

    def get(self, url: str) -> DocNode | None:
        url = self.strip_identity_route(url)
        mount = self._resolve_mount(url)
        shard = self._active_shard(mount.id)
        return shard.get(url) if shard is not None else None

    def get_by_slug(self, slug: str, *, mount: str | None = None) -> DocNode | None:
        if mount is not None:
            shard = self._active_shard(mount)
            if shard is None:
                return None
            return shard.get_by_slug(slug)
        if len(self.mounts) == 1:
            shard = self._active_shard(self.mounts[0].id)
            return shard.get_by_slug(slug) if shard is not None else None
        default = self._active_shard(self.default_mount.id)
        return default.get_by_slug(slug) if default is not None else None

    def get_by_node_id(self, node_id: str) -> DocNode | None:
        mount, edition, slug = node_id.split(":", 2)
        slug = "" if slug == "index" else slug
        with self.use_edition(edition):
            return self.get_by_slug(slug, mount=mount)

    @property
    def translation_index(self) -> dict[str, dict[str, str]]:
        pinned = self._read_generation_context.get()
        if pinned is not None:
            return {key: dict(value) for key, value in pinned.translation_index.items()}
        if self.active_channel != "latest":
            return build_translation_index(self.nodes)
        if self._translation_index is None:
            self._translation_index = build_translation_index(self.nodes)
        return self._translation_index

    def doc_nodes(self, *, lang: str | None = None) -> list[DocNode]:
        items: list[DocNode] = []
        for shard in self._active_shards().values():
            items.extend(shard.doc_nodes(lang=lang))
        return sorted(items, key=lambda n: (n.mount, n.section, n.weight, n.title))

    def all_doc_nodes(self) -> list[DocNode]:
        """Doc nodes across every configured locale."""
        if not self.i18n_config.enabled:
            return self.doc_nodes()
        items: list[DocNode] = []
        for lang in self.i18n_config.language_codes():
            items.extend(self.doc_nodes(lang=lang))
        return sorted(items, key=lambda n: (n.lang, n.mount, n.section, n.weight, n.title))

    def portal_mounts(self) -> list[dict[str, Any]]:
        """Mount cards for the federated portal page."""
        cards: list[dict[str, Any]] = []
        shards = self._active_shards()
        for mount in self.mounts:
            shard = shards.get(mount.id)
            if shard is None:
                continue
            home = shard.get(mount.url_prefix or "/")
            cards.append(
                {
                    "id": mount.id,
                    "label": mount.label,
                    "href": home.url if home is not None else mount.url_prefix or "/",
                    "page_count": len(shard.nodes),
                    "default": mount.default,
                }
            )
        return cards

    def body_html(self, node: DocNode) -> str:
        if node.body_html:
            return node.body_html
        shard = self._shard_for_node(node)
        if shard._remote_presentation_cache is not None:
            return shard.resolve_body_html(node)
        with self._html_cache_lock:
            cached = self._html_cache.get(node.node_id)
        if cached is not None:
            return cached
        html = shard.resolve_body_html(node)
        with self._html_cache_lock:
            self._html_cache[node.node_id] = html
        return html

    def backlinks_for(self, node: DocNode) -> list[dict[str, str]]:
        pinned = self._read_generation_context.get()
        if pinned is not None:
            key = normalize_internal_url(node.url) or node.url
            refs = pinned.backlinks.get(key)
            if refs is not None:
                return [dict(ref) for ref in refs]
            return self._shard_for_node(node).backlinks_for(node)
        if self.active_channel != "latest":
            backlinks = build_federated_backlinks(list(self.nodes), catalog=self)
            key = normalize_internal_url(node.url) or node.url
            return backlinks.get(key, [])
        if self._federated_backlinks:
            key = normalize_internal_url(node.url) or node.url
            return self._federated_backlinks.get(key, [])
        return self._shard_for_node(node).backlinks_for(node)

    def trail(self, node: DocNode) -> list[dict[str, str]]:
        crumbs = self._shard_for_node(node).trail(node)
        if node.mount == self.default_mount.id or len(self.mounts) == 1:
            return crumbs
        mount = next(m for m in self.mounts if m.id == node.mount)
        portal = [{"label": "Portal", "href": "/portal/"}]
        if crumbs and crumbs[0]["href"] == "/":
            return [
                *portal,
                {"label": mount.label, "href": mount.url_prefix or crumbs[0]["href"]},
                *crumbs[1:],
            ]
        return [*portal, *crumbs]

    def prev_next(self, node: DocNode) -> tuple[DocNode | None, DocNode | None]:
        return self._shard_for_node(node).prev_next(node)

    def doc_nodes_for(self, node: DocNode) -> list[DocNode]:
        """Navigation/search scope for the node's locale."""
        return self.doc_nodes(lang=node.lang if self.i18n_config.enabled else None)

    def direct_child_count(
        self,
        slug: str,
        *,
        lang: str | None = None,
        mount: str | None = None,
    ) -> int:
        shards = self._active_shards()
        shard = shards.get(mount) if mount is not None else self._shard_for_slug(slug)
        return shard.direct_child_count(slug, lang=lang) if shard is not None else 0

    def _catalog_rail_for_mount(self, active_url: str | None) -> list[dict[str, Any]] | None:
        """Section icon rail for a mount catalog page; None for app/portal surfaces."""
        if not active_url or active_url in ("/", "/portal/", "/search"):
            return None
        mount = self._resolve_mount(active_url)
        shard = self._active_shards().get(mount.id)
        if shard is None:
            return None
        if mount.default or active_url.startswith("/shared"):
            return shard.catalog_rail_items(active_url)
        return None

    def catalog_rail_items(
        self,
        active_url: str | None = None,
        *,
        lang: str | None = None,
    ) -> list[dict[str, Any]]:
        shards = self._active_shards()
        if len(self.mounts) == 1:
            shard = shards.get(self.mounts[0].id)
            if shard is None:
                return []
            return shard.catalog_rail_items(
                active_url,
                lang=lang,
                home_mark=self.site_mark,
            )
        mount_rail = self._catalog_rail_for_mount(active_url)
        if mount_rail is not None:
            return mount_rail
        items: list[dict[str, Any]] = [
            {
                "title": "Home",
                "href": "/",
                "mark": self.site_mark,
                "active": active_url == "/",
            },
            {
                "title": "Portal",
                "href": "/portal/",
                "mark": "P",
                "active": active_url == "/portal/",
            },
        ]
        items.append(
            {
                "title": "Search",
                "href": "/search",
                "mark": "⌕",
                "active": active_url == "/search",
            }
        )
        return items

    def _shard_for_slug(self, slug: str) -> DocCatalog | None:
        node = self.get_by_slug(slug)
        if node is not None:
            return self._shard_for_node(node)
        shards = self._active_shards()
        if not shards:
            return None
        return shards.get(self.mounts[0].id) or next(iter(shards.values()))

    def docs_section_nav(
        self,
        active_url: str | None = None,
        *,
        lang: str | None = None,
    ) -> list[dict[str, Any]]:
        shards = self._active_shards()
        if len(self.mounts) == 1:
            shard = shards.get(self.mounts[0].id)
            return shard.docs_section_nav(active_url, lang=lang) if shard is not None else []
        if active_url:
            mount = self._resolve_mount(active_url)
            shard = shards.get(mount.id)
            if shard is not None:
                return shard.docs_section_nav(active_url, lang=lang)
        return self.nav_tree(active_url=active_url, lang=lang)

    def nav_tree(
        self, active_url: str | None = None, *, lang: str | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        shards = self._active_shards()
        if len(self.mounts) > 1:
            items.append(
                {
                    "title": "Portal",
                    "href": "/portal/",
                    "active": active_url == "/portal/",
                }
            )
        for mount in self.mounts:
            shard = shards.get(mount.id)
            if shard is None:
                continue
            section_items = shard.nav_tree(active_url=active_url)
            if len(self.mounts) == 1:
                return section_items
            if not section_items:
                continue
            home = shard.get(mount.url_prefix or "/")
            items.append(
                {
                    "title": mount.label,
                    "href": home.url if home is not None else mount.url_prefix or "/",
                    "open": active_url is not None
                    and (
                        active_url == (mount.url_prefix or "/")
                        or active_url.startswith(mount.url_prefix or "")
                    ),
                    "children": section_items,
                }
            )
        return items

    def search(self, query: str, *, limit: int = 12) -> list[DocNode]:
        return [hit.node for hit in self.search_hits(query, limit=limit)]

    def search_hits(self, query: str, *, limit: int = 12) -> list[SearchHit]:
        from furatena.catalog.access import AccessPermission, accessible_nodes

        nodes = accessible_nodes(
            self,
            self.doc_nodes(),
            permission=AccessPermission.SEARCH,
            include_private=self.include_private,
        )
        remote_mounts = self._remote_mount_ids()
        local_nodes = [node for node in nodes if node.mount not in remote_mounts]
        hits = search_nodes(
            local_nodes,
            query,
            limit=limit * 2,
            documents=self.ast_documents(),
        )
        hits.extend(
            SearchHit(
                node=hit.node,
                score=round(hit.score * 100, 4),
                snippet=hit.snippet,
            )
            for hit in self.federated_search_hits(query, nodes=nodes, limit=limit * 2)
        )
        hits.sort(
            key=lambda hit: (
                -hit.score,
                hit.node.weight,
                hit.node.title.casefold(),
                hit.node.node_id,
            )
        )
        return hits[:limit]

    def federated_search_hits(
        self,
        query: str,
        *,
        nodes: list[DocNode],
        limit: int,
        mount: str | None = None,
        edition: str | None = None,
        status: str | None = None,
        include_preview: bool = False,
        include_eol: bool = False,
    ) -> tuple[FederatedCatalogSearchHit, ...]:
        """Query per-shard indexes and intersect them with already-authorized nodes."""
        if self.remote_shards is None:
            return ()
        remote_mounts = self._remote_mount_ids()
        if mount is not None:
            remote_mounts &= {mount}
        if not remote_mounts:
            return ()
        target_edition = edition or self.active_channel
        uses_active_edition = target_edition == self.active_channel
        allowed = {
            node.node_id: node
            for node in nodes
            if node.mount in remote_mounts
            and (uses_active_edition or node.edition == target_edition)
        }
        if not allowed:
            return ()
        result = self.remote_shards.search(
            query,
            mounts=remote_mounts,
            edition=target_edition,
            limit=max(limit * 2, 16),
            status=status,
            include_preview=include_preview,
            include_eol=include_eol,
        )
        hits = tuple(
            FederatedCatalogSearchHit(
                node=allowed[hit.node_id],
                score=hit.score,
                snippet=hit.snippet,
                keyword_score=hit.keyword_score,
                tfidf_score=hit.tfidf_score,
            )
            for hit in result.hits
            if hit.node_id in allowed
        )
        return hits[:limit]

    def _remote_mount_ids(self) -> set[str]:
        return {mount.id for mount in self.mounts if mount.source.provider == "remote-shard"}

    def graph_edges(self) -> list[EdgeRecord]:
        pinned = self._read_generation_context.get()
        if pinned is not None and pinned.edges is not None:
            return list(pinned.edges)
        with self._publication_lock:
            latest = self.active_channel == "latest"
            if latest and self._edges is not None:
                return self._edges
            from furatena.catalog.graph_schema import build_translation_edges, edge_record

            url_index = {node.url: node.node_id for node in self.nodes}
            nodes_by_id = {node.node_id: node for node in self.nodes}
            edges: list[EdgeRecord] = []
            for shard in self._active_shards().values():
                if not shard.auto_reload and getattr(shard, "_frozen_edges", None) is not None:
                    edges.extend(shard.graph_edges())
                else:
                    edges.extend(
                        edge_record(edge)
                        for edge in build_graph_edges(
                            shard,
                            url_index=url_index,
                            nodes_by_id=nodes_by_id,
                        )
                    )
            if self.i18n_config.enabled:
                edges.extend(edge_record(edge) for edge in build_translation_edges(self.nodes))
            projection = self.edition_projection()
            seen = {
                (
                    edge["kind"],
                    edge["source"],
                    edge["target"],
                    edge.get("mount", ""),
                    edge.get("edition", ""),
                )
                for edge in edges
            }
            for edge in projection.edges_for(self.active_channel):
                key = (
                    edge["kind"],
                    edge["source"],
                    edge["target"],
                    edge.get("mount", ""),
                    edge.get("edition", ""),
                )
                if key not in seen:
                    edges.append(edge)
                    seen.add(key)
            if latest and pinned is None:
                self._edges = edges
            return edges

    def edition_projection(self) -> EditionProjection:
        """Return the immutable, visibility-safe cross-edition projection."""
        with self._edition_projection_lock:
            cached = self._edition_projection_cache
            if cached is None:
                from furatena.catalog.edition_projection import (
                    load_or_build_edition_projection,
                )

                cached = load_or_build_edition_projection(self)
                self._edition_projection_cache = cached
            return cached

    def resolve_edition_page(
        self,
        node: DocNode,
        target_edition: str,
    ) -> EditionPageResolution | None:
        """Resolve a page through the shared cross-edition composition index."""
        return self.edition_projection().resolve(
            mount=node.mount,
            source_node_id=node.node_id,
            target_edition=target_edition,
        )

    def public_cross_edition_node_ids(self) -> frozenset[str]:
        """Public page targets that may appear outside the active edition payload."""
        return self.edition_projection().public_node_ids

    def public_cross_edition_source_ids(self) -> frozenset[str]:
        """Public page/release sources generated by the edition projection."""
        return self.edition_projection().public_source_ids

    def version_resolution_metrics(self) -> dict[str, Any]:
        """Return deterministic direct-hit and fallback metrics for this source set."""
        from copy import deepcopy

        return deepcopy(dict(self.edition_projection().metrics))

    def namespaces(self) -> list[NamespaceRecord]:
        pinned = self._read_generation_context.get()
        if pinned is not None and pinned.namespaces is not None:
            return list(pinned.namespaces)
        with self._publication_lock:
            latest = self.active_channel == "latest"
            if latest and self._namespaces is not None:
                return self._namespaces
            from furatena.catalog.graph_schema import namespace_record

            records: list[NamespaceRecord] = []
            shards = self._active_shards()
            for mount in self.mounts:
                shard = shards.get(mount.id)
                if shard is None:
                    continue
                lifecycle = self.edition_lifecycle_for(mount.id, self.active_channel)
                records.append(
                    namespace_record(
                        mount.id,
                        mount.label,
                        edition=self.active_channel,
                        page_count=len(shard.nodes),
                        tenant=self.catalog_identity.get("tenant"),
                        workspace=self.catalog_identity.get("workspace"),
                        site=self.catalog_identity.get("site"),
                        edition_status=lifecycle.status,
                        release_date=lifecycle.release_date,
                        end_of_life=lifecycle.end_of_life,
                        banner=lifecycle.banner,
                    )
                )
            if latest and pinned is None:
                self._namespaces = records
            return records

    def ast_documents(self) -> dict[str, Document]:
        """Merge live Patitas AST documents from all mount shards."""
        documents: dict[str, Document] = {}
        for shard in self._active_shards().values():
            documents.update(shard.ast_documents())
        return documents

    @classmethod
    def from_config(
        cls,
        config_path: Path,
        *,
        repo_root: Path,
        app_root: Path | None = None,
        rewrites_path: Path | None = None,
        inventories_path: Path | None = None,
        catalog_identity: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> CatalogRegistry:
        mounts = load_mounts(config_path, repo_root=repo_root)
        return cls(
            mounts,
            repo_root=repo_root,
            app_root=app_root or config_path.parent,
            rewrites_path=rewrites_path,
            inventories_path=inventories_path,
            catalog_identity=catalog_identity,
            **kwargs,
        )


def _freeze_backlinks(
    value: Mapping[str, list[dict[str, str]]],
) -> Mapping[str, tuple[Mapping[str, str], ...]]:
    return MappingProxyType(
        {key: tuple(MappingProxyType(dict(item)) for item in items) for key, items in value.items()}
    )


def _freeze_translation_index(
    value: Mapping[str, Mapping[str, str]],
) -> Mapping[str, Mapping[str, str]]:
    return MappingProxyType({key: MappingProxyType(dict(items)) for key, items in value.items()})


def _doc_catalog_resident_size(catalog: DocCatalog) -> int:
    """Account Python overhead for the graph structures retained by one shard."""
    seen: set[int] = set()
    values = [
        catalog,
        catalog.__dict__,
        catalog._nodes,
        catalog._nodes_by_url,
        catalog._nodes_by_slug,
        catalog._frozen_edges,
        catalog._backlinks,
        catalog._nav_cache,
        catalog._html_cache,
        catalog._ast_documents,
        catalog._body_by_slug,
        catalog._raw_pages,
        catalog._stubs,
        catalog._slug_to_url,
        catalog._doc_nodes,
        catalog._doc_nodes_lang,
    ]
    return sum(_deep_resident_size(value, seen) for value in values)


def _deep_resident_size(value: Any, seen: set[int]) -> int:
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, Mapping):
        return size + sum(
            _deep_resident_size(key, seen) + _deep_resident_size(item, seen)
            for key, item in value.items()
        )
    if isinstance(value, tuple | list | set | frozenset):
        return size + sum(_deep_resident_size(item, seen) for item in value)
    fields = getattr(type(value), "__dataclass_fields__", None)
    if isinstance(fields, dict):
        return size + sum(
            _deep_resident_size(getattr(value, name), seen)
            for name in fields
            if hasattr(value, name)
        )
    return size
