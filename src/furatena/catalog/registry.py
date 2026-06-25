"""Catalog registry — federated mounts over DocCatalog shards."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from furatena.catalog.catalog_nav import CatalogNavConfig
from furatena.catalog.graph import build_federated_backlinks, normalize_internal_url
from furatena.catalog.graph_schema import build_graph_edges
from furatena.catalog.loader import DocCatalog
from furatena.catalog.models import DocNode
from furatena.catalog.runtime import ServeMode
from furatena.catalog.search import SearchHit, search_nodes
from furatena.catalog.sources.types import MountSourceConfig
from furatena.catalog.versions import active_channel_id
from furatena.catalog.i18n import DocsI18nConfig, build_translation_index
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


def _normalize_prefix(prefix: str) -> str:
    if not prefix or prefix == "/":
        return ""
    return prefix if prefix.endswith("/") else f"{prefix}/"


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
        content_raw = str(item.get("content_root") or "").strip()
        content_root = Path(content_raw)
        if not content_root.is_absolute():
            content_root = (config_path.parent / content_root).resolve()
        mounts.append(
            MountConfig(
                id=mount_id,
                label=str(item.get("label") or mount_id),
                content_root=content_root,
                url_prefix=_normalize_prefix(str(item.get("url_prefix") or "")),
                default=bool(item.get("default")),
                source=MountSourceConfig.from_mount_dict(item),
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
        workers: int | None = None,
        i18n_config: DocsI18nConfig | None = None,
        catalog_nav: CatalogNavConfig | None = None,
        site_mark: str = "𒀭",
    ) -> None:
        self.repo_root = repo_root
        self.app_root = app_root or repo_root
        self.rewrites_path = rewrites_path
        self.inventories_path = inventories_path
        self.autodoc_config = autodoc_config
        self.autodoc_enabled = autodoc
        self.auto_reload = auto_reload
        self.active_channel = channel or active_channel_id()
        self.i18n_config = i18n_config or DocsI18nConfig()
        self.catalog_nav = catalog_nav
        self.site_mark = site_mark
        self.frozen_dir = frozen_dir
        self.lazy_html = lazy_html
        self.serve_mode = serve_mode
        self.mounts = mounts
        self._html_cache: dict[str, str] = {}
        self._shards: dict[str, DocCatalog] = {}
        self._mount_for_url: list[tuple[str, MountConfig]] = []
        self._edges: list[dict[str, Any]] | None = None
        self._namespaces: list[dict[str, Any]] | None = None
        self._federated_backlinks: dict[str, list[dict[str, str]]] = {}
        self._translation_index: dict[str, dict[str, str]] | None = None
        self._inventory_store = None
        self._watcher: SourceWatcher | None = None
        self._workers = resolve_workers(workers)
        from furatena.catalog.rewrites import load_rewrite_table, set_rewrite_table

        set_rewrite_table(load_rewrite_table(rewrites_path))
        self._federated_slug_urls = self._prescan_federated_slugs()
        self._load_shards()
        self._finalize_federated()
        if auto_reload:
            self._start_watcher()

    def _build_live_shard(
        self,
        mount: MountConfig,
        *,
        cached_autodoc: list[DocNode] | None,
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
            edition=self.active_channel,
            cached_autodoc_nodes=cached_autodoc if mount.default else None,
            source_config=mount.source,
            federated_slug_urls=self._federated_slug_urls,
            workers=self._workers,
            i18n_config=self.i18n_config,
            catalog_nav=self.catalog_nav if mount.default else None,
        )
        if cached_autodoc is not None and mount.default and self.frozen_dir is not None:
            shard._frozen_shard_dir = self.frozen_dir / "mounts" / mount.id
        shard._federated_slug_urls = self._federated_slug_urls
        return shard

    def _load_shards(self) -> None:
        from furatena.catalog.autodoc_cache import load_cached_autodoc_nodes

        self._shards = {}
        self._mount_for_url = []
        use_frozen = self.serve_mode in {ServeMode.HYBRID, ServeMode.PREVIEW} and self.frozen_dir is not None
        cached_autodoc = None
        if use_frozen and self.autodoc_enabled:
            cached_autodoc = load_cached_autodoc_nodes(
                config_path=self.autodoc_config,
                repo_root=self.repo_root,
                frozen_dir=self.frozen_dir,
            )

        live_mount_jobs: list[tuple[MountConfig, Path | None]] = []
        for mount in self.mounts:
            shard_frozen = None
            if use_frozen and self.frozen_dir is not None:
                candidate = self.frozen_dir / "mounts" / mount.id
                if (candidate / "catalog.json").is_file():
                    shard_frozen = candidate
            if shard_frozen is not None and self.serve_mode == ServeMode.HYBRID:
                shard = DocCatalog.from_frozen(
                    shard_frozen,
                    content_root=mount.content_root,
                    mount=mount.id,
                    lazy_html=self.lazy_html,
                    catalog_nav=self.catalog_nav if mount.default else None,
                )
                shard.enable_author_overlay(
                    autodoc_config=self.autodoc_config if mount.default else None,
                    repo_root=self.repo_root,
                    cached_autodoc_nodes=cached_autodoc if mount.default else None,
                )
                shard._federated_slug_urls = self._federated_slug_urls
                self._shards[mount.id] = shard
            elif shard_frozen is not None:
                shard = DocCatalog.from_frozen(
                    shard_frozen,
                    content_root=mount.content_root,
                    mount=mount.id,
                    lazy_html=self.lazy_html,
                    catalog_nav=self.catalog_nav if mount.default else None,
                )
                shard._federated_slug_urls = self._federated_slug_urls
                self._shards[mount.id] = shard
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
                        self._shards[mount.id] = future.result()
            else:
                for mount, shard_frozen in live_mount_jobs:
                    self._shards[mount.id] = self._build_live_shard(
                        mount,
                        cached_autodoc=cached_autodoc,
                    )

        for mount in self.mounts:
            prefix = mount.url_prefix or "/"
            self._mount_for_url.append((prefix, mount))
        self._mount_for_url.sort(key=lambda item: len(item[0]), reverse=True)
        self._edges = None
        self._namespaces = None

    def _prescan_federated_slugs(self) -> dict[str, str]:
        from furatena.catalog.sources import FilesystemScanner

        urls: dict[str, str] = {}
        for mount in self.mounts:
            if not mount.content_root.is_dir():
                continue
            scanner = FilesystemScanner(mount.source)
            for page in scanner.scan(mount.content_root, url_prefix=mount.url_prefix):
                urls[f"{mount.id}:{page.slug}"] = page.url
                if mount.default:
                    urls.setdefault(page.slug, page.url)
        return urls

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

    @property
    def inventory_store(self):
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
            shard = self._shards[mount.id]
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
        return self._shards[node.mount]

    @property
    def default_mount(self) -> MountConfig:
        return next((m for m in self.mounts if m.default), self.mounts[0])

    @property
    def channels(self):
        return self.channels_for(self.default_mount.id)

    def channels_for(self, mount_id: str | None = None):
        """Release/version channels for one mount shard."""
        if mount_id and mount_id in self._shards:
            return self._shards[mount_id].channels
        default = self._shards.get(self.default_mount.id)
        if default is None:
            default = next(iter(self._shards.values()))
        return default.channels

    @property
    def nodes(self) -> tuple[DocNode, ...]:
        items: list[DocNode] = []
        for shard in self._shards.values():
            items.extend(shard.nodes)
        return tuple(items)

    def get(self, url: str) -> DocNode | None:
        mount = self._resolve_mount(url)
        return self._shards[mount.id].get(url)

    def get_by_slug(self, slug: str, *, mount: str | None = None) -> DocNode | None:
        if mount is not None:
            return self._shards[mount].get_by_slug(slug)
        for shard in self._shards.values():
            node = shard.get_by_slug(slug)
            if node is not None:
                return node
        return None

    def get_by_node_id(self, node_id: str) -> DocNode | None:
        mount, _edition, slug = node_id.split(":", 2)
        slug = "" if slug == "index" else slug
        return self.get_by_slug(slug, mount=mount)

    @property
    def translation_index(self) -> dict[str, dict[str, str]]:
        if self._translation_index is None:
            self._translation_index = build_translation_index(self.nodes)
        return self._translation_index

    def doc_nodes(self, *, lang: str | None = None) -> list[DocNode]:
        items: list[DocNode] = []
        for shard in self._shards.values():
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
        for mount in self.mounts:
            shard = self._shards[mount.id]
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
            return portal + [{"label": mount.label, "href": mount.url_prefix or crumbs[0]["href"]}] + crumbs[1:]
        return portal + crumbs

    def prev_next(self, node: DocNode) -> tuple[DocNode | None, DocNode | None]:
        return self._shard_for_node(node).prev_next(node)

    def doc_nodes_for(self, node: DocNode) -> list[DocNode]:
        """Navigation/search scope for the node's locale."""
        return self.doc_nodes(lang=node.lang if self.i18n_config.enabled else None)

    def direct_child_count(self, slug: str, *, lang: str | None = None) -> int:
        return self._shard_for_slug(slug).direct_child_count(slug, lang=lang)

    def _catalog_rail_for_mount(self, active_url: str | None) -> list[dict[str, Any]] | None:
        """Section icon rail for a mount catalog page; None for app/portal surfaces."""
        if not active_url or active_url in ("/", "/portal/", "/search"):
            return None
        mount = self._resolve_mount(active_url)
        shard = self._shards.get(mount.id)
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
        if len(self.mounts) == 1:
            return self._shards[self.mounts[0].id].catalog_rail_items(
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

    def _shard_for_slug(self, slug: str) -> DocCatalog:
        node = self.get_by_slug(slug)
        if node is not None:
            return self._shard_for_node(node)
        return self._shards[self.mounts[0].id]

    def docs_section_nav(
        self,
        active_url: str | None = None,
        *,
        lang: str | None = None,
    ) -> list[dict[str, Any]]:
        if len(self.mounts) == 1:
            return self._shards[self.mounts[0].id].docs_section_nav(active_url, lang=lang)
        if active_url:
            mount = self._resolve_mount(active_url)
            shard = self._shards.get(mount.id)
            if shard is not None:
                return shard.docs_section_nav(active_url, lang=lang)
        return self.nav_tree(active_url=active_url, lang=lang)

    def nav_tree(self, active_url: str | None = None, *, lang: str | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if len(self.mounts) > 1:
            items.append(
                {
                    "title": "Portal",
                    "href": "/portal/",
                    "active": active_url == "/portal/",
                }
            )
        for mount in self.mounts:
            shard = self._shards[mount.id]
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
        return search_nodes(self.doc_nodes(), query, limit=limit, documents=self.ast_documents())

    def graph_edges(self) -> list[dict[str, Any]]:
        if self._edges is not None:
            return self._edges
        from furatena.catalog.graph_schema import build_translation_edges, edge_record

        url_index = {node.url: node.node_id for node in self.nodes}
        nodes_by_id = {node.node_id: node for node in self.nodes}
        edges: list[dict[str, Any]] = []
        for shard in self._shards.values():
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
        self._edges = edges
        return edges

    def namespaces(self) -> list[dict[str, Any]]:
        if self._namespaces is not None:
            return self._namespaces
        from furatena.catalog.graph_schema import namespace_record

        records: list[dict[str, Any]] = []
        for mount in self.mounts:
            shard = self._shards[mount.id]
            records.append(
                namespace_record(
                    mount.id,
                    mount.label,
                    edition=self.active_channel,
                    page_count=len(shard.nodes),
                )
            )
        self._namespaces = records
        return records

    def ast_documents(self) -> dict[str, object]:
        """Merge live Patitas AST documents from all mount shards."""
        documents: dict[str, object] = {}
        for shard in self._shards.values():
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
        **kwargs: Any,
    ) -> CatalogRegistry:
        mounts = load_mounts(config_path, repo_root=repo_root)
        return cls(
            mounts,
            repo_root=repo_root,
            app_root=app_root or config_path.parent,
            rewrites_path=rewrites_path,
            inventories_path=inventories_path,
            **kwargs,
        )
