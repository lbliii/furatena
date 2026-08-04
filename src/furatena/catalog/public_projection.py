"""Read-only, revision-bound simulation of every public catalog projection."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from threading import Event, Lock, RLock
from types import MappingProxyType
from typing import Any

from furatena.catalog.access import AccessPermission, AccessSubject, accessible_nodes
from furatena.catalog.application_roots import ApplicationRoots
from furatena.catalog.author_store import source_revision
from furatena.catalog.author_truth import AuthorFreshness, AuthorPublicationTruth
from furatena.catalog.lifecycle import visibility_state
from furatena.catalog.pdf_export import PDFExportOptions, export_pdfs
from furatena.catalog.publication_contracts import PublicationPlan, canonical_json_bytes
from furatena.catalog.render import DocsRenderer
from furatena.catalog.render_context import RenderContextService
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import (
    StaticExportOptions,
    export_static_site,
    url_path_to_output_file,
)
from furatena.catalog.structure_index import build_structure_index
from furatena.catalog.validation import ValidationSnapshotService
from furatena.catalog.visibility_audit import (
    VisibilityCanary,
    scan_visibility_leaks,
)

PUBLIC_PROJECTION_SCHEMA_VERSION = 1


class PublicProjectionOperation(StrEnum):
    PUBLISH = "publish"
    UNPUBLISH = "unpublish"
    ARCHIVE = "archive"


class PublicProjectionSurfaceId(StrEnum):
    HTML = "anonymous_html"
    ROUTE = "route_availability"
    NAVIGATION = "navigation"
    SIDEBAR = "sidebar"
    BREADCRUMBS = "breadcrumbs"
    SEARCH = "search"
    SUGGESTIONS = "suggestions"
    DCP_CATALOG = "dcp_catalog"
    DCP_STRUCTURE = "dcp_structure"
    CHANNELS = "channels"
    STATIC = "static_export"
    SITEMAP = "sitemap"
    PDF = "pdf"
    PAGE_TEXT = "page_text"
    PAGE_MARKDOWN = "page_markdown"
    LLMS = "llms_txt"
    LLMS_FULL = "llms_full_txt"
    TOOLS = "tools"
    MCP = "mcp_public_resources"


PUBLIC_PROJECTION_SURFACES = tuple(PublicProjectionSurfaceId)


class PublicProjectionChange(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class PublicProjectionPlan:
    """The exact source transition inspected by the projection simulator."""

    plan_id: str
    plan_digest: str
    source_revision: str
    change_digest: str
    node_id: str
    operation: PublicProjectionOperation
    previous_visibility: str
    resulting_visibility: str
    workflow_plan: bool = False

    @classmethod
    def create(
        cls,
        *,
        source_revision: str,
        change: str,
        node_id: str,
        operation: PublicProjectionOperation | str,
        previous_visibility: str,
        resulting_visibility: str,
    ) -> PublicProjectionPlan:
        normalized_operation = PublicProjectionOperation(operation)
        change_digest = _digest_text(change)
        payload = {
            "schema_version": PUBLIC_PROJECTION_SCHEMA_VERSION,
            "kind": "local_public_projection_plan",
            "source_revision": _digest(source_revision, "source revision"),
            "change_digest": change_digest,
            "node_id": _required(node_id, "node id"),
            "operation": normalized_operation.value,
            "previous_visibility": _required(previous_visibility, "previous visibility"),
            "resulting_visibility": _required(resulting_visibility, "resulting visibility"),
        }
        plan_digest = _digest_bytes(canonical_json_bytes(payload))
        return cls(
            plan_id=f"projection-plan:{plan_digest.removeprefix('sha256:')}",
            plan_digest=plan_digest,
            source_revision=payload["source_revision"],
            change_digest=change_digest,
            node_id=payload["node_id"],
            operation=normalized_operation,
            previous_visibility=payload["previous_visibility"],
            resulting_visibility=payload["resulting_visibility"],
        )

    @classmethod
    def from_workflow_plan(cls, plan: PublicationPlan) -> PublicProjectionPlan:
        operation = PublicProjectionOperation(plan.request.operation.value)
        return cls(
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            source_revision=plan.bindings.source_revision,
            change_digest=plan.changeset.diff_sha256,
            node_id=plan.identity.node_id,
            operation=operation,
            previous_visibility=plan.request.previous_visibility,
            resulting_visibility=plan.request.resulting_visibility,
            workflow_plan=True,
        )

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _required(self.plan_id, "plan id"))
        object.__setattr__(self, "plan_digest", _digest(self.plan_digest, "plan digest"))
        object.__setattr__(
            self, "source_revision", _digest(self.source_revision, "source revision")
        )
        object.__setattr__(self, "change_digest", _digest(self.change_digest, "change digest"))
        object.__setattr__(self, "node_id", _required(self.node_id, "node id"))
        object.__setattr__(self, "operation", PublicProjectionOperation(self.operation))
        object.__setattr__(
            self,
            "previous_visibility",
            _required(self.previous_visibility, "previous visibility"),
        )
        object.__setattr__(
            self,
            "resulting_visibility",
            _required(self.resulting_visibility, "resulting visibility"),
        )
        expected = {
            PublicProjectionOperation.PUBLISH: "public",
            PublicProjectionOperation.UNPUBLISH: "draft",
            PublicProjectionOperation.ARCHIVE: "archived",
        }[self.operation]
        if self.resulting_visibility != expected:
            raise ValueError(
                f"A {self.operation.value} public projection must result in {expected} visibility."
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "plan_digest": self.plan_digest,
            "source_revision": self.source_revision,
            "change_digest": self.change_digest,
            "node_id": self.node_id,
            "operation": self.operation.value,
            "previous_visibility": self.previous_visibility,
            "resulting_visibility": self.resulting_visibility,
            "kind": "workflow" if self.workflow_plan else "local_transition",
        }


@dataclass(frozen=True, slots=True)
class PublicProjectionDeliveryIdentity:
    plane: str
    identity: str | None
    state: str
    freshness: str

    def to_dict(self) -> dict[str, object]:
        return {
            "plane": self.plane,
            "identity": self.identity,
            "state": self.state,
            "freshness": self.freshness,
            "drift": self.freshness != AuthorFreshness.CURRENT.value,
        }


@dataclass(frozen=True, slots=True)
class PublicProjectionSurface:
    surface_id: PublicProjectionSurfaceId
    route: str | None
    previous_present: bool
    resulting_present: bool
    previous_digest: str | None
    resulting_digest: str | None
    change: PublicProjectionChange
    preview: object | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "preview", _freeze_json(self.preview))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.surface_id.value,
            "route": self.route,
            "previous_present": self.previous_present,
            "resulting_present": self.resulting_present,
            "previous_digest": self.previous_digest,
            "resulting_digest": self.resulting_digest,
            "change": self.change.value,
            "affected": self.change != PublicProjectionChange.UNCHANGED,
            "preview": _thaw_json(self.preview),
        }


@dataclass(frozen=True, slots=True)
class PublicProjectionInspection:
    ok: bool
    complete: bool
    read_only: bool
    plan: PublicProjectionPlan
    source: PublicProjectionDeliveryIdentity
    frozen_artifact: PublicProjectionDeliveryIdentity
    deployed_artifact: PublicProjectionDeliveryIdentity
    surfaces: tuple[PublicProjectionSurface, ...]
    privacy: Mapping[str, object]
    diagnostics: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "privacy", _freeze_json(dict(self.privacy)))
        object.__setattr__(
            self,
            "diagnostics",
            tuple(_freeze_json(dict(item)) for item in self.diagnostics),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PUBLIC_PROJECTION_SCHEMA_VERSION,
            "kind": "public_projection_inspection",
            "ok": self.ok,
            "complete": self.complete,
            "read_only": self.read_only,
            "plan": self.plan.to_dict(),
            "identities": {
                "source": self.source.to_dict(),
                "frozen_artifact": self.frozen_artifact.to_dict(),
                "deployed_artifact": self.deployed_artifact.to_dict(),
            },
            "surfaces": [surface.to_dict() for surface in self.surfaces],
            "privacy": _thaw_json(self.privacy),
            "diagnostics": [_thaw_json(item) for item in self.diagnostics],
        }


@dataclass(frozen=True, slots=True)
class _SurfaceSnapshot:
    route: str | None
    target: object | None
    complete_payload: object


@dataclass(frozen=True, slots=True)
class _SurfaceBuild:
    surfaces: Mapping[PublicProjectionSurfaceId, _SurfaceSnapshot]
    privacy: Mapping[str, object]


OutputMutator = Callable[[Path], None]
SurfaceBuilder = Callable[[Any, Any, Any | None, Any | None, OutputMutator | None], _SurfaceBuild]


@dataclass(slots=True)
class _ProjectionFlight:
    event: Event
    epoch: int
    result: PublicProjectionInspection | None = None


class PublicProjectionInspectionCache:
    """Small lock-owned LRU with same-key build coalescing for free-threaded Python."""

    def __init__(self, *, max_entries: int = 8, max_bytes: int = 64 * 1024 * 1024) -> None:
        if max_entries < 1 or max_bytes < 1:
            raise ValueError("Public projection cache entry and byte bounds must be positive.")
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lock = RLock()
        self._build_lock = RLock()
        self._entries: OrderedDict[str, tuple[PublicProjectionInspection, int]] = OrderedDict()
        self._inflight: dict[str, _ProjectionFlight] = {}
        self._bytes = 0
        self._epoch = 0

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def byte_count(self) -> int:
        with self._lock:
            return self._bytes

    def invalidate(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0
            self._epoch += 1

    def get_or_compute(
        self,
        key: str,
        compute: Callable[[], PublicProjectionInspection],
    ) -> PublicProjectionInspection:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached[0]
            flight = self._inflight.get(key)
            if flight is not None and flight.epoch != self._epoch:
                flight = None
            leader = flight is None
            if flight is None:
                flight = _ProjectionFlight(Event(), self._epoch)
                self._inflight[key] = flight
        if not leader:
            flight.event.wait()
            if flight.result is None:
                return self.get_or_compute(key, compute)
            return flight.result

        try:
            # Static export temporarily owns process-wide renderer/environment state.
            # Serialize distinct cache misses without holding the metadata lock.
            with self._build_lock:
                result = compute()
        except BaseException:
            with self._lock:
                if self._inflight.get(key) is flight:
                    self._inflight.pop(key, None)
                flight.event.set()
            raise
        with self._lock:
            flight.result = result
            if flight.epoch == self._epoch and _cacheable_inspection(result):
                size = len(canonical_json_bytes(result.to_dict()))
                if size <= self.max_bytes:
                    self._entries[key] = (result, size)
                    self._bytes += size
                    while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                        _old_key, (_old_value, old_size) = self._entries.popitem(last=False)
                        self._bytes -= old_size
            if self._inflight.get(key) is flight:
                self._inflight.pop(key, None)
            flight.event.set()
        return result


class PublicProjectionInspector:
    """Build current and proposed public outputs without mutating catalog or source state."""

    def __init__(
        self,
        catalog: Any,
        *,
        config: Any | None = None,
        docs_app: Any | None = None,
        surface_builder: SurfaceBuilder | None = None,
        output_mutator: OutputMutator | None = None,
        cache: PublicProjectionInspectionCache | None = None,
    ) -> None:
        self.catalog = catalog
        self.config = config
        self.docs_app = docs_app
        self.surface_builder = surface_builder or _production_surface_snapshots
        self.output_mutator = output_mutator
        self.cache = cache or PublicProjectionInspectionCache()

    def inspect(
        self,
        node: Any,
        plan: PublicProjectionPlan,
        *,
        current_source_revision: str,
        publication: AuthorPublicationTruth | None = None,
    ) -> PublicProjectionInspection:
        def compute() -> PublicProjectionInspection:
            return self._inspect_uncached(
                node,
                plan,
                current_source_revision=current_source_revision,
                publication=publication,
            )

        if self.output_mutator is not None:
            return compute()
        key = _inspection_cache_key(
            self.catalog,
            self.docs_app,
            self.surface_builder,
            node,
            plan,
            current_source_revision=current_source_revision,
            publication=publication,
        )
        return self.cache.get_or_compute(key, compute)

    def _inspect_uncached(
        self,
        node: Any,
        plan: PublicProjectionPlan,
        *,
        current_source_revision: str,
        publication: AuthorPublicationTruth | None = None,
    ) -> PublicProjectionInspection:
        diagnostics = _binding_diagnostics(
            self.catalog,
            node,
            plan,
            current_source_revision,
            require_source_binding=self.docs_app is not None,
        )
        identities = _delivery_identities(plan, publication)
        if diagnostics:
            return PublicProjectionInspection(
                ok=False,
                complete=False,
                read_only=True,
                plan=plan,
                source=identities[0],
                frozen_artifact=identities[1],
                deployed_artifact=identities[2],
                surfaces=(),
                privacy={"status": "not_run", "canary_count": 0, "matches": []},
                diagnostics=diagnostics,
            )

        original_fingerprint = _catalog_memory_fingerprint(self.catalog)
        projected = replace(node, meta=_projected_meta(node.meta, plan.operation))
        try:
            current_catalog = _ProjectedCatalog(self.catalog, node)
            resulting_catalog = _ProjectedCatalog(self.catalog, projected)
            current_build = self.surface_builder(
                current_catalog,
                node,
                self.config,
                self.docs_app,
                None,
            )
            resulting_build = self.surface_builder(
                resulting_catalog,
                projected,
                self.config,
                self.docs_app,
                self.output_mutator,
            )
            current = current_build.surfaces
            resulting = resulting_build.surfaces
            missing = set(PUBLIC_PROJECTION_SURFACES) - set(current) | (
                set(PUBLIC_PROJECTION_SURFACES) - set(resulting)
            )
            if missing:
                raise ValueError(
                    "projection builder omitted required surfaces: "
                    + ", ".join(sorted(item.value for item in missing))
                )
            surfaces = tuple(
                _surface_result(surface_id, current[surface_id], resulting[surface_id])
                for surface_id in PUBLIC_PROJECTION_SURFACES
            )
            privacy = resulting_build.privacy
        except Exception as error:
            return PublicProjectionInspection(
                ok=False,
                complete=False,
                read_only=True,
                plan=plan,
                source=identities[0],
                frozen_artifact=identities[1],
                deployed_artifact=identities[2],
                surfaces=(),
                privacy={"status": "not_run", "canary_count": 0, "matches": []},
                diagnostics=(
                    {
                        "rule_id": "fura.public_projection.incomplete",
                        "severity": "error",
                        "message": f"Public projection did not complete: {_safe_error(error)}",
                        "next_action": "Repair the failing projection before approving or executing the plan.",
                    },
                ),
            )
        if _catalog_memory_fingerprint(self.catalog) != original_fingerprint:
            return PublicProjectionInspection(
                ok=False,
                complete=False,
                read_only=False,
                plan=plan,
                source=identities[0],
                frozen_artifact=identities[1],
                deployed_artifact=identities[2],
                surfaces=surfaces,
                privacy=privacy,
                diagnostics=(
                    {
                        "rule_id": "fura.public_projection.mutated",
                        "severity": "error",
                        "message": "Public inspection changed the source catalog in memory.",
                        "next_action": "Reject the projection implementation and restore a read-only adapter.",
                    },
                ),
            )
        privacy_ok = privacy.get("status") == "pass"
        return PublicProjectionInspection(
            ok=privacy_ok,
            complete=True,
            read_only=True,
            plan=plan,
            source=identities[0],
            frozen_artifact=identities[1],
            deployed_artifact=identities[2],
            surfaces=surfaces,
            privacy=privacy,
            diagnostics=(
                ()
                if privacy_ok
                else (
                    {
                        "rule_id": "fura.public_projection.privacy_canary",
                        "severity": "error",
                        "message": "Protected-content canaries appeared in a proposed public projection.",
                        "next_action": "Stop publication and repair visibility filtering on every affected surface.",
                    },
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class PublicProjectionTransportAdapter:
    """Thin transport label over the same immutable inspection service."""

    transport: str
    inspector: PublicProjectionInspector

    def inspect(
        self,
        node: Any,
        plan: PublicProjectionPlan,
        *,
        current_source_revision: str,
        publication: AuthorPublicationTruth | None = None,
    ) -> dict[str, object]:
        if self.transport not in {"browser", "cli", "mcp", "automation"}:
            raise ValueError(
                f"The requested public projection transport is unsupported: {self.transport}."
            )
        return self.inspector.inspect(
            node,
            plan,
            current_source_revision=current_source_revision,
            publication=publication,
        ).to_dict()


def inspect_public_transition(
    catalog: Any,
    node: Any,
    transition: Any,
    *,
    current_source_revision: str,
    config: Any | None = None,
    docs_app: Any | None = None,
    publication: AuthorPublicationTruth | None = None,
    transport: str = "automation",
) -> dict[str, object]:
    """Project one authorized author dry-run through the shared transport adapter."""

    if not bool(getattr(transition, "ok", False)):
        raise ValueError("Public projection requires a successful author transition dry run.")
    if not bool(getattr(transition, "dry_run", False)):
        raise ValueError("Public projection accepts only read-only author transition results.")
    plan = PublicProjectionPlan.create(
        source_revision=current_source_revision,
        change=str(getattr(transition, "diff", "")),
        node_id=node.node_id,
        operation=str(getattr(transition, "operation", "")),
        previous_visibility=str(getattr(transition, "previous_visibility", "")),
        resulting_visibility=str(getattr(transition, "resulting_visibility", "")),
    )
    cache = (
        getattr(docs_app, "_public_projection_inspection_cache", None)
        if docs_app is not None
        else None
    )
    projection = PublicProjectionTransportAdapter(
        transport,
        PublicProjectionInspector(
            catalog,
            config=config,
            docs_app=docs_app,
            cache=cache,
        ),
    ).inspect(
        node,
        plan,
        current_source_revision=current_source_revision,
        publication=publication,
    )
    transition_payload = transition.to_dict()
    return {
        **transition_payload,
        **projection,
        "transition": transition_payload,
    }


class _ProjectedCatalog:
    """Read-through catalog view that replaces one immutable node in O(N)."""

    def __init__(self, catalog: Any, node: Any) -> None:
        self._catalog = catalog
        self._node = node
        self._node_id = node.node_id
        self.nodes = tuple(self._replace(item) for item in catalog.nodes)

    def _replace(self, item: Any) -> Any:
        return self._node if item.node_id == self._node_id else item

    def doc_nodes(self, *, lang: str | None = None) -> list[Any]:
        return [self._replace(item) for item in self._catalog.doc_nodes(lang=lang)]

    def all_doc_nodes(self) -> list[Any]:
        method = getattr(self._catalog, "all_doc_nodes", None)
        values = method() if callable(method) else self._catalog.doc_nodes()
        return [self._replace(item) for item in values]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._catalog, name)


def _production_surface_snapshots(
    catalog: Any,
    target: Any,
    config: Any | None,
    docs_app: Any | None,
    output_mutator: OutputMutator | None,
) -> _SurfaceBuild:
    """Run the real live/static/PDF producers against an isolated catalog clone."""

    if docs_app is None or config is None:
        raise ValueError(
            "complete public projection requires the composed DocsApp production runtime"
        )
    site_name = str(config.site.name or "Furatena")

    with tempfile.TemporaryDirectory(prefix="furatena-public-projection-") as raw_root:
        root = Path(raw_root)
        public_catalog = _clone_public_catalog(catalog, root / "source")
        public_target = next(
            (node for node in public_catalog.nodes if node.node_id == target.node_id),
            None,
        )
        target_public = public_target is not None
        runtime = _clone_docs_runtime(docs_app, public_catalog, root)
        output_dir = root / "public"
        export_static_site(
            runtime,
            StaticExportOptions(
                output_dir=output_dir,
                base_path="",
                include_index_txt=True,
                include_portal=True,
                include_search=True,
                allow_lifecycle_errors=True,
            ),
        )
        structure_path = output_dir / "structure.json"
        structure_path.write_text(
            json.dumps(
                build_structure_index(
                    public_catalog,
                    subject=AccessSubject.anonymous(),
                ),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        pdf_result = None
        if public_catalog.doc_nodes():
            pdf_result = export_pdfs(
                public_catalog,
                config=config,
                options=PDFExportOptions(
                    output_dir=output_dir / "pdf",
                    site_name=site_name,
                    update_channel_manifest=False,
                ),
            )
        if output_mutator is not None:
            output_mutator(output_dir)

        graph = _json_file(output_dir / "catalog.json")
        structure = _json_file(output_dir / "structure.json")
        search = _json_file(output_dir / "search.json")
        channels = _json_file(output_dir / "channels.json")
        tools = _json_file(output_dir / "tools.json")
        llms_index = _text_file(output_dir / "llms.txt")
        llms_full = _text_file(output_dir / "llms-full.txt")
        sitemap = _text_file(output_dir / "sitemap.xml")
        graph_target = _find_record(graph.get("pages", ()), target.node_id)
        search_target = _find_record(search.get("entries", ()), target.node_id)
        structure_target = (
            {
                "node_id": target.node_id,
                "directives": _records_for_node(structure.get("directives", ()), target.node_id),
                "headings": _records_for_node(structure.get("headings", ()), target.node_id),
            }
            if target_public
            else None
        )

        html_path = output_dir / url_path_to_output_file(target.url)
        text_route = f"{target.url.rstrip('/')}/index.txt"
        markdown_route = f"{target.url.rstrip('/')}.md"
        text_path = output_dir / url_path_to_output_file(text_route)
        markdown_path = output_dir / url_path_to_output_file(markdown_route)
        html = _optional_text_file(html_path)
        page_text = _optional_text_file(text_path)
        page_markdown = _optional_text_file(markdown_path)

        context: Mapping[str, Any] = {}
        if public_target is not None:
            context = runtime.render_context.page_context(public_target)
        navigation = context.get("catalog_rail_items", ())
        sidebar = context.get("nav_items", ())
        breadcrumbs = context.get("breadcrumb_items", ())
        navigation_target = _find_href_record(navigation, target.url)
        sidebar_target = _find_href_record(sidebar, target.url)
        breadcrumb_target = _find_href_record(breadcrumbs, target.url)

        suggestions = [
            {"node_id": item["node_id"], "title": item["title"], "url": item["url"]}
            for item in search.get("entries", ())
            if isinstance(item, Mapping) and all(key in item for key in ("node_id", "title", "url"))
        ]
        suggestions_target = _find_record(suggestions, target.node_id)
        channels_target = (
            {"node_id": target.node_id, "page_count": channels.get("page_count")}
            if target_public
            else None
        )
        tools_target = (
            {
                "node_id": target.node_id,
                "page_count": tools.get("page_count"),
                "api_operation": next(
                    (
                        item
                        for item in tools.get("api_operations", ())
                        if isinstance(item, Mapping) and item.get("node_id") == target.node_id
                    ),
                    None,
                ),
            }
            if target_public
            else None
        )
        llms_target = (
            {"node_id": target.node_id, "title": target.title, "url": target.url}
            if target_public
            and target.title in llms_index
            and f"{target.url.rstrip('/')}.md" in llms_index
            else None
        )
        llms_full_target = (
            {"node_id": target.node_id, "title": target.title, "url": target.url}
            if target_public and target.title in llms_full
            else None
        )
        pdf_paths = tuple(pdf_result.paths) if pdf_result is not None else ()
        pdf_text = _pdf_output_text(pdf_paths)
        pdf_target = (
            {
                "node_id": target.node_id,
                "title": target.title,
                "page_count": _pdf_page_count(pdf_paths),
                "text_digest": _digest_text(pdf_text),
            }
            if target_public and target.title in pdf_text
            else None
        )
        static_inventory = _artifact_inventory(output_dir)
        static_target = (
            {
                "node_id": target.node_id,
                "path": html_path.relative_to(output_dir).as_posix(),
                "digest": _file_digest(html_path),
            }
            if html_path.is_file()
            else None
        )
        canaries = _visibility_canaries(catalog)
        audit = scan_visibility_leaks(output_dir, canaries)
        privacy = {
            "status": "pass" if audit.ok else "fail",
            "protected_node_count": len(canaries),
            "canary_count": sum(len(canary.tokens) for canary in canaries),
            "scanned_artifacts": audit.scanned_artifacts,
            "matches": [
                {
                    "source_path": finding.source_path,
                    "artifact": finding.artifact.as_posix(),
                    "token_digest": _digest_text(finding.token),
                }
                for finding in audit.findings
            ],
        }

        snapshots = {
            PublicProjectionSurfaceId.HTML: _SurfaceSnapshot(
                target.url,
                (
                    {
                        "node_id": target.node_id,
                        "url": target.url,
                        "media_type": "text/html",
                        "html": html,
                    }
                    if html is not None
                    else None
                ),
                {"path": html_path.relative_to(output_dir).as_posix(), "html": html},
            ),
            PublicProjectionSurfaceId.ROUTE: _SurfaceSnapshot(
                target.url,
                {"node_id": target.node_id, "status": 200} if html is not None else None,
                {"available": html is not None},
            ),
            PublicProjectionSurfaceId.NAVIGATION: _SurfaceSnapshot(
                None, navigation_target, navigation
            ),
            PublicProjectionSurfaceId.SIDEBAR: _SurfaceSnapshot(None, sidebar_target, sidebar),
            PublicProjectionSurfaceId.BREADCRUMBS: _SurfaceSnapshot(
                None, breadcrumb_target, breadcrumbs
            ),
            PublicProjectionSurfaceId.SEARCH: _SurfaceSnapshot(
                "/search.json", search_target, search
            ),
            PublicProjectionSurfaceId.SUGGESTIONS: _SurfaceSnapshot(
                "/search/suggest", suggestions_target, suggestions
            ),
            PublicProjectionSurfaceId.DCP_CATALOG: _SurfaceSnapshot(
                "/catalog.json", graph_target, graph
            ),
            PublicProjectionSurfaceId.DCP_STRUCTURE: _SurfaceSnapshot(
                "/structure.json", structure_target, structure
            ),
            PublicProjectionSurfaceId.CHANNELS: _SurfaceSnapshot(
                "/channels.json", channels_target, channels
            ),
            PublicProjectionSurfaceId.STATIC: _SurfaceSnapshot(
                None, static_target, static_inventory
            ),
            PublicProjectionSurfaceId.SITEMAP: _SurfaceSnapshot(
                "/sitemap.xml",
                {"node_id": target.node_id, "url": target.url}
                if target_public and target.url in sitemap
                else None,
                sitemap,
            ),
            PublicProjectionSurfaceId.PDF: _SurfaceSnapshot(None, pdf_target, pdf_text),
            PublicProjectionSurfaceId.PAGE_TEXT: _SurfaceSnapshot(
                text_route,
                {"node_id": target.node_id, "url": text_route, "text": page_text}
                if page_text is not None
                else None,
                page_text or "",
            ),
            PublicProjectionSurfaceId.PAGE_MARKDOWN: _SurfaceSnapshot(
                markdown_route,
                {
                    "node_id": target.node_id,
                    "url": markdown_route,
                    "markdown": page_markdown,
                }
                if page_markdown is not None
                else None,
                page_markdown or "",
            ),
            PublicProjectionSurfaceId.LLMS: _SurfaceSnapshot("/llms.txt", llms_target, llms_index),
            PublicProjectionSurfaceId.LLMS_FULL: _SurfaceSnapshot(
                "/llms-full.txt", llms_full_target, llms_full
            ),
            PublicProjectionSurfaceId.TOOLS: _SurfaceSnapshot("/tools.json", tools_target, tools),
            PublicProjectionSurfaceId.MCP: _SurfaceSnapshot(
                "fura://catalog",
                graph_target,
                {"catalog": graph, "search": search, "structure": structure},
            ),
        }
        return _SurfaceBuild(MappingProxyType(snapshots), MappingProxyType(privacy))


def _clone_public_catalog(catalog: Any, source_root: Path) -> Any:
    """Clone one catalog graph and retain only anonymous-exportable nodes."""

    allowed = accessible_nodes(
        catalog,
        catalog.nodes,
        subject=AccessSubject.anonymous(),
        permission=AccessPermission.EXPORT,
    )
    allowed_by_id = {node.node_id: replace(node, meta=copy.deepcopy(node.meta)) for node in allowed}
    registry = getattr(catalog, "_catalog", catalog)
    if hasattr(registry, "_shards"):
        clone = copy.copy(registry)
        clone._publication_lock = RLock()
        clone._read_generation_context = ContextVar(
            f"furatena_projection_read_generation_{id(clone)}",
            default=None,
        )
        clone.mounts = tuple(
            replace(
                mount,
                content_root=(source_root / mount.id).resolve(),
            )
            for mount in registry.mounts
        )
        for mount in clone.mounts:
            mount.content_root.mkdir(parents=True, exist_ok=True)
        cloned_mounts = {mount.id: mount for mount in clone.mounts}
        clone._mount_for_url = [
            (prefix, cloned_mounts[mount.id]) for prefix, mount in registry._mount_for_url
        ]
        clone._shards = {
            mount_id: _clone_public_shard(
                shard,
                allowed_by_id,
                content_root=source_root / mount_id,
            )
            for mount_id, shard in registry._shards.items()
        }
        clone._edition_shards = {}
        clone._edition_shards_lock = RLock()
        clone._edition_context = ContextVar(
            f"furatena_projection_edition_{id(clone)}",
            default=registry.active_channel,
        )
        clone._html_cache = {}
        clone._edges = None
        clone._namespaces = None
        clone._query_graph_cache = {}
        clone._query_graph_lock = Lock()
        clone._translation_index = None
        clone._federated_backlinks = {}
        clone._federated_slug_urls = dict(registry._federated_slug_urls)
        clone._source_sync_status = copy.deepcopy(registry._source_sync_status)
        clone._shard_status = copy.deepcopy(registry._shard_status)
        clone._discovered_editions = dict(registry._discovered_editions)
        clone._generation_lock = Lock()
        clone._watcher = None
        clone._inventory_store = _clone_inventory_store(registry._inventory_store)
        clone.auto_reload = False
        clone.include_private = False
        for shard in clone._shards.values():
            shard._renderer.attach_reference_context(
                catalog=clone,
                inventory_store=clone._inventory_store,
            )
        # Rebuild the immutable request snapshot from the visibility-filtered
        # shards. Reusing the source registry's snapshot would let request-level
        # read pinning observe nodes that the projection clone intentionally
        # removed.
        clone._read_generation = None
        clone._finalize_federated()
        return clone
    source_root.mkdir(parents=True, exist_ok=True)
    clone = _clone_public_shard(registry, allowed_by_id, content_root=source_root)
    inventory_store = _clone_inventory_store(getattr(registry._renderer, "_inventory_store", None))
    clone._renderer.attach_reference_context(
        catalog=clone,
        inventory_store=inventory_store,
    )
    return clone


def _clone_public_shard(
    shard: Any,
    allowed_by_id: Mapping[str, Any],
    *,
    content_root: Path,
) -> Any:
    clone = copy.copy(shard)
    clone.content_root = content_root.resolve()
    nodes = [allowed_by_id[node.node_id] for node in shard.nodes if node.node_id in allowed_by_id]
    clone._nodes = nodes
    clone._nodes_by_url = {}
    clone._nodes_by_slug = {}
    for node in nodes:
        clone._nodes_by_url[node.url] = node
        if node.url != "/":
            clone._nodes_by_url[node.url.rstrip("/")] = node
        clone._nodes_by_slug[node.slug] = node
    clone._doc_nodes = None
    clone._doc_nodes_lang = None
    clone._nav_cache = {}
    clone._html_cache = {}
    clone._renderer = DocsRenderer()
    clone._source_mtimes = dict(getattr(shard, "_source_mtimes", {}))
    clone._raw_pages = list(getattr(shard, "_raw_pages", ()))
    clone._stubs = dict(getattr(shard, "_stubs", {}))
    clone._slug_to_url = dict(getattr(shard, "_slug_to_url", {}))
    clone._body_by_slug = dict(getattr(shard, "_body_by_slug", {}))
    clone._last_invalidations = dict(getattr(shard, "_last_invalidations", {}))
    clone._last_invalidation_regions = dict(getattr(shard, "_last_invalidation_regions", {}))
    clone._federated_slug_urls = dict(getattr(shard, "_federated_slug_urls", {}))
    cached_autodoc = getattr(shard, "_cached_autodoc_nodes", None)
    clone._cached_autodoc_nodes = list(cached_autodoc) if cached_autodoc is not None else None
    clone._ast_documents = {
        slug: document
        for slug, document in getattr(shard, "_ast_documents", {}).items()
        if slug in clone._nodes_by_slug
    }
    clone.auto_reload = False
    clone.include_private = False
    clone._watcher = None
    clone._finalize_graph()
    return clone


def _clone_inventory_store(store: Any | None) -> Any | None:
    if store is None:
        return None
    clone = copy.copy(store)
    clone.entries = dict(store.entries)
    clone.role_domains = dict(store.role_domains)
    return clone


def _clone_docs_runtime(docs_app: Any, catalog: Any, root: Path) -> Any:
    """Recompose the production app over isolated state and the projected graph."""

    runtime = copy.copy(docs_app)
    runtime.catalog = catalog
    runtime.roots = ApplicationRoots(
        site=docs_app.roots.site,
        platform=docs_app.roots.platform,
        state=(root / "state").resolve(),
        output=(root / "runtime-output").resolve(),
        managed=False,
    )
    runtime.roots.ensure_writable_roots()
    runtime.serve = ServeConfig(ServeMode.HYBRID, None, False, False)
    runtime.author_subject = AccessSubject.anonymous()
    runtime.validation = ValidationSnapshotService(
        catalog,
        views=runtime.views,
        docs=runtime.config,
        theme=runtime.theme,
        template_env=runtime._validation_template_env,
    )
    runtime.render_context = RenderContextService(
        runtime.config,
        catalog,
        runtime.views,
        runtime.locale_service,
        runtime.theme,
        runtime.validation,
    )
    runtime.app = runtime._build_app()
    return runtime


def _json_file(path: Path) -> dict[str, Any]:
    payload = json.loads(_text_file(path))
    if not isinstance(payload, dict):
        raise ValueError(f"projection artifact must contain a JSON object: {path.name}")
    return payload


def _text_file(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"projection producer omitted required artifact: {path.name}")
    return path.read_text(encoding="utf-8")


def _optional_text_file(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _file_digest(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _artifact_inventory(output_dir: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(output_dir).as_posix(),
            "digest": _file_digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    ]


def _pdf_output_text(paths: Sequence[Path]) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for path in paths for page in PdfReader(path).pages)


def _pdf_page_count(paths: Sequence[Path]) -> int:
    from pypdf import PdfReader

    return sum(len(PdfReader(path).pages) for path in paths)


def _find_href_record(values: object, href: str) -> object | None:
    if isinstance(values, Mapping):
        if values.get("href") == href or values.get("url") == href:
            return dict(values)
        for value in values.values():
            match = _find_href_record(value, href)
            if match is not None:
                return match
    elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
        for value in values:
            match = _find_href_record(value, href)
            if match is not None:
                return match
    return None


def _visibility_canaries(catalog: Any) -> tuple[VisibilityCanary, ...]:
    public_nodes = accessible_nodes(
        catalog,
        catalog.nodes,
        subject=AccessSubject.anonymous(),
        permission=AccessPermission.EXPORT,
    )
    public_ids = {node.node_id for node in public_nodes}
    public_text = "\n".join(
        "\n".join(
            (
                node.url,
                node.slug,
                node.source_path,
                node.title,
                node.body_md,
                node.body_text,
            )
        )
        for node in public_nodes
    )
    canaries: list[VisibilityCanary] = []
    for node in catalog.nodes:
        if node.node_id in public_ids:
            continue
        values = [node.url, node.slug, node.source_path, node.title]
        for body in (node.body_md, node.body_text):
            values.extend(line.strip().strip("#").strip() for line in body.splitlines())
        tokens = tuple(
            dict.fromkeys(
                token for token in values if 6 <= len(token) <= 240 and token not in public_text
            )
        )
        if not tokens:
            continue
        boundary = visibility_state(node.meta)
        canaries.append(
            VisibilityCanary(
                source_path=node.source_path,
                boundary=boundary if boundary != "public" else "protected",
                tokens=tokens,
            )
        )
    return tuple(canaries)


def _surface_result(
    surface_id: PublicProjectionSurfaceId,
    previous: _SurfaceSnapshot,
    resulting: _SurfaceSnapshot,
) -> PublicProjectionSurface:
    previous_digest = _value_digest(previous.target) if previous.target is not None else None
    resulting_digest = _value_digest(resulting.target) if resulting.target is not None else None
    if previous.target is None and resulting.target is not None:
        change = PublicProjectionChange.ADDED
    elif previous.target is not None and resulting.target is None:
        change = PublicProjectionChange.REMOVED
    elif previous_digest != resulting_digest:
        change = PublicProjectionChange.CHANGED
    else:
        change = PublicProjectionChange.UNCHANGED
    return PublicProjectionSurface(
        surface_id=surface_id,
        route=resulting.route or previous.route,
        previous_present=previous.target is not None,
        resulting_present=resulting.target is not None,
        previous_digest=previous_digest,
        resulting_digest=resulting_digest,
        change=change,
        preview=resulting.target,
    )


def _binding_diagnostics(
    catalog: Any,
    node: Any,
    plan: PublicProjectionPlan,
    current_source_revision: str,
    *,
    require_source_binding: bool,
) -> tuple[Mapping[str, str], ...]:
    failures: list[Mapping[str, str]] = []
    current_revision = _digest(current_source_revision, "current source revision")
    current_visibility = visibility_state(node.meta)
    catalog_source_revision = _catalog_source_revision(catalog, node)
    for rule_id, matches, message, next_action in (
        (
            "fura.public_projection.source_stale",
            current_revision == plan.source_revision,
            "The inspected source revision no longer matches the projection plan.",
            "Read the current source and create a fresh projection plan.",
        ),
        (
            "fura.public_projection.node_mismatch",
            node.node_id == plan.node_id,
            "The projection plan targets a different catalog node.",
            "Load the node identified by the plan before inspecting it.",
        ),
        (
            "fura.public_projection.lifecycle_stale",
            current_visibility == plan.previous_visibility,
            "The source lifecycle changed after the projection plan was created.",
            "Create a fresh plan from the current lifecycle state.",
        ),
        (
            "fura.public_projection.catalog_stale",
            not require_source_binding or catalog_source_revision == current_revision,
            "The catalog node was not built from the exact inspected source revision.",
            "Refresh the catalog from source and create a fresh projection plan.",
        ),
    ):
        if not matches:
            failures.append(
                {
                    "rule_id": rule_id,
                    "severity": "error",
                    "message": message,
                    "next_action": next_action,
                }
            )
    return tuple(failures)


def _catalog_source_revision(catalog: Any, node: Any) -> str | None:
    registry = getattr(catalog, "_catalog", catalog)
    roots: list[Path] = []
    mounts = getattr(registry, "mounts", ())
    if mounts:
        roots.extend(
            mount.content_root for mount in mounts if getattr(mount, "id", None) == node.mount
        )
    else:
        content_root = getattr(registry, "content_root", None)
        if content_root is not None:
            roots.append(Path(content_root))
    for root in roots:
        candidate = (Path(root) / node.source_path).resolve()
        if candidate.is_file():
            return source_revision(candidate.read_text(encoding="utf-8"))
    return None


def _delivery_identities(
    plan: PublicProjectionPlan, publication: AuthorPublicationTruth | None
) -> tuple[
    PublicProjectionDeliveryIdentity,
    PublicProjectionDeliveryIdentity,
    PublicProjectionDeliveryIdentity,
]:
    source = PublicProjectionDeliveryIdentity(
        "source", plan.source_revision, plan.previous_visibility, AuthorFreshness.CURRENT.value
    )
    if publication is None:
        unavailable = PublicProjectionDeliveryIdentity(
            "frozen_artifact", None, "unavailable", AuthorFreshness.UNKNOWN.value
        )
        deployment = PublicProjectionDeliveryIdentity(
            "deployed_artifact", None, "unavailable", AuthorFreshness.UNKNOWN.value
        )
        return source, unavailable, deployment
    return (
        source,
        PublicProjectionDeliveryIdentity(
            "frozen_artifact",
            publication.artifact.identity,
            publication.artifact.state.value,
            publication.artifact.freshness.value,
        ),
        PublicProjectionDeliveryIdentity(
            "deployed_artifact",
            publication.deployment.identity,
            publication.deployment.state.value,
            publication.deployment.freshness.value,
        ),
    )


def _inspection_cache_key(
    catalog: Any,
    docs_app: Any | None,
    surface_builder: SurfaceBuilder,
    node: Any,
    plan: PublicProjectionPlan,
    *,
    current_source_revision: str,
    publication: AuthorPublicationTruth | None,
) -> str:
    identities = _delivery_identities(plan, publication)
    configuration_fingerprint = "unavailable"
    renderer_fingerprint_value = "unavailable"
    if docs_app is not None:
        validation = getattr(docs_app, "validation", None)
        fingerprint = getattr(validation, "configuration_fingerprint", None)
        if callable(fingerprint):
            configuration_fingerprint = str(fingerprint())
        from furatena.catalog.renderer_fingerprint import renderer_fingerprint

        renderer_fingerprint_value = renderer_fingerprint(
            docs_app.config.root,
            theme_id=docs_app.config.theme.id,
            skin_pack_root=(
                docs_app.theme.presentation.skin.root
                if docs_app.theme.presentation.skin is not None
                else None
            ),
            platform_root=docs_app.roots.platform,
            presentation_digest=docs_app.theme.presentation.content_digest,
        )
    payload = {
        "schema_version": PUBLIC_PROJECTION_SCHEMA_VERSION,
        "plan": plan.to_dict(),
        "current_source_revision": _digest(
            current_source_revision,
            "current source revision",
        ),
        "node": _cache_value(node),
        "catalog_build_fingerprint": _catalog_memory_fingerprint(catalog),
        "configuration_fingerprint": configuration_fingerprint,
        "renderer_fingerprint": renderer_fingerprint_value,
        "surface_builder": (
            f"{getattr(surface_builder, '__module__', '')}."
            f"{getattr(surface_builder, '__qualname__', type(surface_builder).__qualname__)}"
        ),
        "visibility_subject": "anonymous",
        "visibility_permission": AccessPermission.EXPORT.value,
        "delivery_identities": [identity.to_dict() for identity in identities],
    }
    return _value_digest(payload)


def _cacheable_inspection(result: PublicProjectionInspection) -> bool:
    return bool(
        result.ok
        and result.complete
        and result.read_only
        and result.privacy.get("status") == "pass"
        and not result.diagnostics
    )


def _projected_meta(
    meta: Mapping[str, Any], operation: PublicProjectionOperation
) -> dict[str, Any]:
    projected = dict(meta)
    if operation == PublicProjectionOperation.PUBLISH:
        projected.pop("draft", None)
        projected.pop("archived_at", None)
        projected["visibility"] = "public"
    elif operation == PublicProjectionOperation.UNPUBLISH:
        projected["draft"] = True
        projected["visibility"] = "draft"
        projected.pop("published_at", None)
        projected.pop("archived_at", None)
    else:
        projected.pop("draft", None)
        projected.pop("published_at", None)
        projected["visibility"] = "archived"
    return projected


def _catalog_memory_fingerprint(catalog: Any) -> str:
    values = [_cache_value(node) for node in catalog.nodes]
    return _value_digest(values)


def _cache_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _cache_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _cache_value(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_cache_value(item) for item in value), key=repr)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_cache_value(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_thaw_json(item) for item in value]
    return value


def _find_record(values: Sequence[Any], node_id: str) -> object | None:
    return next(
        (
            value
            for value in values
            if isinstance(value, Mapping) and value.get("node_id") == node_id
        ),
        None,
    )


def _records_for_node(values: object, node_id: str) -> list[object]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [
        value for value in values if isinstance(value, Mapping) and value.get("node_id") == node_id
    ]


def _value_digest(value: object) -> str:
    return _digest_bytes(canonical_json_bytes(value))


def _digest_text(value: str) -> str:
    return _digest_bytes(value.encode("utf-8"))


def _digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _digest(value: object, label: str) -> str:
    text = _required(value, label).lower()
    if len(text) != 71 or not text.startswith("sha256:"):
        raise ValueError(f"{label} must be a sha256 digest")
    try:
        int(text.removeprefix("sha256:"), 16)
    except ValueError as error:
        raise ValueError(f"{label} must be a sha256 digest") from error
    return text


def _required(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} must be non-empty")
    return text


def _safe_error(error: Exception) -> str:
    return " ".join(str(error).split())[:300] or type(error).__name__
