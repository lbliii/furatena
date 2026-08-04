"""Incremental cross-shard link and sharded discovery contracts."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog
from furatena.catalog.link_reconciliation import (
    IncrementalLinkIndex,
    LinkNode,
    OutboundLink,
    ShardLinkSnapshot,
    remote_shard_link_snapshot,
)
from furatena.catalog.registry import CatalogRegistry, MountConfig
from furatena.catalog.shard_discovery import discovery_mount_id, discovery_mount_token
from tests.support import copy_app_theme, write_minimal_docs_yaml


def _snapshot(
    mount: str,
    *,
    fingerprint: str,
    urls: tuple[str, ...],
    links: tuple[tuple[str, str, str], ...] = (),
) -> ShardLinkSnapshot:
    nodes = tuple(
        LinkNode(
            node_id=f"{mount}:latest:{index}",
            mount=mount,
            edition="latest",
            title=f"{mount.title()} {index}",
            url=url,
        )
        for index, url in enumerate(urls)
    )
    by_url = {node.url: node for node in nodes}
    outbound = tuple(
        OutboundLink(
            source_id=by_url[source].node_id,
            source_mount=mount,
            source_edition="latest",
            source_title=by_url[source].title,
            source_url=source,
            target_url=target,
            target_mount=target_mount,
        )
        for source, target, target_mount in links
    )
    return ShardLinkSnapshot(
        key=f"{mount}:latest",
        mount=mount,
        edition="latest",
        fingerprint=fingerprint,
        nodes=nodes,
        outbound=outbound,
    )


def test_one_shard_update_measures_only_its_edge_neighborhood(tmp_path: Path) -> None:
    index = IncrementalLinkIndex(tmp_path / "links" / "v1")
    alpha = _snapshot(
        "alpha",
        fingerprint="alpha-v1",
        urls=("/alpha/a/",),
        links=(("/alpha/a/", "/beta/b/", "beta"),),
    )
    beta = _snapshot("beta", fingerprint="beta-v1", urls=("/beta/b/",))
    gamma_urls = tuple(f"/gamma/{item}/" for item in range(200))
    gamma = _snapshot(
        "gamma",
        fingerprint="gamma-v1",
        urls=gamma_urls,
        links=tuple((url, "/gamma/0/", "gamma") for url in gamma_urls[1:]),
    )
    index.reconcile({item.key: item for item in (alpha, beta, gamma)})

    changed = _snapshot(
        "alpha",
        fingerprint="alpha-v2",
        urls=("/alpha/a/",),
        links=(("/alpha/a/", "/beta/missing/", "beta"),),
    )
    metrics = index.reconcile({changed.key: changed})

    assert metrics["changed_shards"] == 1
    assert metrics["untouched_shards"] == 2
    assert metrics["scanned_source_nodes"] == 2
    assert metrics["neighborhood_edges"] == 2
    assert metrics["neighborhood_edges"] < index.status()["edge_count"]
    health = index.mount_health("alpha")
    assert health["status"] == "degraded"
    assert health["broken_cross_shard_links"] == [
        {
            "source": "/alpha/a/",
            "target": "/beta/missing/",
            "target_mount": "beta",
            "edition": "latest",
        }
    ]


def test_persistent_index_and_concurrent_shards_remain_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "links" / "v1"
    index = IncrementalLinkIndex(path)
    snapshots = tuple(
        _snapshot(
            mount,
            fingerprint=f"{mount}-v1",
            urls=(f"/{mount}/",),
        )
        for mount in ("alpha", "beta", "gamma")
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda snapshot: index.reconcile({snapshot.key: snapshot}), snapshots))

    payloads = [
        json.loads(item.read_text(encoding="utf-8"))
        for item in sorted((path / "shards").glob("*.json"))
    ]
    assert sorted(item["key"] for item in payloads) == [
        "alpha:latest",
        "beta:latest",
        "gamma:latest",
    ]
    restored = IncrementalLinkIndex(path)
    assert restored.status()["shard_count"] == 3
    assert restored.status()["load_error"] is None


def test_stored_shards_select_colon_mount_identity_exactly(tmp_path: Path) -> None:
    index = IncrementalLinkIndex(tmp_path / "links" / "v1")
    colon_mount = _snapshot("legacy:docs", fingerprint="colon-v1", urls=("/legacy-docs/",))
    prefix_mount = _snapshot("legacy", fingerprint="prefix-v1", urls=("/legacy/",))
    index.reconcile({item.key: item for item in (colon_mount, prefix_mount)})

    assert index.shard_keys_for_mount("legacy:docs") == frozenset({colon_mount.key})
    assert index.shard_keys_for_mount("legacy") == frozenset({prefix_mount.key})

    registry = object.__new__(CatalogRegistry)
    registry._link_index = index
    registry._shards = {}
    registry.remote_shards = None
    registry._reconcile_link_shards(mount_ids=frozenset({"legacy:docs"}))

    assert index.shard_keys() == frozenset({prefix_mount.key})


def _rewrite_record_integrity(payload: dict[str, object]) -> None:
    authenticated = {key: value for key, value in payload.items() if key != "integrity_sha256"}
    encoded = json.dumps(authenticated, sort_keys=True, separators=(",", ":")).encode()
    payload["integrity_sha256"] = hashlib.sha256(encoded).hexdigest()


def test_persistent_record_tamper_is_rejected_and_repaired(tmp_path: Path) -> None:
    path = tmp_path / "links" / "v1"
    snapshot = _snapshot("alpha", fingerprint="verified", urls=("/alpha/",))
    IncrementalLinkIndex(path).reconcile({snapshot.key: snapshot})
    record = next((path / "shards").glob("*.json"))
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["shard"]["fingerprint"] = "valid-looking-tamper"
    record.write_text(json.dumps(payload), encoding="utf-8")

    corrupted = IncrementalLinkIndex(path)
    assert corrupted.fingerprint_for(snapshot.key) is None
    assert corrupted.shard_keys() == frozenset()
    assert "integrity check failed" in corrupted.status()["load_error"]

    corrupted.reconcile({})
    assert "integrity check failed" in corrupted.status()["load_error"]

    corrupted.reconcile({snapshot.key: snapshot})
    repaired = IncrementalLinkIndex(path)
    assert repaired.fingerprint_for(snapshot.key) == "verified"
    assert repaired.status()["load_error"] is None


def test_persistent_record_rejects_boolean_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "links" / "v1"
    snapshot = _snapshot("alpha", fingerprint="verified", urls=("/alpha/",))
    IncrementalLinkIndex(path).reconcile({snapshot.key: snapshot})
    record = next((path / "shards").glob("*.json"))
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["schema_version"] = True
    _rewrite_record_integrity(payload)
    record.write_text(json.dumps(payload), encoding="utf-8")

    rejected = IncrementalLinkIndex(path)
    assert rejected.shard_keys() == frozenset()
    assert "unsupported link-index schema version" in rejected.status()["load_error"]


def test_discovery_tokens_preserve_safe_ids_and_canonicalize_legacy_ids() -> None:
    class Catalog:
        mounts = (
            type("Mount", (), {"id": "safe-id"})(),
            type("Mount", (), {"id": "legacy/id"})(),
        )

    safe = discovery_mount_token("safe-id")
    legacy = discovery_mount_token("legacy/id")

    assert safe == "safe-id"
    assert "/" not in legacy
    assert discovery_mount_id(Catalog(), safe) == "safe-id"
    assert discovery_mount_id(Catalog(), legacy) == "legacy/id"
    assert discovery_mount_id(Catalog(), legacy + "=") is None
    assert discovery_mount_id(Catalog(), "~" + "A" * 1_000_000) is None


def test_source_health_reports_broken_cross_shard_links_per_mount(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "_index.md").write_text(
        "---\ntitle: Alpha\n---\n# Alpha\n\n[Missing beta](/beta/missing/)\n",
        encoding="utf-8",
    )
    (beta / "_index.md").write_text(
        "---\ntitle: Beta\n---\n# Beta\n",
        encoding="utf-8",
    )
    registry = CatalogRegistry(
        (
            MountConfig("alpha", "Alpha", alpha, url_prefix="/alpha/", default=True),
            MountConfig("beta", "Beta", beta, url_prefix="/beta/"),
        ),
        repo_root=tmp_path,
        app_root=tmp_path,
        autodoc=False,
    )

    health = registry.source_health()
    by_mount = {item["id"]: item for item in health["mounts"]}
    assert health["ok"] is False
    assert by_mount["alpha"]["status"] == "degraded"
    assert by_mount["alpha"]["link_integrity"]["broken_cross_shard_count"] == 1
    assert by_mount["beta"]["link_integrity"]["status"] == "healthy"
    assert health["link_reconciliation"]["persistence"] == "atomic-sharded-json"


def test_remote_snapshot_reads_immutable_dcp_sequences() -> None:
    catalog = MappingProxyType(
        {
            "pages": (
                MappingProxyType(
                    {
                        "node_id": "alpha:latest:guide",
                        "title": "Guide",
                        "url": "/alpha/guide/",
                        "content": MappingProxyType(
                            {
                                "links": (
                                    MappingProxyType(
                                        {
                                            "href": "/beta/api/",
                                            "text": "API",
                                            "line": 7,
                                        }
                                    ),
                                )
                            }
                        ),
                    }
                ),
            )
        }
    )

    snapshot = remote_shard_link_snapshot(
        catalog,
        mount="alpha",
        edition="latest",
        fingerprint="verified",
        target_mount_for_url=lambda _url: "beta",
    )

    assert snapshot.outbound[0].target_url == "/beta/api/"
    assert snapshot.outbound[0].line == 7


def test_on_demand_remote_backlinks_publish_to_future_read_generations(tmp_path: Path) -> None:
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "_index.md").write_text("---\ntitle: Alpha\n---\n# Alpha\n", encoding="utf-8")
    (beta / "_index.md").write_text("---\ntitle: Beta\n---\n# Beta\n", encoding="utf-8")
    registry = CatalogRegistry(
        (
            MountConfig("alpha", "Alpha", alpha, url_prefix="/alpha/", default=True),
            MountConfig("beta", "Beta", beta, url_prefix="/beta/"),
        ),
        repo_root=tmp_path,
        app_root=tmp_path,
        autodoc=False,
    )
    target = registry.get("/beta/")
    assert target is not None
    remote = SimpleNamespace(
        identity="remote:latest",
        mount="remote",
        edition="latest",
        fingerprint="remote-v1",
        catalog={
            "pages": [
                {
                    "node_id": "remote:latest:index",
                    "title": "Remote",
                    "url": "/remote/",
                    "content": {"links": [{"href": "/beta/", "text": "Beta", "line": 4}]},
                }
            ]
        },
    )

    registry._reconcile_remote_link_snapshot(remote)

    with registry.read_snapshot():
        assert registry.backlinks_for(target) == [{"title": "Remote", "href": "/remote/"}]


def test_pending_remote_link_status_has_synchronized_ownership(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    (content / "_index.md").write_text("---\ntitle: Home\n---\n# Home\n", encoding="utf-8")
    registry = CatalogRegistry(
        (MountConfig("docs", "Docs", content, default=True),),
        repo_root=tmp_path,
        app_root=tmp_path,
        autodoc=False,
    )

    def mutate(offset: int) -> None:
        for index in range(500):
            identity = f"remote:{offset + index}"
            registry._set_pending_remote_link_shard(identity, "docs")
            registry._set_pending_remote_link_shard(identity, None)

    def read() -> None:
        for _ in range(100):
            registry.source_health()

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(mutate, worker * 1_000) for worker in range(3)]
        futures.extend(pool.submit(read) for _ in range(3))
        for future in futures:
            future.result()

    assert registry.source_health()["link_reconciliation"]["pending_remote_shards"] == []


@pytest.mark.parametrize("full_rebuild", [False, True])
def test_freeze_prunes_discovery_artifacts_when_mount_becomes_protected(
    tmp_path: Path, *, full_rebuild: bool
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    app_root = tmp_path / "app"
    app_root.mkdir()
    copy_app_theme(app_root, repo_root / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    for mount in ("alpha", "beta"):
        content = tmp_path / mount
        content.mkdir()
        (content / "_index.md").write_text(
            f"---\ntitle: {mount.title()} Secret\n---\n# {mount}\n",
            encoding="utf-8",
        )

    def write_mounts(*, protect_beta: bool) -> None:
        access = "\n    access:\n      teams: [secret]" if protect_beta else ""
        (app_root / "mounts.yaml").write_text(
            "mounts:\n"
            "  - id: alpha\n"
            "    label: Alpha\n"
            "    content_root: ../alpha\n"
            "    url_prefix: /alpha/\n"
            "    default: true\n"
            "  - id: beta\n"
            "    label: Beta\n"
            "    content_root: ../beta\n"
            f"    url_prefix: /beta/{access}\n",
            encoding="utf-8",
        )

    output = tmp_path / "frozen"
    options = FreezeCatalogOptions(
        docs_config=app_root / "docs.yaml",
        app_root=app_root,
        repo_root=tmp_path,
        output_dir=output,
        autodoc=False,
    )
    write_mounts(protect_beta=False)
    freeze_catalog(options)
    assert (output / "llms" / "beta.txt").is_file()
    assert (output / "sitemaps" / "beta.xml").is_file()

    write_mounts(protect_beta=True)
    freeze_catalog(
        FreezeCatalogOptions(
            docs_config=options.docs_config,
            app_root=options.app_root,
            repo_root=options.repo_root,
            output_dir=options.output_dir,
            full_rebuild=full_rebuild,
            autodoc=False,
        )
    )

    assert "/llms/beta.txt" not in (output / "llms.txt").read_text(encoding="utf-8")
    assert "/sitemaps/beta.xml" not in (output / "sitemap.xml").read_text(encoding="utf-8")
    assert not (output / "llms" / "beta.txt").exists()
    assert not (output / "sitemaps" / "beta.xml").exists()
