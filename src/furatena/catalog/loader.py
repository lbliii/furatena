"""Load documentation sources into a DocCatalog."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from furatena.catalog.autodoc import generate_autodoc_nodes
from furatena.catalog.catalog_nav import CatalogNavConfig, resolve_doc_sections, section_index_slug
from furatena.catalog.context import NodeStub
from furatena.catalog.ast_store import document_from_json
from furatena.catalog.content_ir import content_ir_from_record
from furatena.catalog.graph import build_backlinks
from furatena.catalog.i18n import (
    DocsI18nConfig,
    locale_slug,
    locale_url,
    node_matches_language,
    resolve_page_lang,
    resolve_translation_key,
    strip_locale_prefix,
)
from furatena.catalog.incremental import htmx_swap_hints, needs_graph_rebuild
from furatena.catalog.models import DocNode, SectionChunk, TocEntry
from furatena.catalog.graph_schema import infer_section_root
from furatena.catalog.render import DocsRenderer
from furatena.catalog.search import SearchHit, search_nodes
from furatena.catalog.sources import FilesystemScanner, MountSourceConfig, PageSource, get_content_adapter
from furatena.catalog.versions import (
    DocChannel,
    active_channel_id,
    infer_release_channels,
    node_matches_channel,
)
from furatena.catalog.workers import resolve_workers

_MD_LINK_RE = re.compile(r"\]\((/[^)#]+)\)")


def _normalize_url(url: str) -> str:
    if url == "/":
        return url
    return url if url.endswith("/") else f"{url}/"



def _is_docs_tree_slug(slug: str, config: DocsI18nConfig) -> bool:
    canonical = strip_locale_prefix(slug.strip("/"), config)
    return canonical == "docs" or canonical.startswith("docs/")


def _infer_section(slug: str) -> str:
    parts = slug.split("/")
    if len(parts) >= 3 and parts[1] == "docs":
        return parts[2]
    if len(parts) >= 2 and parts[0] == "docs":
        return parts[1]
    return parts[0] if parts else "root"


def _docs_slug_prefix(lang: str | None, config: DocsI18nConfig) -> str:
    if not lang or not config.enabled or lang == config.default_language:
        return ""
    return f"{lang}/"


def _extract_md_links(body: str) -> set[str]:
    urls: set[str] = set()
    for match in _MD_LINK_RE.finditer(body):
        urls.add(_normalize_url(match.group(1)))
    return urls


def _build_md_backlinks(
    pages: list[tuple[str, str, str, str]],
    valid_urls: set[str],
) -> dict[str, list[dict[str, str]]]:
    """Build backlink map from markdown hrefs before HTML render."""
    incoming: dict[str, list[dict[str, str]]] = {}
    for url, _slug, title, body in pages:
        for target in _extract_md_links(body):
            if target == url or target not in valid_urls:
                continue
            incoming.setdefault(target, []).append({"title": title, "href": url})
    for target, refs in incoming.items():
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for ref in refs:
            if ref["href"] in seen:
                continue
            seen.add(ref["href"])
            deduped.append(ref)
        incoming[target] = sorted(deduped, key=lambda r: r["title"].lower())
    return incoming


class DocCatalog:
    """In-memory documentation graph."""

    def __init__(
        self,
        content_root: Path,
        *,
        auto_reload: bool = False,
        autodoc_config: Path | None = None,
        repo_root: Path | None = None,
        channel: str | None = None,
        autodoc: bool = True,
        mount: str = "chirp",
        url_prefix: str = "",
        edition: str | None = None,
        lazy_html: bool = False,
        cached_autodoc_nodes: list[DocNode] | None = None,
        source_config: MountSourceConfig | None = None,
        federated_slug_urls: dict[str, str] | None = None,
        reference_catalog=None,
        inventory_store=None,
        workers: int | None = None,
        i18n_config: DocsI18nConfig | None = None,
        catalog_nav: CatalogNavConfig | None = None,
    ) -> None:
        self.content_root = content_root
        self.auto_reload = auto_reload
        self.autodoc_config = autodoc_config
        self.repo_root = repo_root or content_root.parent.parent
        self.autodoc_enabled = autodoc
        self.mount = mount
        self.url_prefix = url_prefix
        self.lazy_html = lazy_html
        self.source_config = source_config or MountSourceConfig()
        self.active_channel = edition or channel or active_channel_id()
        self.channels: tuple[DocChannel, ...] = infer_release_channels(content_root)
        self._frozen_pages_dir: Path | None = None
        self._frozen_shard_dir: Path | None = None
        self._html_cache: dict[str, str] = {}
        self._nodes: list[DocNode] = []
        self._nodes_by_url: dict[str, DocNode] = {}
        self._nodes_by_slug: dict[str, DocNode] = {}
        self._doc_nodes: list[DocNode] | None = None
        self._nav_cache: dict[str, list[dict[str, Any]]] = {}
        self._backlinks: dict[str, list[dict[str, str]]] = {}
        self._source_mtimes: dict[Path, float] = {}
        self._raw_pages: list[dict[str, Any]] = []
        self._stubs: dict[str, NodeStub] = {}
        self._slug_to_url: dict[str, str] = {}
        self._renderer = DocsRenderer()
        self._renderer.attach_reference_context(
            catalog=reference_catalog,
            inventory_store=inventory_store,
        )
        self._scanner = FilesystemScanner(self.source_config)
        self._federated_slug_urls = federated_slug_urls or {}
        self._watcher = None
        self._cached_autodoc_nodes = cached_autodoc_nodes
        self._ast_documents: dict[str, object] = {}
        self._body_by_slug: dict[str, str] = {}
        self._last_invalidations: dict[str, tuple[str, ...]] = {}
        self._workers = resolve_workers(workers)
        self.i18n_config = i18n_config or DocsI18nConfig()
        self.catalog_nav = catalog_nav
        self._doc_nodes_lang: str | None = None
        self._load()

    def attach_watcher(self, watcher) -> None:
        """Use a background watcher instead of stat-scanning on every request."""
        self._watcher = watcher

    def enable_author_overlay(
        self,
        *,
        autodoc_config: Path | None = None,
        repo_root: Path | None = None,
        cached_autodoc_nodes: list[DocNode] | None = None,
    ) -> None:
        """Hybrid mode: frozen baseline with live markdown overlay."""
        self.auto_reload = True
        self.autodoc_config = autodoc_config or self.autodoc_config
        self.repo_root = repo_root or self.repo_root
        self._cached_autodoc_nodes = cached_autodoc_nodes
        if not self._raw_pages:
            self._scan_sources()

    def refresh_if_stale(self) -> bool:
        """Reindex dirty source nodes when files change (dev helper)."""
        if not self.auto_reload:
            return False
        dirty_paths = self._collect_dirty_paths()
        if not dirty_paths:
            return False

        from furatena.catalog.directives.kida_render import clear_directive_cache

        clear_directive_cache()
        source_files = self._iter_source_files()
        if not self._raw_pages:
            self._scan_sources()
        if len(dirty_paths) > len(source_files) // 2:
            self._load()
        else:
            self._reindex_paths(dirty_paths)
        return True

    def _iter_source_files(self) -> list[Path]:
        files: list[Path] = []
        for ext in self.source_config.tracked_extensions():
            files.extend(self.content_root.rglob(f"*{ext}"))
        return sorted(files)

    def _collect_dirty_paths(self) -> set[Path]:
        extensions = self.source_config.tracked_extensions()
        if self._watcher is not None:
            dirty = self._watcher.drain_dirty()
            return {path for path in dirty if path.suffix.lower() in extensions}
        current_files = self._iter_source_files()
        current_set = set(current_files)
        tracked_set = set(self._source_mtimes)
        dirty_paths: set[Path] = set()
        if current_set != tracked_set:
            dirty_paths = current_set ^ tracked_set
            dirty_paths |= {
                path
                for path in current_files
                if self._source_mtimes.get(path) != path.stat().st_mtime
            }
        else:
            for path in current_files:
                mtime = path.stat().st_mtime
                if self._source_mtimes.get(path) != mtime:
                    dirty_paths.add(path)
        return dirty_paths

    def _reset_indexes(self) -> None:
        self._nodes = []
        self._nodes_by_url = {}
        self._nodes_by_slug = {}
        self._doc_nodes = None
        self._doc_nodes_lang = None
        self._nav_cache = {}
        self._backlinks = {}
        self._source_mtimes = {}
        self._raw_pages = []
        self._stubs = {}
        self._slug_to_url = {}
        self._ast_documents = {}
        self._body_by_slug = {}
        self._last_invalidations = {}

    def _scan_locale_pages(self, scanned: list[PageSource]) -> list[PageSource]:
        """Load translated pages from ``_locale/{lang}/`` overlay directories."""
        if not self.i18n_config.enabled:
            return []
        locale_root = self.content_root / self.i18n_config.locale_content_dir
        if not locale_root.is_dir():
            return []

        overlay_pages: list[PageSource] = []
        for lang in self.i18n_config.language_codes():
            if lang == self.i18n_config.default_language:
                continue
            lang_dir = locale_root / lang
            if not lang_dir.is_dir():
                continue
            lang_scanned = self._scanner.scan(lang_dir, url_prefix=self.url_prefix)
            for page in lang_scanned:
                self._source_mtimes[page.path] = page.path.stat().st_mtime
                meta = dict(page.meta)
                meta.setdefault("lang", lang)
                overlay_pages.append(
                    PageSource(
                        path=page.path,
                        content_format=page.content_format,
                        meta=meta,
                        body=page.body,
                        source_path=str(
                            Path(self.i18n_config.locale_content_dir) / lang / page.source_path
                        ),
                        url=locale_url(page.url, lang, self.i18n_config),
                        slug=locale_slug(page.slug, lang, self.i18n_config),
                    )
                )
        return overlay_pages

    def _scan_sources(self) -> list[dict[str, Any]]:
        scanned = self._scanner.scan(self.content_root, url_prefix=self.url_prefix)
        for page in scanned:
            self._source_mtimes[page.path] = page.path.stat().st_mtime

        overlay = self._scan_locale_pages(scanned)
        if overlay:
            scanned = list(scanned) + overlay

        slug_to_url = self._scanner.build_slug_to_url(scanned)
        scanned = self._scanner.resolve_wikilinks(
            scanned,
            slug_to_url,
            federated_slug_urls=self._federated_slug_urls,
        )
        raw_pages = self._scanner.page_dicts(scanned)

        stubs: dict[str, NodeStub] = {}
        for page in raw_pages:
            meta = page["meta"]
            slug = page["slug"]
            stubs[slug] = NodeStub(
                slug=slug,
                url=page["url"],
                title=str(
                    meta.get("title") or slug.rsplit("/", 1)[-1].replace("-", " ").title()
                ),
                description=str(meta.get("description") or ""),
                weight=int(meta.get("weight") or 100),
                page_type=str(meta.get("type") or meta.get("layout") or "page"),
            )

        self._raw_pages = raw_pages
        self._stubs = stubs
        self._slug_to_url = slug_to_url
        return raw_pages

    def _scan_markdown_sources(self) -> list[dict[str, Any]]:
        """Backward-compatible alias for markdown-era callers."""
        return self._scan_sources()

    def _compute_md_backlinks(self) -> dict[str, list[dict[str, str]]]:
        valid_urls = {_normalize_url(item["url"]) for item in self._raw_pages}
        return _build_md_backlinks(
            [
                (item["url"], item["slug"], self._stubs[item["slug"]].title, item["body"])
                for item in self._raw_pages
            ],
            valid_urls,
        )

    def _page_to_node(
        self,
        page: dict[str, Any],
        *,
        old_document: object | None = None,
        old_body: str | None = None,
        renderer: DocsRenderer | None = None,
        md_backlinks: dict[str, list[dict[str, str]]] | None = None,
    ) -> DocNode:
        meta = dict(page["meta"])
        slug = page["slug"]
        body = page["body"]
        content_format = str(page.get("content_format") or "patitas-markdown")
        active_renderer = renderer or self._renderer
        adapter = get_content_adapter(content_format, renderer=active_renderer)
        page_source = PageSource(
            path=page["path"],
            content_format=content_format,
            meta=meta,
            body=body,
            source_path=page["source_path"],
            url=page["url"],
            slug=slug,
        )

        parsed_document = None
        if old_document is not None and old_body is not None and old_body != body:
            parsed_document, _content_ir = adapter.parse_incremental(
                body,
                old_document,
                previous_body=old_body,
            )

        if md_backlinks is None:
            md_backlinks = self._compute_md_backlinks()

        def render_markdown(
            nested_body: str,
            nested_source_rel: str,
            nested_slug: str,
            nested_stack: set[str],
            nested_depth: int,
        ) -> str:
            nested_renderer = DocsRenderer()
            nested_adapter = get_content_adapter("patitas-markdown", renderer=nested_renderer)
            nested_source = PageSource(
                path=self.content_root / nested_source_rel,
                content_format="patitas-markdown",
                meta={},
                body=nested_body,
                source_path=nested_source_rel,
                url=self._slug_to_url.get(nested_slug, f"/{nested_slug}/"),
                slug=nested_slug,
            )
            adapted = nested_adapter.adapt(
                nested_source,
                stubs=self._stubs,
                render_markdown=render_markdown,
                get_backlinks=lambda url: md_backlinks.get(_normalize_url(url), []),
                content_root=self.content_root,
                include_stack=nested_stack,
                include_depth=nested_depth,
                mount=self.mount,
            )
            return adapted.body_html

        adapted = adapter.adapt(
            page_source,
            stubs=self._stubs,
            render_markdown=render_markdown,
            get_backlinks=lambda url: md_backlinks.get(_normalize_url(url), []),
            content_root=self.content_root,
            document=parsed_document,
            mount=self.mount,
        )
        document = adapted.native_document
        if document is not None:
            self._ast_documents[slug] = document
        self._body_by_slug[slug] = body
        doc_version = meta.get("doc_version") or meta.get("version")
        if doc_version is not None:
            meta["doc_version"] = str(doc_version)
        view_kind = str(meta.get("layout") or meta.get("kind") or meta.get("type") or "doc")
        page_lang = resolve_page_lang(meta, config=self.i18n_config, slug=slug)
        meta["lang"] = page_lang
        translation_key = resolve_translation_key(
            meta,
            slug=slug,
            lang=page_lang,
            config=self.i18n_config,
        )
        meta["translation_key"] = translation_key
        return DocNode(
            url=page["url"],
            slug=slug,
            title=self._stubs[slug].title,
            description=self._stubs[slug].description,
            layout=view_kind,
            weight=self._stubs[slug].weight,
            section=str(meta.get("section") or _infer_section(slug)),
            tags=frozenset(meta.get("tags") or ()),
            body_md=body,
            body_html=adapted.body_html,
            toc=adapted.toc,
            source_path=page["source_path"],
            meta=meta,
            mount=self.mount,
            edition=self.active_channel,
            lang=page_lang,
            translation_key=translation_key,
            section_root=infer_section_root(meta=meta, slug=slug, source_path=page["source_path"]),
            content_ir=adapted.content_ir,
            ast_json=adapted.native_ast,
            content_format=content_format,
            body_text=adapted.body_text,
            sections=adapted.sections,
        )

    def _register_node(self, node: DocNode) -> None:
        self._nodes.append(node)
        self._nodes_by_url[node.url] = node
        if node.url != "/":
            self._nodes_by_url[node.url.rstrip("/")] = node
        self._nodes_by_slug[node.slug] = node

    def _unregister_slug(self, slug: str) -> None:
        node = self._nodes_by_slug.pop(slug, None)
        if node is None:
            return
        self._nodes = [item for item in self._nodes if item.slug != slug]
        self._nodes_by_url.pop(node.url, None)
        if node.url != "/":
            self._nodes_by_url.pop(node.url.rstrip("/"), None)

    def _merge_autodoc_nodes(self) -> None:
        if not self.autodoc_enabled or self.autodoc_config is None:
            return
        for node in self._nodes[:]:
            if node.meta.get("source") == "autodoc":
                self._unregister_slug(node.slug)
        cached = self._cached_autodoc_nodes
        if cached is None:
            from furatena.catalog.autodoc_cache import load_cached_autodoc_nodes

            cached = load_cached_autodoc_nodes(
                config_path=self.autodoc_config,
                repo_root=self.repo_root,
                frozen_dir=getattr(self, "_frozen_shard_dir", None),
            )
        if cached is not None:
            for node in cached:
                self._register_node(node)
            if self._frozen_pages_dir is None and self._frozen_shard_dir is not None:
                pages_dir = self._frozen_shard_dir / "pages"
                if pages_dir.is_dir():
                    self._frozen_pages_dir = pages_dir
            return
        for node in generate_autodoc_nodes(
            self.autodoc_config,
            repo_root=self.repo_root,
            workers=self._workers,
        ):
            self._register_node(node)

    def _finalize_graph(self) -> None:
        self._doc_nodes = None
        self._doc_nodes_lang = None
        self._nav_cache = {}
        self._backlinks = build_backlinks(self._nodes, catalog=self)

    def _reindex_paths(self, dirty_paths: set[Path]) -> None:
        self._scan_sources()
        graph_dirty = False
        for path in dirty_paths:
            if not path.exists():
                for page in list(self._raw_pages):
                    if page["path"] == path:
                        slug = page["slug"]
                        self._unregister_slug(slug)
                        self._ast_documents.pop(slug, None)
                        self._body_by_slug.pop(slug, None)
                        self._last_invalidations.pop(slug, None)
                        graph_dirty = True
                continue
            for page in self._raw_pages:
                if page["path"] != path:
                    continue
                slug = page["slug"]
                old_document = self._ast_documents.get(slug)
                old_body = self._body_by_slug.get(slug)
                self._unregister_slug(slug)
                node = self._page_to_node(page, old_document=old_document, old_body=old_body)
                new_document = self._ast_documents.get(slug)
                content_format = str(page.get("content_format") or "patitas-markdown")
                adapter = get_content_adapter(content_format, renderer=self._renderer)
                regions = adapter.invalidation_regions(old_document, new_document)
                self._last_invalidations[slug] = htmx_swap_hints(regions)
                if needs_graph_rebuild(regions):
                    graph_dirty = True
                self._register_node(node)
                self._html_cache.pop(node.node_id, None)
        self._merge_autodoc_nodes()
        if graph_dirty:
            self._finalize_graph()
        else:
            self._doc_nodes = None

    def invalidation_hints(self, slug: str) -> tuple[str, ...]:
        """htmx swap targets to refresh after the last incremental reindex."""
        return self._last_invalidations.get(slug.strip("/"), ())

    def clear_invalidation_hints(self, slug: str) -> None:
        """Drop pending author reload hints after a selective refresh."""
        self._last_invalidations.pop(slug.strip("/"), None)

    def stale_invalidation_entries(self) -> list[tuple[str, tuple[str, ...]]]:
        """Return ``(slug, hints)`` pairs awaiting author reload."""
        return sorted(self._last_invalidations.items())

    def _load_pages_parallel(self, raw_pages: list[dict[str, Any]]) -> None:
        md_backlinks = self._compute_md_backlinks()

        def adapt_page(page: dict[str, Any]) -> DocNode:
            renderer = DocsRenderer()
            return self._page_to_node(page, renderer=renderer, md_backlinks=md_backlinks)

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for node in pool.map(adapt_page, raw_pages):
                self._register_node(node)

    def _load(self) -> None:
        self._reset_indexes()
        raw_pages = self._scan_sources()
        if self._workers > 1 and len(raw_pages) > 1:
            self._load_pages_parallel(raw_pages)
        else:
            md_backlinks = self._compute_md_backlinks()
            for page in raw_pages:
                self._register_node(self._page_to_node(page, md_backlinks=md_backlinks))
        if self.autodoc_config is not None:
            self._merge_autodoc_nodes()
        self._finalize_graph()

    def resolve_body_html(self, node: DocNode) -> str:
        if node.body_html:
            return node.body_html
        cached = self._html_cache.get(node.node_id)
        if cached is not None:
            return cached
        if node.html_path and self._frozen_pages_dir is not None:
            path = self._frozen_pages_dir / node.html_path
            if path.is_file():
                html = path.read_text(encoding="utf-8").strip()
                self._html_cache[node.node_id] = html
                return html
        return ""

    def body_html(self, node: DocNode) -> str:
        """Return rendered HTML for a node (eager or lazy-loaded)."""
        return self.resolve_body_html(node)

    def backlinks_for(self, node: DocNode) -> list[dict[str, str]]:
        """Pages that link to this doc (from rendered HTML + markdown graph)."""
        return self._backlinks.get(_normalize_url(node.url), [])

    @property
    def nodes(self) -> tuple[DocNode, ...]:
        return tuple(self._nodes)

    def get(self, url: str) -> DocNode | None:
        normalized = url if url.endswith("/") or url == "/" else f"{url}/"
        return self._nodes_by_url.get(normalized) or self._nodes_by_url.get(url)

    def get_by_slug(self, slug: str, *, mount: str | None = None) -> DocNode | None:
        _ = mount
        return self._nodes_by_slug.get(slug.strip("/"))

    def doc_nodes(self, *, lang: str | None = None) -> list[DocNode]:
        effective_lang = lang
        if effective_lang is None and self.i18n_config.enabled:
            effective_lang = self.i18n_config.default_language
        if self._doc_nodes is not None and self._doc_nodes_lang == effective_lang:
            return self._doc_nodes
        has_docs_tree = (self.content_root / "docs").is_dir()
        if has_docs_tree:
            candidates = [
                n
                for n in self.nodes
                if (
                    _is_docs_tree_slug(n.slug, self.i18n_config)
                    or n.meta.get("source") == "autodoc"
                )
                and node_matches_channel(n.meta.get("doc_version"), self.active_channel)
                and node_matches_language(n, effective_lang or n.lang, self.i18n_config)
            ]
        else:
            candidates = [
                n
                for n in self.nodes
                if node_matches_channel(n.meta.get("doc_version"), self.active_channel)
                and node_matches_language(n, effective_lang or n.lang, self.i18n_config)
            ]
        self._doc_nodes = sorted(
            candidates,
            key=lambda n: (n.section, n.weight, n.title),
        )
        self._doc_nodes_lang = effective_lang
        return self._doc_nodes

    def all_doc_nodes(self) -> list[DocNode]:
        """Documentation nodes across every configured locale."""
        if not self.i18n_config.enabled:
            return self.doc_nodes()
        items: list[DocNode] = []
        for lang in self.i18n_config.language_codes():
            items.extend(self.doc_nodes(lang=lang))
        return sorted(items, key=lambda n: (n.lang, n.section, n.weight, n.title))

    def trail(self, node: DocNode) -> list[dict[str, str]]:
        crumbs: list[dict[str, str]] = [{"label": "Home", "href": "/"}]
        parts = node.slug.split("/")
        built: list[str] = []
        for part in parts:
            built.append(part)
            slug = "/".join(built)
            candidate = self.get_by_slug(slug)
            if candidate and candidate.url != node.url:
                crumbs.append({"label": candidate.title, "href": candidate.url})
        crumbs.append({"label": node.title, "href": node.url})
        return crumbs

    def prev_next(self, node: DocNode) -> tuple[DocNode | None, DocNode | None]:
        docs = self.doc_nodes(lang=node.lang if self.i18n_config.enabled else None)
        try:
            index = next(i for i, item in enumerate(docs) if item.url == node.url)
        except StopIteration:
            return None, None
        prev_node = docs[index - 1] if index > 0 else None
        next_node = docs[index + 1] if index + 1 < len(docs) else None
        return prev_node, next_node

    def _section_nav_children(
        self,
        section_slug: str,
        pages: list[DocNode],
        *,
        active_url: str | None,
    ) -> list[dict[str, Any]]:
        """Nested nav items under a section index slug (e.g. docs/build-apps)."""
        section_pages = [
            page
            for page in pages
            if page.slug == section_slug or page.slug.startswith(f"{section_slug}/")
        ]

        def branch_open(href: str, children: list[dict[str, Any]]) -> bool:
            if active_url == href:
                return True
            if href and active_url and active_url.startswith(href.rstrip("/") + "/"):
                return True
            return any(child.get("active") or child.get("open") for child in children)

        def items_for_parent(parent_slug: str) -> list[dict[str, Any]]:
            direct: list[DocNode] = []
            deeper_by_segment: dict[str, list[DocNode]] = {}

            for page in section_pages:
                if page.slug == parent_slug:
                    continue
                if not page.slug.startswith(f"{parent_slug}/"):
                    continue
                rel = page.slug[len(parent_slug) + 1 :]
                parts = rel.split("/")
                if len(parts) == 1:
                    direct.append(page)
                else:
                    deeper_by_segment.setdefault(parts[0], []).append(page)

            result: list[dict[str, Any]] = []
            handled_segments: set[str] = set()

            for page in sorted(direct, key=lambda n: (n.weight, n.title)):
                segment = page.slug.rsplit("/", 1)[-1]
                handled_segments.add(segment)
                nested_source = deeper_by_segment.get(segment, [])
                if nested_source:
                    child_items = items_for_parent(page.slug)
                    entry: dict[str, Any] = {
                        "title": page.title,
                        "href": page.url,
                        "active": page.url == active_url,
                        "children": child_items,
                    }
                    entry["open"] = branch_open(page.url, child_items)
                    result.append(entry)
                else:
                    result.append(
                        {
                            "title": page.title,
                            "href": page.url,
                            "active": page.url == active_url,
                        }
                    )

            for segment, nested_pages in sorted(deeper_by_segment.items()):
                if segment in handled_segments:
                    continue
                branch_slug = f"{parent_slug}/{segment}"
                index_node = self.get_by_slug(branch_slug)
                child_items = items_for_parent(branch_slug)
                if not child_items:
                    continue
                href = index_node.url if index_node else child_items[0]["href"]
                title = (
                    index_node.title
                    if index_node
                    else segment.replace("-", " ").title()
                )
                entry = {
                    "title": title,
                    "href": href,
                    "active": href == active_url,
                    "children": child_items,
                }
                entry["open"] = branch_open(href, child_items)
                result.append(entry)

            return result

        return items_for_parent(section_slug)

    def nav_tree(self, active_url: str | None = None, *, lang: str | None = None) -> list[dict[str, Any]]:
        """Build chirp-ui nav_tree items from the docs hierarchy."""
        effective_lang = lang
        if effective_lang is None and self.i18n_config.enabled:
            effective_lang = self.i18n_config.default_language
        cache_key = f"{effective_lang or 'all'}:{active_url or ''}"
        cached = self._nav_cache.get(cache_key)
        if cached is not None:
            return cached

        slug_prefix = _docs_slug_prefix(effective_lang, self.i18n_config)
        sections: dict[str, list[DocNode]] = {}
        for node in self.doc_nodes(lang=effective_lang):
            if node.slug == "docs":
                continue
            section = node.section
            sections.setdefault(section, []).append(node)

        resolved_sections = resolve_doc_sections(
            sections_map=sections,
            slug_prefix=slug_prefix,
            nav_config=self.catalog_nav,
            get_index_node=self.get_by_slug,
        )

        items: list[dict[str, Any]] = []
        for section in resolved_sections:
            section_id = section.id
            pages = sections.get(section_id, [])
            if not pages:
                continue
            index_slug = section_index_slug(slug_prefix, section_id)
            index_node = self.get_by_slug(index_slug)
            section_href = index_node.url if index_node else pages[0].url
            section_urls = {section_href, *(p.url for p in pages)}
            section_active = active_url in section_urls or (
                active_url is not None
                and active_url.startswith(section_href.rstrip("/") + "/")
            )
            children = self._section_nav_children(
                index_slug,
                pages,
                active_url=active_url,
            )
            items.append(
                {
                    "title": section.label,
                    "href": section_href,
                    "open": section_active,
                    "active": section_href == active_url,
                    "children": children,
                }
            )

        release_node = self.get_by_slug(f"{slug_prefix}releases".strip("/"))
        if release_node:
            items.append(
                {
                    "title": "Releases",
                    "href": release_node.url,
                    "active": active_url == release_node.url
                    or (active_url or "").startswith("/releases/"),
                }
            )

        self._nav_cache[cache_key] = items
        return items

    def docs_section_nav(
        self,
        active_url: str | None = None,
        *,
        lang: str | None = None,
    ) -> list[dict[str, Any]]:
        """Secondary catalog panel: only the active top-level docs section."""
        tree = self.nav_tree(active_url=active_url, lang=lang)
        if not active_url:
            return tree
        for item in tree:
            if item.get("open") or item.get("active"):
                return [item]
        for item in tree:
            href = item.get("href") or ""
            if href and (
                active_url == href
                or active_url.startswith(href.rstrip("/") + "/")
            ):
                scoped = dict(item)
                scoped["open"] = True
                return [scoped]
        return tree[:1] if tree else tree

    def direct_child_count(self, parent_slug: str, *, lang: str | None = None) -> int:
        """Count immediate child pages under a catalog slug."""
        parent = parent_slug.strip("/")
        if not parent:
            return 0
        depth = len(parent.split("/"))
        count = 0
        for node in self.doc_nodes(lang=lang):
            parts = node.slug.split("/")
            if len(parts) != depth + 1:
                continue
            if "/".join(parts[:-1]) != parent:
                continue
            if node.slug == parent:
                continue
            count += 1
        return count

    def catalog_rail_items(
        self,
        active_url: str | None = None,
        *,
        lang: str | None = None,
        home_mark: str = "𐂛",
    ) -> list[dict[str, Any]]:
        """Top-level section shortcuts for the docs catalog icon rail."""
        effective_lang = lang
        if effective_lang is None and self.i18n_config.enabled:
            effective_lang = self.i18n_config.default_language
        slug_prefix = _docs_slug_prefix(effective_lang, self.i18n_config)
        sections: dict[str, list[DocNode]] = {}
        for node in self.doc_nodes(lang=effective_lang):
            if node.slug == "docs":
                continue
            sections.setdefault(node.section, []).append(node)

        resolved_sections = resolve_doc_sections(
            sections_map=sections,
            slug_prefix=slug_prefix,
            nav_config=self.catalog_nav,
            get_index_node=self.get_by_slug,
        )

        items: list[dict[str, Any]] = [
            {
                "title": "Home",
                "href": "/",
                "mark": home_mark,
                "active": active_url == "/",
            }
        ]
        for section in resolved_sections:
            pages = sections.get(section.id, [])
            if not pages:
                continue
            index_slug = section_index_slug(slug_prefix, section.id)
            index_node = self.get_by_slug(index_slug)
            href = index_node.url if index_node else pages[0].url
            items.append(
                {
                    "title": section.label,
                    "href": href,
                    "mark": section.mark,
                    "icon": section.icon,
                    "active": bool(
                        active_url == href
                        or (
                            active_url is not None
                            and active_url.startswith(href.rstrip("/") + "/")
                        )
                    ),
                }
            )
        items.append(
            {
                "title": "Search",
                "href": "/search",
                "mark": "⌕",
                "active": active_url == "/search",
            }
        )
        return items

    def ast_documents(self) -> dict[str, object]:
        """Live Patitas AST documents keyed by catalog ``node_id``."""
        documents: dict[str, object] = {}
        for slug, document in self._ast_documents.items():
            node = self._nodes_by_slug.get(slug)
            if node is not None:
                documents[node.node_id] = document
        return documents

    def search(self, query: str, *, limit: int = 12) -> list[DocNode]:
        return [hit.node for hit in self.search_hits(query, limit=limit)]

    def search_hits(self, query: str, *, limit: int = 12) -> list[SearchHit]:
        return search_nodes(self.doc_nodes(), query, limit=limit, documents=self.ast_documents())

    def graph_edges(self) -> list[dict[str, Any]]:
        from furatena.catalog.graph_schema import build_graph_edges, edge_record

        return [edge_record(edge) for edge in build_graph_edges(self)]

    def namespaces(self) -> list[dict[str, Any]]:
        from furatena.catalog.graph_schema import namespace_record

        return [
            namespace_record(
                self.mount,
                self.mount.replace("-", " ").title(),
                edition=self.active_channel,
                page_count=len(self.nodes),
            )
        ]

    @classmethod
    def from_frozen(
        cls,
        frozen_dir: Path,
        *,
        content_root: Path | None = None,
        mount: str = "chirp",
        lazy_html: bool = True,
        catalog_nav: CatalogNavConfig | None = None,
    ) -> DocCatalog:
        """Load pre-rendered pages from a freeze export (fast production startup)."""
        graph_path = frozen_dir / "catalog.json"
        pages_dir = frozen_dir / "pages"
        if not graph_path.is_file():
            raise FileNotFoundError(f"Missing frozen catalog: {graph_path}")

        import json

        raw = json.loads(graph_path.read_text(encoding="utf-8"))
        catalog = cls.__new__(cls)
        catalog.content_root = content_root or frozen_dir
        catalog.auto_reload = False
        catalog.autodoc_config = None
        catalog.repo_root = content_root or frozen_dir
        catalog.autodoc_enabled = False
        catalog.mount = mount
        catalog.url_prefix = ""
        catalog.lazy_html = lazy_html
        catalog.active_channel = str(raw.get("channel") or raw.get("edition") or active_channel_id())
        catalog.channels = infer_release_channels(catalog.content_root)
        catalog._frozen_pages_dir = pages_dir
        catalog._frozen_shard_dir = frozen_dir
        catalog._html_cache = {}
        catalog._nodes = []
        catalog._nodes_by_url = {}
        catalog._nodes_by_slug = {}
        catalog._doc_nodes = None
        catalog._nav_cache = {}
        catalog._backlinks = {}
        catalog._source_mtimes = {}
        catalog._raw_pages = []
        catalog._stubs = {}
        catalog._slug_to_url = {}
        catalog._renderer = DocsRenderer()
        catalog._ast_documents = {}
        catalog._last_invalidations = {}
        catalog.i18n_config = DocsI18nConfig()
        catalog.catalog_nav = catalog_nav
        catalog._doc_nodes_lang = None
        catalog._workers = 1
        catalog.source_config = MountSourceConfig()
        catalog._scanner = FilesystemScanner(catalog.source_config)
        catalog._federated_slug_urls = {}
        catalog._watcher = None

        ast_dir = frozen_dir / "ast"
        for page in raw.get("pages", []):
            slug = page.get("slug", "")
            slug_file = slug or "index"
            html_rel = f"{slug_file}.html"
            html_path = pages_dir / html_rel
            body_html = ""
            html_path_ref: str | None = None
            if lazy_html:
                html_path_ref = html_rel if html_path.is_file() else None
            elif html_path.is_file():
                body_html = html_path.read_text(encoding="utf-8").strip()
            toc_raw = page.get("toc") or []
            toc = tuple(
                TocEntry(anchor=e["anchor"], text=e["text"], depth=int(e["depth"]))
                for e in toc_raw
                if isinstance(e, dict)
            )
            content_ir = content_ir_from_record(page.get("content"))
            sections_raw = page.get("sections") or []
            sections = tuple(
                SectionChunk(
                    id=str(item.get("id") or ""),
                    heading=str(item.get("heading") or ""),
                    depth=int(item.get("depth") or 0),
                    text=str(item.get("text") or ""),
                )
                for item in sections_raw
                if isinstance(item, dict) and item.get("text")
            )
            ast_json = None
            ast_rel = page.get("ast_path")
            if not ast_rel and isinstance(page.get("native_ast"), dict):
                ast_rel = page["native_ast"].get("path")
            if ast_rel and ast_dir.is_dir():
                ast_file = ast_dir / str(ast_rel)
                if ast_file.is_file():
                    ast_json = ast_file.read_text(encoding="utf-8")
            node = DocNode(
                url=page["url"],
                slug=slug,
                title=page.get("title", ""),
                description=page.get("description", ""),
                layout="doc",
                weight=int(page.get("weight") or 100),
                section=str(page.get("section") or ""),
                tags=frozenset(page.get("tags") or ()),
                body_md=page.get("body_source") or page.get("body_md") or "",
                body_html=body_html,
                toc=toc,
                source_path=str(page.get("source_path") or ""),
                meta={
                    "source": page.get("source") or "markdown",
                    "doc_version": page.get("doc_version"),
                    "lang": page.get("lang"),
                    "translation_key": page.get("translation_key"),
                },
                mount=str(page.get("mount") or mount),
                edition=str(page.get("edition") or catalog.active_channel),
                lang=str(page.get("lang") or "en"),
                translation_key=page.get("translation_key"),
                section_root=bool(page.get("section_root")),
                html_path=html_path_ref,
                content_ir=content_ir,
                ast_json=ast_json,
                content_format=str(page.get("content_format") or "patitas-markdown"),
                body_text=str(page.get("body_text") or ""),
                sections=sections,
            )
            if ast_json:
                try:
                    catalog._ast_documents[slug] = document_from_json(ast_json)
                except Exception:
                    pass
            catalog._register_node(node)

        catalog._backlinks = {
            page["url"]: page.get("backlinks") or []
            for page in raw.get("pages", [])
            if isinstance(page, dict) and page.get("url")
        }
        return catalog
