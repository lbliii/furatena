"""Content and config validation for ``fura check``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from furatena.catalog.ast_store import ast_roundtrip_error
from furatena.catalog.content_lint import lint_page_ast
from furatena.catalog.directives.manifest import (
    check_manifest_registry_alignment,
    validate_directive_manifest,
)
from furatena.catalog.directives.registry import create_directive_registry
from furatena.catalog.format_compat import (
    mdx_compatibility_findings,
    myst_compatibility_findings,
    rst_compatibility_findings,
    warning_messages,
)
from furatena.catalog.frontmatter_lint import lint_front_matter
from furatena.catalog.graph import normalize_internal_url
from furatena.catalog.lifecycle import check_lifecycle_sources
from furatena.catalog.render import DocsRenderer
from furatena.catalog.view_lint import check_view_templates

if TYPE_CHECKING:
    from furatena.catalog.config import DocsConfig
    from furatena.catalog.models import DocNode
    from furatena.catalog.registry import CatalogRegistry
    from furatena.catalog.theme import DocsTheme
    from furatena.catalog.views import ViewRegistry

_CATALOG_PREFIXES = ("/docs/", "/shared/")

_renderer: DocsRenderer | None = None
_registry = None


class CatalogLike(Protocol):
    def get(self, url: str) -> DocNode | None: ...

    @property
    def nodes(self) -> tuple[DocNode, ...]: ...


def catalog_url_prefixes(catalog: CatalogLike) -> frozenset[str]:
    """Return internal URL prefixes that should resolve in the federated catalog."""
    prefixes: set[str] = set(_CATALOG_PREFIXES)
    for node in catalog.nodes:
        url = node.url
        if not url or url == "/":
            continue
        parts = url.strip("/").split("/", 1)
        if parts and parts[0]:
            prefixes.add(f"/{parts[0]}/")
    return frozenset(sorted(prefixes))


def should_validate_catalog_link(href: str, catalog: CatalogLike | None = None) -> bool:
    """Return whether a normalized internal href must resolve in the catalog."""
    prefixes = catalog_url_prefixes(catalog) if catalog is not None else _CATALOG_PREFIXES
    return any(href.startswith(prefix) for prefix in prefixes)


def check_broken_internal_links(catalog: CatalogLike) -> list[str]:
    """Return errors for markdown links that do not resolve in the catalog."""
    errors: list[str] = []
    for node in catalog.nodes:
        for link in _page_links(node):
            href = normalize_internal_url(str(link["href"]))
            if href is None or not should_validate_catalog_link(href, catalog):
                continue
            target = _resolve_catalog_target(catalog, href, source=node)
            if target is None:
                location = _format_location(node, link.get("line"))
                label = link.get("text") or href
                errors.append(f"{location}: broken internal link [{label}]({href})")
    return sorted(errors)


def check_content_lint(catalog: CatalogLike) -> tuple[list[str], list[str]]:
    """Run markdown structure, directive contract, and front matter lint."""
    errors: list[str] = []
    warnings: list[str] = []
    renderer = _get_renderer()
    registry = _get_registry()

    for node in catalog.nodes:
        if node.meta.get("source") == "autodoc":
            continue
        if node.meta.get("draft"):
            continue
        source = node.source_path or node.slug or node.url
        if node.body_md:
            document, content_ir = renderer.parse(node.body_md)
            page_errors, page_warnings = lint_page_ast(
                document,
                content_ir,
                registry,
                source=source,
                body=node.body_md,
            )
            errors.extend(page_errors)
            warnings.extend(page_warnings)
        elif node.content_ir is not None:
            from furatena.catalog.content_lint import lint_unknown_directives

            warnings.extend(
                lint_unknown_directives(node.content_ir, registry, source=source),
            )
        if node.body_md and node.content_format == "mdx":
            warnings.extend(
                warning_messages(
                    mdx_compatibility_findings(
                        node.body_md,
                        source_path=source,
                        known_directives=registry.names,
                    )
                )
            )
        elif node.body_md and node.content_format == "myst-markdown":
            warnings.extend(
                warning_messages(
                    myst_compatibility_findings(
                        node.body_md,
                        source_path=source,
                        known_directives=registry.names,
                    )
                )
            )
        elif node.body_md and node.content_format == "docutils-rst":
            warnings.extend(
                warning_messages(
                    rst_compatibility_findings(
                        node.body_md,
                        source_path=source,
                    )
                )
            )

    return sorted(errors), sorted(warnings)


def check_front_matter(
    catalog: CatalogLike,
    *,
    views: ViewRegistry | None = None,
) -> tuple[list[str], list[str]]:
    """Validate front matter for all catalog pages."""
    errors: list[str] = []
    warnings: list[str] = []
    for node in catalog.nodes:
        if node.meta.get("source") == "autodoc" or node.meta.get("draft"):
            continue
        page_errors, page_warnings = lint_front_matter(node, views=views)
        errors.extend(page_errors)
        warnings.extend(page_warnings)
    return sorted(errors), sorted(warnings)


def check_view_config(views: ViewRegistry) -> list[str]:
    """Return view registry configuration warnings."""
    return views.validate_config()


def check_view_templates_for_config(
    docs: DocsConfig,
    theme: DocsTheme,
    *,
    strict: bool = False,
    repo_root: Path | None = None,
    catalog: CatalogRegistry | None = None,
) -> tuple[list[str], list[str]]:
    """Run Kida block/context checks on registered view templates."""
    return check_view_templates(
        docs,
        theme,
        strict=strict,
        repo_root=repo_root,
        catalog=catalog,
    )


def check_directive_manifest() -> tuple[list[str], list[str]]:
    """Validate directive manifest templates, theme hooks, and registry alignment."""
    errors, warnings = validate_directive_manifest()
    errors.extend(check_manifest_registry_alignment())
    return sorted(errors), sorted(warnings)


def check_ast_roundtrip(catalog: CatalogLike) -> list[str]:
    """Warn when persisted AST sidecars cannot deserialize on the current Patitas."""
    warnings: list[str] = []
    for node in catalog.nodes:
        if node.meta.get("source") == "autodoc" or node.meta.get("draft"):
            continue
        if not node.ast_json:
            continue
        error = ast_roundtrip_error(node.ast_json)
        if error is None:
            continue
        source = node.source_path or node.slug or node.url
        warnings.append(
            f"{source}: frozen AST incompatible with Patitas { _patitas_version() }: {error}"
        )
    return sorted(warnings)


def check_unresolved_references(
    catalog: CatalogLike,
    *,
    inventory_store=None,
) -> list[str]:
    """Return errors for unresolved ``{xref}`` / ``{py}`` role targets."""
    import re

    from furatena.catalog.references.resolver import resolve_reference

    role_re = re.compile(r"\{(\w+)\}`([^`]+)`")
    errors: list[str] = []
    for node in catalog.nodes:
        if node.meta.get("draft") or not node.body_md:
            continue
        for match in role_re.finditer(node.body_md):
            role_name = match.group(1)
            if role_name not in {"xref", "doc", "py"}:
                continue
            target = match.group(2).strip()
            resolved = resolve_reference(
                target,
                catalog=catalog,
                inventory_store=inventory_store,
                role_name=role_name,
            )
            if resolved.resolved:
                continue
            line = node.body_md.count("\n", 0, match.start()) + 1
            location = _format_location(node, line)
            errors.append(f"{location}: unresolved reference {{{role_name}}}`{target}`")
    return sorted(errors)


def check_cross_edition_links(
    catalog: CatalogLike,
    *,
    strict: bool = False,
) -> tuple[list[str], list[str]]:
    """Flag internal links that target a page on a different edition."""
    errors: list[str] = []
    warnings: list[str] = []
    for node in catalog.nodes:
        for link in _page_links(node):
            href = normalize_internal_url(str(link["href"]))
            if href is None or not should_validate_catalog_link(href, catalog):
                continue
            target = _resolve_catalog_target(catalog, href, source=node)
            if target is None:
                continue
            if target.edition == node.edition:
                continue
            location = _format_location(node, link.get("line"))
            label = link.get("text") or href
            message = (
                f"{location}: cross-edition link [{label}]({href}) "
                f"({node.edition} -> {target.edition})"
            )
            if strict:
                errors.append(message)
            else:
                warnings.append(message)
    return sorted(errors), sorted(warnings)


def check_catalog(
    catalog: CatalogLike,
    *,
    views: ViewRegistry | None = None,
    docs: DocsConfig | None = None,
    theme: DocsTheme | None = None,
    strict_views: bool = False,
    strict_edition_links: bool = False,
    inventory_store=None,
) -> tuple[list[str], list[str]]:
    """Run docs-specific checks. Returns ``(errors, warnings)``."""
    edition_strict = strict_edition_links or strict_views
    errors = check_broken_internal_links(catalog)
    errors.extend(
        check_unresolved_references(catalog, inventory_store=inventory_store)
    )
    manifest_errors, manifest_warnings = check_directive_manifest()
    errors.extend(manifest_errors)
    lint_errors, lint_warnings = check_content_lint(catalog)
    errors.extend(lint_errors)
    fm_errors, fm_warnings = check_front_matter(catalog, views=views)
    errors.extend(fm_errors)
    warnings = lint_warnings + fm_warnings + manifest_warnings
    lifecycle_errors, lifecycle_warnings = check_lifecycle_sources(catalog)
    errors.extend(lifecycle_errors)
    warnings.extend(lifecycle_warnings)
    cross_errors, cross_warnings = check_cross_edition_links(
        catalog,
        strict=edition_strict,
    )
    errors.extend(cross_errors)
    warnings.extend(cross_warnings)
    from furatena.catalog.link_lint import check_body_link_boost, check_directive_template_hrefs

    boost_errors, boost_warnings = check_body_link_boost(
        catalog,
        strict=edition_strict,
    )
    errors.extend(boost_errors)
    warnings.extend(boost_warnings)
    template_errors, template_warnings = check_directive_template_hrefs(
        strict=edition_strict,
    )
    errors.extend(template_errors)
    warnings.extend(template_warnings)
    warnings.extend(check_ast_roundtrip(catalog))
    from furatena.catalog.rendering_heads import check_rendering_head_contracts

    head_errors, head_warnings = check_rendering_head_contracts(catalog)
    errors.extend(head_errors)
    warnings.extend(head_warnings)
    warnings.extend(check_view_config(views) if views is not None else [])
    if docs is not None and theme is not None:
        view_errors, view_warnings = check_view_templates_for_config(
            docs,
            theme,
            strict=strict_views,
            repo_root=catalog.repo_root,
            catalog=catalog,
        )
        errors.extend(view_errors)
        warnings.extend(view_warnings)
        from furatena.catalog.theme_lint import check_theme_assets

        theme_errors, theme_warnings = check_theme_assets(docs)
        errors.extend(theme_errors)
        warnings.extend(theme_warnings)
        from furatena.catalog.delivery import check_delivery_config

        delivery_errors, delivery_warnings = check_delivery_config(docs, catalog)
        errors.extend(delivery_errors)
        warnings.extend(delivery_warnings)
    errors.extend(check_dcp_schema(catalog))
    return sorted(errors), sorted(warnings)


def check_dcp_schema(catalog: CatalogLike) -> list[str]:
    """Validate merged catalog export against DCP v3 JSON Schema."""
    from furatena.catalog.dcp_validate import validate_catalog_graph

    if not hasattr(catalog, "graph_edges"):
        return []
    return validate_catalog_graph(catalog)


def _mount_prefixed_href(catalog: CatalogLike, href: str, mount_id: str) -> str | None:
    """When *mount_id* uses a URL prefix, map default-path links to that mount."""
    mounts = getattr(catalog, "mounts", None)
    if not mounts:
        return None
    mount = next((item for item in mounts if item.id == mount_id), None)
    if mount is None or not mount.url_prefix:
        return None
    prefix = mount.url_prefix.rstrip("/")
    normalized = href if href.startswith("/") else f"/{href}"
    if normalized == prefix or normalized.startswith(f"{prefix}/"):
        return None
    return f"{prefix}{normalized}"


def _resolve_catalog_target(
    catalog: CatalogLike,
    href: str,
    *,
    source: DocNode | None = None,
) -> DocNode | None:
    """Resolve an internal href, including mount prefixes and ``/_index/`` aliases."""
    target = catalog.get(href)
    if target is not None:
        return target
    if source is not None:
        prefixed = _mount_prefixed_href(catalog, href, source.mount)
        if prefixed is not None:
            target = catalog.get(prefixed)
            if target is not None:
                return target
    mounts = getattr(catalog, "mounts", None)
    if mounts:
        for mount in mounts:
            if source is not None and mount.id == source.mount:
                continue
            prefixed = _mount_prefixed_href(catalog, href, mount.id)
            if prefixed is None:
                continue
            target = catalog.get(prefixed)
            if target is not None:
                return target
    if href.endswith("/_index/"):
        shortened = href[: -len("_index/")]
        target = catalog.get(shortened)
        if target is not None:
            return target
        if source is not None:
            prefixed = _mount_prefixed_href(catalog, shortened, source.mount)
            if prefixed is not None:
                target = catalog.get(prefixed)
                if target is not None:
                    return target
            if mounts:
                for mount in mounts:
                    if mount.id == source.mount:
                        continue
                    prefixed = _mount_prefixed_href(catalog, shortened, mount.id)
                    if prefixed is None:
                        continue
                    target = catalog.get(prefixed)
                    if target is not None:
                        return target
    return None


def _get_renderer() -> DocsRenderer:
    global _renderer
    if _renderer is None:
        _renderer = DocsRenderer()
    return _renderer


def _get_registry():
    global _registry
    if _registry is None:
        _registry = create_directive_registry()
    return _registry


def _page_links(node: DocNode) -> list[dict[str, object]]:
    content_ir = node.content_ir
    if content_ir is None and node.body_md:
        content_ir = _get_renderer().parse(node.body_md)[1]
    if content_ir is not None:
        from furatena.catalog.content_ir import collect_content_ir_urls

        links = [
            {"href": link.href, "text": link.text, "line": link.line}
            for link in content_ir.links
        ]
        markdown_hrefs = {
            normalized
            for link in content_ir.links
            if (normalized := normalize_internal_url(link.href)) is not None
        }
        for href in sorted(collect_content_ir_urls(content_ir)):
            if href in markdown_hrefs:
                continue
            links.append({"href": href, "text": "", "line": None})
        return links
    return []


def _format_location(node: DocNode, line: object | None) -> str:
    source = node.source_path or node.slug or node.url
    if line is None:
        return str(source)
    return f"{source}:{line}"


def _patitas_version() -> str:
    try:
        import patitas

        return str(getattr(patitas, "__version__", "unknown"))
    except ImportError:
        return "unknown"
