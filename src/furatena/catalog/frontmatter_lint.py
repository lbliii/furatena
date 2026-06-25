"""Front matter validation for catalog pages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from furatena.catalog.view_kinds import VIEW_KIND_BY_NAME

if TYPE_CHECKING:
    from furatena.catalog.models import DocNode
    from furatena.catalog.views import ViewRegistry


def lint_front_matter(
    node: DocNode,
    *,
    views: ViewRegistry | None = None,
) -> tuple[list[str], list[str]]:
    """Validate page front matter against view kinds and compose config."""
    errors: list[str] = []
    warnings: list[str] = []
    source = node.source_path or node.slug or node.url
    view_kind = node.layout or "doc"

    if view_kind not in VIEW_KIND_BY_NAME:
        mapped = views.config.views.get(view_kind) if views is not None else None
        if mapped is None:
            warnings.append(
                f"{source}: custom view kind '{view_kind}' — document it in VIEWS.md",
            )

    if view_kind == "collection" and views is not None:
        collection_id = (
            node.meta.get("collection")
            or node.meta.get("collection_id")
            or node.slug.rsplit("/", 1)[-1]
        )
        if not views.has_collection(str(collection_id)):
            errors.append(
                f"{source}: collection view references unknown collection '{collection_id}'",
            )

    doc_version = node.meta.get("doc_version") or node.meta.get("version")
    if doc_version is not None and not str(doc_version).strip():
        errors.append(f"{source}: doc_version must not be empty")

    return errors, warnings
