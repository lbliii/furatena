"""Cohesive, source-inspectable route registrars for DocsApp."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from chirp import OOB, App, EventStream, FormAction, Fragment, Page, Request, Response, SSEEvent
from chirp.errors import MethodNotAllowed, NotFound, PayloadTooLarge

from furatena.catalog.build_identity import deployed_build_identity
from furatena.catalog.channel_manifest import channel_manifest
from furatena.catalog.deployment_profiles import deployment_profiles_manifest
from furatena.catalog.develop_exports import DEVELOP_EXPORTS, develop_export
from furatena.catalog.docs_app import (
    _AUTHOR_SSE_EVENT,
    _author_authorization_denied,
    _author_conflict,
    _draft_source_text,
    _form_bool,
    _json_response,
    _query_bool,
)
from furatena.catalog.error_experience import recovery_hits_for_query
from furatena.catalog.export import (
    api_operations_json,
    catalog_graph,
    llms_full_txt,
    meta_json,
    search_json,
    surface_json,
    tools_manifest,
)
from furatena.catalog.export import (
    llms_txt as llms_index_txt,
)
from furatena.catalog.graph_schema import EdgeKind
from furatena.catalog.operational_status import operational_status, process_health
from furatena.catalog.query import (
    DEFAULT_GRAPH_QUERY_LIMIT,
    MAX_GRAPH_QUERY_LIMIT,
    query_catalog_graph,
)
from furatena.catalog.runtime import ServeMode
from furatena.catalog.semantic import retrieve_node, semantic_index_json, semantic_search_json
from furatena.catalog.sitemap import sitemap_xml
from furatena.catalog.structure_index import build_structure_index
from furatena.cli.authoring import (
    author_new,
    author_read_source,
    author_save_source,
    author_transition,
)

_CATALOG_QUERY_FILTERS = frozenset(
    {
        "mount",
        "tag",
        "format",
        "owner",
        "team",
        "locale",
        "lang",
        "edge_kind",
        "edge",
        "kind",
        "link_edge",
        "target",
        "to",
        "linked_to",
        "source",
        "from",
        "linked_from",
        "limit",
        "offset",
    }
)
_GRAPH_EDGE_KINDS = frozenset(item.value for item in EdgeKind)


def _catalog_query_error(request: Request, edge_kind: str | None) -> Response | None:
    unknown = sorted(set(request.query.keys()) - _CATALOG_QUERY_FILTERS)
    invalid: dict[str, object] = {}
    if unknown:
        invalid["unknown"] = unknown
    normalized_edge = str(edge_kind or "").strip().lower()
    if normalized_edge and normalized_edge not in _GRAPH_EDGE_KINDS:
        invalid["edge_kind"] = normalized_edge
    for name, default, minimum, maximum in (
        ("limit", DEFAULT_GRAPH_QUERY_LIMIT, 1, MAX_GRAPH_QUERY_LIMIT),
        ("offset", 0, 0, None),
    ):
        raw = request.query.get(name)
        try:
            value = int(raw) if raw not in (None, "") else default
        except TypeError, ValueError:
            invalid[name] = raw
            continue
        if value < minimum or (maximum is not None and value > maximum):
            invalid[name] = value
    if not invalid:
        return None
    return Response(
        json.dumps(
            {
                "error": "invalid catalog query filters",
                "invalid_filters": invalid,
                "allowed_filters": sorted(_CATALOG_QUERY_FILTERS),
                "allowed_edge_kinds": sorted(_GRAPH_EDGE_KINDS),
            },
            indent=2,
        ),
        status=400,
        content_type="application/json; charset=utf-8",
    )


def register_public_routes(docs: Any, app: App) -> None:
    """Register one cohesive route surface."""
    self = docs

    @app.route("/healthz", referenced=True)
    def healthz(request: Request):
        body = process_health()
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

    @app.route("/readyz", referenced=True)
    def readyz(request: Request):
        body = operational_status(self)["readiness"]
        return Response(
            json.dumps(body, indent=2),
            status=int(body["http_status"]),
            content_type="application/json; charset=utf-8",
        )

    @app.route("/catalog/operational-status.json", referenced=True)
    def operational_status_json(request: Request):
        body = operational_status(self)
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

    @app.route("/catalog/freshness.json", referenced=True)
    def freshness_json(request: Request):
        body = operational_status(self)["freshness"]
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

    @app.route("/catalog/artifacts.json", referenced=True)
    def artifacts_json(request: Request):
        body = operational_status(self)["artifacts"]
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

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

    @app.route("/develop/", referenced=True)
    def develop_index(request: Request):
        ctx = {
            **self._shell_context(request=request),
            "develop_exports": DEVELOP_EXPORTS,
        }
        return self._render_view("views/develop.html", request, **ctx)

    @app.route("/develop/{export_id}/", referenced=True)
    def develop_export_preview(request: Request, export_id: str):
        item = develop_export(export_id)
        if item is None:
            raise NotFound(f"Develop export not found: {export_id}")
        ctx = {
            **self._shell_context(request=request),
            "develop_export": item,
            "develop_sample": self._develop_export_sample(item),
        }
        return self._render_view("views/develop_export.html", request, **ctx)


def register_author_routes(docs: Any, app: App) -> None:
    """Register one cohesive route surface."""
    self = docs

    @app.route("/docs/_author/stale")
    def author_stale(request: Request):
        if not self.serve.auto_reload:
            body = {"event": _AUTHOR_SSE_EVENT, "generation": "0", "stale": [], "current": None}
            return Response(json.dumps(body), content_type="application/json; charset=utf-8")
        slug = (request.query.get("slug") or "").strip("/")
        body = self._author_invalidation_payload(slug or None)
        return Response(json.dumps(body), content_type="application/json; charset=utf-8")

    @app.route("/docs/_author/events", referenced=True)
    async def author_events(request: Request):
        slug = (request.query.get("slug") or "").strip("/")

        async def stream():
            if not self.serve.auto_reload:
                return
            last_generation = ""
            while True:
                payload = self._author_invalidation_payload(slug or None)
                if payload["current"] or payload["stale"]:
                    generation = str(payload["generation"])
                    if generation != last_generation:
                        last_generation = generation
                        yield SSEEvent(
                            data=json.dumps(payload, separators=(",", ":")),
                            event=_AUTHOR_SSE_EVENT,
                            id=generation,
                        )
                await asyncio.sleep(0.25)

        return EventStream(stream(), heartbeat_interval=5.0)

    @app.route("/docs/_author/dashboard", referenced=True)
    def author_dashboard(request: Request):
        if not self._is_author_mode():
            return Response("author dashboard is available only in author mode", status=404)
        denied = self._browser_author_denial("status")
        if denied is not None:
            return denied
        self._ensure_catalog()
        ctx = self._author_dashboard_context(request)
        if _query_bool(request, "json", default=False):
            return _json_response({"ok": True, "data": ctx["author_dashboard"]})
        return Page.mounted("views/author_dashboard.html", **ctx)

    @app.route("/docs/_author/studio", referenced=True)
    def author_studio(request: Request):
        if not self._is_author_mode():
            return Response("author studio is available only in author mode", status=404)
        self._ensure_catalog()
        node = self._author_node_from_request_or_none(request)
        create = _query_bool(request, "new", default=False)
        if node is None and not create:
            raise NotFound("Author studio target not found.")
        if create:
            denied = self._browser_author_denial("new")
            if denied is not None:
                return denied
        elif node is not None:
            denied = self._browser_node_denial(node, "read")
            if denied is not None:
                return denied
        ctx = self._author_studio_context(
            request,
            node=node,
            create_slug=(request.query.get("slug") or "").strip().strip("/"),
            title=(request.query.get("title") or "").strip() or None,
        )
        return Page.mounted("views/author_studio.html", **ctx)

    @app.route("/docs/_author/studio/save", methods=["POST"], referenced=True)
    async def author_studio_save(request: Request):
        if not self._is_author_mode():
            return _json_response(
                {"ok": False, "error": "author studio saves are available only in author mode"},
                status=404,
            )
        self._ensure_catalog()
        form = await request.form()
        slug = str(form.get("slug") or request.query.get("slug") or "").strip().strip("/")
        source_text = str(form.get("source") or "")
        expected_revision = str(form.get("source_revision") or "").strip() or None
        title = str(form.get("title") or "").strip() or None
        create = str(form.get("mode") or "").strip() == "create"
        result = None
        subject = self._browser_author_subject()

        if create:
            source_text = _draft_source_text(source_text, slug=slug, title=title)
            result = author_new(
                slug,
                mounts=tuple(self.catalog.mounts),
                subject=subject,
                title=title,
                dry_run=False,
                confirmed=True,
                store=self.author_store,
            )
            if result.ok:
                result = author_save_source(
                    slug,
                    mounts=tuple(self.catalog.mounts),
                    subject=subject,
                    source_text=source_text,
                    expected_revision=result.source_revision,
                    mount_id=result.mount,
                    dry_run=False,
                    confirmed=True,
                    store=self.author_store,
                )
        else:
            node = self.catalog.get_by_slug(slug)
            mount_id = node.mount if node is not None else None
            result = author_save_source(
                slug,
                mounts=tuple(self.catalog.mounts),
                subject=subject,
                source_text=source_text,
                expected_revision=expected_revision,
                mount_id=mount_id,
                dry_run=False,
                confirmed=True,
                store=self.author_store,
            )

        if _author_authorization_denied(result):
            return _json_response({"ok": False, "data": result.to_dict()}, status=403)
        self._reindex_author_result(result)
        node = self.catalog.get_by_slug(slug)
        ctx = self._author_studio_context(
            request,
            node=node,
            source_text=source_text,
            result=result,
            create_slug=slug,
            title=title,
            saved=bool(result.ok),
        )
        status = 200 if result.ok else 409 if _author_conflict(result) else 422
        if request.is_htmx:
            # htmx does not swap 4xx responses by default, but author save
            # diagnostics need to render inline in the studio workspace.
            return Fragment(
                "views/author_studio.html",
                "author_studio_workspace",
                status=200,
                **ctx,
            )
        if result.ok and node is not None:
            return Page.mounted("views/author_studio.html", **ctx)
        return _json_response({"ok": False, "data": result.to_dict()}, status=status)

    @app.route("/docs/_author/page.json")
    def author_page_status(request: Request):
        if not self._is_author_mode():
            return _json_response(
                {"ok": False, "error": "author page status is available only in author mode"},
                status=404,
            )
        self._ensure_catalog()
        node = self._author_node_from_request(request)
        denied = self._browser_node_denial(node, "status")
        if denied is not None:
            return denied
        if request.is_htmx:
            return self._author_page_chrome_fragment(node)
        return _json_response(self._author_page_chrome(node))

    @app.route("/docs/_author/source")
    def author_page_source(request: Request):
        if not self._is_author_mode():
            return Response("author source is available only in author mode", status=404)
        self._ensure_catalog()
        node = self._author_node_from_request(request)
        result, source_text = author_read_source(
            node.slug,
            mounts=tuple(self.catalog.mounts),
            subject=self._browser_author_subject(),
            mount_id=node.mount,
            store=self.author_store,
        )
        if _author_authorization_denied(result):
            return _json_response({"ok": False, "data": result.to_dict()}, status=403)
        if not result.ok or source_text is None:
            return _json_response({"ok": False, "data": result.to_dict()}, status=404)
        return Response(source_text, content_type="text/plain; charset=utf-8")

    @app.route("/docs/_author/transition", methods=["POST"], referenced=True)
    async def author_page_transition(request: Request):
        if not self._is_author_mode():
            return _json_response(
                {
                    "ok": False,
                    "diagnostics": [
                        {
                            "severity": "error",
                            "message": "author lifecycle transitions require author mode",
                            "rule_id": "fura.author.authorization",
                        }
                    ],
                },
                status=403,
            )
        self._ensure_catalog()
        form = await request.form()
        node_id = str(form.get("node_id") or "").strip()
        slug = str(form.get("slug") or "").strip().strip("/")
        node = self.catalog.get_by_node_id(node_id) if node_id else None
        if node is None and slug:
            node = self.catalog.get_by_slug(slug)
        if node is None:
            return _json_response(
                {
                    "ok": False,
                    "diagnostics": [
                        {
                            "severity": "error",
                            "message": "author page target not found",
                            "rule_id": "fura.author.target",
                        }
                    ],
                },
                status=404,
            )
        operation = str(form.get("operation") or "").strip()
        expected_revision = str(form.get("source_revision") or "").strip() or None
        if operation not in {"draft", "publish", "unpublish", "archive"}:
            return _json_response(
                {
                    "ok": False,
                    "diagnostics": [
                        {
                            "severity": "error",
                            "message": "operation must be draft, publish, unpublish, or archive",
                            "rule_id": "fura.author",
                        }
                    ],
                },
                status=400,
            )
        result = author_transition(
            operation,
            node.slug,
            mounts=tuple(self.catalog.mounts),
            subject=self._browser_author_subject(),
            expected_revision=expected_revision,
            mount_id=node.mount,
            dry_run=_form_bool(form, "dry_run", default=True),
            confirmed=_form_bool(form, "confirmed", default=False),
            store=self.author_store,
        )
        if not result.ok:
            status = (
                403
                if _author_authorization_denied(result)
                else 409
                if _author_conflict(result)
                else 422
            )
            return _json_response({"ok": False, "data": result.to_dict()}, status=status)
        self._reindex_author_result(result)
        refreshed = self.catalog.get_by_slug(node.slug, mount=node.mount) or node
        if request.is_htmx:
            return FormAction(
                refreshed.url,
                self._author_page_chrome_fragment(refreshed),
                trigger="furaAuthorTransition",
            )
        return FormAction(refreshed.url)


def register_search_routes(docs: Any, app: App) -> None:
    """Register one cohesive route surface."""
    self = docs

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
        if request.headers.get("HX-Request") and target == "search-results-panel":
            ctx["search_oob"] = True
            main = Fragment("search.html", "search_results", **ctx)
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
        return Page.mounted("search.html", **ctx)

    @app.route("/errors/suggest")
    def error_suggest(request: Request):
        self._ensure_catalog()
        query = (request.query.get("q") or "").strip()
        keyword_hits, semantic_hits = (
            recovery_hits_for_query(self, query, limit=6) if query else ((), ())
        )
        return Fragment(
            "partials/error_suggest_panel.html",
            "error_suggest_panel",
            search_query=query,
            keyword_hits=keyword_hits,
            semantic_hits=semantic_hits,
            hits=keyword_hits,
        )

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
        subject = self._output_access_subject(request)
        if query:
            from furatena.catalog.export import search_json_for_query

            body = search_json_for_query(
                self.catalog,
                query,
                base_url=base,
                subject=subject,
            )
        else:
            frozen = self._frozen_artifact_response(
                "search.json",
                content_type="application/json; charset=utf-8",
            )
            if frozen is not None:
                return frozen
            body = search_json(self.catalog, base_url=base, subject=subject)
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")


def register_catalog_routes(docs: Any, app: App) -> None:
    """Register one cohesive route surface."""
    self = docs

    @app.route("/tools.json", referenced=True)
    def tools_json(request: Request):
        self._ensure_catalog()
        frozen = self._frozen_artifact_response(
            "tools.json",
            content_type="application/json; charset=utf-8",
        )
        if frozen is not None:
            return frozen
        body = tools_manifest(
            self.catalog,
            base_url=self._site_base(request),
            site_name=self.config.site.name,
            subject=self._output_access_subject(request),
        )
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

    @app.route("/catalog/api-operations.json", referenced=True)
    def catalog_api_operations_json(request: Request):
        self._ensure_catalog()
        frozen = self._frozen_artifact_response(
            "catalog/api-operations.json",
            content_type="application/json; charset=utf-8",
        )
        if frozen is not None:
            return frozen
        body = api_operations_json(
            self.catalog,
            base_url=self._site_base(request),
            subject=self._output_access_subject(request),
        )
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

    @app.route("/semantic.json", referenced=True)
    def semantic_json_route(request: Request):
        self._ensure_catalog()
        frozen = self._frozen_artifact_response(
            "semantic.json",
            content_type="application/json; charset=utf-8",
        )
        if frozen is not None:
            return frozen
        body = semantic_index_json(
            self.catalog,
            self.embedding_index,
            subject=self._output_access_subject(request),
        )
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

    @app.route("/structure.json", referenced=True)
    def structure_json_route(request: Request):
        self._ensure_catalog()
        frozen = self._frozen_artifact_response(
            "structure.json",
            content_type="application/json; charset=utf-8",
        )
        if frozen is not None:
            return frozen
        body = build_structure_index(
            self.catalog,
            subject=self._output_access_subject(request),
        )
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

    @app.route("/sitemap.xml", referenced=True)
    def sitemap(request: Request):
        self._ensure_catalog()
        body = sitemap_xml(
            self.catalog,
            base_url=self._site_base(request),
            subject=self._output_access_subject(request),
        )
        return Response(body, content_type="application/xml; charset=utf-8")

    @app.route("/index.txt", referenced=True)
    def home_index_txt(request: Request):
        self._ensure_catalog()
        match = self._resolve_page_from_path("/")
        return self._plaintext_node_response(match.node)

    @app.route("/search/semantic", referenced=True)
    def search_semantic(request: Request):
        self._ensure_catalog()
        base = self._site_base(request)
        query = (request.query.get("q") or "").strip()
        mount = (request.query.get("mount") or "").strip() or None
        edition = (request.query.get("edition") or "").strip() or None
        tag = (request.query.get("tag") or "").strip() or None
        url_prefix = (request.query.get("url_prefix") or "").strip() or None
        subject = self._output_access_subject(request)
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
                tag=tag,
                url_prefix=url_prefix,
                subject=subject,
            )
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

    @app.route("/catalog/retrieve", referenced=True)
    def catalog_retrieve(request: Request):
        self._ensure_catalog()
        node_id = (request.query.get("id") or request.query.get("node_id") or "").strip()
        if not node_id:
            return Response(
                json.dumps({"error": "missing node id"}),
                status=400,
                content_type="application/json; charset=utf-8",
            )
        payload = retrieve_node(
            self.catalog,
            self.embedding_index,
            node_id,
            include_private=False,
            subject=self._output_access_subject(request),
        )
        if payload is None:
            return Response(
                json.dumps({"error": "not found"}),
                status=404,
                content_type="application/json; charset=utf-8",
            )
        return Response(
            json.dumps(payload, indent=2), content_type="application/json; charset=utf-8"
        )

    @app.route("/catalog.json", referenced=True)
    def catalog_json(request: Request):
        self._ensure_catalog()
        frozen = self._frozen_artifact_response(
            "catalog.json",
            content_type="application/json; charset=utf-8",
        )
        if frozen is not None:
            return frozen
        body = json.dumps(
            catalog_graph(
                self.catalog,
                subject=self._output_access_subject(request),
            ),
            indent=2,
        )
        return Response(body, content_type="application/json; charset=utf-8")

    @app.route("/catalog/query.json", referenced=True)
    @app.route("/graph/query.json", referenced=True)
    def catalog_query_json(request: Request):
        self._ensure_catalog()
        edge_kind = (
            request.query.get("edge_kind")
            or request.query.get("edge")
            or request.query.get("kind")
            or request.query.get("link_edge")
        )
        target = (
            request.query.get("target") or request.query.get("to") or request.query.get("linked_to")
        )
        source = (
            request.query.get("source")
            or request.query.get("from")
            or request.query.get("linked_from")
        )
        query_error = _catalog_query_error(request, edge_kind)
        if query_error is not None:
            return query_error
        payload = query_catalog_graph(
            self.catalog,
            mount=request.query.get("mount"),
            tag=request.query.get("tag"),
            format=request.query.get("format"),
            owner=request.query.get("owner") or request.query.get("team"),
            locale=request.query.get("locale") or request.query.get("lang"),
            edge_kind=edge_kind,
            source=source,
            target=target,
            subject=self._output_access_subject(request),
            limit=int(request.query.get("limit") or DEFAULT_GRAPH_QUERY_LIMIT),
            offset=int(request.query.get("offset") or 0),
        )
        return Response(
            json.dumps(payload, indent=2), content_type="application/json; charset=utf-8"
        )

    @app.route("/catalog/source-health.json", referenced=True)
    def catalog_source_health_json(request: Request):
        self._ensure_catalog()
        mount = (request.query.get("mount") or "").strip() or None
        body = self.catalog.source_health(mount=mount)
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

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
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")

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
        return Response(payload, content_type="application/octet-stream")

    @app.route("/llms.txt", referenced=True)
    def llms_txt(request: Request):
        self._ensure_catalog()
        frozen = self._frozen_artifact_response(
            "llms.txt",
            content_type="text/plain; charset=utf-8",
        )
        if frozen is not None:
            return frozen
        body = llms_index_txt(
            self.catalog,
            site_name=self.config.site.name,
            site_description=self.config.site.description,
            subject=self._output_access_subject(request),
        )
        return Response(body, content_type="text/plain; charset=utf-8")

    @app.route("/llms-full.txt", referenced=True)
    def llms_full(request: Request):
        self._ensure_catalog()
        frozen = self._frozen_artifact_response(
            "llms-full.txt",
            content_type="text/plain; charset=utf-8",
        )
        if frozen is not None:
            return frozen
        body = llms_full_txt(
            self.catalog,
            site_name=self.config.site.name,
            subject=self._output_access_subject(request),
        )
        return Response(body, content_type="text/plain; charset=utf-8")

    @app.route("/meta.json", referenced=True)
    def meta_json_route(request: Request):
        self._ensure_catalog()
        payload = meta_json(
            self.catalog,
            subject=self._output_access_subject(request),
        )
        payload["build"] = deployed_build_identity(self.catalog)
        body = json.dumps(payload, indent=2)
        return Response(body, content_type="application/json; charset=utf-8")

    @app.route("/surface.json", referenced=True)
    def surface_json_route():
        self._ensure_catalog()
        body = json.dumps(surface_json(self.config, self.catalog), indent=2)
        return Response(body, content_type="application/json; charset=utf-8")

    @app.route("/channels.json", referenced=True)
    def channels_json_route(request: Request):
        self._ensure_catalog()
        body = json.dumps(
            channel_manifest(
                self.catalog,
                config=self.config,
                base_url=self._site_base(request),
                mode="live",
            ),
            indent=2,
        )
        return Response(body, content_type="application/json; charset=utf-8")

    @app.route("/deployment-profiles.json", referenced=True)
    def deployment_profiles_json_route(request: Request):
        body = json.dumps(
            deployment_profiles_manifest(base_url=self._site_base(request)),
            indent=2,
        )
        return Response(body, content_type="application/json; charset=utf-8")

    @app.route("/routes.json", referenced=True)
    def routes_json_route(request: Request):
        from furatena.catalog.route_manifest import route_manifest_payload

        body = route_manifest_payload(app, catalog=self.catalog)
        return Response(json.dumps(body, indent=2), content_type="application/json; charset=utf-8")


def og_image(name: str) -> Response:
    """Render the source-inspectable Open Graph image route."""
    label = name.replace("-", " ").title()
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#0f172a"/>
  <text x="80" y="180" fill="#e2e8f0" font-family="system-ui,sans-serif" font-size="48" font-weight="700">Furatena</text>
  <text x="80" y="280" fill="#94a3b8" font-family="system-ui,sans-serif" font-size="36">{label}</text>
</svg>"""
    return Response(svg, content_type="image/svg+xml; charset=utf-8")


def register_media_routes(app: App) -> None:
    """Register source-inspectable media handlers."""
    app.route("/og/{name}", referenced=True)(og_image)


def register_error_routes(docs: Any, app: App) -> None:
    """Register one cohesive route surface."""
    self = docs

    @app.route("/favicon.ico", referenced=False)
    def favicon():
        branding_dir = next(
            (
                mount.directory
                for mount in self.theme.static_mounts
                if mount.url_prefix == "/docs-theme/branding"
            ),
            None,
        )
        if branding_dir is None:
            raise NotFound("Favicon not configured.")
        icon = branding_dir / "favicon.ico"
        if not icon.is_file():
            icon = branding_dir / "favicon.svg"
        if not icon.is_file():
            raise NotFound("Favicon not found.")
        content_type = "image/x-icon" if icon.suffix == ".ico" else "image/svg+xml"
        return Response(icon.read_bytes(), content_type=content_type)

    @app.error(404)
    @app.error(NotFound)
    def not_found(request: Request, exc: Exception | None = None):
        return self._render_error(request, exc, status=404)

    @app.error(403)
    def forbidden(request: Request, exc: Exception | None = None):
        detail = str(getattr(exc, "detail", ""))
        if detail.startswith("CSRF token"):
            return _json_response(
                {
                    "ok": False,
                    "diagnostics": [
                        {
                            "severity": "error",
                            "message": detail,
                            "rule_id": "fura.author.csrf",
                        }
                    ],
                },
                status=403,
            )
        return self._render_error(request, exc, status=403)

    @app.error(405)
    @app.error(MethodNotAllowed)
    def method_not_allowed(request: Request, exc: Exception | None = None):
        if request.path == "/docs/_author/transition":
            return _json_response(
                {
                    "ok": False,
                    "diagnostics": [
                        {
                            "severity": "error",
                            "message": "author lifecycle transitions require POST",
                            "rule_id": "fura.author.method",
                        }
                    ],
                },
                status=405,
            )
        return self._render_error(request, exc, status=405)

    @app.error(413)
    @app.error(PayloadTooLarge)
    def payload_too_large(request: Request, exc: Exception | None = None):
        return self._render_error(request, exc, status=413)

    @app.error(500)
    def server_error(request: Request, exc: Exception | None = None):
        return self._render_error(request, exc, status=500)
