"""Shared Kida environment wiring for Furatena lint and smoke checks."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from kida import ChoiceLoader, Environment, FileSystemLoader, PackageLoader
from kida.exceptions import UndefinedError

if TYPE_CHECKING:
    from pathlib import Path

    from furatena.catalog.config import DocsConfig
    from furatena.catalog.registry import CatalogRegistry
    from furatena.catalog.theme import DocsTheme


def build_docs_template_env(
    docs: DocsConfig,
    theme: DocsTheme,
    *,
    repo_root: Path | None = None,
) -> Environment:
    """Return the frozen DocsApp Kida env, or a minimal stub env on failure."""
    if repo_root is not None:
        try:
            from furatena.catalog.docs_app import DocsApp
            from furatena.catalog.runtime import ServeConfig, ServeMode

            docs_app = DocsApp(
                docs,
                repo_root=repo_root,
                autodoc=False,
                serve=ServeConfig(ServeMode.PREVIEW, None, True, False),
            )
            chirp_app = docs_app.app
            chirp_app.freeze()
            env = chirp_app._runtime_state.kida_env
            if env is not None:
                return env
        except Exception as exc:
            warnings.warn(
                f"DocsApp template env unavailable ({exc}); falling back to stub env",
                stacklevel=2,
            )
    return _stub_docs_template_env(docs, theme)


def registered_template_globals(env: Environment) -> frozenset[str]:
    """Names available as Kida globals on *env*."""
    globals_map = getattr(env, "globals", {}) or {}
    return frozenset(str(name) for name in globals_map)


def smoke_render_template(
    env: Environment,
    template_name: str,
    context: dict[str, object],
    *,
    block_name: str | None = None,
) -> str | None:
    """Render a template or block; return an error string on failure."""
    try:
        template = env.get_template(template_name)
    except Exception as exc:
        return f"{template_name}: failed to load ({exc})"
    try:
        if block_name:
            template.render_block(block_name, context)
        else:
            template.render(context)
    except UndefinedError as exc:
        return f"{template_name}: {exc}"
    except Exception as exc:
        return f"{template_name}: render failed ({exc})"
    return None


def check_search_shell_templates(
    env: Environment,
    catalog: CatalogRegistry,
) -> list[str]:
    """Smoke-render search partials with catalog-native lint context."""
    from furatena.catalog.search_experience import search_lint_context

    context = search_lint_context(catalog)
    errors: list[str] = []
    partials = (
        "partials/search_mount_rail.html",
        "partials/search_scope_rail.html",
        "partials/search_workspace_panel.html",
        "partials/search_results.html",
        "partials/search_discovery.html",
        "partials/search_facets.html",
    )
    for template_name in partials:
        message = smoke_render_template(env, template_name, context)
        if message:
            errors.append(message)
    message = smoke_render_template(
        env,
        "search.html",
        context,
        block_name="search_results",
    )
    if message:
        errors.append(message)
    return sorted(errors)


def _stub_docs_template_env(docs: DocsConfig, theme: DocsTheme) -> Environment:
    """Minimal env when DocsApp cannot be constructed (offline/tooling fallback)."""
    loaders = [FileSystemLoader(str(path)) for path in theme.template_roots]
    loaders.append(FileSystemLoader(str(docs.framework_templates_dir)))
    loaders.append(PackageLoader("chirp.templating", "macros"))
    try:
        import chirp_ui  # noqa: F401

        loaders.append(PackageLoader("chirp_ui", "templates"))
    except ImportError:
        pass
    env = Environment(loader=ChoiceLoader(loaders), auto_reload=False)
    _register_stub_filters_and_globals(env)
    return env


def _register_stub_filters_and_globals(env: Environment) -> None:
    def _identity(value, *args, **kwargs):
        return value

    for name in (
        "boost_doc_links",
        "doc_body",
        "node_toc_items",
        "collection_toc_items",
        "html_attrs",
        "highlight_search",
    ):
        if name not in env.filters:
            env.filters[name] = _identity
    stubs = {
        "build_toc_tree": lambda items: items,
        "route_link_attrs": lambda href, **kwargs: {},
        "search_partial_attrs": lambda: {},
        "search_hit_url": lambda hit: getattr(hit, "url", "/search"),
        "search_hit_heading": lambda hit: None,
        "csp_nonce": lambda: "",
        "csrf_token": lambda: "",
        "fura_effects_code": lambda: "flat",
        "fura_effects_cards": lambda: "flat",
        "fura_effects_hero": lambda: "wash",
        "fura_presentation": lambda: {
            "content_digest": "lint",
            "layout": {"id": "lint", "version": "0.0.0", "source": "compatibility"},
            "skin": None,
        },
    }
    for name, func in stubs.items():
        if name not in env.globals:
            env.globals[name] = func
