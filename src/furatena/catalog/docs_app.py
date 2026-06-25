"""DocsApp — configure and run a Furatena hypermedia application."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from furatena.catalog.config import DocsConfig, load_docs_config
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.export import catalog_graph, llms_full_txt, meta_json, search_json, surface_json, tools_manifest
from furatena.catalog.links import boost_internal_links
from furatena.catalog.incremental import is_partial_reload
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.semantic import retrieve_node, semantic_search_json
from furatena.catalog.seo import (
    canonical_url as build_canonical_url,
    docs_base_url,
    json_ld_script,
    json_ld_article,
    og_image_url,
)
from furatena.catalog.sitemap import sitemap_xml
from furatena.catalog.dev_banner import extra_reload_dirs
from furatena.catalog.i18n import (
    active_language_override,
    detect_lang_from_path,
    fallback_context,
    locale_context,
    LocalizedNodeMatch,
    resolve_localized_node,
    supported_app_locales,
)
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.workers import resolve_workers
from furatena.catalog.search_experience import (
    build_search_workspace_context,
    highlight_search_terms,
    hybrid_search_hits,
    search_hit_heading,
    search_hit_url,
    search_nav_attrs,
)
from furatena.catalog.theme import DocsTheme
from furatena.catalog.toc import build_toc_tree, collection_toc_items, node_toc_items
from furatena.catalog.versions import channel_context
from furatena.catalog.views import ViewRegistry
from chirp import App, AppConfig, Fragment, OOB, Page, Request, Response, Template
from chirp.errors import NotFound
from chirp.ext.chirp_ui import use_chirp_ui
from chirp.i18n import get_locale, set_locale
from chirp.middleware.static import StaticFiles

_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def _static_cache_control(url_prefix: str, mode: ServeMode) -> str:
    """Cache-Control for docs theme static mounts."""
    if url_prefix.startswith("/docs-assets"):
        return _IMMUTABLE_CACHE
    if url_prefix == "/docs-theme/branding":
        return _IMMUTABLE_CACHE if mode == ServeMode.PREVIEW else "public, max-age=86400"
    if url_prefix.startswith("/docs-theme/js"):
        return "public, max-age=86400"
    if url_prefix in ("/docs-theme/local", "/docs-theme/tokens", "/docs-theme/fonts"):
        return _IMMUTABLE_CACHE if mode == ServeMode.PREVIEW else "public, max-age=300"
    return "public, max-age=3600"


class DocsApp:
    """Hypermedia docs app: catalog graph + views + theme + shell."""

    def __init__(
        self,
        config: DocsConfig,
        *,
        repo_root: Path,
        autodoc_config: Path | None = None,
        autodoc: bool = True,
        serve: ServeConfig | None = None,
        frozen_dir: Path | None = None,
        lazy_html: bool = False,
        workers: int | None = None,
    ) -> None:
        self.config = config
        self.repo_root = repo_root
        self.serve = serve or ServeConfig(ServeMode.AUTHOR, None, False, True)
        frozen = self.serve.frozen_dir or frozen_dir
        self.theme = DocsTheme.from_docs_config(config, frozen_dir=frozen if self.serve.mode != ServeMode.AUTHOR else None)
        self.views = ViewRegistry(config)
        self.catalog = CatalogRegistry.from_config(
            config.mounts_path or config.root / "mounts.yaml",
            repo_root=repo_root,
            app_root=config.root,
            rewrites_path=config.rewrites_path,
            inventories_path=config.inventories_path,
            autodoc_config=autodoc_config,
            autodoc=autodoc,
            auto_reload=self.serve.auto_reload,
            frozen_dir=frozen,
            lazy_html=self.serve.lazy_html if serve else lazy_html,
            serve_mode=self.serve.mode,
            workers=resolve_workers(workers),
            i18n_config=config.i18n,
        )
        semantic_path = (frozen or config.root / "frozen") / "semantic.json"
        self.embedding_index = EmbeddingIndex.load(semantic_path) or EmbeddingIndex.from_nodes(
            list(self.catalog.nodes),
            documents=self.catalog.ast_documents(),
        )
        if self.serve.warn_stale_freeze:
            print("Note: content is newer than frozen/ — run `fura freeze` for a fresh export.")
        self.app = self._build_app()

    def _build_app(self) -> App:
        component_dirs = tuple(
            str(path) for path in (*self.theme.template_roots, self.config.framework_templates_dir)
        )
        preview = self.serve.mode == ServeMode.PREVIEW
        skip_checks = preview or os.environ.get("CHIRP_SKIP_CONTRACT_CHECKS", "").lower() in {
            "1",
            "true",
            "yes",
        }
        i18n = self.config.i18n
        locales_dir = self.config.locales_dir or self.config.root / "locales"
        app = App(
            AppConfig(
                template_dir=self.config.templates_dir,
                component_dirs=component_dirs,
                debug=not preview,
                skip_contract_checks=skip_checks,
                htmx=True,
                i18n_enabled=i18n.enabled,
                i18n_supported_locales=supported_app_locales(i18n),
                i18n_default_locale=i18n.default_language,
                i18n_directory=str(locales_dir),
            )
        )
        use_chirp_ui(app)
        if i18n.enabled:
            app.template_global("get_locale")(get_locale)
        app.template_global("csrf_token")(lambda: "")
        app.template_global("fura_author")(lambda: self.serve.auto_reload)
        app.template_global("docs_stylesheets")(lambda: self.theme.stylesheet_hrefs)
        app.template_filter("doc_body")(self.body_html)
        app.template_filter("boost_doc_links")(self._boost_doc_links)
        app.template_filter("node_toc_items")(node_toc_items)
        app.template_filter("collection_toc_items")(collection_toc_items)
        app.template_global("build_toc_tree")(build_toc_tree)
        app.template_global("search_hit_url")(lambda hit: search_hit_url(hit, self.embedding_index))
        app.template_global("search_hit_heading")(lambda hit: search_hit_heading(hit, self.embedding_index))
        app.template_global("search_partial_attrs")(
            lambda: {
                "hx-disinherit": "hx-select hx-target hx-swap",
                "hx-select": "#search-results-panel",
            }
        )
        app.template_filter("search_shell_nav")(search_nav_attrs)
        app.template_filter("highlight_search")(highlight_search_terms)

        for assets in self.theme.static_mounts:
            app.add_middleware(
                StaticFiles(
                    directory=str(assets.directory),
                    prefix=assets.url_prefix,
                    cache_control=_static_cache_control(assets.url_prefix, self.serve.mode),
                )
            )
        for reload_dir in (*self.theme.reload_dirs, *extra_reload_dirs(self.config, self.repo_root)):
            app.add_reload_dir(str(reload_dir))

        self._register_contract_refs(app)
        self._register_routes(app)
        return app

    def body_html(self, node) -> str:
        return self.catalog.body_html(node)

    def _boost_doc_links(self, html: str) -> str:
        return boost_internal_links(html, self._route_link_attrs)

    def _route_link_attrs(self, href: str) -> dict[str, object]:
        base = self.app._mutable_state.template_globals.get("route_link_attrs")
        shell_attrs = {
            "hx-boost": "true",
            "hx-target": "#main",
            "hx-swap": "innerHTML",
            "hx-select": "#page-root",
            "hx-sync": "#main:replace",
        }
        if base is not None:
            attrs = base(href, fallback=shell_attrs)
            if attrs:
                return attrs
        if isinstance(href, str) and href.startswith("/") and not href.startswith("//"):
            return dict(shell_attrs)
        return {}

    def _ensure_catalog(self) -> None:
        self.catalog.refresh_if_stale()

    def _request_language(self, request: Request | None, *, node=None) -> str:
        i18n = self.config.i18n
        override = active_language_override()
        if override and override in i18n.language_codes():
            return override
        if node is not None:
            return node.lang
        if request is not None:
            return detect_lang_from_path(request.path, i18n)
        return i18n.default_language

    def _locale_template_context(
        self,
        *,
        request: Request | None = None,
        node=None,
        locale_match: LocalizedNodeMatch | None = None,
    ) -> dict[str, Any]:
        active_lang = (
            locale_match.requested_lang
            if locale_match is not None
            else self._request_language(request, node=node)
        )
        if self.config.i18n.enabled:
            set_locale(active_lang)
        fallback_url = locale_match.requested_url if locale_match and locale_match.fallback else None
        ctx = locale_context(
            self.config.i18n,
            active_lang=active_lang,
            node=node,
            translation_index=self.catalog.translation_index,
            fallback_url=fallback_url,
        )
        if request is not None and node is not None and not (locale_match and locale_match.fallback):
            base = self._site_base(request)
            from furatena.catalog.i18n import alternate_links

            ctx["alternate_links"] = alternate_links(
                node,
                translation_index=self.catalog.translation_index,
                config=self.config.i18n,
                base_url=base,
            )
        return ctx

    def _resolve_doc_lookup(
        self,
        lookup: str,
        *,
        requested_lang: str | None = None,
    ) -> LocalizedNodeMatch:
        i18n = self.config.i18n
        lang = requested_lang or i18n.default_language
        if i18n.enabled and lang != i18n.default_language:
            match = resolve_localized_node(
                self.catalog,
                lookup,
                requested_lang=lang,
                config=i18n,
            )
            if match is not None:
                return match
        node = self.catalog.get_by_slug(lookup.strip("/"))
        if node is None:
            raise NotFound(f"Document not found: /{lookup.strip('/')}/")
        return LocalizedNodeMatch(
            node=node,
            requested_lang=node.lang,
            fallback=False,
            requested_url=node.url,
        )

    def _site_base(self, request: Request | None = None) -> str:
        host = request.headers.get("host") if request is not None else None
        return docs_base_url(host)

    def _page_context(
        self,
        node,
        *,
        query: str = "",
        request: Request | None = None,
        locale_match: LocalizedNodeMatch | None = None,
    ) -> dict[str, Any]:
        self._ensure_catalog()
        if locale_match is None:
            page_lang = self._request_language(request, node=node)
            locale_match = LocalizedNodeMatch(
                node=node,
                requested_lang=page_lang,
                fallback=False,
                requested_url=node.url,
            )
        page_lang = locale_match.requested_lang
        active_url = locale_match.requested_url if locale_match.fallback else node.url
        prev_node, next_node = self.catalog.prev_next(node)
        base = self._site_base(request)
        page_url = build_canonical_url(base, active_url)
        view_name = self.views.resolve(node, self.catalog)
        surface = self.views.surface(view_name)
        child_count = self.catalog.direct_child_count(node.slug, lang=page_lang)
        llm_txt_url = f"{page_url.rstrip('/')}/index.txt"
        nav_items = (
            self.catalog.docs_section_nav(active_url=active_url, lang=page_lang)
            if surface == "catalog"
            else self.catalog.nav_tree(active_url=active_url, lang=page_lang)
        )
        ctx: dict[str, Any] = {
            "node": node,
            "active_view": view_name,
            "chirp_docs_surface": surface,
            "nav_items": nav_items,
            "catalog_rail_items": self.catalog.catalog_rail_items(active_url=active_url, lang=page_lang),
            "child_page_count": child_count,
            "llm_txt_url": llm_txt_url,
            "breadcrumb_items": self.catalog.trail(node),
            "prev_page": prev_node,
            "next_page": next_node,
            "search_query": query,
            "page_count": len(self.catalog.doc_nodes(lang=page_lang)),
            "backlinks": self.catalog.backlinks_for(node),
            "canonical_url": page_url,
            "og_image_url": og_image_url(base, node),
            "json_ld": json_ld_script(json_ld_article(node=node, page_url=page_url)),
            **channel_context(self.catalog.channels, self.catalog.active_channel),
            **self._locale_template_context(request=request, node=node, locale_match=locale_match),
            **fallback_context(locale_match, config=self.config.i18n),
        }
        ctx.update(self.views.compose(node, self.catalog))
        ctx.update(self._view_chrome_context(view_name, node, ctx))
        return ctx

    @staticmethod
    def _view_chrome_context(view_name: str, node, ctx: dict[str, Any]) -> dict[str, Any]:
        """Layout variables for theme block layouts (catalog + app surfaces)."""
        if view_name in {"views/doc.html", "views/changelog.html"}:
            toc_len = len(getattr(node, "toc", ()) or ())
            return {
                "catalog_surface": "doc",
                "catalog_with_toc": toc_len > 0,
                "catalog_layout_extra": "",
            }
        if view_name == "views/doc_list.html":
            toc_len = len(getattr(node, "toc", ()) or ())
            return {
                "catalog_surface": "doc-list",
                "catalog_with_toc": toc_len > 0,
                "catalog_layout_extra": "",
            }
        if view_name == "views/collection.html":
            sections = ctx.get("collection_sections") or ()
            collection = ctx.get("collection")
            subtitle = getattr(node, "description", "") or (
                collection.title if collection is not None else ""
            )
            return {
                "catalog_surface": "collection",
                "catalog_with_toc": len(sections) > 0,
                "catalog_layout_extra": "chirp-theme-track-layout",
                "catalog_title": getattr(node, "title", ""),
                "catalog_subtitle": subtitle,
            }
        if view_name == "views/home.html":
            return {"app_surface": "home", "app_page_cls": "chirp-theme-home"}
        if view_name == "views/portal.html":
            return {"app_surface": "portal", "app_page_cls": "fura-page"}
        if view_name == "views/page.html":
            return {"app_surface": "page", "app_page_cls": "chirp-theme-home"}
        return {}

    def _search_hits(
        self,
        query: str,
        *,
        limit: int = 12,
        section: str | None = None,
        mount: str | None = None,
        tag: str | None = None,
        channel: str | None = None,
        lang: str | None = None,
        global_search: bool = False,
    ):
        effective_lang = lang or self.config.i18n.default_language
        return hybrid_search_hits(
            self.catalog,
            self.embedding_index,
            query,
            limit=limit,
            section=section,
            mount=mount,
            tag=tag,
            channel=channel,
            lang=effective_lang if self.config.i18n.enabled else None,
            global_search=global_search,
        ).hits

    def _search_context(
        self,
        request: Request,
        query: str,
        *,
        section: str | None = None,
        mount: str | None = None,
        tag: str | None = None,
        channel: str | None = None,
        lang: str | None = None,
        global_search: bool = False,
        limit: int = 24,
        partial: bool = False,
    ) -> dict[str, Any]:
        section_value = section or ""
        mount_value = mount or ""
        tag_value = tag or ""
        channel_value = channel or self.catalog.active_channel or "latest"
        page_lang = lang or self._request_language(request)
        workspace = build_search_workspace_context(
            self.catalog,
            self.embedding_index,
            query=query,
            section=section_value,
            mount=mount_value,
            tag=tag_value,
            channel=channel_value,
            lang=page_lang if self.config.i18n.enabled else "",
            global_search=global_search,
            limit=limit,
            include_shell_extras=not partial,
        )
        layout = {
            "catalog_surface": "search",
            "catalog_with_toc": True,
            "catalog_layout_extra": "chirp-theme-search-layout",
            "catalog_title": "",
            "catalog_subtitle": "",
            "chirp_docs_surface": "catalog",
            "app_surface": "search",
            "app_page_cls": "",
            "breadcrumb_items": [{"label": "Search", "href": "/search"}],
        }
        if partial:
            return {
                **workspace,
                **layout,
                **channel_context(self.catalog.channels, self.catalog.active_channel),
                **self._locale_template_context(request=request),
            }
        return {
            **self._shell_context(query=query, request=request),
            **workspace,
            **layout,
            "catalog_rail_items": self.catalog.catalog_rail_items(
                active_url="/search",
                lang=page_lang,
            ),
            "nav_items": self.catalog.docs_section_nav(active_url="/search", lang=page_lang),
        }

    def _shell_context(self, *, query: str = "", request: Request | None = None) -> dict[str, Any]:
        self._ensure_catalog()
        page_lang = self._request_language(request)
        return {
            "nav_items": self.catalog.nav_tree(lang=page_lang),
            "chirp_docs_surface": "app",
            "breadcrumb_items": [{"label": "Search", "href": "/search"}],
            "search_query": query,
            "search_section": "",
            "search_sections": [],
            "search_facet_links": [],
            "search_global_expand_url": "",
            "search_global_expand_nav_attrs": {},
            "search_reset_nav_attrs": search_nav_attrs("/search"),
            "search_popular_links": [],
            "search_aside_links": [],
            "search_discovery_sections": [],
            "search_result_section_links": [],
            "page_count": len(self.catalog.doc_nodes(lang=page_lang)),
            "node": None,
            **channel_context(self.catalog.channels, self.catalog.active_channel),
            **self._locale_template_context(request=request),
        }

    def _is_author_reload(self, request: Request) -> bool:
        return self.serve.auto_reload and bool(request.headers.get("HX-Docs-Author-Reload"))

    def _render_author_reload(self, view_name: str, request: Request, node, **context: Any):
        hints = self.catalog.invalidation_hints(node.slug)
        if not hints:
            return Response("", status=204)

        hint_set = set(hints)
        oob_fragments: list[Any] = []

        if "head-meta" in hint_set:
            oob_fragments.append(
                Fragment("partials/head_meta_oob.html", "head_meta_oob", **context),
            )
        if "toc-panel" in hint_set:
            oob_fragments.append(
                Fragment("partials/toc_panel_oob.html", "toc_panel_oob", **context),
            )
        if "docs-sidebar" in hint_set:
            oob_fragments.append(
                Fragment("partials/docs_sidebar_oob.html", "docs_sidebar_oob", **context),
            )

        if "page-root" in hint_set or not is_partial_reload(hints):
            main: Any = Page.mounted(view_name, **context)
        elif oob_fragments:
            main = oob_fragments.pop(0)
        else:
            main = Page.mounted(view_name, **context)

        self.catalog.clear_invalidation_hints(node.slug)
        if oob_fragments:
            return OOB(main, *oob_fragments)
        return main

    def _render_view(self, view_name: str, request: Request, **context: Any):
        node = context.get("node")
        if node is not None and self._is_author_reload(request):
            return self._render_author_reload(view_name, request, node, **context)

        main = Page.mounted(view_name, **context)
        if request.is_htmx and request.is_boosted and not request.is_history_restore:
            return OOB(
                main,
                Fragment(
                    "partials/head_meta_oob.html",
                    "head_meta_oob",
                    **context,
                ),
            )
        return main

    def _render_node(
        self,
        node,
        request: Request,
        *,
        locale_match: LocalizedNodeMatch | None = None,
    ) -> Any:
        ctx = self._page_context(node, request=request, locale_match=locale_match)
        view_name = ctx["active_view"]
        return self._render_view(view_name, request, **ctx)

    def _register_routes(self, app: App) -> None:
        @app.route("/portal/", referenced=True)
        def portal(request: Request):
            self._ensure_catalog()
            ctx = {
                **self._shell_context(request=request),
                "mounts": self.catalog.portal_mounts(),
                "page_count": len(self.catalog.nodes),
            }
            view_name = self.config.views.get("portal") or "views/portal.html"
            ctx["chirp_docs_surface"] = self.views.surface(view_name)
            ctx.update(self._view_chrome_context(view_name, ctx.get("node"), ctx))
            return self._render_view(view_name, request, **ctx)

        @app.route("/")
        def home(request: Request):
            node = self.catalog.get("/")
            if node is None:
                raise NotFound("Home page not found.")
            return self._render_node(node, request)

        @app.route("/shared/", referenced=True)
        @app.route("/shared/{slug:path}", referenced=True)
        def shared_docs(request: Request, slug: str = ""):
            slug = slug.strip("/")
            lookup = slug if slug else ""
            node = self.catalog.get_by_slug(lookup, mount="shared")
            if node is None and slug:
                node = self.catalog.get_by_slug(f"shared/{slug}", mount="shared")
            if node is None:
                raise NotFound(f"Shared page not found: /shared/{slug}/")
            return self._render_node(node, request)

        @app.route("/docs/_author/stale")
        def author_stale(request: Request):
            if not self.serve.auto_reload:
                body = {"stale": [], "current": None}
                return Response(json.dumps(body)).with_header(
                    "Content-Type", "application/json; charset=utf-8"
                )
            self._ensure_catalog()
            slug = (request.query.get("slug") or "").strip("/")
            entries = self.catalog.author_stale_entries(slug or None)
            current = None
            if slug:
                hints = self.catalog.invalidation_hints(slug)
                if hints:
                    current = {"slug": slug, "hints": list(hints), "dirty": True}
            body = {"stale": entries, "current": current}
            return Response(json.dumps(body)).with_header(
                "Content-Type", "application/json; charset=utf-8"
            )

        @app.route("/docs/", referenced=True)
        @app.route("/docs/{slug:path}", referenced=True)
        def docs(request: Request, slug: str = ""):
            slug = slug.strip("/")
            if request.path.endswith("/index.txt"):
                if slug.endswith("/index.txt"):
                    slug = slug[: -len("/index.txt")]
                elif slug == "index.txt":
                    slug = ""
                lookup = f"docs/{slug}" if slug else "docs"
                match = self._resolve_doc_lookup(lookup)
                node = match.node
                if node is None:
                    raise NotFound(f"Document not found: /{lookup}/")
                body = "\n".join(
                    part
                    for part in (
                        f"# {node.title}",
                        "",
                        node.description,
                        "",
                        node.body_md.strip(),
                    )
                    if part is not None
                )
                return Response(body).with_header("Content-Type", "text/plain; charset=utf-8")
            lookup = f"docs/{slug}" if slug else "docs"
            match = self._resolve_doc_lookup(lookup)
            return self._render_node(match.node, request, locale_match=match)

        @app.route("/api/", referenced=True)
        @app.route("/api/{slug:path}", referenced=True)
        def api_docs(request: Request, slug: str = ""):
            slug = slug.strip("/")
            lookup = f"api/{slug}" if slug else "api"
            node = self.catalog.get_by_slug(lookup)
            if node is None:
                raise NotFound(f"API reference page not found: /{lookup}/")
            return self._render_node(node, request)

        @app.route("/releases/", referenced=True)
        @app.route("/releases/{slug:path}", referenced=True)
        def releases(request: Request, slug: str = ""):
            slug = slug.strip("/")
            lookup = f"releases/{slug}" if slug else "releases"
            node = self.catalog.get_by_slug(lookup)
            if node is None:
                raise NotFound(f"Release page not found: /{lookup}/")
            return self._render_node(node, request)

        @app.route("/search")
        def search(request: Request):
            self._ensure_catalog()
            query = (request.query.get("q") or "").strip()
            section = (request.query.get("section") or "").strip() or None
            mount = (request.query.get("mount") or "").strip() or None
            tag = (request.query.get("tag") or "").strip() or None
            channel = (request.query.get("channel") or "").strip() or None
            lang = (request.query.get("lang") or "").strip() or None
            global_search = (request.query.get("global") or "").strip() in {"1", "true", "yes"}
            target = (request.htmx_target_id or "").strip() if request.headers.get("HX-Request") else ""
            partial = target == "search-results-panel"
            ctx = self._search_context(
                request,
                query,
                section=section,
                mount=mount,
                tag=tag,
                channel=channel,
                lang=lang,
                global_search=global_search,
                partial=partial,
            )
            if request.headers.get("HX-Request"):
                ctx["search_oob"] = True
                if request.is_boosted and target != "search-results-panel":
                    return Template("search.html", **ctx)
                main = Fragment("search.html", "search_results", **ctx)
                if target == "search-results-panel":
                    return OOB(
                        main,
                        Fragment(
                            "partials/search_mount_rail_oob.html",
                            "search_mount_rail_oob",
                            **ctx,
                        ),
                        Fragment(
                            "partials/search_scope_rail_oob.html",
                            "search_scope_rail_oob",
                            **ctx,
                        ),
                        Fragment(
                            "partials/search_spotlight_oob.html",
                            "search_spotlight_oob",
                            **ctx,
                        ),
                        Fragment(
                            "partials/search_discovery_oob.html",
                            "search_discovery_oob",
                            **ctx,
                        ),
                    )
                return main
            return Template("search.html", **ctx)

        @app.route("/search/suggest")
        def search_suggest(request: Request):
            self._ensure_catalog()
            query = (request.query.get("q") or "").strip()
            section = (request.query.get("section") or "").strip() or None
            hits = self._search_hits(query, limit=6, section=section) if query else []
            return Fragment(
                "partials/search_suggest.html",
                "search_suggest",
                search_query=query,
                hits=hits,
            )

        @app.route("/search.json", referenced=True)
        def search_json_route(request: Request):
            self._ensure_catalog()
            base = self._site_base(request)
            query = (request.query.get("q") or "").strip()
            if query:
                from furatena.catalog.export import search_json_for_query

                body = search_json_for_query(self.catalog, query, base_url=base)
            else:
                body = search_json(self.catalog, base_url=base)
            return Response(json.dumps(body, indent=2)).with_header(
                "Content-Type", "application/json; charset=utf-8"
            )

        @app.route("/tools.json", referenced=True)
        def tools_json(request: Request):
            self._ensure_catalog()
            body = tools_manifest(self.catalog, base_url=self._site_base(request))
            return Response(json.dumps(body, indent=2)).with_header(
                "Content-Type", "application/json; charset=utf-8"
            )

        @app.route("/sitemap.xml", referenced=True)
        def sitemap(request: Request):
            self._ensure_catalog()
            body = sitemap_xml(self.catalog, base_url=self._site_base(request))
            return Response(body).with_header("Content-Type", "application/xml; charset=utf-8")

        @app.route("/search/semantic", referenced=True)
        def search_semantic(request: Request):
            self._ensure_catalog()
            base = self._site_base(request)
            query = (request.query.get("q") or "").strip()
            mount = (request.query.get("mount") or "").strip() or None
            edition = (request.query.get("edition") or "").strip() or None
            if not query:
                body = {"schema_version": 1, "query": "", "count": 0, "results": []}
            else:
                body = semantic_search_json(
                    self.catalog,
                    self.embedding_index,
                    query,
                    base_url=base,
                    mount=mount,
                    edition=edition,
                )
            return Response(json.dumps(body, indent=2)).with_header(
                "Content-Type", "application/json; charset=utf-8"
            )

        @app.route("/catalog/retrieve", referenced=True)
        def catalog_retrieve(request: Request):
            self._ensure_catalog()
            node_id = (request.query.get("id") or request.query.get("node_id") or "").strip()
            if not node_id:
                return Response(json.dumps({"error": "missing node id"}), status=400).with_header(
                    "Content-Type", "application/json; charset=utf-8"
                )
            payload = retrieve_node(self.catalog, self.embedding_index, node_id)
            if payload is None:
                return Response(json.dumps({"error": "not found"}), status=404).with_header(
                    "Content-Type", "application/json; charset=utf-8"
                )
            return Response(json.dumps(payload, indent=2)).with_header(
                "Content-Type", "application/json; charset=utf-8"
            )

        @app.route("/catalog.json", referenced=True)
        def catalog_json():
            self._ensure_catalog()
            body = json.dumps(catalog_graph(self.catalog), indent=2)
            return Response(body).with_header("Content-Type", "application/json; charset=utf-8")

        @app.route("/inventories.json", referenced=True)
        def inventories_json_route(request: Request):
            self._ensure_catalog()
            from furatena.catalog.inventories.export import inventories_json

            frozen = self.serve.frozen_dir if self.serve.mode != ServeMode.AUTHOR else None
            body = inventories_json(
                self.catalog,
                base_url=self._site_base(request),
                frozen_dir=frozen,
            )
            return Response(json.dumps(body, indent=2)).with_header(
                "Content-Type", "application/json; charset=utf-8"
            )

        @app.route("/objects.inv", referenced=True)
        @app.route("/inventories/{inventory_id}/objects.inv", referenced=True)
        def inventory_inv(request: Request, inventory_id: str = "local-catalog"):
            self._ensure_catalog()
            from furatena.catalog.inventories.export import inventory_bytes

            if request.path == "/objects.inv":
                specs = (
                    self.catalog.inventory_store.specs
                    if self.catalog.inventory_store is not None
                    else ()
                )
                inventory_id = specs[0].id if specs else "local-catalog"
            frozen = self.serve.frozen_dir if self.serve.mode != ServeMode.AUTHOR else None
            payload = inventory_bytes(self.catalog, inventory_id, frozen_dir=frozen)
            if payload is None:
                raise NotFound(f"Inventory not found: {inventory_id}")
            return Response(payload).with_header(
                "Content-Type", "application/octet-stream",
            )

        @app.route("/llms.txt", referenced=True)
        def llms_txt():
            self._ensure_catalog()
            lines = ["# Chirp Documentation", ""]
            for node in self.catalog.doc_nodes():
                desc = node.description.strip() if node.description else ""
                if desc:
                    lines.append(f"- [{node.title}]({node.url}): {desc}")
                else:
                    lines.append(f"- [{node.title}]({node.url})")
            body = "\n".join(lines) + "\n"
            return Response(body).with_header("Content-Type", "text/plain; charset=utf-8")

        @app.route("/llms-full.txt", referenced=True)
        def llms_full():
            self._ensure_catalog()
            body = llms_full_txt(self.catalog)
            return Response(body).with_header("Content-Type", "text/plain; charset=utf-8")

        @app.route("/meta.json", referenced=True)
        def meta_json_route():
            self._ensure_catalog()
            body = json.dumps(meta_json(self.catalog), indent=2)
            return Response(body).with_header("Content-Type", "application/json; charset=utf-8")

        @app.route("/surface.json", referenced=True)
        def surface_json_route():
            body = json.dumps(surface_json(), indent=2)
            return Response(body).with_header("Content-Type", "application/json; charset=utf-8")

        @app.route("/og/{name}", referenced=True)
        def og_image(name: str):
            label = name.replace("-", " ").title()
            svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#0f172a"/>
  <text x="80" y="180" fill="#e2e8f0" font-family="system-ui,sans-serif" font-size="48" font-weight="700">Furatena</text>
  <text x="80" y="280" fill="#94a3b8" font-family="system-ui,sans-serif" font-size="36">{label}</text>
</svg>"""
            return Response(svg).with_header("Content-Type", "image/svg+xml; charset=utf-8")

        self._register_localized_routes(app)

        @app.error(404)
        def not_found():
            ctx = {
                **self._shell_context(),
                "error_message": "That page is not in the catalog.",
            }
            return (
                Page.mounted("error.html", **ctx),
                404,
            )

    def _register_localized_routes(self, app: App) -> None:
        """Register ``/{lang}/docs/...`` routes for non-default locales."""
        i18n = self.config.i18n
        if not i18n.enabled:
            return

        for lang_code in i18n.language_codes():
            if lang_code == i18n.default_language:
                continue
            prefix = f"/{lang_code}"

            @app.route(f"{prefix}/docs/", referenced=True)
            @app.route(f"{prefix}/docs/{{slug:path}}", referenced=True)
            def localized_docs(request: Request, slug: str = "", lang=lang_code):
                slug = slug.strip("/")
                if request.path.endswith("/index.txt"):
                    if slug.endswith("/index.txt"):
                        slug = slug[: -len("/index.txt")]
                    elif slug == "index.txt":
                        slug = ""
                    lookup = f"{lang}/docs/{slug}" if slug else f"{lang}/docs"
                    match = self._resolve_doc_lookup(lookup, requested_lang=lang)
                    node = match.node
                    body = "\n".join(
                        part
                        for part in (
                            f"# {node.title}",
                            "",
                            node.description,
                            "",
                            node.body_md.strip(),
                        )
                        if part is not None
                    )
                    return Response(body).with_header("Content-Type", "text/plain; charset=utf-8")
                lookup = f"{lang}/docs/{slug}" if slug else f"{lang}/docs"
                match = self._resolve_doc_lookup(lookup, requested_lang=lang)
                return self._render_node(match.node, request, locale_match=match)

            @app.route(f"{prefix}/", referenced=True)
            def localized_home(request: Request, lang=lang_code):
                node = self.catalog.get_by_slug(lang)
                if node is None:
                    node = self.catalog.get(f"{prefix}/")
                if node is None:
                    raise NotFound(f"Home page not found for locale: {lang}")
                return self._render_node(node, request)

    @staticmethod
    def _register_contract_refs(app: App) -> None:
        if False:  # noqa: E701 — static references for contract checker only
            Fragment("directives/accordion.html", "_register")
            Fragment("directives/callout.html", "_register")
            Fragment("directives/card_grid.html", "_register")
            Fragment("directives/card_link.html", "_register")
            Fragment("directives/card_static.html", "_register")
            Fragment("directives/child_cards.html", "_register")
            Fragment("directives/code_block.html", "_register")
            Fragment("directives/figure.html", "_register")
            Fragment("directives/glossary.html", "_register")
            Fragment("directives/gist.html", "_register")
            Fragment("directives/literalinclude.html", "_register")
            Fragment("directives/related.html", "_register")
            Fragment("directives/step.html", "_register")
            Fragment("directives/steps.html", "_register")
            Fragment("directives/table.html", "_register")
            Fragment("directives/tabs.html", "_register")
            Fragment("directives/version_callout.html", "_register")
            Fragment("directives/youtube.html", "_register")
            for view in (
                "views/doc.html",
                "views/doc_list.html",
                "views/page.html",
                "views/home.html",
                "views/collection.html",
                "views/changelog.html",
                "views/portal.html",
            ):
                Template(view)
            Template("error.html")
            Template("search.html")
            Template("layouts/docs_catalog.html")
            Template("layouts/docs_app.html")

    @classmethod
    def from_paths(
        cls,
        docs_yaml: Path,
        *,
        repo_root: Path,
        autodoc_config: Path | None = None,
        autodoc: bool | None = None,
        serve: ServeConfig | None = None,
        frozen_dir: Path | None = None,
        lazy_html: bool = False,
        workers: int | None = None,
    ) -> DocsApp:
        config = load_docs_config(docs_yaml)
        if autodoc is None:
            autodoc = os.environ.get("FURA_AUTODOC", "1") != "0"
        return cls(
            config,
            repo_root=repo_root,
            autodoc_config=autodoc_config,
            autodoc=autodoc,
            serve=serve,
            frozen_dir=frozen_dir,
            lazy_html=lazy_html,
            workers=workers,
        )

    def create_app(self) -> App:
        return self.app
