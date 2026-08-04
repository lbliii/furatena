"""App-independent assembly of page, shell, and view rendering contexts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from furatena.catalog.check import check_catalog
from furatena.catalog.delivery import resolve_delivery_for_node
from furatena.catalog.develop_exports import DEVELOP_EXPORTS
from furatena.catalog.i18n import (
    LocaleResolutionService,
    LocalizedNodeMatch,
    active_language_override,
)
from furatena.catalog.search_experience import search_nav_attrs
from furatena.catalog.seo import (
    canonical_url as build_canonical_url,
)
from furatena.catalog.seo import (
    docs_base_url,
    json_ld_article,
    json_ld_script,
    og_image_url,
)
from furatena.catalog.versions import (
    channel_context,
    edition_banner_context,
    version_fallback_context,
)


def markdown_page_url(base: str, page_url: str) -> str:
    """Return the canonical extension alias for a content page."""
    path = page_url.rstrip("/")
    alias = f"{path}.md" if path else "/index.md"
    return build_canonical_url(base, alias)


@dataclass(frozen=True, slots=True)
class RenderContextService:
    """Produce render-ready dictionaries without route registration or app startup."""

    config: Any
    catalog: Any
    views: Any
    locale_service: LocaleResolutionService
    theme: Any = None
    validation: Any = None

    def request_language(self, request: Any | None = None, *, node: Any = None) -> str:
        return self.locale_service.request_language(
            path=request.path if request is not None else "",
            node=node,
            override=active_language_override(),
        )

    def locale_context(
        self,
        *,
        request: Any | None = None,
        node: Any = None,
        locale_match: LocalizedNodeMatch | None = None,
    ) -> dict[str, Any]:
        active_lang = (
            locale_match.requested_lang
            if locale_match is not None
            else self.request_language(request, node=node)
        )
        return self.locale_service.template_context(
            self.catalog,
            path=request.path if request is not None else "",
            node=node,
            locale_match=locale_match,
            override=active_lang,
            base_url=self.site_base(request),
        )

    def site_base(self, request: Any | None = None) -> str:
        host = request.headers.get("host") if request is not None else None
        return docs_base_url(host)

    def site_context(self) -> dict[str, Any]:
        site = self.config.site
        return {
            "site": site,
            "site_name": site.name,
            "site_tagline": site.tagline,
            "site_description": site.description,
            "site_mark": site.mark,
            "site_home": site.home,
            "site_nav": site.navigation,
            "llms_url": "/llms.txt",
            "develop_exports": DEVELOP_EXPORTS,
        }

    def theme_effects_context(self) -> dict[str, str]:
        effects = self.config.theme.effects
        return {
            "fura_effects_code": effects.code,
            "fura_effects_cards": effects.cards,
            "fura_effects_hero": effects.hero,
        }

    def page_context(
        self,
        node: Any,
        *,
        query: str = "",
        request: Any | None = None,
        locale_match: LocalizedNodeMatch | None = None,
        author_chrome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.catalog.refresh_if_stale()
        if locale_match is None:
            page_lang = self.request_language(request, node=node)
            locale_match = LocalizedNodeMatch(
                node=node,
                requested_lang=page_lang,
                fallback=False,
                requested_url=node.url,
            )
        page_lang = locale_match.requested_lang
        active_url = locale_match.requested_url if locale_match.fallback else node.url
        prev_node, next_node = self.catalog.prev_next(node)
        base = self.site_base(request)
        page_url = build_canonical_url(base, active_url)
        view_name = self.views.resolve(node, self.catalog)
        surface = self.views.surface(view_name)
        child_count = self.catalog.direct_child_count(node.slug, lang=page_lang, mount=node.mount)
        llm_txt_url = f"{page_url.rstrip('/')}/index.txt"
        nav_items = (
            self.catalog.docs_section_nav(active_url=active_url, lang=page_lang)
            if surface == "catalog"
            else self.catalog.nav_tree(active_url=active_url, lang=page_lang)
        )
        delivery = resolve_delivery_for_node(self.config, node)
        context: dict[str, Any] = {
            "node": node,
            "delivery": delivery,
            "rendering_head": delivery.head,
            "resolved_theme": delivery,
            "active_view": view_name,
            "chirp_docs_surface": surface,
            "nav_items": nav_items,
            "catalog_rail_items": self.catalog.catalog_rail_items(
                active_url=active_url, lang=page_lang
            ),
            "child_page_count": child_count,
            "llm_txt_url": llm_txt_url,
            "breadcrumb_items": self.catalog.trail(node),
            "prev_page": prev_node,
            "next_page": next_node,
            "search_query": query,
            "page_count": len(self.catalog.doc_nodes(lang=page_lang)),
            "backlinks": self.catalog.backlinks_for(node),
            "canonical_url": page_url,
            "markdown_url": markdown_page_url(base, active_url),
            "og_image_url": og_image_url(base, node),
            "json_ld": json_ld_script(
                json_ld_article(node=node, page_url=page_url, site_name=self.config.site.name)
            ),
            **self.site_context(),
            **channel_context(
                self.catalog.channels_for(node.mount),
                self.catalog.active_channel,
                catalog=self.catalog,
                node=node,
            ),
            **edition_banner_context(self.catalog, node),
            **version_fallback_context(self.catalog, node, request),
            **self.locale_context(request=request, node=node, locale_match=locale_match),
            **self.locale_service.fallback_context(locale_match),
            **self.theme_effects_context(),
        }
        if author_chrome is not None:
            context["author_chrome"] = author_chrome
        context.update(self.views.compose(node, self.catalog))
        context.update(self.view_chrome_context(view_name, node, context))
        return context

    def shell_context(
        self,
        *,
        query: str = "",
        request: Any | None = None,
        local_only: bool = False,
    ) -> dict[str, Any]:
        self.catalog.refresh_if_stale()
        page_lang = self.request_language(request)
        nav_items = (
            self.catalog._local_nav_tree(lang=page_lang)
            if local_only
            else self.catalog.nav_tree(lang=page_lang)
        )
        page_nodes = (
            self.catalog._local_doc_nodes(lang=page_lang)
            if local_only
            else self.catalog.doc_nodes(lang=page_lang)
        )
        return {
            "nav_items": nav_items,
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
            "page_count": len(page_nodes),
            "node": None,
            **channel_context(self.catalog.channels, self.catalog.active_channel),
            **self.site_context(),
            **self.locale_context(request=request),
            **self.theme_effects_context(),
        }

    def author_dashboard_context(
        self,
        request: Any,
        *,
        source_info: Any,
        force_validation: bool = False,
    ) -> dict[str, Any]:
        snapshot = (
            self.validation.snapshot(force=force_validation)
            if self.validation is not None
            else None
        )
        if snapshot is None:
            errors, warnings = check_catalog(
                self.catalog,
                views=getattr(self, "views", None),
                docs=getattr(self, "config", None),
                theme=getattr(self, "theme", None),
                inventory_store=self.catalog.inventory_store,
            )
        else:
            errors, warnings = list(snapshot.errors), list(snapshot.warnings)
        source_to_node = {
            str(getattr(node, "source_path", "") or ""): node
            for node in self.catalog.nodes
            if getattr(node, "source_path", "")
        }
        source_to_mount = {
            source: getattr(node, "mount", "") for source, node in source_to_node.items()
        }
        diagnostics = [
            *(
                _author_dashboard_diagnostic(message, "error", source_to_node, source_to_mount)
                for message in errors
            ),
            *(
                _author_dashboard_diagnostic(message, "warning", source_to_node, source_to_mount)
                for message in warnings
            ),
        ]
        diagnostics_by_mount: dict[str, list[dict[str, Any]]] = {}
        for diagnostic in diagnostics:
            diagnostics_by_mount.setdefault(str(diagnostic["mount"]), []).append(diagnostic)

        stale_entries = self.catalog.author_stale_entries()
        stale_by_mount: dict[str, list[dict[str, Any]]] = {}
        for entry in stale_entries:
            stale_by_mount.setdefault(str(entry.get("mount") or "<catalog>"), []).append(
                dict(entry)
            )

        health_by_mount = {
            item["id"]: item
            for item in self.catalog.source_health().get("mounts", [])
            if isinstance(item, dict) and item.get("id")
        }
        mount_cards: list[dict[str, Any]] = []
        dirty_page_total = 0
        doc_nodes = self.catalog.doc_nodes()
        for mount in self.catalog.mounts:
            nodes = [node for node in doc_nodes if node.mount == mount.id]
            format_counts: dict[str, int] = {}
            dirty_pages: list[dict[str, str]] = []
            for node in nodes:
                content_format = str(getattr(node, "content_format", "") or "unknown")
                format_counts[content_format] = format_counts.get(content_format, 0) + 1
                source = source_info(node)
                if source["dirty"]:
                    dirty_pages.append(
                        {
                            "title": node.title,
                            "slug": node.slug,
                            "url": node.url,
                            "source_path": str(getattr(node, "source_path", "") or ""),
                        }
                    )
            dirty_page_total += len(dirty_pages)
            mount_diagnostics = diagnostics_by_mount.get(mount.id, [])
            mount_errors = [item for item in mount_diagnostics if item["severity"] == "error"]
            mount_warnings = [item for item in mount_diagnostics if item["severity"] == "warning"]
            mount_stale = stale_by_mount.get(mount.id, [])
            source_health = health_by_mount.get(mount.id, {})
            if mount_errors:
                status = "blocked"
            elif source_health.get("status") not in {None, "healthy"}:
                status = "degraded"
            elif dirty_pages or mount_stale:
                status = "stale"
            elif mount_warnings:
                status = "review"
            else:
                status = "clean"
            mount_cards.append(
                {
                    "id": mount.id,
                    "label": mount.label,
                    "default": mount.default,
                    "url_prefix": mount.url_prefix or "/",
                    "source_root": str(mount.content_root),
                    "provider": source_health.get("provider") or mount.source.provider,
                    "status": status,
                    "health": source_health,
                    "page_count": len(nodes),
                    "formats": [
                        {"format": key, "count": count}
                        for key, count in sorted(format_counts.items())
                    ],
                    "dirty_pages": dirty_pages,
                    "dirty_count": len(dirty_pages),
                    "stale_entries": mount_stale,
                    "stale_count": len(mount_stale),
                    "error_count": len(mount_errors),
                    "warning_count": len(mount_warnings),
                    "diagnostics": mount_diagnostics[:6],
                }
            )

        blocking = [item for item in diagnostics if item["severity"] == "error"]
        ctx = {
            **self.shell_context(request=request),
            "app_surface": "author-dashboard",
            "app_page_cls": "chirp-theme-author-dashboard",
            "chirp_docs_surface": "author-dashboard",
            "catalog_title": "Author dashboard",
            "catalog_subtitle": "Import, freshness, and lint status",
            "breadcrumb_items": [{"label": "Author dashboard", "href": "/docs/_author/dashboard"}],
            "author_dashboard": {
                "catalog_generation": snapshot.catalog_generation if snapshot else None,
                "configuration_fingerprint": (
                    snapshot.configuration_fingerprint if snapshot else None
                ),
                "mounts": mount_cards,
                "diagnostics": diagnostics,
                "blocking": blocking[:12],
                "stale_entries": stale_entries,
                "summary": {
                    "mount_count": len(mount_cards),
                    "page_count": sum(item["page_count"] for item in mount_cards),
                    "error_count": len(blocking),
                    "warning_count": len(
                        [item for item in diagnostics if item["severity"] == "warning"]
                    ),
                    "dirty_page_count": dirty_page_total,
                    "stale_entry_count": len(stale_entries),
                    "freshness_count": dirty_page_total + len(stale_entries),
                },
            },
        }
        return ctx

    @staticmethod
    def view_chrome_context(view_name: str, node: Any, context: dict[str, Any]) -> dict[str, Any]:
        if view_name in {"views/doc.html", "views/changelog.html"}:
            return _catalog_layout("doc", node)
        if view_name == "views/api_reference.html":
            return _catalog_layout("api-reference", node)
        if view_name == "views/doc_list.html":
            return _catalog_layout("doc-list", node)
        if view_name == "views/collection.html":
            sections = context.get("collection_sections") or ()
            collection = context.get("collection")
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


def _catalog_layout(surface: str, node: Any) -> dict[str, Any]:
    return {
        "catalog_surface": surface,
        "catalog_with_toc": len(getattr(node, "toc", ()) or ()) > 0,
        "catalog_layout_extra": "",
    }


def _author_dashboard_diagnostic(
    message: str,
    severity: str,
    source_to_node: dict[str, Any],
    source_to_mount: dict[str, str],
) -> dict[str, Any]:
    source_path = "<catalog>"
    line = None
    detail = message
    match = re.match(r"^(?P<source>[^:]+)(?::(?P<line>\d+))?:\s*(?P<detail>.*)$", message)
    if match is not None:
        source_path = match.group("source")
        detail = match.group("detail") or message
        if match.group("line"):
            line = int(match.group("line"))
    node = source_to_node.get(source_path)
    mount = source_to_mount.get(source_path) or getattr(node, "mount", "") or "<catalog>"
    payload: dict[str, Any] = {
        "severity": severity,
        "source_path": source_path,
        "line": line,
        "message": detail,
        "raw_message": message,
        "mount": mount,
    }
    if node is not None:
        payload.update(
            {
                "title": node.title,
                "slug": node.slug,
                "url": node.url,
                "studio_url": f"/docs/_author/studio?slug={node.slug}",
            }
        )
    return payload
