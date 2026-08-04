"""Deterministic cross-edition diffs over normalized Content IR."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from furatena.catalog.access import AccessPermission, AccessSubject
from furatena.catalog.export import provenance_record

CONTENT_IR_DIFF_SCHEMA_VERSION = 1
DEFAULT_CONTENT_IR_DIFF_LIMIT = 100
MAX_CONTENT_IR_DIFF_LIMIT = 500
_EDITION_STATUSES = frozenset({"current", "legacy", "deprecated", "preview", "eol"})
_COMPONENT_ORDER = {"section": 0, "heading": 1, "directive": 2, "link": 3}


class ContentIRDiffError(ValueError):
    """Fail-closed diff error shared by HTTP and MCP transports."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: int = 400,
        recovery: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.recovery = recovery

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": CONTENT_IR_DIFF_SCHEMA_VERSION,
            "ok": False,
            "error": {
                "code": self.code,
                "message": str(self),
                "recovery": self.recovery,
            },
        }


@dataclass(frozen=True, slots=True)
class _EditionContext:
    edition: str
    status: str
    source_ref: str
    resolved_ref: str

    def to_dict(self) -> dict[str, str]:
        return {
            "edition": self.edition,
            "status": self.status,
            "source_ref": self.source_ref,
            "resolved_ref": self.resolved_ref,
        }


@dataclass(frozen=True, slots=True)
class _Component:
    key: str
    index: int
    semantic: dict[str, Any]
    detail: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _NormalizedPage:
    sections: tuple[_Component, ...]
    headings: tuple[_Component, ...]
    directives: tuple[_Component, ...]
    links: tuple[_Component, ...]

    def hash_payload(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "sections": [item.semantic for item in self.sections],
            "headings": [item.semantic for item in self.headings],
            "directives": [item.semantic for item in self.directives],
            "links": [item.semantic for item in self.links],
        }


def diff_content_ir(
    catalog: Any,
    *,
    mount: str,
    from_edition: str,
    to_edition: str,
    slug: str | None = None,
    subject: AccessSubject | None = None,
    include_eol: bool = False,
    limit: int = DEFAULT_CONTENT_IR_DIFF_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Compare one page or roll up one mount across two explicit editions."""
    mount = str(mount).strip()
    from_edition = str(from_edition).strip()
    to_edition = str(to_edition).strip()
    normalized_slug = str(slug).strip().strip("/") if slug is not None else None
    if not isinstance(include_eol, bool):
        raise ContentIRDiffError(
            "Content IR diff include_eol must be a boolean value.",
            code="invalid_include_eol",
            recovery="Pass true only for an intentional archival comparison.",
        )
    limit, offset = _validate_page_bounds(limit, offset)
    _authorize_mount(catalog, mount, subject)
    from_context = _edition_context(catalog, mount, from_edition, include_eol=include_eol)
    to_context = _edition_context(catalog, mount, to_edition, include_eol=include_eol)
    common = {
        "schema_version": CONTENT_IR_DIFF_SCHEMA_VERSION,
        "ok": True,
        "mount": mount,
        "from": from_context.to_dict(),
        "to": to_context.to_dict(),
        "query": {
            "slug": normalized_slug,
            "include_eol": include_eol,
            "limit": limit,
            "offset": offset,
        },
        "limit": limit,
        "offset": offset,
    }
    if normalized_slug is not None:
        return {
            **common,
            **_page_diff(
                catalog,
                mount=mount,
                slug=normalized_slug,
                from_edition=from_edition,
                to_edition=to_edition,
                subject=subject,
                limit=limit,
                offset=offset,
            ),
        }
    return {
        **common,
        **_mount_diff(
            catalog,
            mount=mount,
            from_edition=from_edition,
            to_edition=to_edition,
            subject=subject,
            limit=limit,
            offset=offset,
        ),
    }


def _page_diff(
    catalog: Any,
    *,
    mount: str,
    slug: str,
    from_edition: str,
    to_edition: str,
    subject: AccessSubject | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    from_node = _node_for(catalog, mount, from_edition, slug, subject)
    to_node = _node_for(catalog, mount, to_edition, slug, subject)
    if from_node is None and to_node is None:
        raise ContentIRDiffError(
            f"The page {mount}:{slug} is unavailable in both requested editions.",
            code="page_not_found",
            status=404,
            recovery="Choose an accessible slug present in at least one requested edition.",
        )
    from_page = _normalize_node(from_node) if from_node is not None else None
    to_page = _normalize_node(to_node) if to_node is not None else None
    from_hash = _structural_hash(from_page)
    to_hash = _structural_hash(to_page)
    if from_node is None:
        status = "added"
    elif to_node is None:
        status = "removed"
    elif from_hash == to_hash:
        status = "unchanged"
    else:
        status = "changed"

    # Identical structural hashes avoid allocating typed detail entirely.
    changes = [] if status == "unchanged" else _component_changes(from_page, to_page)
    page = {
        "slug": slug,
        "status": status,
        "from": _page_reference(catalog, from_node) if from_node is not None else None,
        "to": _page_reference(catalog, to_node) if to_node is not None else None,
        "structural_hashes": {"from": from_hash, "to": to_hash},
    }
    return {
        "kind": "page",
        "summary": _page_summary(status, changes),
        "total": len(changes),
        "next_offset": _next_offset(len(changes), limit, offset),
        "page": page,
        "changes": changes[offset : offset + limit],
        "pages": [],
    }


def _mount_diff(
    catalog: Any,
    *,
    mount: str,
    from_edition: str,
    to_edition: str,
    subject: AccessSubject | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    from_nodes = _nodes_for(catalog, mount, from_edition, subject)
    to_nodes = _nodes_for(catalog, mount, to_edition, subject)
    from_by_slug = {str(node.slug): node for node in from_nodes}
    to_by_slug = {str(node.slug): node for node in to_nodes}
    pages: list[dict[str, Any]] = []
    summary = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    for page_slug in sorted(set(from_by_slug) | set(to_by_slug)):
        from_node = from_by_slug.get(page_slug)
        to_node = to_by_slug.get(page_slug)
        from_hash = _structural_hash(_normalize_node(from_node)) if from_node else None
        to_hash = _structural_hash(_normalize_node(to_node)) if to_node else None
        if from_node is None:
            status = "added"
        elif to_node is None:
            status = "removed"
        elif from_hash == to_hash:
            status = "unchanged"
        else:
            status = "changed"
        summary[status] += 1
        pages.append(
            {
                "slug": page_slug,
                "status": status,
                "from": _page_reference(catalog, from_node) if from_node else None,
                "to": _page_reference(catalog, to_node) if to_node else None,
                "structural_hashes": {"from": from_hash, "to": to_hash},
            }
        )
    return {
        "kind": "mount",
        "summary": summary,
        "total": len(pages),
        "next_offset": _next_offset(len(pages), limit, offset),
        "page": None,
        "changes": [],
        "pages": pages[offset : offset + limit],
    }


def _authorize_mount(catalog: Any, mount: str, subject: AccessSubject | None) -> None:
    known = any(str(getattr(item, "id", "")) == mount for item in catalog.mounts)
    allowed = known and bool(
        catalog.can_access_mount(mount, subject, permission=AccessPermission.READ)
    )
    if not allowed:
        raise ContentIRDiffError(
            f"The requested mount {mount!r} is unavailable to this caller.",
            code="mount_not_found",
            status=404,
            recovery="Choose an accessible mount reported by the catalog.",
        )


def _edition_context(
    catalog: Any,
    mount: str,
    edition: str,
    *,
    include_eol: bool,
) -> _EditionContext:
    if not edition or not catalog.has_edition(mount, edition):
        raise ContentIRDiffError(
            f"The edition {edition!r} is unavailable for mount {mount!r}.",
            code="unknown_edition",
            recovery="Choose an edition reported for this mount by channels.json.",
        )
    snapshot = next(
        (
            item
            for item in catalog.discovered_editions_for(mount)
            if str(getattr(item, "id", "")) == edition
        ),
        None,
    )
    if edition == "latest" and snapshot is None:
        return _EditionContext("latest", "current", "", "")
    status = str(getattr(snapshot, "status", "") or "")
    if snapshot is None or status not in _EDITION_STATUSES:
        raise ContentIRDiffError(
            f"The edition {mount}:{edition} has no authoritative lifecycle metadata.",
            code="lifecycle_unavailable",
            status=409,
            recovery="Refresh edition discovery before requesting a cross-version diff.",
        )
    if status == "eol" and not include_eol:
        raise ContentIRDiffError(
            f"The edition {mount}:{edition} is end-of-life and requires explicit opt-in.",
            code="eol_opt_in_required",
            status=403,
            recovery="Set include_eol=true only for an intentional archival comparison.",
        )
    return _EditionContext(
        edition=edition,
        status=status,
        source_ref=str(getattr(snapshot, "ref", "") or ""),
        resolved_ref=str(getattr(snapshot, "resolved_ref", "") or ""),
    )


def _node_for(
    catalog: Any,
    mount: str,
    edition: str,
    slug: str,
    subject: AccessSubject | None,
) -> Any | None:
    with catalog.use_edition(edition):
        node = catalog.get_by_slug(slug, mount=mount)
        if node is None or not catalog.can_access_node(
            node, subject, permission=AccessPermission.READ
        ):
            return None
        _validate_node_identity(node, mount, edition)
        return node


def _nodes_for(
    catalog: Any,
    mount: str,
    edition: str,
    subject: AccessSubject | None,
) -> tuple[Any, ...]:
    with catalog.use_edition(edition):
        nodes = tuple(
            node
            for node in catalog.nodes
            if str(node.mount) == mount
            and catalog.can_access_node(node, subject, permission=AccessPermission.READ)
        )
    for node in nodes:
        _validate_node_identity(node, mount, edition)
    return nodes


def _validate_node_identity(node: Any, mount: str, edition: str) -> None:
    if str(node.mount) != mount or str(node.edition) != edition:
        raise ContentIRDiffError(
            f"The resolved node {node.node_id!r} escaped edition context {mount}:{edition}.",
            code="identity_mismatch",
            status=409,
            recovery="Rebuild the immutable edition shard and retry the diff.",
        )


def _normalize_node(node: Any) -> _NormalizedPage:
    content_ir = node.content_ir
    if content_ir is None:
        raise ContentIRDiffError(
            f"The node {node.node_id!r} has no normalized Content IR.",
            code="content_ir_unavailable",
            status=409,
            recovery="Re-freeze the edition with the current Content IR contract.",
        )
    return _NormalizedPage(
        sections=_components(
            (
                {
                    "id": str(section.id),
                    "heading": str(section.heading),
                    "depth": int(section.depth),
                    "text": str(section.text),
                }
                for section in node.sections
            ),
            identity="id",
        ),
        headings=_components(
            (
                {
                    "level": int(heading.level),
                    "text": str(heading.text),
                    "anchor": str(heading.anchor),
                    "line": heading.line,
                }
                for heading in content_ir.headings
            ),
            identity="anchor",
            location="line",
        ),
        directives=_components(
            (
                {
                    "name": str(directive.name),
                    "options": _plain(directive.options),
                    "line": directive.line,
                }
                for directive in content_ir.directives
            ),
            identity="name",
            location="line",
        ),
        links=_components(
            (
                {
                    "href": str(link.href),
                    "text": str(link.text),
                    "mount": link.mount,
                    "domain": link.domain,
                    "inventory_id": link.inventory_id,
                    "resolved": bool(link.resolved),
                    "line": link.line,
                }
                for link in content_ir.links
            ),
            identity="href",
            location="line",
        ),
    )


def _components(
    records: Any,
    *,
    identity: str,
    location: str | None = None,
) -> tuple[_Component, ...]:
    occurrences: dict[str, int] = {}
    components: list[_Component] = []
    for index, raw in enumerate(records):
        detail = dict(raw)
        base = str(detail.get(identity) or "")
        occurrence = occurrences.get(base, 0) + 1
        occurrences[base] = occurrence
        key = base if occurrence == 1 else f"{base}#{occurrence}"
        semantic = {key: value for key, value in detail.items() if key != location}
        components.append(_Component(key=key, index=index, semantic=semantic, detail=detail))
    return tuple(components)


def _component_changes(
    old: _NormalizedPage | None,
    new: _NormalizedPage | None,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for component, plural in (
        ("section", "sections"),
        ("heading", "headings"),
        ("directive", "directives"),
        ("link", "links"),
    ):
        old_items = getattr(old, plural) if old is not None else ()
        new_items = getattr(new, plural) if new is not None else ()
        old_by_key = {item.key: item for item in old_items}
        new_by_key = {item.key: item for item in new_items}
        for key in sorted(set(old_by_key) | set(new_by_key)):
            before = old_by_key.get(key)
            after = new_by_key.get(key)
            if before is None:
                change = "added"
            elif after is None:
                change = "removed"
            elif before.semantic != after.semantic:
                change = "changed"
            elif before.index != after.index:
                change = "moved"
            else:
                continue
            changes.append(
                {
                    "component": component,
                    "change": change,
                    "identity": key,
                    "moved": bool(
                        before is not None and after is not None and before.index != after.index
                    ),
                    "from_index": before.index if before is not None else None,
                    "to_index": after.index if after is not None else None,
                    "from": before.detail if before is not None else None,
                    "to": after.detail if after is not None else None,
                }
            )
    changes.sort(
        key=lambda item: (
            _COMPONENT_ORDER[str(item["component"])],
            str(item["identity"]),
            str(item["change"]),
        )
    )
    return changes


def _page_summary(status: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
    components = {name: 0 for name in _COMPONENT_ORDER}
    kinds = {name: 0 for name in ("added", "removed", "changed", "moved")}
    for item in changes:
        components[str(item["component"])] += 1
        kinds[str(item["change"])] += 1
    return {
        "status": status,
        "change_count": len(changes),
        "components": components,
        "changes": kinds,
    }


def _page_reference(catalog: Any, node: Any) -> dict[str, Any]:
    provenance = dict(provenance_record(catalog, node))
    provenance["output_channel"] = str(node.edition)
    return {
        "node_id": str(node.node_id),
        "mount": str(node.mount),
        "edition": str(node.edition),
        "slug": str(node.slug),
        "url": str(node.url),
        "title": str(node.title),
        "source_path": str(node.source_path),
        "provenance": provenance,
    }


def _structural_hash(page: _NormalizedPage | None) -> str | None:
    if page is None:
        return None
    encoded = json.dumps(
        page.hash_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_page_bounds(limit: int, offset: int) -> tuple[int, int]:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_CONTENT_IR_DIFF_LIMIT
    ):
        raise ContentIRDiffError(
            f"Content IR diff limit must be between 1 and {MAX_CONTENT_IR_DIFF_LIMIT}.",
            code="invalid_limit",
            recovery=f"Use an integer limit from 1 through {MAX_CONTENT_IR_DIFF_LIMIT}.",
        )
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ContentIRDiffError(
            "Content IR diff offset must be a non-negative integer.",
            code="invalid_offset",
            recovery="Use zero for the first page or a prior response's next_offset.",
        )
    return limit, offset


def _next_offset(total: int, limit: int, offset: int) -> int | None:
    candidate = offset + limit
    return candidate if candidate < total else None


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))
