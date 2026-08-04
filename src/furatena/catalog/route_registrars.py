"""Cohesive, source-inspectable route registrars for DocsApp."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import threading
from collections.abc import Mapping
from typing import Any

from chirp import OOB, App, EventStream, FormAction, Fragment, Page, Request, Response, SSEEvent
from chirp.errors import MethodNotAllowed, NotFound, PayloadTooLarge
from chirp.server.conditional import evaluate_conditional_response

from furatena.catalog.build_identity import deployed_build_identity
from furatena.catalog.catalog_shards import catalog_shard_path, is_safe_mount_id
from furatena.catalog.channel_manifest import channel_manifest
from furatena.catalog.content_deployment import (
    ContentDeploymentConfig,
    ContentDeploymentConflict,
    ContentDeploymentError,
    ContentDeploymentStore,
)
from furatena.catalog.content_ir_diff import (
    DEFAULT_CONTENT_IR_DIFF_LIMIT,
    ContentIRDiffError,
    diff_content_ir,
)
from furatena.catalog.content_refresh import (
    ContentRefreshActor,
    ContentRefreshConflict,
    ContentRefreshRequest,
    ContentRefreshService,
    authenticate_content_actor,
)
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
from furatena.catalog.railway_preview import runtime_railway_preview_manifest
from furatena.catalog.runtime import ServeMode
from furatena.catalog.semantic import retrieve_node, semantic_index_json, semantic_search_json
from furatena.catalog.sitemap import sitemap_xml
from furatena.catalog.structure_index import build_structure_index
from furatena.catalog.version_artifacts import versions_for_mount, versions_manifest
from furatena.cli.authoring import (
    author_new,
    author_read_source,
    author_save_source,
    author_transition,
)

_CATALOG_QUERY_FILTERS = frozenset(
    {
        "mount",
        "edition",
        "status",
        "include_preview",
        "include_eol",
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
_CONTENT_IR_DIFF_FILTERS = frozenset(
    {"mount", "slug", "from", "to", "include_eol", "limit", "offset"}
)
_GRAPH_EDGE_KINDS = frozenset(item.value for item in EdgeKind)
_CATALOG_QUERY_MEDIA_TYPE = "application/vnd.furatena.catalog-query+json;version=1"
_CATALOG_ACCEPT_QUERY = f'{_CATALOG_QUERY_MEDIA_TYPE.partition(";")[0]};version="1"'
_CATALOG_QUERY_CACHE_CONTROL = "private, max-age=0, must-revalidate"
_CONTENT_NO_STORE = (("Cache-Control", "private, no-store"),)


def _mapping_bool(params: Mapping[str, Any], name: str) -> bool:
    raw = params.get(name)
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _content_store() -> ContentDeploymentStore | None:
    config = ContentDeploymentConfig.from_environment()
    return ContentDeploymentStore(config) if config is not None else None


def _schedule_content_restart() -> bool:
    if os.environ.get("FURA_CONTENT_RESTART_AFTER_PROMOTION", "1").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False

    def _terminate() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    timer = threading.Timer(1.0, _terminate)
    timer.daemon = True
    timer.start()
    return True


def _content_json(payload: Mapping[str, Any], *, status: int = 200) -> Response:
    return Response(
        json.dumps(dict(payload), indent=2),
        status=status,
        content_type="application/json; charset=utf-8",
        headers=_CONTENT_NO_STORE,
    )


async def _authorized_content_operation(
    request: Request,
) -> tuple[ContentDeploymentStore, bytes, ContentRefreshActor] | Response:
    store = _content_store()
    if store is None:
        raise NotFound(
            "Managed content operations are unavailable because deployment is not configured."
        )
    body = await request.body()
    actor = authenticate_content_actor(
        request.headers.get("authorization"),
        transport="http",
    )
    if actor is None:
        return Response(
            json.dumps({"error": "content operation authorization failed"}),
            status=401,
            content_type="application/json; charset=utf-8",
            headers=(*_CONTENT_NO_STORE, ("WWW-Authenticate", 'Bearer realm="Furatena content"')),
        )
    return store, body, actor


def _catalog_query_error(params: Mapping[str, Any], edge_kind: str | None) -> Response | None:
    unknown = sorted(set(params) - _CATALOG_QUERY_FILTERS)
    invalid: dict[str, object] = {}
    if unknown:
        invalid["unknown"] = unknown
    for name in _CATALOG_QUERY_FILTERS - {"limit", "offset", "include_preview", "include_eol"}:
        raw = params.get(name)
        if raw is not None and not isinstance(raw, str):
            invalid[name] = raw
    for name in ("include_preview", "include_eol"):
        raw = params.get(name)
        if (raw is not None and not isinstance(raw, (str, bool))) or (
            isinstance(raw, str)
            and raw.strip().lower()
            not in {
                "",
                "0",
                "1",
                "false",
                "true",
                "no",
                "yes",
                "off",
                "on",
            }
        ):
            invalid[name] = raw
    normalized_edge = str(edge_kind or "").strip().lower()
    if normalized_edge and normalized_edge not in _GRAPH_EDGE_KINDS:
        invalid["edge_kind"] = normalized_edge
    for name, default, minimum, maximum in (
        ("limit", DEFAULT_GRAPH_QUERY_LIMIT, 1, MAX_GRAPH_QUERY_LIMIT),
        ("offset", 0, 0, None),
    ):
        raw = params.get(name)
        if isinstance(raw, bool):
            invalid[name] = raw
            continue
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


def _catalog_query_response(
    response: Response,
    *,
    request: Request,
    query_input: Mapping[str, Any] | None = None,
) -> Response:
    """Attach HTTP QUERY discovery and body-aware validators."""
    response = response.with_header("Accept-Query", _CATALOG_ACCEPT_QUERY)
    if request.method != "QUERY" or response.status != 200 or query_input is None:
        return response
    canonical_query = json.dumps(
        query_input,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    response_body = (
        response.body.encode("utf-8") if isinstance(response.body, str) else response.body
    )
    digest = hashlib.sha256(canonical_query + b"\0" + response_body).hexdigest()
    response = (
        response.with_header("ETag", f'"fura-query-v1-{digest}"')
        .with_header("Cache-Control", _CATALOG_QUERY_CACHE_CONTROL)
        .with_header("Vary", "Accept, Content-Type")
    )
    return evaluate_conditional_response(request, response)


def register_public_routes(docs: Any, app: App) -> None:
    """Register one cohesive route surface."""
    self = docs
    portal_view = self.config.views.get("portal") or "views/portal.html"

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

    @app.route("/_fura/content/status", referenced=False)
    def content_status(request: Request):
        store = _content_store()
        if store is None:
            raise NotFound(
                "Managed content status is unavailable because deployment is not configured."
            )
        service = ContentRefreshService(store)
        latest = service.latest()
        raw_status = store.status()
        raw_receipt = raw_status.get("receipt")
        receipt = (
            {
                key: raw_receipt.get(key)
                for key in (
                    "schema_version",
                    "generation",
                    "status",
                    "resolved_ref",
                    "image_digest",
                    "build_commit",
                    "promoted_at",
                    "manifest_digest",
                    "verification_status",
                )
            }
            if isinstance(raw_receipt, dict)
            else None
        )
        running_content = deployed_build_identity(self)["content"]
        verification = raw_status.get("generation_verification")
        if raw_status.get("rollback_hold"):
            lifecycle_state = "rollback"
        elif isinstance(verification, dict) and verification.get("status") == "degraded":
            lifecycle_state = "degraded"
        elif running_content.get("activation_pending_restart"):
            lifecycle_state = "stale"
        elif latest is not None and latest.state.value in {"queued", "staging"}:
            lifecycle_state = "staging"
        elif latest is not None and latest.state.value == "failed":
            lifecycle_state = "degraded" if raw_status.get("active_generation") else "failed"
        else:
            lifecycle_state = "active" if raw_status.get("active_generation") else "failed"
        return _content_json(
            {
                "configured": True,
                "status": raw_status.get("status"),
                "lifecycle_state": lifecycle_state,
                "active_generation": raw_status.get("active_generation"),
                "last_known_good_generation": raw_status.get("last_known_good_generation"),
                "rollback_hold": bool(raw_status.get("rollback_hold")),
                "receipt": receipt,
                "generation_verification": verification,
                "generation_quarantine": raw_status.get("generation_quarantine"),
                "replica_contract": "single_replica_v1",
                "latest_refresh_operation": (
                    latest.public_dict(
                        status_url=f"/_fura/content/operations/{latest.operation_id}",
                        include_failure_message=False,
                    )
                    if latest is not None
                    else None
                ),
            }
        )

    @app.route("/_fura/content/refresh", methods=["POST"], referenced=False)
    async def content_refresh(request: Request):
        authorized = await _authorized_content_operation(request)
        if isinstance(authorized, Response):
            return authorized
        store, body, actor = authorized
        service = ContentRefreshService(store, restart_scheduler=_schedule_content_restart)
        try:
            if not body.strip():
                receipt = service.submit_compatibility(actor=actor)
            else:
                raw = json.loads(body)
                if not isinstance(raw, dict):
                    raise ValueError("The content refresh request body must be a JSON object.")
                receipt = service.submit(ContentRefreshRequest.from_dict(raw), actor=actor)
        except (json.JSONDecodeError, ValueError) as exc:
            return _content_json(
                {"error": {"code": "invalid_refresh_request", "message": str(exc)}},
                status=400,
            )
        except ContentRefreshConflict as exc:
            return _content_json(
                {"error": {"code": exc.code, "message": str(exc)}},
                status=409,
            )
        status_url = f"/_fura/content/operations/{receipt.operation_id}"
        return _content_json(receipt.public_dict(status_url=status_url), status=202)

    @app.route("/_fura/content/operations/{operation_id}", referenced=False)
    async def content_refresh_operation(request: Request, operation_id: str):
        authorized = await _authorized_content_operation(request)
        if isinstance(authorized, Response):
            return authorized
        store, _body, _actor = authorized
        receipt = ContentRefreshService(store).get(operation_id)
        if receipt is None:
            raise NotFound("The requested durable content refresh operation was not found.")
        status_url = f"/_fura/content/operations/{receipt.operation_id}"
        return _content_json(receipt.public_dict(status_url=status_url))

    @app.route("/_fura/content/rollback", methods=["POST"], referenced=False)
    async def content_rollback(request: Request):
        authorized = await _authorized_content_operation(request)
        if isinstance(authorized, Response):
            return authorized
        store, body, actor = authorized
        try:
            reason = "Operator requested content rollback."
            if body.strip():
                raw = json.loads(body)
                if not isinstance(raw, dict) or set(raw) - {"reason"}:
                    raise ValueError("The content rollback request body may contain only reason.")
                reason = str(raw.get("reason") or "").strip()
            receipt = store.rollback(actor=actor.actor, reason=reason)
        except (json.JSONDecodeError, ValueError) as exc:
            return _content_json(
                {"error": {"code": "invalid_rollback_request", "message": str(exc)}},
                status=400,
            )
        except ContentDeploymentConflict as exc:
            return _content_json(
                {"error": {"code": exc.code, "message": str(exc)}},
                status=409,
            )
        except ContentDeploymentError as exc:
            return _content_json({"status": "failed", "error": str(exc)}, status=409)
        restart_scheduled = _schedule_content_restart()
        return _content_json({**receipt, "restart_scheduled": restart_scheduled})

    @app.route("/preview-manifest.json", referenced=True)
    def preview_manifest_json(request: Request):
        status = operational_status(self)
        manifest = runtime_railway_preview_manifest(self, status)
        if manifest is None:
            raise NotFound("Preview manifest is available only in pull-request environments")
        return Response(
            json.dumps(manifest.to_dict(), indent=2),
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

    @app.route("/portal/", referenced=True, template=portal_view)
    def portal(request: Request):
        self._ensure_catalog()
        ctx = {
            **self._shell_context(request=request),
            "mounts": self.catalog.portal_mounts(),
            "page_count": len(self.catalog.nodes),
        }
        ctx["chirp_docs_surface"] = self.views.surface(portal_view)
        ctx.update(self._view_chrome_context(portal_view, ctx.get("node"), ctx))
        return self._render_view(portal_view, request, **ctx)

    @app.route("/develop/", referenced=True, template="views/develop.html")
    def develop_index(request: Request):
        ctx = {
            **self._shell_context(request=request),
            "develop_exports": DEVELOP_EXPORTS,
        }
        return self._render_view("views/develop.html", request, **ctx)

    @app.route(
        "/develop/{export_id}/",
        referenced=True,
        template="views/develop_export.html",
    )
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

    @app.route(
        "/docs/_author/dashboard",
        referenced=True,
        template="views/author_dashboard.html",
    )
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

    @app.route(
        "/docs/_author/studio",
        referenced=True,
        template="views/author_studio.html",
    )
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

    @app.route(
        "/docs/_author/studio/save",
        methods=["POST"],
        referenced=True,
        template="views/author_studio.html",
    )
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
        force_validation = _query_bool(request, "validate", default=False)
        inspect_public = _query_bool(request, "inspect_public", default=False)
        feedback = None
        if inspect_public:
            denied = self._browser_node_denial(node, "inspect_publication_impact")
            if denied is not None:
                return denied
            source = self._author_source_info(node)
            inspection = author_transition(
                "publish",
                node.slug,
                mounts=tuple(self.catalog.mounts),
                subject=self._browser_author_subject(),
                expected_revision=source["revision"],
                mount_id=node.mount,
                dry_run=True,
                confirmed=False,
                store=self.author_store,
            )
            if _author_authorization_denied(inspection):
                return _json_response({"ok": False, "data": inspection.to_dict()}, status=403)
            feedback = inspection.to_dict()
        if request.is_htmx:
            return self._author_page_chrome_fragment(
                node,
                force_validation=force_validation,
                feedback=feedback,
            )
        chrome = self._author_page_chrome(
            node,
            force_validation=force_validation,
            feedback=feedback,
        )
        if feedback is not None:
            return _json_response(
                {
                    **chrome,
                    "public_inspection": feedback,
                }
            )
        return _json_response(chrome)

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
        dry_run = _form_bool(form, "dry_run", default=True)
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
            dry_run=dry_run,
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
            if request.is_htmx and status != 403:
                return self._author_page_chrome_fragment(
                    node,
                    status=status,
                    feedback=result.to_dict(),
                )
            return _json_response({"ok": False, "data": result.to_dict()}, status=status)
        refreshed = self.catalog.get_by_slug(node.slug, mount=node.mount) or node
        if dry_run:
            feedback = result.to_dict()
            if request.is_htmx:
                return self._author_page_chrome_fragment(
                    refreshed,
                    feedback=feedback,
                )
            return FormAction(refreshed.url)
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

    @app.route("/search", template="search.html")
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

    @app.route("/errors/suggest", template="partials/error_suggest_panel.html")
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

    @app.route("/search/suggest", template="partials/search_suggest.html")
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
        status = (request.query.get("status") or "").strip() or None
        include_preview = _query_bool(request, "include_preview", default=False)
        include_eol = _query_bool(request, "include_eol", default=False)
        if query:
            from furatena.catalog.export import search_json_for_query

            body = search_json_for_query(
                self.catalog,
                query,
                base_url=base,
                subject=subject,
                status=status,
                include_preview=include_preview,
                include_eol=include_eol,
            )
        else:
            if not any((status, include_preview, include_eol)):
                frozen = self._frozen_artifact_response(
                    "search.json",
                    content_type="application/json; charset=utf-8",
                )
                if frozen is not None:
                    return frozen
            body = search_json(
                self.catalog,
                base_url=base,
                subject=subject,
                status=status,
                include_preview=include_preview,
                include_eol=include_eol,
            )
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
        selected_edition = edition or "latest"
        status = (request.query.get("status") or "").strip() or None
        include_preview = _query_bool(request, "include_preview", default=False)
        include_eol = _query_bool(request, "include_eol", default=False)
        target_mount = mount or self.catalog.default_mount.id
        if not self.catalog.has_edition(target_mount, selected_edition):
            return Response(
                json.dumps(
                    {
                        "error": "unknown edition",
                        "edition": selected_edition,
                        "mount": target_mount,
                        "recovery": "choose an edition reported by channels.json",
                    },
                    indent=2,
                ),
                status=400,
                content_type="application/json; charset=utf-8",
            )
        with self.catalog.use_edition(selected_edition):
            if not query:
                body = {"schema_version": 1, "query": "", "count": 0, "results": []}
            else:
                body = semantic_search_json(
                    self.catalog,
                    self._edition_embedding_index(selected_edition),
                    query,
                    base_url=base,
                    mount=mount,
                    edition=selected_edition,
                    tag=tag,
                    url_prefix=url_prefix,
                    subject=subject,
                    status=status,
                    include_preview=include_preview,
                    include_eol=include_eol,
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
            include_eol=_query_bool(request, "include_eol", default=False),
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

    @app.route("/catalog/mounts/{mount_id}", referenced=True)
    def catalog_mount_json(request: Request, mount_id: str):
        self._ensure_catalog()
        if not mount_id.endswith(".json"):
            raise NotFound(f"Catalog shard not found: {mount_id}")
        mount_id = mount_id[: -len(".json")]
        if not is_safe_mount_id(mount_id):
            raise NotFound(f"Catalog shard not found: {mount_id}")
        subject = self._output_access_subject(request)
        shard = self.catalog._active_shards().get(mount_id)
        if shard is None or not self.catalog.can_access_mount(
            mount_id,
            subject,
            permission="export",
        ):
            raise NotFound(f"Catalog shard not found: {mount_id}")

        route_path = catalog_shard_path(mount_id)
        if route_path is not None:
            frozen = self._frozen_artifact_response(
                route_path.as_posix(),
                content_type="application/json; charset=utf-8",
            )
            if frozen is not None:
                return frozen
        # Older freezes keep the canonical shard under mounts/<id>/catalog.json.
        frozen = self._frozen_artifact_response(
            f"mounts/{mount_id}/catalog.json",
            content_type="application/json; charset=utf-8",
        )
        if frozen is not None:
            return frozen

        graph = catalog_graph(shard, subject=subject)
        graph["mount"] = mount_id
        return Response(
            json.dumps(graph, indent=2),
            content_type="application/json; charset=utf-8",
        )

    @app.route(
        "/catalog/query.json",
        methods=["GET", "QUERY"],
        query_media_types=(_CATALOG_QUERY_MEDIA_TYPE,),
        referenced=True,
    )
    @app.route(
        "/graph/query.json",
        methods=["GET", "QUERY"],
        query_media_types=(_CATALOG_QUERY_MEDIA_TYPE,),
        referenced=True,
    )
    async def catalog_query_json(request: Request):
        self._ensure_catalog()
        if request.method == "QUERY" and request.path == "/graph/query.json":
            return _catalog_query_response(
                Response(
                    json.dumps({"redirect": "/catalog/query.json"}),
                    status=308,
                    content_type="application/json; charset=utf-8",
                ).with_header("Location", "/catalog/query.json"),
                request=request,
            )
        params: Mapping[str, Any] = request.query
        if request.method == "QUERY":
            if request.query:
                return _catalog_query_response(
                    Response(
                        json.dumps(
                            {
                                "error": "QUERY filters must be supplied in the request content",
                                "invalid_filters": {"query_string": sorted(request.query.keys())},
                            },
                            indent=2,
                        ),
                        status=400,
                        content_type="application/json; charset=utf-8",
                    ),
                    request=request,
                )
            try:
                query_content = await request.json()
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return _catalog_query_response(
                    Response(
                        json.dumps(
                            {"error": "malformed catalog query JSON", "detail": str(exc)},
                            indent=2,
                        ),
                        status=400,
                        content_type="application/json; charset=utf-8",
                    ),
                    request=request,
                )
            if not isinstance(query_content, dict):
                return _catalog_query_response(
                    Response(
                        json.dumps(
                            {"error": "catalog query content must be a JSON object"}, indent=2
                        ),
                        status=422,
                        content_type="application/json; charset=utf-8",
                    ),
                    request=request,
                )
            params = query_content
        edge_kind = (
            params.get("edge_kind")
            or params.get("edge")
            or params.get("kind")
            or params.get("link_edge")
        )
        target = params.get("target") or params.get("to") or params.get("linked_to")
        source = params.get("source") or params.get("from") or params.get("linked_from")
        query_error = _catalog_query_error(
            params, str(edge_kind) if edge_kind is not None else None
        )
        if query_error is not None:
            return _catalog_query_response(query_error, request=request)
        requested_edition = str(params.get("edition") or "latest").strip()
        target_mount = str(params.get("mount") or self.catalog.default_mount.id).strip()
        if not self.catalog.has_edition(target_mount, requested_edition):
            return _catalog_query_response(
                Response(
                    json.dumps(
                        {
                            "error": "unknown edition",
                            "edition": requested_edition,
                            "mount": target_mount,
                            "recovery": "choose an edition reported by channels.json",
                        },
                        indent=2,
                    ),
                    status=400,
                    content_type="application/json; charset=utf-8",
                ),
                request=request,
            )
        with self.catalog.use_edition(requested_edition):
            try:
                payload = query_catalog_graph(
                    self.catalog,
                    mount=params.get("mount"),
                    edition=requested_edition,
                    status=params.get("status"),
                    include_preview=_mapping_bool(params, "include_preview"),
                    include_eol=_mapping_bool(params, "include_eol"),
                    tag=params.get("tag"),
                    format=params.get("format"),
                    owner=params.get("owner") or params.get("team"),
                    locale=params.get("locale") or params.get("lang"),
                    edge_kind=str(edge_kind) if edge_kind is not None else None,
                    source=str(source) if source is not None else None,
                    target=str(target) if target is not None else None,
                    subject=self._output_access_subject(request),
                    limit=int(params.get("limit") or DEFAULT_GRAPH_QUERY_LIMIT),
                    offset=int(params.get("offset") or 0),
                )
            except ValueError as exc:
                return _catalog_query_response(
                    Response(
                        json.dumps({"error": "invalid lifecycle filter", "detail": str(exc)}),
                        status=400,
                        content_type="application/json; charset=utf-8",
                    ),
                    request=request,
                )
        return _catalog_query_response(
            Response(json.dumps(payload, indent=2), content_type="application/json; charset=utf-8"),
            request=request,
            query_input=params,
        )

    @app.route("/catalog/diff", referenced=True)
    def catalog_content_ir_diff(request: Request):
        self._ensure_catalog()
        unknown = sorted(set(request.query) - _CONTENT_IR_DIFF_FILTERS)
        from_edition = str(request.query.get("from") or "").strip()
        to_edition = str(request.query.get("to") or "").strip()
        invalid: dict[str, Any] = {}
        if unknown:
            invalid["unknown"] = unknown
        if not from_edition:
            invalid["from"] = request.query.get("from")
        if not to_edition:
            invalid["to"] = request.query.get("to")
        include_eol_raw = request.query.get("include_eol")
        if include_eol_raw is not None and str(include_eol_raw).strip().lower() not in {
            "0",
            "1",
            "false",
            "no",
            "off",
            "on",
            "true",
            "yes",
        }:
            invalid["include_eol"] = include_eol_raw
        try:
            limit = int(request.query.get("limit") or DEFAULT_CONTENT_IR_DIFF_LIMIT)
            offset = int(request.query.get("offset") or 0)
        except TypeError, ValueError:
            invalid["pagination"] = {
                "limit": request.query.get("limit"),
                "offset": request.query.get("offset"),
            }
            limit = DEFAULT_CONTENT_IR_DIFF_LIMIT
            offset = 0
        if invalid:
            return Response(
                json.dumps(
                    {
                        "schema_version": 1,
                        "ok": False,
                        "error": {
                            "code": "invalid_query",
                            "message": "Invalid Content IR diff query.",
                            "recovery": "Pass from/to editions and only documented diff parameters.",
                            "invalid": invalid,
                        },
                    },
                    indent=2,
                ),
                status=400,
                content_type="application/json; charset=utf-8",
            )
        try:
            payload = diff_content_ir(
                self.catalog,
                mount=str(request.query.get("mount") or self.catalog.default_mount.id),
                slug=str(request.query["slug"]) if "slug" in request.query else None,
                from_edition=from_edition,
                to_edition=to_edition,
                include_eol=_query_bool(request, "include_eol", default=False),
                subject=self._output_access_subject(request),
                limit=limit,
                offset=offset,
            )
        except ContentIRDiffError as exc:
            return Response(
                json.dumps(exc.to_payload(), indent=2),
                status=exc.status,
                content_type="application/json; charset=utf-8",
            )
        return Response(
            json.dumps(payload, indent=2),
            content_type="application/json; charset=utf-8",
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

    @app.route("/versions.json", referenced=True)
    def versions_json_route(request: Request):
        self._ensure_catalog()
        body = json.dumps(
            versions_manifest(
                self.catalog,
                base_url=self._site_base(request),
                base_path=os.environ.get("FURA_BASE_PATH", ""),
            ),
            indent=2,
        )
        return Response(body, content_type="application/json; charset=utf-8")

    @app.route("/versions/mounts/{mount_id}", referenced=True)
    def versions_mount_json(request: Request, mount_id: str):
        self._ensure_catalog()
        if not mount_id.endswith(".json"):
            raise NotFound(f"Versions artifact was not found for the requested mount: {mount_id}.")
        mount_id = mount_id[: -len(".json")]
        if not is_safe_mount_id(mount_id):
            raise NotFound(f"Versions artifact was not found for the requested mount: {mount_id}.")
        try:
            payload = versions_for_mount(
                self.catalog,
                mount_id,
                base_url=self._site_base(request),
                base_path=os.environ.get("FURA_BASE_PATH", ""),
            )
        except KeyError:
            raise NotFound(
                f"Versions artifact was not found for the requested mount: {mount_id}."
            ) from None
        return Response(
            json.dumps(payload, indent=2),
            content_type="application/json; charset=utf-8",
        )

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
