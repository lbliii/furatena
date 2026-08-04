"""Cross-edition graph, resolution, and shared-content projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping as RuntimeMapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from furatena.catalog.access import AccessPermission, AccessSubject, accessible_nodes
from furatena.catalog.content_ir import content_ir_record
from furatena.catalog.edition_routing import edition_path
from furatena.catalog.graph_schema import EdgeKind, parse_node_id
from furatena.catalog.record_types import EdgeRecord

if TYPE_CHECKING:
    from collections.abc import Mapping

    from furatena.catalog.models import DocNode
    from furatena.catalog.registry import CatalogRegistry

EDITION_PROJECTION_SCHEMA_VERSION = 1
EDITION_PROJECTION_FILENAME = "edition-projection.json"


@dataclass(frozen=True, slots=True)
class EditionPageRecord:
    """One public source-backed page identity in the edition projection."""

    node_id: str
    shared_node_id: str
    content_digest: str
    mount: str
    edition: str
    slug: str
    url: str
    title: str
    source_path: str
    source_ref: str
    resolved_ref: str
    supersedes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> EditionPageRecord:
        return cls(
            node_id=str(raw["node_id"]),
            shared_node_id=str(raw["shared_node_id"]),
            content_digest=str(raw["content_digest"]),
            mount=str(raw["mount"]),
            edition=str(raw["edition"]),
            slug=str(raw["slug"]),
            url=str(raw["url"]),
            title=str(raw["title"]),
            source_path=str(raw["source_path"]),
            source_ref=str(raw["source_ref"]),
            resolved_ref=str(raw["resolved_ref"]),
            supersedes=tuple(str(item) for item in raw.get("supersedes", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "shared_node_id": self.shared_node_id,
            "content_digest": self.content_digest,
            "mount": self.mount,
            "edition": self.edition,
            "slug": self.slug,
            "url": self.url,
            "title": self.title,
            "source_path": self.source_path,
            "source_ref": self.source_ref,
            "resolved_ref": self.resolved_ref,
            "supersedes": list(self.supersedes),
        }


@dataclass(frozen=True, slots=True)
class EditionPageResolution:
    """Deterministic result of resolving one page into a target edition."""

    page: EditionPageRecord
    resolution: str


@dataclass(frozen=True, slots=True)
class EditionProjection:
    """Immutable cross-edition projection with constant-time hot-path indices."""

    pages: tuple[EditionPageRecord, ...]
    shared_nodes: tuple[dict[str, Any], ...]
    edges: tuple[EdgeRecord, ...]
    metrics: Mapping[str, Any]
    edition_order: Mapping[str, tuple[str, ...]]
    _by_key: Mapping[tuple[str, str, str], EditionPageRecord]
    _by_node_id: Mapping[str, EditionPageRecord]
    _replacement: Mapping[tuple[str, str], EditionPageRecord]
    _public_source_ids: frozenset[str]

    @classmethod
    def create(
        cls,
        *,
        pages: tuple[EditionPageRecord, ...],
        shared_nodes: tuple[dict[str, Any], ...],
        edges: tuple[EdgeRecord, ...],
        metrics: Mapping[str, Any],
        edition_order: Mapping[str, tuple[str, ...]],
    ) -> EditionProjection:
        by_key = {(page.mount, page.edition, page.slug): page for page in pages}
        by_node_id = {page.node_id: page for page in pages}
        adjacency: dict[str, set[str]] = {}
        for edge in edges:
            if edge["kind"] != EdgeKind.SUPERSEDES.value:
                continue
            source = by_node_id.get(edge["source"])
            target = by_node_id.get(edge["target"])
            if source is None or target is None or source.mount != target.mount:
                continue
            adjacency.setdefault(source.node_id, set()).add(target.node_id)
            adjacency.setdefault(target.node_id, set()).add(source.node_id)
        replacement: dict[tuple[str, str], EditionPageRecord] = {}
        visited: set[str] = set()
        for start in sorted(adjacency):
            if start in visited:
                continue
            pending = [start]
            component: list[EditionPageRecord] = []
            while pending:
                node_id = pending.pop()
                if node_id in visited:
                    continue
                visited.add(node_id)
                page = by_node_id.get(node_id)
                if page is not None:
                    component.append(page)
                pending.extend(sorted(adjacency.get(node_id, ()), reverse=True))
            by_edition: dict[str, list[EditionPageRecord]] = {}
            for page in component:
                by_edition.setdefault(page.edition, []).append(page)
            unique = {
                edition: members[0] for edition, members in by_edition.items() if len(members) == 1
            }
            for source in component:
                for edition, target in unique.items():
                    if edition != source.edition:
                        replacement[(source.node_id, edition)] = target
        release_sources = {
            edge["source"] for edge in edges if edge["source"].startswith("release:")
        }
        return cls(
            pages=pages,
            shared_nodes=shared_nodes,
            edges=edges,
            metrics=MappingProxyType(dict(metrics)),
            edition_order=MappingProxyType(dict(edition_order)),
            _by_key=MappingProxyType(by_key),
            _by_node_id=MappingProxyType(by_node_id),
            _replacement=MappingProxyType(replacement),
            _public_source_ids=frozenset((*by_node_id, *release_sources)),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> EditionProjection:
        errors = validate_edition_projection_payload(raw)
        if errors:
            raise ValueError(
                f"The invalid edition projection payload failed schema validation: {errors[0]}."
            )
        pages = tuple(
            EditionPageRecord.from_dict(item)
            for item in raw.get("pages", ())
            if isinstance(item, dict)
        )
        edge_items: list[EdgeRecord] = []
        for item in raw.get("edges", ()):
            if not isinstance(item, dict):
                continue
            edge_items.append(
                {
                    "kind": str(item["kind"]),
                    "source": str(item["source"]),
                    "target": str(item["target"]),
                    "mount": str(item["mount"]),
                    "edition": str(item["edition"]),
                }
            )
        edges = tuple(edge_items)
        shared_nodes = tuple(
            dict(item) for item in raw.get("shared_nodes", ()) if isinstance(item, dict)
        )
        order_raw = raw.get("edition_order") or {}
        edition_order = {
            str(mount): tuple(str(item) for item in editions)
            for mount, editions in order_raw.items()
            if isinstance(editions, list)
        }
        metrics_raw = raw.get("metrics")
        metrics: Mapping[str, Any] = metrics_raw if isinstance(metrics_raw, dict) else {}
        return cls.create(
            pages=pages,
            shared_nodes=shared_nodes,
            edges=edges,
            metrics=metrics,
            edition_order=edition_order,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EDITION_PROJECTION_SCHEMA_VERSION,
            "page_count": len(self.pages),
            "shared_node_count": len(self.shared_nodes),
            "deduplicated_page_count": len(self.pages) - len(self.shared_nodes),
            "edition_order": {
                mount: list(editions) for mount, editions in sorted(self.edition_order.items())
            },
            "pages": [page.to_dict() for page in self.pages],
            "shared_nodes": [dict(item) for item in self.shared_nodes],
            "edges": [dict(edge) for edge in self.edges],
            "metrics": _plain(self.metrics),
        }

    @property
    def public_node_ids(self) -> frozenset[str]:
        return frozenset(self._by_node_id)

    @property
    def public_source_ids(self) -> frozenset[str]:
        return self._public_source_ids

    def edges_for(self, edition: str) -> tuple[EdgeRecord, ...]:
        """Return page edges for an edition plus mount release chronology."""
        return tuple(
            edge
            for edge in self.edges
            if edge["edition"] == edition or edge["source"].startswith("release:")
        )

    def resolve(
        self,
        *,
        mount: str,
        source_node_id: str,
        target_edition: str,
    ) -> EditionPageResolution | None:
        """Resolve direct, declared replacement, ancestor, then mount landing."""
        source = self._by_node_id.get(source_node_id)
        if source is None or source.mount != mount:
            return None
        direct = self._by_key.get((mount, target_edition, source.slug))
        if direct is not None:
            return EditionPageResolution(direct, "direct")
        replacement = self._replacement.get((source.node_id, target_edition))
        if replacement is not None and replacement.mount == mount:
            return EditionPageResolution(replacement, "supersedes")
        parts = [part for part in source.slug.strip("/").split("/") if part]
        for end in range(len(parts) - 1, 0, -1):
            ancestor = self._by_key.get((mount, target_edition, "/".join(parts[:end])))
            if ancestor is not None:
                return EditionPageResolution(ancestor, "ancestor")
        landing = self._by_key.get((mount, target_edition, ""))
        if landing is not None:
            return EditionPageResolution(landing, "landing")
        return None


def build_edition_projection(registry: CatalogRegistry) -> EditionProjection:
    """Build one visibility-safe cross-edition projection from real source shards."""
    pages: list[EditionPageRecord] = []
    order: dict[str, tuple[str, ...]] = {}
    statuses: dict[tuple[str, str], str] = {}
    for mount in sorted(registry.mounts, key=lambda item: item.id):
        editions = registry.edition_ids_for(mount.id)
        if len(editions) < 2:
            continue
        order[mount.id] = editions
        snapshots = {item.id: item for item in registry.discovered_editions_for(mount.id)}
        for edition in editions:
            snapshot = snapshots.get(edition)
            statuses[(mount.id, edition)] = str(getattr(snapshot, "status", "") or "current")
            with registry.use_edition(edition):
                nodes = accessible_nodes(
                    registry,
                    registry.nodes,
                    subject=AccessSubject.anonymous(),
                    permission=AccessPermission.EXPORT,
                )
            for node in nodes:
                if node.mount != mount.id:
                    continue
                digest = _content_digest(node)
                pages.append(
                    EditionPageRecord(
                        node_id=node.node_id,
                        shared_node_id=_shared_node_id(node, digest),
                        content_digest=digest,
                        mount=node.mount,
                        edition=node.edition,
                        slug=node.slug,
                        url=node.url,
                        title=node.title,
                        source_path=node.source_path,
                        source_ref=str(getattr(snapshot, "ref", "") or ""),
                        resolved_ref=str(getattr(snapshot, "resolved_ref", "") or ""),
                        supersedes=_supersedes_values(node),
                    )
                )
    pages.sort(key=lambda item: (item.mount, _edition_rank(order, item), item.slug, item.node_id))
    edges = _projection_edges(tuple(pages), order)
    shared_nodes = _shared_nodes(tuple(pages))
    seed = EditionProjection.create(
        pages=tuple(pages),
        shared_nodes=shared_nodes,
        edges=edges,
        metrics={},
        edition_order=order,
    )
    metrics = _resolution_metrics(seed, statuses)
    return EditionProjection.create(
        pages=seed.pages,
        shared_nodes=seed.shared_nodes,
        edges=seed.edges,
        metrics=metrics,
        edition_order=seed.edition_order,
    )


def load_or_build_edition_projection(registry: CatalogRegistry) -> EditionProjection:
    """Load a frozen projection when available, otherwise derive it from source shards."""
    frozen_root = registry.frozen_root
    path = frozen_root / EDITION_PROJECTION_FILENAME if frozen_root is not None else None
    if path is not None and path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"The invalid frozen edition projection could not be decoded: {path}."
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(
                f"The frozen edition projection payload must be a JSON object: {path}."
            )
        return EditionProjection.from_dict(payload)
    return build_edition_projection(registry)


def write_edition_projection(registry: CatalogRegistry, out_dir: Path) -> bool:
    """Write the versioned projection sidecar and report whether public bytes changed."""
    projection = registry.edition_projection()
    path = out_dir / EDITION_PROJECTION_FILENAME
    if not projection.pages:
        if path.exists():
            path.unlink()
            return True
        return False
    payload = projection.to_dict()
    errors = validate_edition_projection_payload(payload)
    if errors:
        raise ValueError(
            f"The generated edition projection payload failed schema validation: {errors[0]}."
        )
    body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    path.write_text(body, encoding="utf-8")
    return previous != body


def edition_projection_schema_path() -> Path:
    """Return the shipped strict schema for the v1 projection contract."""
    return (
        Path(__file__).resolve().parent
        / "schemas"
        / "edition-projection"
        / "v1"
        / "projection.schema.json"
    )


def validate_edition_projection_payload(payload: Mapping[str, Any]) -> list[str]:
    """Return stable, human-readable v1 schema violations."""
    import jsonschema

    schema = json.loads(edition_projection_schema_path().read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors: list[str] = []
    for error in sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in error.path) or "root"
        errors.append(f"{location}: {error.message}")
    return errors


def _content_digest(node: DocNode) -> str:
    payload = {
        "schema_version": 1,
        "mount": node.mount,
        "slug": node.slug,
        "title": node.title,
        "description": node.description,
        "layout": node.layout,
        "tags": sorted(node.tags),
        "lang": node.lang,
        "translation_key": node.translation_key,
        "content_format": node.content_format,
        "body_source": node.body_source,
        "body_text": node.body_text,
        "content": content_ir_record(node.content_ir, schema_version=3),
        "sections": [
            {
                "id": section.id,
                "heading": section.heading,
                "depth": section.depth,
                "text": section.text,
            }
            for section in node.sections
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _shared_node_id(node: DocNode, digest: str) -> str:
    slug = node.slug.strip("/") or "index"
    return f"{node.mount}:shared-{digest}:{slug}"


def _supersedes_values(node: DocNode) -> tuple[str, ...]:
    raw = node.meta.get("supersedes")
    if raw in (None, ""):
        return ()
    values = raw if isinstance(raw, (list, tuple, set, frozenset)) else (raw,)
    return tuple(sorted({str(item).strip() for item in values if str(item).strip()}))


def _edition_rank(order: Mapping[str, tuple[str, ...]], page: EditionPageRecord) -> int:
    try:
        return order[page.mount].index(page.edition)
    except KeyError, ValueError:
        return 1_000_000


def _projection_edges(
    pages: tuple[EditionPageRecord, ...],
    order: Mapping[str, tuple[str, ...]],
) -> tuple[EdgeRecord, ...]:
    by_key = {(page.mount, page.edition, page.slug): page for page in pages}
    parent = {page.node_id: page.node_id for page in pages}
    declared_pairs: list[tuple[EditionPageRecord, EditionPageRecord]] = []

    def find(node_id: str) -> str:
        root = node_id
        while parent[root] != root:
            root = parent[root]
        while parent[node_id] != node_id:
            next_id = parent[node_id]
            parent[node_id] = root
            node_id = next_id
        return root

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    by_slug: dict[tuple[str, str], list[EditionPageRecord]] = {}
    for page in pages:
        by_slug.setdefault((page.mount, page.slug), []).append(page)
    for group in by_slug.values():
        anchor = group[0]
        for page in group[1:]:
            union(anchor.node_id, page.node_id)

    for page in pages:
        for selector in page.supersedes:
            target = _declared_target(page, selector, by_key, pages, order)
            if target is not None and target.mount == page.mount:
                union(page.node_id, target.node_id)
                declared_pairs.append((page, target))

    groups: dict[str, list[EditionPageRecord]] = {}
    for page in pages:
        groups.setdefault(find(page.node_id), []).append(page)

    edges: dict[tuple[str, str, str, str, str], EdgeRecord] = {}

    def add(kind: EdgeKind, source: str, target: str, mount: str, edition: str) -> None:
        key = (kind.value, source, target, mount, edition)
        edges[key] = {
            "kind": kind.value,
            "source": source,
            "target": target,
            "mount": mount,
            "edition": edition,
        }

    for mount, editions in sorted(order.items()):
        for newer, older in pairwise(editions):
            add(
                EdgeKind.SUPERSEDES,
                _release_id(mount, newer),
                _release_id(mount, older),
                mount,
                newer,
            )

    for source, target in declared_pairs:
        add(
            EdgeKind.SUPERSEDES,
            source.node_id,
            target.node_id,
            source.mount,
            source.edition,
        )

    for group in groups.values():
        group.sort(key=lambda item: (_edition_rank(order, item), item.slug, item.node_id))
        editions = tuple(dict.fromkeys(page.edition for page in group))
        for page in group:
            for edition in editions:
                add(
                    EdgeKind.AVAILABLE_IN,
                    page.node_id,
                    _release_id(page.mount, edition),
                    page.mount,
                    page.edition,
                )
        unique_by_edition: dict[str, EditionPageRecord] = {}
        ambiguous: set[str] = set()
        for page in group:
            if page.edition in unique_by_edition:
                ambiguous.add(page.edition)
            unique_by_edition[page.edition] = page
        ordered = [
            unique_by_edition[edition]
            for edition in order.get(group[0].mount, ())
            if edition in unique_by_edition and edition not in ambiguous
        ]
        for newer, older in pairwise(ordered):
            add(
                EdgeKind.SUPERSEDES,
                newer.node_id,
                older.node_id,
                newer.mount,
                newer.edition,
            )
    return tuple(edges[key] for key in sorted(edges))


def _declared_target(
    page: EditionPageRecord,
    selector: str,
    by_key: Mapping[tuple[str, str, str], EditionPageRecord],
    pages: tuple[EditionPageRecord, ...],
    order: Mapping[str, tuple[str, ...]],
) -> EditionPageRecord | None:
    if selector.count(":") >= 2:
        try:
            mount, edition, slug = parse_node_id(selector)
        except ValueError:
            return None
        if mount != page.mount:
            return None
        return by_key.get((mount, edition, "" if slug == "index" else slug))
    editions = order.get(page.mount, ())
    try:
        source_rank = editions.index(page.edition)
    except ValueError:
        return None
    slug = selector.strip("/")
    for edition in editions[source_rank + 1 :]:
        target = by_key.get((page.mount, edition, slug))
        if target is not None:
            return target
    return next(
        (
            item
            for item in pages
            if item.mount == page.mount and item.slug == slug and item.edition != page.edition
        ),
        None,
    )


def _shared_nodes(pages: tuple[EditionPageRecord, ...]) -> tuple[dict[str, Any], ...]:
    groups: dict[str, list[EditionPageRecord]] = {}
    for page in pages:
        groups.setdefault(page.shared_node_id, []).append(page)
    return tuple(
        {
            "node_id": node_id,
            "mount": members[0].mount,
            "slug": members[0].slug,
            "content_digest": members[0].content_digest,
            "members": [
                {
                    "node_id": page.node_id,
                    "edition": page.edition,
                    "source_path": page.source_path,
                    "source_ref": page.source_ref,
                    "resolved_ref": page.resolved_ref,
                }
                for page in sorted(members, key=lambda item: (item.edition, item.node_id))
            ],
        }
        for node_id, members in sorted(groups.items())
    )


def _resolution_metrics(
    projection: EditionProjection,
    statuses: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    total_counts = {name: 0 for name in ("direct", "supersedes", "ancestor", "landing", "missing")}
    mounts: dict[str, dict[str, Any]] = {}
    for mount, editions in sorted(projection.edition_order.items()):
        counts = {name: 0 for name in total_counts}
        selectable = tuple(
            edition for edition in editions if statuses.get((mount, edition), "current") != "eol"
        )
        mount_pages = [page for page in projection.pages if page.mount == mount]
        for page in mount_pages:
            for target in selectable:
                if target == page.edition:
                    continue
                result = projection.resolve(
                    mount=mount,
                    source_node_id=page.node_id,
                    target_edition=target,
                )
                counts[result.resolution if result is not None else "missing"] += 1
        mounts[mount] = _metric_record(counts)
        for name, count in counts.items():
            total_counts[name] += count
    return {
        **_metric_record(total_counts),
        "mounts": mounts,
    }


def _metric_record(counts: Mapping[str, int]) -> dict[str, Any]:
    attempts = sum(counts.values())
    direct = counts["direct"]
    fallback = attempts - direct - counts["missing"]
    return {
        "attempts": attempts,
        "direct_hits": direct,
        "fallbacks": fallback,
        "missing": counts["missing"],
        "direct_hit_rate": round(direct / attempts, 6) if attempts else 1.0,
        "fallback_rate": round(fallback / attempts, 6) if attempts else 0.0,
        "resolutions": dict(counts),
    }


def _release_id(mount: str, edition: str) -> str:
    return f"release:{mount}:{edition}"


def _plain(value: Any) -> Any:
    if isinstance(value, RuntimeMapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def projection_page_url(page: EditionPageRecord) -> str:
    """Return a canonical route even for older sidecars with unscoped page URLs."""
    if page.edition == "latest" or page.url.startswith(f"/v{page.edition}/"):
        return page.url
    return edition_path(page.url, page.edition)
