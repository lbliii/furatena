"""Revision-bound public projection and transport parity contracts."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock

import pytest

from furatena.catalog.author_store import source_revision
from furatena.catalog.author_truth import (
    ArtifactTruthState,
    AuthorFreshness,
    AuthorPlaneKey,
    AuthorPublicationTruth,
    AuthorTruthPlane,
    DeploymentTruthState,
    RepositoryTruthState,
)
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.public_projection import (
    PUBLIC_PROJECTION_SURFACES,
    PublicProjectionChange,
    PublicProjectionInspectionCache,
    PublicProjectionInspector,
    PublicProjectionPlan,
    PublicProjectionSurfaceId,
    PublicProjectionTransportAdapter,
    _SurfaceBuild,
    _SurfaceSnapshot,
)
from furatena.catalog.runtime import ServeConfig, ServeMode
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]


def _digest(label: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"


def _catalog(tmp_path: Path) -> tuple[DocsApp, object, Path]:
    app_root = tmp_path / "app"
    copy_app_theme(app_root, REPO / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    content = app_root / "content"
    docs = content / "docs"
    docs.mkdir(parents=True)
    (docs / "public.md").write_text(
        "---\ntitle: Existing public guide\nvisibility: public\n---\n"
        "# Existing public guide\n\nSafe public material.\n",
        encoding="utf-8",
    )
    draft = docs / "planned.md"
    draft.write_text(
        "---\ntitle: Planned public guide\nvisibility: draft\n---\n"
        "# Planned public guide\n\nProposed public material.\n",
        encoding="utf-8",
    )
    (docs / "protected.md").write_text(
        "---\ntitle: Protected launch memorandum\nvisibility: private\n---\n"
        "# Protected launch memorandum\n\nCANARY-PROTECTED-TEAM-ALPHA-395.\n",
        encoding="utf-8",
    )
    write_mounts_yaml(app_root / "mounts.yaml", content)
    docs_app = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        workers=1,
    )
    target = docs_app.catalog.get_by_slug("docs/planned")
    assert target is not None
    return docs_app, target, draft


def _plan(
    node: object,
    *,
    operation: str = "publish",
    revision: str | None = None,
) -> PublicProjectionPlan:
    resulting = {"publish": "public", "unpublish": "draft", "archive": "archived"}[operation]
    return PublicProjectionPlan.create(
        source_revision=revision or _digest("source"),
        change=f"visibility -> {resulting}",
        node_id=node.node_id,
        operation=operation,
        previous_visibility=node.meta["visibility"],
        resulting_visibility=resulting,
    )


def _node_revision(docs_app: DocsApp, node: object) -> str:
    mount = next(item for item in docs_app.catalog.mounts if item.id == node.mount)
    return source_revision((mount.content_root / node.source_path).read_text(encoding="utf-8"))


def _publication_truth() -> AuthorPublicationTruth:
    return AuthorPublicationTruth(
        repository=AuthorTruthPlane(
            key=AuthorPlaneKey.REPOSITORY,
            label="Repository",
            state=RepositoryTruthState.COMMITTED,
            freshness=AuthorFreshness.CURRENT,
            summary="Exact source commit is available.",
            identity_label="Commit",
            identity="a" * 40,
        ),
        artifact=AuthorTruthPlane(
            key=AuthorPlaneKey.ARTIFACT,
            label="Artifact",
            state=ArtifactTruthState.BUILT,
            freshness=AuthorFreshness.STALE,
            summary="Frozen artifact predates the plan.",
            identity_label="Artifact",
            identity="artifact-previous",
        ),
        deployment=AuthorTruthPlane(
            key=AuthorPlaneKey.DEPLOYMENT,
            label="Deployment",
            state=DeploymentTruthState.HEALTHY,
            freshness=AuthorFreshness.STALE,
            summary="Production still serves the previous artifact.",
            identity_label="Deployment",
            identity="deployment-previous",
        ),
    )


def _projection_runtime_state(catalog: object) -> object:
    shards = tuple(catalog._shards.values())
    return {
        "registry_mutable_owners": (
            id(catalog._edition_context),
            id(catalog._edition_shards_lock),
            id(catalog._query_graph_lock),
            id(catalog._generation_lock),
        ),
        "registry_caches": (
            repr(catalog._html_cache),
            repr(catalog._edition_shards),
            repr(catalog._query_graph_cache),
            repr(catalog._translation_index),
            repr(catalog._federated_backlinks),
        ),
        "shards": tuple(
            (
                id(shard._renderer),
                id(shard._renderer._reference_catalog),
                id(shard._renderer._inventory_store),
                repr(shard._html_cache),
                repr(shard._nav_cache),
                tuple(sorted((str(path), mtime) for path, mtime in shard._source_mtimes.items())),
            )
            for shard in shards
        ),
    }


def _counting_builder(
    calls: list[str],
    lock: Lock,
    *,
    privacy_status: str = "pass",
):
    def build(catalog, target, _config, _docs_app, _output_mutator):
        with lock:
            calls.append(target.meta["visibility"])
        time.sleep(0.02)
        present = target.meta["visibility"] == "public"
        snapshots = {
            surface_id: _SurfaceSnapshot(
                route=f"/{surface_id.value}",
                target={"node_id": target.node_id, "surface": surface_id.value}
                if present
                else None,
                complete_payload={
                    "catalog_size": len(catalog.nodes),
                    "surface": surface_id.value,
                },
            )
            for surface_id in PUBLIC_PROJECTION_SURFACES
        }
        return _SurfaceBuild(
            snapshots,
            {
                "status": privacy_status,
                "protected_node_count": 0,
                "canary_count": 0,
                "matches": [],
            },
        )

    return build


def test_draft_publish_enumerates_every_surface_without_mutation_or_canary_leak(
    tmp_path: Path,
) -> None:
    docs_app, node, source = _catalog(tmp_path)
    catalog = docs_app.catalog
    revision = _node_revision(docs_app, node)
    source_before = source.read_bytes()
    catalog_before = tuple((item.node_id, dict(item.meta)) for item in catalog.nodes)
    runtime_before = _projection_runtime_state(catalog)

    result = PublicProjectionInspector(
        catalog,
        config=docs_app.config,
        docs_app=docs_app,
    ).inspect(
        node,
        _plan(node, revision=revision),
        current_source_revision=revision,
        publication=_publication_truth(),
    )
    payload = result.to_dict()

    assert result.ok is True
    assert result.complete is True
    assert result.read_only is True
    assert {surface.surface_id for surface in result.surfaces} == set(PUBLIC_PROJECTION_SURFACES)
    changes = {surface.surface_id: surface.change for surface in result.surfaces}
    assert changes[PublicProjectionSurfaceId.HTML] == PublicProjectionChange.ADDED
    assert changes[PublicProjectionSurfaceId.ROUTE] == PublicProjectionChange.ADDED
    assert changes[PublicProjectionSurfaceId.SEARCH] == PublicProjectionChange.ADDED
    assert changes[PublicProjectionSurfaceId.STATIC] == PublicProjectionChange.ADDED
    assert changes[PublicProjectionSurfaceId.PDF] == PublicProjectionChange.ADDED
    assert result.privacy["status"] == "pass"
    assert result.privacy["protected_node_count"] == 1
    assert result.privacy["matches"] == ()
    assert payload["identities"]["source"]["identity"] == revision
    assert payload["identities"]["frozen_artifact"]["identity"] == "artifact-previous"
    assert payload["identities"]["frozen_artifact"]["drift"] is True
    assert payload["identities"]["deployed_artifact"]["identity"] == ("deployment-previous")
    assert source.read_bytes() == source_before
    assert tuple((item.node_id, dict(item.meta)) for item in catalog.nodes) == catalog_before
    assert _projection_runtime_state(catalog) == runtime_before
    assert node.meta["visibility"] == "draft"


@pytest.mark.parametrize("operation", ["unpublish", "archive"])
def test_public_removal_is_exact_on_every_supported_surface(tmp_path: Path, operation: str) -> None:
    docs_app, draft, _source = _catalog(tmp_path)
    catalog = docs_app.catalog
    public = catalog.get_by_slug("docs/public")
    assert public is not None
    revision = _node_revision(docs_app, public)

    result = PublicProjectionInspector(
        catalog,
        config=docs_app.config,
        docs_app=docs_app,
    ).inspect(
        public,
        _plan(public, operation=operation, revision=revision),
        current_source_revision=revision,
    )

    assert result.ok is True
    assert len(result.surfaces) == len(PUBLIC_PROJECTION_SURFACES)
    changes = {surface.surface_id: surface.change for surface in result.surfaces}
    assert changes[PublicProjectionSurfaceId.HTML] == PublicProjectionChange.REMOVED
    assert changes[PublicProjectionSurfaceId.ROUTE] == PublicProjectionChange.REMOVED
    assert changes[PublicProjectionSurfaceId.SEARCH] == PublicProjectionChange.REMOVED
    assert changes[PublicProjectionSurfaceId.STATIC] == PublicProjectionChange.REMOVED
    assert changes[PublicProjectionSurfaceId.PDF] == PublicProjectionChange.REMOVED
    assert draft.meta["visibility"] == "draft"


@pytest.mark.parametrize(
    ("revision", "node_id", "visibility", "rule_id"),
    [
        (_digest("new-source"), None, None, "fura.public_projection.source_stale"),
        (None, "other:latest:docs/planned", None, "fura.public_projection.node_mismatch"),
        (None, None, "public", "fura.public_projection.lifecycle_stale"),
    ],
)
def test_stale_or_cross_node_plan_fails_before_rendering(
    tmp_path: Path,
    revision: str | None,
    node_id: str | None,
    visibility: str | None,
    rule_id: str,
) -> None:
    docs_app, node, _source = _catalog(tmp_path)
    catalog = docs_app.catalog
    plan = _plan(node)
    if node_id is not None or visibility is not None:
        plan = PublicProjectionPlan(
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            source_revision=plan.source_revision,
            change_digest=plan.change_digest,
            node_id=node_id or plan.node_id,
            operation=plan.operation,
            previous_visibility=visibility or plan.previous_visibility,
            resulting_visibility=plan.resulting_visibility,
        )

    result = PublicProjectionInspector(catalog).inspect(
        node,
        plan,
        current_source_revision=revision or _digest("source"),
    )

    assert result.ok is False
    assert result.complete is False
    assert result.surfaces == ()
    assert rule_id in {item["rule_id"] for item in result.diagnostics}


def test_production_runtime_rejects_catalog_not_built_from_exact_source(
    tmp_path: Path,
) -> None:
    docs_app, node, _source = _catalog(tmp_path)
    stale_revision = _digest("not-the-node-source")

    result = PublicProjectionInspector(
        docs_app.catalog,
        config=docs_app.config,
        docs_app=docs_app,
    ).inspect(
        node,
        _plan(node, revision=stale_revision),
        current_source_revision=stale_revision,
    )

    assert result.ok is False
    assert result.complete is False
    assert result.surfaces == ()
    assert {item["rule_id"] for item in result.diagnostics} == {
        "fura.public_projection.catalog_stale"
    }


def test_builder_fault_cannot_report_partial_success(tmp_path: Path) -> None:
    docs_app, node, _source = _catalog(tmp_path)
    catalog = docs_app.catalog

    def fail_after_partial(_catalog, _node, _config, _docs_app, _output_mutator):
        raise RuntimeError("injected static export failure")

    result = PublicProjectionInspector(catalog, surface_builder=fail_after_partial).inspect(
        node,
        _plan(node),
        current_source_revision=_digest("source"),
    )

    assert result.ok is False
    assert result.complete is False
    assert result.surfaces == ()
    assert result.diagnostics[0]["rule_id"] == "fura.public_projection.incomplete"


def test_browser_cli_mcp_and_automation_share_identical_golden_result(
    tmp_path: Path,
) -> None:
    docs_app, node, _source = _catalog(tmp_path)
    catalog = docs_app.catalog
    inspector = PublicProjectionInspector(
        catalog,
        config=docs_app.config,
        docs_app=docs_app,
    )
    revision = _node_revision(docs_app, node)
    plan = _plan(node, revision=revision)

    durations: list[float] = []
    payloads = []
    for transport in ("browser", "cli", "mcp", "automation"):
        started = time.perf_counter()
        payloads.append(
            PublicProjectionTransportAdapter(transport, inspector).inspect(
                node,
                plan,
                current_source_revision=revision,
            )
        )
        durations.append(time.perf_counter() - started)

    assert payloads[1:] == payloads[:-1]
    assert max(durations[1:]) < durations[0]
    assert payloads[0]["ok"] is True
    assert {item["id"] for item in payloads[0]["surfaces"]} == {
        surface.value for surface in PUBLIC_PROJECTION_SURFACES
    }
    html = next(
        item
        for item in payloads[0]["surfaces"]
        if item["id"] == PublicProjectionSurfaceId.HTML.value
    )
    assert html["preview"]["media_type"] == "text/html"
    assert "Proposed public material" in html["preview"]["html"]


def test_real_output_corruption_is_detected_by_visibility_audit(tmp_path: Path) -> None:
    docs_app, node, _source = _catalog(tmp_path)

    def inject_canary(output_dir: Path) -> None:
        llms = output_dir / "llms.txt"
        llms.write_text(
            llms.read_text(encoding="utf-8") + "\nCANARY-PROTECTED-TEAM-ALPHA-395.\n",
            encoding="utf-8",
        )

    result = PublicProjectionInspector(
        docs_app.catalog,
        config=docs_app.config,
        docs_app=docs_app,
        output_mutator=inject_canary,
    ).inspect(
        node,
        _plan(node, revision=_node_revision(docs_app, node)),
        current_source_revision=_node_revision(docs_app, node),
    )

    assert result.ok is False
    assert result.complete is True
    assert result.privacy["status"] == "fail"
    assert any(match["artifact"] == "llms.txt" for match in result.privacy["matches"])
    assert result.diagnostics[0]["rule_id"] == "fura.public_projection.privacy_canary"


def test_cache_coalesces_concurrent_same_key_and_returns_immutable_payloads(
    tmp_path: Path,
) -> None:
    docs_app, node, _source = _catalog(tmp_path)
    calls: list[str] = []
    call_lock = Lock()
    cache = PublicProjectionInspectionCache(max_entries=4, max_bytes=1_000_000)
    inspector = PublicProjectionInspector(
        docs_app.catalog,
        surface_builder=_counting_builder(calls, call_lock),
        cache=cache,
    )
    plan = _plan(node)

    def inspect_once(_index: int):
        return inspector.inspect(
            node,
            plan,
            current_source_revision=_digest("source"),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(inspect_once, range(8)))

    assert len(calls) == 2
    assert all(result is results[0] for result in results)
    assert cache.entry_count == 1
    payload = results[0].to_dict()
    payload["surfaces"][0]["preview"]["surface"] = "mutated-caller-copy"
    assert results[0].to_dict()["surfaces"][0]["preview"]["surface"] != ("mutated-caller-copy")


def test_cache_key_binds_delivery_identity_and_explicit_invalidation(tmp_path: Path) -> None:
    docs_app, node, _source = _catalog(tmp_path)
    calls: list[str] = []
    cache = PublicProjectionInspectionCache(max_entries=4, max_bytes=1_000_000)
    inspector = PublicProjectionInspector(
        docs_app.catalog,
        surface_builder=_counting_builder(calls, Lock()),
        cache=cache,
    )
    plan = _plan(node)
    first_truth = _publication_truth()
    second_truth = replace(
        first_truth,
        artifact=replace(first_truth.artifact, identity="artifact-newer"),
    )

    for truth in (first_truth, first_truth, second_truth):
        result = inspector.inspect(
            node,
            plan,
            current_source_revision=_digest("source"),
            publication=truth,
        )
        assert result.ok is True

    assert len(calls) == 4
    assert cache.entry_count == 2
    cache.invalidate()
    assert cache.entry_count == 0
    assert cache.byte_count == 0
    inspector.inspect(
        node,
        plan,
        current_source_revision=_digest("source"),
        publication=first_truth,
    )
    assert len(calls) == 6


def test_post_invalidation_caller_never_joins_old_epoch_flight(tmp_path: Path) -> None:
    docs_app, node, _source = _catalog(tmp_path)
    cache = PublicProjectionInspectionCache(max_entries=4, max_bytes=1_000_000)
    started = Event()
    release = Event()
    lock = Lock()
    calls = 0

    def blocking_builder(catalog, target, _config, _docs_app, _output_mutator):
        nonlocal calls
        with lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            started.set()
            assert release.wait(timeout=5)
        present = target.meta["visibility"] == "public"
        return _SurfaceBuild(
            {
                surface_id: _SurfaceSnapshot(
                    route=f"/{surface_id.value}",
                    target={
                        "node_id": target.node_id,
                        "surface": surface_id.value,
                        "build_call": call_number,
                    }
                    if present
                    else None,
                    complete_payload={"catalog_size": len(catalog.nodes)},
                )
                for surface_id in PUBLIC_PROJECTION_SURFACES
            },
            {"status": "pass", "canary_count": 0, "matches": []},
        )

    inspector = PublicProjectionInspector(
        docs_app.catalog,
        surface_builder=blocking_builder,
        cache=cache,
    )
    plan = _plan(node)

    with ThreadPoolExecutor(max_workers=2) as executor:
        old_future = executor.submit(
            inspector.inspect,
            node,
            plan,
            current_source_revision=_digest("source"),
        )
        assert started.wait(timeout=5)
        cache.invalidate()
        new_future = executor.submit(
            inspector.inspect,
            node,
            plan,
            current_source_revision=_digest("source"),
        )
        release.set()
        old_result = old_future.result(timeout=10)
        new_result = new_future.result(timeout=10)

    assert calls == 4
    assert new_result is not old_result
    assert cache.entry_count == 1
    html = next(
        surface
        for surface in new_result.surfaces
        if surface.surface_id == PublicProjectionSurfaceId.HTML
    )
    assert html.preview["build_call"] == 4


def test_cache_enforces_lru_bounds_and_rejects_failed_results(tmp_path: Path) -> None:
    docs_app, node, _source = _catalog(tmp_path)
    cache = PublicProjectionInspectionCache(max_entries=2, max_bytes=1_000_000)
    calls: list[str] = []
    inspector = PublicProjectionInspector(
        docs_app.catalog,
        surface_builder=_counting_builder(calls, Lock()),
        cache=cache,
    )
    for suffix in ("one", "two", "three"):
        plan = PublicProjectionPlan.create(
            source_revision=_digest("source"),
            change=f"visibility -> public ({suffix})",
            node_id=node.node_id,
            operation="publish",
            previous_visibility="draft",
            resulting_visibility="public",
        )
        assert inspector.inspect(
            node,
            plan,
            current_source_revision=_digest("source"),
        ).ok
    assert cache.entry_count == 2
    assert 0 < cache.byte_count <= cache.max_bytes

    failed_calls: list[str] = []
    failed_cache = PublicProjectionInspectionCache()
    failed = PublicProjectionInspector(
        docs_app.catalog,
        surface_builder=_counting_builder(
            failed_calls,
            Lock(),
            privacy_status="fail",
        ),
        cache=failed_cache,
    )
    for _index in range(2):
        assert not failed.inspect(
            node,
            _plan(node),
            current_source_revision=_digest("source"),
        ).ok
    assert len(failed_calls) == 4
    assert failed_cache.entry_count == 0
