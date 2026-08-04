"""Catalog registry — federated mounts over DocCatalog shards."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Lock, RLock
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from patitas.nodes import Document

    from furatena.catalog.inventories import InventoryStore

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
        self.i18n_config = i18n_config or DocsI18nConfig()
        self.catalog_nav = catalog_nav
        self.site_mark = site_mark
        self.catalog_identity = normalize_identity(catalog_identity)
        state_root = self.app_root / ".docs-cache" / "source-sync-state"
        namespace = self.identity_cache_namespace()
        self.source_sync_state = source_sync_state or SourceSyncStateStore(
            state_root / namespace if namespace else state_root
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
        if serve_mode == ServeMode.PREVIEW and self.scoped_frozen_dir is not None:
            self._discovered_editions.update(_frozen_editions(self.scoped_frozen_dir, self.mounts))
        from furatena.catalog.edition_lifecycle import lifecycle_lookup

        self._edition_lifecycle = lifecycle_lookup(self.mounts, self._discovered_editions)
        self._html_cache: dict[str, str] = {}
        self._shards: dict[str, DocCatalog] = {}
        self._edition_shards: dict[str, dict[str, DocCatalog]] = {}
        self._edition_shards_lock = RLock()
        self._mount_for_url: list[tuple[str, MountConfig]] = []
        self._edges: list[EdgeRecord] | None = None
        self._namespaces: list[NamespaceRecord] | None = None
        self._query_graph_cache: dict[
            tuple[str, bool, AccessSubject | None], CatalogGraphRecord
        ] = {}
        self._query_graph_lock = Lock()
        self._generation = 0
        self._generation_lock = Lock()
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
        error: Exception | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "stage": stage,
            "loaded": status == "ok",
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

    def _load_shards(self) -> None:
        from furatena.catalog.autodoc_cache import load_cached_autodoc_nodes

        self._shards = {}
        self._mount_for_url = []
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
        self._mount_for_url.sort(key=lambda item: len(item[0]), reverse=True)
        self._edges = None
        self._namespaces = None
        self._query_graph_cache.clear()

    def _prescan_federated_slugs(self) -> dict[str, str]:
        from furatena.catalog.sources import FilesystemScanner

        urls: dict[str, str] = {}
        for mount in self.mounts:
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
            loaded = shard is not None
            has_error = "error" in sync or "error" in index
            if not loaded:
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
            yield
        finally:
            self._edition_context.reset(token)

    def edition_ids_for(self, mount_id: str) -> tuple[str, ...]:
        """Public edition ids discovered for one mount, including ``latest``."""
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

    def _active_shards(self) -> dict[str, DocCatalog]:
        edition = self.active_channel
        if edition == self._default_channel or edition == "latest":
            return self._shards
        with self._edition_shards_lock:
            cached = self._edition_shards.get(edition)
            if cached is not None:
                return cached
            shards = self._load_edition_shards(edition)
            self._edition_shards[edition] = shards
            return shards

    def _load_edition_shards(self, edition: str) -> dict[str, DocCatalog]:
        from furatena.catalog.edition_routing import edition_path

        shards: dict[str, DocCatalog] = {}
        for mount in self.mounts:
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

    def query_graph_snapshot(
        self,
        *,
        include_private: bool = False,
        subject: AccessSubject | None = None,
    ) -> CatalogGraphRecord:
        """Return one access-scoped graph serialization per catalog generation."""
        key = (self.active_channel, include_private, subject)
        with self._query_graph_lock:
            cached = self._query_graph_cache.get(key)
            if cached is not None:
                return cached
            from furatena.catalog.export import catalog_graph

            graph = catalog_graph(cast(Any, self), include_private=include_private, subject=subject)
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

        shards = self._active_shards()
        if mount_hint and mount_hint in shards:
            return self._resolve_link_slug(slug, mount=mount_hint, edition=effective_edition)

        mounts_to_try: list[str] = []
        if source_mount and source_mount in shards:
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

        nodes = list(self.nodes)
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

    @property
    def generation(self) -> int:
        """Monotonic generation incremented after federated catalog publication."""
        with self._generation_lock:
            return self._generation

    @property
    def inventory_store(self) -> InventoryStore | None:
        return self._inventory_store

    def inventories_metadata(self) -> list[dict[str, Any]]:
        if self._inventory_store is None:
            return []
        return self._inventory_store.metadata()

    def _start_watcher(self) -> None:
        roots = tuple(mount.content_root for mount in self.mounts if mount.content_root.is_dir())
        if not roots:
            return
        extensions: set[str] = set()
        for mount in self.mounts:
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
        return self._active_shards()[node.mount]

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
        shard = self._active_shards().get(mount.id)
        return shard.get(url) if shard is not None else None

    def get_by_slug(self, slug: str, *, mount: str | None = None) -> DocNode | None:
        shards = self._active_shards()
        if mount is not None:
            shard = shards.get(mount)
            if shard is None:
                return None
            return shard.get_by_slug(slug)
        if len(self.mounts) == 1:
            shard = shards.get(self.mounts[0].id)
            return shard.get_by_slug(slug) if shard is not None else None
        default = shards.get(self.default_mount.id)
        return default.get_by_slug(slug) if default is not None else None

    def get_by_node_id(self, node_id: str) -> DocNode | None:
        mount, _edition, slug = node_id.split(":", 2)
        slug = "" if slug == "index" else slug
        return self.get_by_slug(slug, mount=mount)

    @property
    def translation_index(self) -> dict[str, dict[str, str]]:
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
        cached = self._html_cache.get(node.node_id)
        if cached is not None:
            return cached
        html = self._shard_for_node(node).resolve_body_html(node)
        self._html_cache[node.node_id] = html
        return html

    def backlinks_for(self, node: DocNode) -> list[dict[str, str]]:
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
        return search_nodes(nodes, query, limit=limit, documents=self.ast_documents())

    def graph_edges(self) -> list[EdgeRecord]:
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
        if latest:
            self._edges = edges
        return edges

    def namespaces(self) -> list[NamespaceRecord]:
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
        if latest:
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
