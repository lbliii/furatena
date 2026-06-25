"""Built-in view kinds and surface metadata for Furatena."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Surface = Literal["app", "catalog"]


@dataclass(frozen=True, slots=True)
class ViewKindSpec:
    """Author-facing page kind (front matter ``layout`` / ``kind``).

    A **view kind** selects which view template renders a catalog node.
    It is not the template itself — see ``ViewRegistry.resolve``.
    """

    kind: str
    template_key: str
    default_template: str
    surface: Surface = "catalog"
    compose: bool = False
    description: str = ""
    required_context: frozenset[str] = frozenset()
    required_blocks: frozenset[str] = frozenset({"page_root"})
    optional_context: frozenset[str] = frozenset()


_CATALOG_CONTEXT = frozenset(
    {
        "node",
        "nav_items",
        "breadcrumb_items",
        "breadcrumbs",
        "chirp_docs_surface",
        "active_view",
        "catalog_rail_items",
        "catalog_surface",
        "catalog_with_toc",
        "catalog_layout_extra",
        "catalog_title",
        "catalog_subtitle",
        "child_page_count",
        "prev_page",
        "next_page",
        "backlinks",
        "search_query",
        "page_count",
        "canonical_url",
        "og_image_url",
        "json_ld",
        "llm_txt_url",
    }
)

_APP_CONTEXT = frozenset({"nav_items", "chirp_docs_surface", "active_view"})
_APP_CHROME = frozenset(
    {
        "app_surface",
        "app_page_cls",
        "node",
        "mounts",
        "page_count",
        "search_query",
        "breadcrumb_items",
    }
)

_CATALOG_OPTIONAL = frozenset(
    {
        "csp_nonce",
        "csrf_token",
        "docs_stylesheets",
        "node.toc",
        "node.description",
        "node.slug",
        "node.title",
        "node.body_md",
        "page_subtitle",
        "page_title",
        "shell_outlet_attrs",
        "shell_runtime_script",
        "toast_container",
        "hits",
        "search_section",
        "search_sections",
        "search_facet_links",
        "search_scope_rail",
        "search_spotlight",
        "search_global",
        "search_hit_groups",
        "search_global_expand_url",
        "search_global_expand_nav_attrs",
        "search_reset_nav_attrs",
        "search_popular_links",
        "search_aside_links",
        "search_discovery_sections",
        "search_result_section_links",
        "search_oob",
        "search_partial_attrs",
        "search_hit_url",
        "search_hit_heading",
    }
)

VIEW_KINDS: tuple[ViewKindSpec, ...] = (
    ViewKindSpec(
        "doc",
        "doc",
        "views/doc.html",
        "catalog",
        False,
        "Single documentation page — icon rail, section nav, hero, article, optional TOC.",
        required_context=_CATALOG_CONTEXT,
        optional_context=_CATALOG_OPTIONAL,
    ),
    ViewKindSpec(
        "doc_list",
        "doc_list",
        "views/doc_list.html",
        "catalog",
        False,
        "Section index — same chrome as doc, with child-page cards.",
        required_context=_CATALOG_CONTEXT,
        optional_context=_CATALOG_OPTIONAL,
    ),
    ViewKindSpec(
        "collection",
        "collection",
        "views/collection.html",
        "catalog",
        True,
        "Multi-node read-through — stitches member pages from ``compose.collection`` data.",
        required_context=_CATALOG_CONTEXT
        | frozenset({"collection_sections", "toc_items", "toc_panel_title"}),
        optional_context=_CATALOG_OPTIONAL | frozenset({"collection", "collection_error"}),
    ),
    ViewKindSpec(
        "changelog",
        "changelog",
        "views/changelog.html",
        "catalog",
        False,
        "Release notes — doc chrome with changelog-specific body treatment.",
        required_context=_CATALOG_CONTEXT,
        optional_context=_CATALOG_OPTIONAL,
    ),
    ViewKindSpec(
        "page",
        "page",
        "views/page.html",
        "app",
        False,
        "Simple marketing/content page inside the app surface (extends home by default).",
        required_context=_APP_CONTEXT | frozenset({"node"}),
        optional_context=_APP_CHROME,
    ),
    ViewKindSpec(
        "home",
        "home",
        "views/home.html",
        "app",
        False,
        "Site home — app surface with optional site nav; no docs catalog rail.",
        required_context=_APP_CONTEXT | frozenset({"node"}),
        optional_context=_APP_CHROME,
    ),
    ViewKindSpec(
        "portal",
        "portal",
        "views/portal.html",
        "app",
        False,
        "Multi-mount portal hub — app surface listing federated doc mounts.",
        required_context=_APP_CONTEXT | frozenset({"mounts", "page_count"}),
        optional_context=_APP_CHROME | frozenset({"node"}),
    ),
)

VIEW_KIND_BY_NAME: dict[str, ViewKindSpec] = {spec.kind: spec for spec in VIEW_KINDS}

DEFAULT_CATALOG_VIEW_TEMPLATES: frozenset[str] = frozenset(
    spec.default_template for spec in VIEW_KINDS if spec.surface == "catalog"
)

DEFAULT_APP_VIEW_TEMPLATES: frozenset[str] = frozenset(
    spec.default_template for spec in VIEW_KINDS if spec.surface == "app"
)
