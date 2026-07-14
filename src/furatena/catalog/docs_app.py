"""DocsApp — configure and run a Furatena hypermedia application."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from chirp import (
    OOB,
    App,
    AppConfig,
    Fragment,
    Page,
    Request,
    Response,
)
from chirp.errors import NotFound
from chirp.ext.chirp_ui import use_chirp_ui
from chirp.i18n import get_locale, set_locale
from chirp.middleware.csrf import CSRFMiddleware, get_csrf_token
from chirp.middleware.security_headers import SecurityHeadersConfig
from chirp.middleware.sessions import SessionMiddleware, get_session
from chirp.middleware.stack import secure_stack
from chirp.middleware.static import StaticFiles
from kida.template import Markup

from furatena.catalog.access import (
    AccessPermission,
    AccessPolicy,
    AccessRole,
    AccessSubject,
    author_permission_for,
    evaluate_author_access,
)
from furatena.catalog.author_store import AuthorMutationStore, FilesystemAuthorMutationStore
from furatena.catalog.channel_manifest import channel_manifest
from furatena.catalog.conditional_response import ConditionalResponseMiddleware
from furatena.catalog.config import DocsConfig, load_docs_config
from furatena.catalog.csp import GoogleFontsCSPMiddleware
from furatena.catalog.deployment_profiles import deployment_profiles_manifest
from furatena.catalog.dev_reload import (
    browser_reload_dirs,
    clear_dev_server_record,
    dev_server_pid_path,
    run_docs_dev_server,
    stop_dev_server,
    write_dev_server_record,
)
from furatena.catalog.develop_exports import DevelopExport
from furatena.catalog.embedding_providers import build_embedding_index
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.error_experience import build_error_context
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
from furatena.catalog.frozen_artifacts import FrozenArtifactStore
from furatena.catalog.gateway_identity import (
    GatewayIdentityError,
    identity_from_trusted_session,
)
from furatena.catalog.i18n import (
    LocaleResolutionService,
    LocalizedNodeMatch,
    supported_app_locales,
)
from furatena.catalog.identity import scoped_frozen_dir
from furatena.catalog.incremental import is_partial_reload
from furatena.catalog.lifecycle import visibility_state
from furatena.catalog.links import boost_internal_links, shell_link_attrs
from furatena.catalog.observability import OperationalEventEmitter
from furatena.catalog.preview_security import (
    PreviewAccessCredentials,
    PreviewConfigurationError,
    PreviewEnvironment,
    PreviewSecurityMiddleware,
)
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.render_context import RenderContextService
from furatena.catalog.retrieval_feedback import RetrievalFeedbackCollector
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.search_experience import (
    build_search_workspace_context,
    highlight_search_terms,
    hybrid_search_hits,
    search_hit_heading,
    search_hit_url,
    search_nav_attrs,
)
from furatena.catalog.seo import (
    docs_base_url,
)
from furatena.catalog.theme import DocsTheme
from furatena.catalog.toc import build_toc_tree, collection_toc_items, node_toc_items
from furatena.catalog.validation import ValidationSnapshotService
from furatena.catalog.vendor_paths import resolve_htmx_preview
from furatena.catalog.versions import channel_context
from furatena.catalog.views import ViewRegistry
from furatena.catalog.workers import resolve_workers
from furatena.cli.authoring import (
    author_read_source,
)

_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def _accept_quality(accept: str, media_type: str) -> tuple[float, int]:
    """Return the RFC-style quality and specificity for one representation."""
    target_type, target_subtype = media_type.split("/", 1)
    best: tuple[int, float] | None = None
    for item in accept.split(","):
        media_range, *parameters = (part.strip() for part in item.split(";"))
        if "/" not in media_range:
            continue
        range_type, range_subtype = media_range.lower().split("/", 1)
        if range_type not in {"*", target_type}:
            continue
        if range_subtype not in {"*", target_subtype}:
            continue

        quality = 1.0
        for parameter in parameters:
            name, separator, value = parameter.partition("=")
            if separator and name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
                quality = min(1.0, max(0.0, quality))
                break

        specificity = int(range_type != "*") + int(range_subtype != "*")
        candidate = (specificity, quality)
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return 0.0, -1
    return best[1], best[0]


def _prefers_markdown(accept: str | None) -> bool:
    """Select markdown only when the client explicitly prefers it to HTML."""
    if not accept:
        return False
    markdown_quality, markdown_specificity = _accept_quality(accept, "text/markdown")
    html_quality, _ = _accept_quality(accept, "text/html")
    return markdown_specificity == 2 and markdown_quality > 0 and markdown_quality > html_quality


_AUTHOR_SSE_EVENT = "author-invalidate"
_DEPLOYMENT_ENVS = frozenset({"staging", "production"})


def _htmx_assets_markup(preview_version: str | None) -> Markup:
    if preview_version is None:
        return Markup(
            '<script src="/docs-vendor/htmx.min.js" data-chirp="htmx"></script>\n'
            '<script src="/docs-vendor/htmx-ext-sse.js"></script>\n'
            '<script src="/docs-vendor/htmx-ext-preload.js"></script>'
        )
    version = html.escape(preview_version, quote=True)
    policy = html.escape(
        json.dumps(
            {
                "noSwap": [204, 304, "5xx"],
                "defaultTimeout": 60_000,
                "compat": {"swapErrorResponseCodes": True},
            },
            separators=(",", ":"),
        ),
        quote=True,
    )
    return Markup(
        f'<meta name="htmx-config" content="{policy}" data-chirp="htmx-config" '
        f'data-chirp-htmx-tier="4-preview" data-chirp-htmx-version="{version}">\n'
        f'<script src="/docs-vendor/htmx-{version}.min.js" data-chirp="htmx" '
        f'data-chirp-htmx-role="core" data-chirp-htmx-tier="4-preview" '
        f'data-chirp-htmx-version="{version}" data-fura-htmx-preview="{version}"></script>\n'
        f'<script src="/docs-vendor/htmx-2-compat-{version}.min.js" '
        f'data-chirp="htmx-extension" data-chirp-htmx-extension="compat" '
        f'data-chirp-htmx-tier="4-preview" data-chirp-htmx-version="{version}"></script>\n'
        f'<script src="/docs-vendor/hx-sse-{version}.min.js" '
        f'data-chirp="htmx-extension" data-chirp-htmx-extension="sse" '
        f'data-chirp-htmx-tier="4-preview" data-chirp-htmx-version="{version}"></script>\n'
        f'<script src="/docs-vendor/hx-preload-{version}.min.js" '
        f'data-chirp="htmx-extension" data-chirp-htmx-extension="preload" '
        f'data-chirp-htmx-tier="4-preview" data-chirp-htmx-version="{version}"></script>'
    )


def _author_sse_markup(
    node: Any,
    preview_version: str | None,
    include_reload_trigger: bool = True,
) -> Markup:
    slug = html.escape(str(node.slug), quote=True)
    if preview_version is not None:
        return Markup(
            '<div id="fura-author-sse" hidden '
            f'hx-sse:connect="/docs/_author/events?slug={slug}" '
            'data-fura-sse-extension-active="1" '
            'hx-target="this"></div>'
        )
    url = html.escape(str(node.url), quote=True)
    reload_trigger = ""
    if include_reload_trigger:
        reload_trigger = (
            f'<button type="button" hidden hx-get="{url}" '
            'hx-trigger="sse:author-invalidate" hx-target="#page-root" '
            'hx-select="#page-root" hx-swap="outerHTML" '
            'hx-headers=\'{"HX-Docs-Author-Reload":"1"}\'></button>'
        )
    return Markup(
        '<div id="fura-author-sse" hidden hx-ext="sse" '
        f'sse-connect="/docs/_author/events?slug={slug}" '
        'hx-disinherit="hx-target hx-swap" hx-target="this">'
        '<div hidden sse-swap="author-invalidate" hx-target="this" hx-swap="none"></div>'
        f"{reload_trigger}</div>"
    )


def _runtime_environment() -> str:
    """Return the Chirp security posture for this Furatena process."""
    return (
        (os.environ.get("FURA_ENV") or os.environ.get("CHIRP_ENV") or "development").strip().lower()
    )


def _session_secret(environment: str) -> str:
    """Resolve a stable deployment secret or an ephemeral local-only secret."""
    configured = os.environ.get("FURA_SESSION_SECRET") or os.environ.get("CHIRP_SECRET_KEY")
    if configured:
        return configured
    if environment in _DEPLOYMENT_ENVS:
        return ""
    return secrets.token_urlsafe(32)


def _server_keep_alive_timeout() -> float:
    """Return the Pounce keep-alive timeout configured for this process."""
    raw = os.environ.get("FURA_KEEP_ALIVE_TIMEOUT", "5").strip()
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError("FURA_KEEP_ALIVE_TIMEOUT must be a number") from exc
    if timeout <= 0:
        raise ValueError("FURA_KEEP_ALIVE_TIMEOUT must be greater than zero")
    return timeout


def _active_form_proof() -> str:
    """Return the request token, or no token while rendering an error handler."""
    try:
        return get_csrf_token()
    except LookupError:
        return ""


def _static_cache_control(url_prefix: str, mode: ServeMode) -> str:
    """Cache-Control for docs theme static mounts."""
    if url_prefix.startswith("/docs-assets"):
        return _IMMUTABLE_CACHE
    if url_prefix == "/docs-vendor":
        return _IMMUTABLE_CACHE
    if url_prefix == "/docs-theme/branding":
        return _IMMUTABLE_CACHE if mode == ServeMode.PREVIEW else "public, max-age=86400"
    if url_prefix.startswith("/docs-theme/js"):
        return "public, max-age=86400"
    if url_prefix in (
        "/docs-theme/local",
        "/docs-theme/tokens",
        "/docs-theme/fonts",
        "/docs-theme/generated",
    ):
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
        author_subject: AccessSubject | None = None,
        author_store: AuthorMutationStore | None = None,
        retrieval_feedback: RetrievalFeedbackCollector | None = None,
        observability: OperationalEventEmitter | None = None,
    ) -> None:
        self.config = config
        self.htmx_preview_version = resolve_htmx_preview()
        self.locale_service = LocaleResolutionService(config.i18n)
        self.repo_root = repo_root
        self.serve = serve or ServeConfig(ServeMode.AUTHOR, None, False, True)
        self.preview_environment = PreviewEnvironment.from_environment()
        if self.preview_environment is not None and (
            self.serve.mode != ServeMode.PREVIEW or self.serve.auto_reload
        ):
            raise PreviewConfigurationError(
                "a pull-request preview requires frozen preview mode with auto-reload disabled"
            )
        self.author_subject = author_subject or (
            AccessSubject.from_values(actor="local-author", roles=[AccessRole.ADMIN])
            if self.serve.mode == ServeMode.AUTHOR
            else AccessSubject.anonymous()
        )
        self.author_store = author_store or FilesystemAuthorMutationStore()
        self.retrieval_feedback = retrieval_feedback or RetrievalFeedbackCollector.disabled()
        self.observability = observability or OperationalEventEmitter.from_environment()
        frozen = self.serve.frozen_dir or frozen_dir
        self.theme = DocsTheme.from_docs_config(
            config, frozen_dir=frozen if self.serve.mode != ServeMode.AUTHOR else None
        )
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
            include_private=self.serve.mode == ServeMode.AUTHOR,
            workers=resolve_workers(workers),
            i18n_config=config.i18n,
            catalog_nav=config.catalog,
            site_mark=config.site.mark,
            catalog_identity=config.identity.to_meta(),
        )
        self.frozen_artifacts = FrozenArtifactStore(
            self.catalog.frozen_root if self.serve.mode != ServeMode.AUTHOR else None
        )
        self.validation = ValidationSnapshotService(
            self.catalog,
            views=self.views,
            docs=self.config,
            theme=self.theme,
            template_env=self._validation_template_env,
        )
        self.render_context = RenderContextService(
            config,
            self.catalog,
            self.views,
            self.locale_service,
            self.theme,
            self.validation,
        )
        semantic_root = scoped_frozen_dir(
            frozen or config.root / "frozen", config.identity.to_meta()
        )
        semantic_path = semantic_root / "semantic.json"
        self.embedding_index = EmbeddingIndex.load(semantic_path) or build_embedding_index(
            list(self.catalog.nodes),
            documents=self.catalog.ast_documents(),
        )
        if self.serve.warn_stale_freeze:
            print("Note: content is newer than frozen/ — run `fura freeze` for a fresh export.")
        self.app = self._build_app()

    def _validation_template_env(self):
        app = getattr(self, "app", None)
        runtime = getattr(app, "_runtime_state", None)
        return getattr(runtime, "kida_env", None)

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
        environment = _runtime_environment()
        app_config = AppConfig(
            template_dir=self.config.templates_dir,
            component_dirs=component_dirs,
            debug=not preview,
            skip_contract_checks=skip_checks,
            htmx=True,
            htmx_version=self.htmx_preview_version or "2.0.10",
            reload_dirs=browser_reload_dirs(self.theme),
            i18n_enabled=i18n.enabled,
            i18n_supported_locales=supported_app_locales(i18n),
            i18n_default_locale=i18n.default_language,
            i18n_directory=str(locales_dir),
            env=environment,
            secret_key=_session_secret(environment),
            keep_alive_timeout=_server_keep_alive_timeout(),
        )
        app = App(app_config)
        use_chirp_ui(app)
        if self.preview_environment is not None:
            app.add_middleware(
                PreviewSecurityMiddleware(PreviewAccessCredentials.from_environment()),
                priority=-100,
            )
        security_middleware = secure_stack(
            app_config,
            headers=SecurityHeadersConfig(content_security_policy=None),
        )
        if preview:
            # Preview serves only public, frozen content.  Chirp's session
            # middleware currently saves a cookie on every response, so keep
            # the public stack stateless until conditional cookie writes land
            # upstream.  CSRF is paired with those sessions and only protects
            # author mutations; author mode keeps the complete stack below.
            security_middleware = [
                middleware
                for middleware in security_middleware
                if not isinstance(middleware, (SessionMiddleware, CSRFMiddleware))
            ]
        for middleware in security_middleware:
            app.add_middleware(middleware)
        app.template_global("fura_form_proof")(_active_form_proof)
        if i18n.enabled:
            app.template_global("get_locale")(get_locale)
        app.template_global("fura_author")(lambda: self.serve.auto_reload)
        app.template_global("fura_author_mode")(lambda: self._is_author_mode())
        app.template_global("docs_stylesheets")(lambda: self.theme.stylesheet_hrefs)
        app.template_global("fura_htmx4_preview")(lambda: self.htmx_preview_version is not None)
        app.template_global("fura_htmx_version")(lambda: self.htmx_preview_version or "2.0.4")
        app.template_global("fura_htmx_assets")(
            lambda: _htmx_assets_markup(self.htmx_preview_version)
        )
        app.template_global("fura_author_sse_markup")(
            lambda node, include_reload_trigger=True: _author_sse_markup(
                node,
                self.htmx_preview_version,
                include_reload_trigger,
            )
        )
        app.template_global("fura_effects_code")(lambda: self.config.theme.effects.code)
        app.template_global("fura_effects_cards")(lambda: self.config.theme.effects.cards)
        app.template_global("fura_effects_hero")(lambda: self.config.theme.effects.hero)
        app.template_filter("doc_body")(self.body_html)
        app.template_filter("boost_doc_links")(self._boost_doc_links)
        app.template_filter("node_toc_items")(node_toc_items)
        app.template_filter("collection_toc_items")(collection_toc_items)
        app.template_global("build_toc_tree")(build_toc_tree)
        app.template_global("search_hit_url")(lambda hit: search_hit_url(hit, self.embedding_index))
        app.template_global("search_hit_heading")(
            lambda hit: search_hit_heading(hit, self.embedding_index)
        )
        app.template_global("search_partial_attrs")(
            lambda: {
                "hx-disinherit": "hx-select hx-target hx-swap",
                "hx-select": "#search-results-panel",
            }
        )
        app.template_filter("search_shell_nav")(search_nav_attrs)
        app.template_filter("highlight_search")(highlight_search_terms)
        app.template_global("route_link_attrs")(self._shell_route_link_attrs)

        for assets in self.theme.static_mounts:
            app.add_middleware(
                StaticFiles(
                    directory=str(assets.directory),
                    prefix=assets.url_prefix,
                    cache_control=_static_cache_control(assets.url_prefix, self.serve.mode),
                )
            )
        app.add_middleware(GoogleFontsCSPMiddleware())
        app.add_middleware(ConditionalResponseMiddleware(self._response_last_modified))
        self._register_contract_refs(app)
        self._register_routes(app)
        return app

    def body_html(self, node) -> str:
        return self.catalog.body_html(node)

    def _boost_doc_links(self, html: str) -> str:
        return boost_internal_links(html, self._route_link_attrs)

    def _shell_route_link_attrs(
        self,
        href: str | None,
        *,
        boost: bool = True,
        external: bool = False,
        disabled: bool = False,
        **_kwargs: object,
    ) -> dict[str, object]:
        if href is None or disabled or not boost or external:
            return {}
        return shell_link_attrs(href, htmx4=self.htmx_preview_version is not None)

    def _route_link_attrs(self, href: str) -> dict[str, object]:
        return shell_link_attrs(href, htmx4=self.htmx_preview_version is not None)

    def _ensure_catalog(self) -> None:
        self.catalog.refresh_if_stale()

    def _frozen_artifact_response(
        self,
        relative_path: str,
        *,
        content_type: str,
    ) -> Response | None:
        """Return a frozen public sidecar outside mutable author mode."""
        if self.serve.mode == ServeMode.AUTHOR:
            return None
        return self.frozen_artifacts.response(relative_path, content_type=content_type)

    def _request_language(self, request: Request | None, *, node=None) -> str:
        return self.render_context.request_language(request, node=node)

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
        return self.render_context.locale_context(
            request=request,
            node=node,
            locale_match=locale_match,
        )

    @staticmethod
    def _node_markdown(node) -> str:
        return "\n".join(
            part
            for part in (
                f"# {node.title}",
                "",
                node.description,
                "",
                (
                    "> For AI agents: the complete documentation index is available at "
                    "[llms.txt](/llms.txt). Markdown versions are available at each "
                    "page's `.md` URL."
                ),
                "",
                node.body_md.strip(),
            )
            if part is not None
        )

    @classmethod
    def _plaintext_node_response(cls, node) -> Response:
        body = cls._node_markdown(node)
        return Response(body, content_type="text/plain; charset=utf-8")

    @classmethod
    def _markdown_node_response(cls, node) -> Response:
        body = cls._node_markdown(node)
        return Response(body, content_type="text/markdown; charset=utf-8")

    def _render_negotiated_node(
        self,
        node,
        request: Request,
        *,
        locale_match: LocalizedNodeMatch | None = None,
    ):
        if _prefers_markdown(request.headers.get("accept")):
            return self._markdown_node_response(node).with_vary("Accept")
        return self._render_node(node, request, locale_match=locale_match)

    def _resolve_page_from_path(
        self,
        path: str,
        *,
        requested_lang: str | None = None,
    ) -> LocalizedNodeMatch:
        match = self.locale_service.resolve_page(
            self.catalog,
            path,
            requested_lang=requested_lang,
        )
        if match is None:
            raise NotFound(f"Page not found: {path}")
        return match

    def _render_catalog_page(
        self,
        request: Request,
        *,
        requested_lang: str | None = None,
    ):
        self._ensure_catalog()
        path = request.path
        if path.endswith("/index.txt"):
            doc_path = f"{path[: -len('index.txt')].rstrip('/')}/"
            match = self._resolve_page_from_path(doc_path, requested_lang=requested_lang)
            if not self.catalog.can_access_node(
                match.node,
                self._output_access_subject(request),
                permission=AccessPermission.READ,
            ):
                raise NotFound("Page not found.")
            return self._plaintext_node_response(match.node)
        if path.endswith("/index.md") or path.endswith(".md"):
            if path.endswith("/index.md"):
                doc_path = f"{path[: -len('index.md')].rstrip('/')}/"
            else:
                doc_path = f"{path[: -len('.md')].rstrip('/')}/"
            match = self._resolve_page_from_path(doc_path, requested_lang=requested_lang)
            if not self.catalog.can_access_node(
                match.node,
                self._output_access_subject(request),
                permission=AccessPermission.READ,
            ):
                raise NotFound("Page not found.")
            return self._markdown_node_response(match.node)
        match = self._resolve_page_from_path(path, requested_lang=requested_lang)
        subject = (
            self._browser_author_subject() if self._is_author_mode() else AccessSubject.anonymous()
        )
        if not self.catalog.can_access_node(
            match.node,
            subject,
            permission=AccessPermission.READ,
        ):
            raise NotFound("Page not found.")
        return self._render_negotiated_node(match.node, request, locale_match=match)

    def _register_mount_routes(self, app: App) -> None:
        """Register URL handlers from mount configuration."""
        tenant_prefix = self.catalog.route_prefix.rstrip("/")
        if tenant_prefix:

            @app.route(f"{tenant_prefix}/", referenced=True)
            def tenant_home(request: Request):
                self._ensure_catalog()
                node = self.catalog.get("/")
                if node is None:
                    raise NotFound("Home page not found.")
                return self._render_negotiated_node(node, request)

            @app.route(f"{tenant_prefix}.md", referenced=True)
            @app.route(f"{tenant_prefix}/{{slug:path}}", referenced=True)
            def tenant_catalog_page(request: Request, slug: str = ""):
                return self._render_catalog_page(request)

        @app.route("/")
        def home(request: Request):
            self._ensure_catalog()
            node = self.catalog.get("/")
            if node is None:
                raise NotFound("Home page not found.")
            return self._render_negotiated_node(node, request)

        @app.route("/index.md", referenced=True)
        def home_markdown(request: Request):
            self._ensure_catalog()
            node = self.catalog.get("/")
            if node is None or not self.catalog.can_access_node(
                node,
                self._output_access_subject(request),
                permission=AccessPermission.READ,
            ):
                raise NotFound("Page not found.")
            return self._markdown_node_response(node)

        for mount in self.catalog.mounts:
            prefix = (mount.url_prefix or "").rstrip("/")
            if prefix:
                mount_id = mount.id
                mount_prefix = prefix

                @app.route(f"{mount_prefix}.md", referenced=True)
                @app.route(f"{mount_prefix}/")
                @app.route(f"{mount_prefix}/{{slug:path}}", referenced=True)
                def prefixed_mount(request: Request, slug: str = "", _mount_id=mount_id):
                    return self._render_catalog_page(request)

                prefixed_mount.__name__ = f"mount_{mount_id.replace('-', '_')}"
                continue

            if not mount.default:
                continue

            locale_sections = set()
            if self.config.i18n.enabled:
                locale_sections = {
                    code
                    for code in self.config.i18n.language_codes()
                    if code != self.config.i18n.default_language
                }
            for section in self.catalog.default_mount_sections():
                section_name = section
                if section_name in locale_sections:
                    continue

                @app.route(f"/{section_name}.md", referenced=True)
                @app.route(f"/{section_name}/")
                @app.route(f"/{section_name}/{{slug:path}}", referenced=True)
                def default_mount_section(request: Request, slug: str = "", _section=section_name):
                    return self._render_catalog_page(request)

                default_mount_section.__name__ = f"default_{section_name.replace('-', '_')}"

    def _site_base(self, request: Request | None = None) -> str:
        host = request.headers.get("host") if request is not None else None
        return docs_base_url(host)

    def _include_private_output(self, request: Request | None = None) -> bool:
        if self.serve.mode != ServeMode.AUTHOR:
            return False
        if request is None:
            return False
        return (request.query.get("include_private") or "").strip().lower() in {"1", "true", "yes"}

    def _output_access_subject(self, request: Request | None = None) -> AccessSubject:
        if request is not None and self._include_private_output(request):
            return self._browser_author_subject()
        return AccessSubject.anonymous()

    def _is_author_mode(self) -> bool:
        return self.serve.mode == ServeMode.AUTHOR

    def _browser_author_subject(self) -> AccessSubject:
        """Resolve the server-owned subject from the signed browser session."""
        session = get_session()
        raw = session.get("fura_author_subject")
        if isinstance(raw, dict) and raw.get("source") == "trusted_gateway":
            try:
                return identity_from_trusted_session(
                    raw,
                    expected_identity=self.config.identity.to_meta(),
                ).subject
            except GatewayIdentityError:
                return AccessSubject.anonymous()
        if not isinstance(raw, dict):
            raw = {
                "actor": self.author_subject.actor,
                "roles": [role.value for role in self.author_subject.roles],
                "teams": sorted(self.author_subject.teams),
            }
            session["fura_author_subject"] = raw
        return AccessSubject.from_values(
            actor=str(raw.get("actor") or ""),
            roles=raw.get("roles") or (),
            teams=raw.get("teams") or (),
        )

    def _browser_author_denial(self, operation: str) -> Response | None:
        decision = evaluate_author_access(
            operation,
            AccessPolicy(),
            self._browser_author_subject(),
        )
        if decision.allowed:
            return None
        return _json_response(
            {
                "ok": False,
                "diagnostics": [
                    {
                        "severity": "error",
                        "message": (
                            f"browser subject requires role {decision.required_role.value} "
                            f"for {operation}"
                        ),
                        "rule_id": "fura.author.authorization",
                    }
                ],
            },
            status=403,
        )

    def _browser_node_denial(self, node: Any, operation: str) -> Response | None:
        decision = self.catalog.access_decision_for_node(
            node,
            self._browser_author_subject(),
            permission=author_permission_for(operation),
        )
        if decision.allowed:
            return None
        return _json_response(
            {
                "ok": False,
                "diagnostics": [
                    {
                        "severity": "error",
                        "message": (
                            f"browser subject requires role {decision.required_role.value} "
                            f"for {operation}; {decision.reason}"
                        ),
                        "rule_id": "fura.author.authorization",
                    }
                ],
            },
            status=403,
        )

    def _author_page_chrome(self, node, *, force_validation: bool = False) -> dict[str, Any]:
        source = self._author_source_info(node)
        validation = self._author_validation_status(node, force=force_validation)
        visibility = visibility_state(getattr(node, "meta", {}) or {})
        export_included = self.catalog.can_access_node(node, permission=AccessPermission.EXPORT)
        stale_entries = self.catalog.author_stale_entries(node.slug)
        dirty = bool(source["dirty"])
        stale = dirty or bool(stale_entries)
        states: list[str] = [visibility]
        if visibility in {"private", "internal", "unlisted"}:
            states.append("private")
        states.append("invalid" if validation["errors"] else "valid")
        if dirty:
            states.append("dirty")
        states.append("stale" if stale else "clean")
        states.append("public-output" if export_included else "excluded-output")
        source_path = source["path"]
        query = f"slug={node.slug}"
        return {
            "enabled": True,
            "node_id": node.node_id,
            "slug": node.slug,
            "title": node.title,
            "source_path": source_path,
            "source_exists": source["exists"],
            "content_format": node.content_format,
            "visibility": visibility,
            "states": sorted(set(states), key=states.index),
            "freshness": "dirty" if dirty else ("stale" if stale else "clean"),
            "validation": validation,
            "last_indexed_at": source["last_indexed_at"],
            "source_modified_at": source["modified_at"],
            "source_revision": source["revision"],
            "export_impact": {
                "included": export_included,
                "label": "Included in public output"
                if export_included
                else "Excluded from public output",
                "reason": "public visibility" if export_included else f"{visibility} visibility",
            },
            "stale": stale_entries,
            "actions": {
                "dashboard": "/docs/_author/dashboard",
                "status": f"/docs/_author/page.json?{query}",
                "studio": f"/docs/_author/studio?{query}",
                "open_source": f"/docs/_author/source?{query}",
                "validate": f"/docs/_author/page.json?{query}&validate=1",
                "transition": "/docs/_author/transition",
                "inspect_public": f"/docs/_author/page.json?{query}&inspect_public=1",
            },
        }

    def _author_page_chrome_fragment(
        self,
        node,
        *,
        status: int = 200,
        force_validation: bool = False,
    ):
        return Fragment(
            "partials/author_chrome.html",
            "author_chrome",
            status=status,
            author_chrome=self._author_page_chrome(node, force_validation=force_validation),
        )

    def _author_dashboard_context(self, request: Request) -> dict[str, Any]:
        return self.render_context.author_dashboard_context(
            request,
            source_info=self._author_source_info,
            force_validation=(request.query.get("validate") or "").strip().lower()
            in {"1", "true", "yes", "on"},
        )

    def _author_source_info(self, node) -> dict[str, Any]:
        source_path = str(getattr(node, "source_path", "") or "")
        mount = next((item for item in self.catalog.mounts if item.id == node.mount), None)
        path = (
            (mount.content_root / source_path).resolve()
            if mount is not None and source_path
            else None
        )
        indexed_mtime = self._author_indexed_mtime(node, path)
        current_mtime = path.stat().st_mtime if path is not None and path.is_file() else None
        revision = (
            f"sha256:{hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()}"
            if path is not None and path.is_file()
            else None
        )
        return {
            "path": str(path) if path is not None else source_path,
            "exists": bool(path is not None and path.is_file()),
            "dirty": bool(
                path is not None
                and path.is_file()
                and indexed_mtime is not None
                and current_mtime is not None
                and indexed_mtime != current_mtime
            ),
            "last_indexed_at": _iso_from_mtime(indexed_mtime),
            "modified_at": _iso_from_mtime(current_mtime),
            "revision": revision,
        }

    def _author_indexed_mtime(self, node, path: Path | None) -> float | None:
        if path is None:
            return None
        shard = getattr(self.catalog, "_shards", {}).get(node.mount)
        source_mtimes = getattr(shard, "_source_mtimes", {}) if shard is not None else {}
        return source_mtimes.get(path)

    def _response_last_modified(self, request: Request) -> float | None:
        path = request.path
        if path == "/index.md" or path == "/index.txt":
            doc_path = "/"
        elif path.endswith("/index.md"):
            doc_path = f"{path[: -len('index.md')].rstrip('/')}/"
        elif path.endswith(".md"):
            doc_path = f"{path[: -len('.md')].rstrip('/')}/"
        elif path.endswith("/index.txt"):
            doc_path = f"{path[: -len('index.txt')].rstrip('/')}/"
        elif "." not in path.rsplit("/", 1)[-1]:
            doc_path = path
        else:
            return None

        try:
            match = self._resolve_page_from_path(doc_path)
        except NotFound:
            return None
        source_path = str(getattr(match.node, "source_path", "") or "")
        mount = next(
            (item for item in self.catalog.mounts if item.id == match.node.mount),
            None,
        )
        source = (mount.content_root / source_path).resolve() if mount and source_path else None
        if source is not None and source.is_file():
            return source.stat().st_mtime
        return self._author_indexed_mtime(match.node, source)

    def _author_validation_status(self, node, *, force: bool = False) -> dict[str, Any]:
        snapshot = self.validation.snapshot(force=force)
        source = str(getattr(node, "source_path", "") or "")
        page_errors = _messages_for_source(snapshot.errors, source)
        page_warnings = _messages_for_source(snapshot.warnings, source)
        return {
            "ok": not page_errors,
            "errors": page_errors,
            "warnings": page_warnings,
            "error_count": len(page_errors),
            "warning_count": len(page_warnings),
            "catalog_generation": snapshot.catalog_generation,
            "configuration_fingerprint": snapshot.configuration_fingerprint,
        }

    def _author_node_from_request(self, request: Request):
        node_id = (request.query.get("node_id") or "").strip()
        if node_id:
            node = self.catalog.get_by_node_id(node_id)
            if node is not None:
                return node
        slug = (request.query.get("slug") or "").strip().strip("/")
        if slug:
            node = self.catalog.get_by_slug(slug)
            if node is not None:
                return node
        raise NotFound("Author page target not found.")

    def _author_node_from_request_or_none(self, request: Request):
        try:
            return self._author_node_from_request(request)
        except NotFound:
            return None

    def _author_studio_context(
        self,
        request: Request,
        *,
        node=None,
        source_text: str | None = None,
        result: Any | None = None,
        create_slug: str | None = None,
        title: str | None = None,
        saved: bool = False,
    ) -> dict[str, Any]:
        if node is not None:
            ctx = self._page_context(node, request=request)
            read_result, current_source = author_read_source(
                node.slug,
                mounts=tuple(self.catalog.mounts),
                subject=self._browser_author_subject(),
                mount_id=node.mount,
                store=self.author_store,
            )
            if result is None and not read_result.ok:
                result = read_result
            source_text = source_text if source_text is not None else (current_source or "")
            slug = node.slug
            page_title = node.title
            source_info = self._author_source_info(node)
            preview_html = self._boost_doc_links(self.body_html(node))
            visibility = visibility_state(getattr(node, "meta", {}) or {})
            source_path = source_info["path"]
            mode = "edit"
        else:
            slug = (create_slug or (request.query.get("slug") or "")).strip().strip("/")
            page_title = title or _title_from_slug(slug)
            source_text = (
                source_text if source_text is not None else _compose_draft_source(slug, page_title)
            )
            ctx = {
                **self._site_context(),
                **self._locale_template_context(request=request),
                **self._theme_effects_context(),
                **channel_context(self.catalog.channels, self.catalog.active_channel),
                "node": None,
                "page_count": len(self.catalog.doc_nodes()),
                "search_query": "",
                "app_surface": "author-studio",
                "app_page_cls": "chirp-theme-author-studio",
                "chirp_docs_surface": "author-studio",
            }
            source_path = ""
            preview_html = ""
            visibility = "draft"
            mode = "create"

        diagnostics = []
        data = None
        if result is not None:
            data = result.to_dict() if hasattr(result, "to_dict") else result
            diagnostics = list(data.get("diagnostics") or []) if isinstance(data, dict) else []

        ctx.update(
            {
                "app_surface": "author-studio",
                "app_page_cls": "chirp-theme-author-studio",
                "chirp_docs_surface": "author-studio",
                "catalog_title": "Author studio",
                "catalog_subtitle": page_title,
                "author_studio": {
                    "mode": mode,
                    "slug": slug,
                    "title": page_title,
                    "source_text": source_text or "",
                    "source_revision": read_result.source_revision if node is not None else None,
                    "source_path": source_path,
                    "source_regions": _source_heading_regions(source_text or ""),
                    "has_ast": bool(getattr(node, "ast_json", None)) if node is not None else False,
                    "source_provenance": "patitas-ast"
                    if getattr(node, "ast_json", None)
                    else "source-lines",
                    "preview_html": preview_html,
                    "visibility": visibility,
                    "save_url": "/docs/_author/studio/save",
                    "page_url": getattr(node, "url", "") if node is not None else "",
                    "saved": saved,
                    "ok": not diagnostics,
                    "diagnostics": diagnostics,
                    "result": data,
                },
            }
        )
        return ctx

    def _reindex_author_result(self, result: Any) -> None:
        if not getattr(result, "ok", False):
            return
        mount_id = getattr(result, "mount", None)
        changed_files = tuple(getattr(result, "changed_files", ()) or ())
        if not mount_id or not changed_files:
            return
        shard = getattr(self.catalog, "_shards", {}).get(mount_id)
        if shard is None:
            self.catalog.refresh_if_stale()
            return
        shard._reindex_paths({Path(path) for path in changed_files})
        self.catalog._edges = None
        self.catalog._namespaces = None
        self.catalog._translation_index = None
        self.catalog._finalize_federated()

    def _theme_effects_context(self) -> dict[str, str]:
        return self.render_context.theme_effects_context()

    def _site_context(self) -> dict[str, Any]:
        return self.render_context.site_context()

    def _page_context(
        self,
        node,
        *,
        query: str = "",
        request: Request | None = None,
        locale_match: LocalizedNodeMatch | None = None,
    ) -> dict[str, Any]:
        active_lang = (
            locale_match.requested_lang
            if locale_match is not None
            else self._request_language(request, node=node)
        )
        if self.config.i18n.enabled:
            set_locale(active_lang)
        author_chrome = None
        if self._is_author_mode() and self.catalog.can_access_node(
            node,
            self._browser_author_subject(),
            permission=AccessPermission.AUTHOR,
        ):
            author_chrome = self._author_page_chrome(node)
        return {
            **self.render_context.page_context(
                node,
                query=query,
                request=request,
                locale_match=locale_match,
                author_chrome=author_chrome,
            ),
            **self._preview_template_context(),
        }

    @staticmethod
    def _view_chrome_context(view_name: str, node, ctx: dict[str, Any]) -> dict[str, Any]:
        return RenderContextService.view_chrome_context(view_name, node, ctx)

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
        if query:
            hits = workspace.get("hits")
            self.retrieval_feedback.record_query(
                query,
                tenant=self.config.identity.tenant,
                surface="browser",
                result_count=len(hits) if isinstance(hits, list) else 0,
                metadata={
                    "section": section_value or None,
                    "mount": mount_value or None,
                    "tag": tag_value or None,
                    "edition": channel_value,
                    "lang": page_lang,
                    "global": global_search,
                },
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
        active_lang = self._request_language(request)
        if self.config.i18n.enabled:
            set_locale(active_lang)
        return {
            **self.render_context.shell_context(query=query, request=request),
            **self._preview_template_context(),
        }

    def _preview_template_context(self) -> dict[str, object]:
        preview = self.preview_environment
        return {"pr_preview": preview.template_context if preview is not None else None}

    def _is_author_reload(self, request: Request) -> bool:
        return self.serve.auto_reload and bool(request.headers.get("HX-Docs-Author-Reload"))

    def _author_invalidation_payload(self, slug: str | None = None) -> dict[str, Any]:
        """Current author-mode invalidation payload shared by polling and SSE."""
        normalized = slug.strip("/") if slug else ""
        self._ensure_catalog()
        entries = self.catalog.author_stale_entries(normalized or None)
        stale: list[dict[str, Any]] = []
        for entry in entries:
            entry_slug = str(entry.get("slug") or "")
            node = self.catalog.get_by_slug(entry_slug, mount=str(entry.get("mount") or "") or None)
            source_path = node.source_path if node is not None else ""
            stale.append(
                {
                    "slug": entry_slug,
                    "mount": str(entry.get("mount") or ""),
                    "hints": list(entry.get("hints") or ()),
                    "dirty_paths": [source_path] if source_path else [],
                }
            )

        current = None
        if normalized:
            hints = self.catalog.invalidation_hints(normalized)
            if hints:
                node = self.catalog.get_by_slug(normalized)
                source_path = node.source_path if node is not None else ""
                current = {
                    "slug": normalized,
                    "hints": list(hints),
                    "target_hints": list(hints),
                    "dirty_paths": [source_path] if source_path else [],
                    "dirty": True,
                    "reload": not is_partial_reload(hints),
                }
        generation_seed = json.dumps(
            {"current": current, "stale": stale},
            sort_keys=True,
            separators=(",", ":"),
        )
        generation = hashlib.sha256(generation_seed.encode("utf-8")).hexdigest()[:16]
        return {
            "event": _AUTHOR_SSE_EVENT,
            "generation": generation,
            "stale": stale,
            "current": current,
        }

    def _render_author_reload(self, view_name: str, request: Request, **context: Any):
        node = context.get("node")
        if node is None:
            return Response("", status=204)
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
            return self._render_author_reload(view_name, request, **context)

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

    def _render_error(self, request: Request, exc: Exception | None, *, status: int):
        ctx = build_error_context(self, request, status=status, exc=exc)
        main = Page.mounted("error.html", **ctx)
        if request.is_htmx and request.is_boosted and not request.is_history_restore:
            return OOB(
                main,
                Fragment("partials/error_meta_oob.html", "error_meta_oob", **ctx),
            )
        return main, status

    def _develop_export_sample(self, export: DevelopExport, *, limit: int = 12_000) -> str:
        self._ensure_catalog()
        if export.id == "catalog":
            body = json.dumps(catalog_graph(self.catalog), indent=2)
        elif export.id == "llms":
            body = llms_index_txt(
                self.catalog,
                site_name=self.config.site.name,
                site_description=self.config.site.description,
            )
        elif export.id == "llms-full":
            body = llms_full_txt(self.catalog, site_name=self.config.site.name)
        elif export.id == "search":
            body = json.dumps(search_json(self.catalog), indent=2)
        elif export.id == "tools":
            body = json.dumps(
                tools_manifest(self.catalog, site_name=self.config.site.name),
                indent=2,
            )
        elif export.id == "api-operations":
            body = json.dumps(api_operations_json(self.catalog), indent=2)
        elif export.id == "meta":
            body = json.dumps(meta_json(self.catalog), indent=2)
        elif export.id == "surface":
            body = json.dumps(surface_json(self.config, self.catalog), indent=2)
        elif export.id == "channels":
            body = json.dumps(
                channel_manifest(
                    self.catalog,
                    config=self.config,
                ),
                indent=2,
            )
        elif export.id == "deployment-profiles":
            body = json.dumps(deployment_profiles_manifest(), indent=2)
        else:
            body = ""
        if len(body) > limit:
            return (
                body[:limit] + "\n\n… (truncated preview — download raw export for full payload)\n"
            )
        return body

    def _register_routes(self, app: App) -> None:
        from furatena.catalog.route_registrars import (
            register_author_routes,
            register_catalog_routes,
            register_error_routes,
            register_media_routes,
            register_public_routes,
            register_search_routes,
        )

        register_public_routes(self, app)
        self._register_mount_routes(app)
        register_author_routes(self, app)
        register_search_routes(self, app)
        register_catalog_routes(self, app)
        register_media_routes(app)
        self._register_localized_routes(app)
        register_error_routes(self, app)

    def _register_localized_routes(self, app: App) -> None:
        """Register ``/{lang}/docs/...`` routes for non-default locales."""
        i18n = self.config.i18n
        if not i18n.enabled:
            return

        for lang_code in i18n.language_codes():
            if lang_code == i18n.default_language:
                continue
            prefix = f"/{lang_code}"

            @app.route(f"{prefix}/{{slug:path}}", referenced=True)
            def localized_page(request: Request, slug: str = "", lang=lang_code):
                slug = slug.strip("/")
                if slug == "docs" or slug.startswith("docs/"):
                    return self._render_catalog_page(request, requested_lang=lang)
                if not slug:
                    node = self.catalog.get_path(f"/{lang}/")
                    if node is None:
                        node = self.catalog.get_by_slug(lang, mount=self.catalog.default_mount.id)
                    if node is None:
                        raise NotFound(f"Home page not found for locale: {lang}")
                    return self._render_node(node, request)
                raise NotFound(f"Document not found: /{lang}/{slug}/")

    def _register_contract_refs(self, app: App) -> None:
        directive_templates = (
            "accordion",
            "callout",
            "card_grid",
            "card_link",
            "card_static",
            "child_cards",
            "code_block",
            "figure",
            "glossary",
            "gist",
            "literalinclude",
            "related",
            "step",
            "steps",
            "table",
            "tabs",
            "version_callout",
            "youtube",
        )
        for name in directive_templates:
            app.declare_template(f"directives/{name}.html")

        dynamic_templates = {
            *self.config.views.values(),
            *self.config.overrides.values(),
        }
        dynamic_templates.update(
            {
                "error.html",
                "layout.html",
                "partials/author_sse.html",
                "partials/error_meta_oob.html",
            }
        )
        for template in sorted(dynamic_templates):
            app.declare_template(template)

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
        author_subject: AccessSubject | None = None,
        author_store: AuthorMutationStore | None = None,
        retrieval_feedback: RetrievalFeedbackCollector | None = None,
        observability: OperationalEventEmitter | None = None,
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
            author_subject=author_subject,
            author_store=author_store,
            retrieval_feedback=retrieval_feedback,
            observability=observability,
        )

    def create_app(self) -> App:
        return self.app

    def run_serve(self, *, host: str | None = None, port: int | None = None) -> None:
        """Start the dev or preview server with Furatena reload wiring."""
        resolved_host = host or self.app.config.host
        resolved_port = port or self.app.config.port
        pid_path = dev_server_pid_path(self.repo_root)
        stop_dev_server(self.repo_root, host=resolved_host, port=resolved_port)
        write_dev_server_record(
            pid_path,
            pid=os.getpid(),
            host=resolved_host,
            port=resolved_port,
        )
        try:
            if self.serve.mode == ServeMode.PREVIEW or not self.serve.auto_reload:
                self.app.run(host=host, port=port)
                return
            run_docs_dev_server(self, host=host, port=port)
        finally:
            clear_dev_server_record(pid_path)


def _json_response(payload: dict[str, Any], *, status: int = 200) -> Response:
    return Response(
        json.dumps(_jsonable(payload), sort_keys=True),
        status=status,
        content_type="application/json; charset=utf-8",
    )


def _form_bool(form: Any, key: str, *, default: bool = False) -> bool:
    value = form.get(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _author_authorization_denied(result: Any) -> bool:
    return any(
        getattr(diagnostic, "rule_id", "") == "fura.author.authorization"
        for diagnostic in getattr(result, "diagnostics", ())
    )


def _author_conflict(result: Any) -> bool:
    return any(
        getattr(diagnostic, "rule_id", "") == "fura.author.conflict"
        for diagnostic in getattr(result, "diagnostics", ())
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _title_from_slug(slug: str) -> str:
    leaf = (slug.strip("/").rsplit("/", 1)[-1] or "Untitled").strip()
    return leaf.replace("-", " ").replace("_", " ").title()


def _compose_draft_source(slug: str, title: str | None = None) -> str:
    page_title = title or _title_from_slug(slug)
    meta = {
        "title": page_title,
        "draft": True,
        "visibility": "draft",
        "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n# {page_title}\n"


def _draft_source_text(source_text: str, *, slug: str, title: str | None = None) -> str:
    try:
        from furatena.catalog.sources.parse import parse_source_text

        meta, body = parse_source_text(source_text, content_format="patitas-markdown")
    except Exception:
        return source_text
    meta = dict(meta)
    meta.setdefault("title", title or _title_from_slug(slug))
    meta["draft"] = True
    meta["visibility"] = "draft"
    meta.setdefault(
        "updated_at",
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body.lstrip()}"


def _source_heading_regions(source_text: str) -> list[dict[str, object]]:
    regions: list[dict[str, object]] = []
    for line_number, line in enumerate(source_text.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        heading = match.group(2).strip()
        anchor = re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
        regions.append(
            {
                "line": line_number,
                "depth": len(match.group(1)),
                "heading": heading,
                "anchor": anchor,
            }
        )
    return regions


def _messages_for_source(messages: list[str], source_path: str) -> list[str]:
    if not source_path:
        return []
    return [message for message in messages if message.startswith(f"{source_path}:")]


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


def _iso_from_mtime(mtime: float | None) -> str | None:
    if mtime is None:
        return None
    return (
        datetime.fromtimestamp(mtime, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _query_bool(request: Request, name: str, *, default: bool) -> bool:
    raw = request.query.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}
