"""Compose-by-reference remote shard mount contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from furatena.catalog.federation_artifacts import hub_payload_digest
from furatena.catalog.federation_publish import (
    PublishShardOptions,
    build_published_shard,
    hub_entry,
)
from furatena.catalog.remote_shards import (
    RemoteHTTPResponse,
    RemoteShardFetchError,
    RemoteShardMountRegistry,
    RemoteShardVerificationError,
    StrictHTTPSFetcher,
)

ORIGIN = "https://hub.example"
HUB_URL = f"{ORIGIN}/federation/hub.json"


class MemoryTransport:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.calls: list[str] = []
        self.final_urls: dict[str, str] = {}
        self.content_lengths: dict[str, int] = {}
        self.blockers: dict[str, tuple[threading.Event, threading.Event]] = {}
        self._lock = threading.Lock()

    def fetch(self, url: str, *, max_bytes: int, timeout_seconds: float) -> RemoteHTTPResponse:
        _ = max_bytes, timeout_seconds
        with self._lock:
            self.calls.append(url)
        blocker = self.blockers.get(url)
        if blocker is not None:
            started, release = blocker
            started.set()
            if not release.wait(timeout=10):
                raise TimeoutError(f"timed out waiting to release {url}")
        value = self.objects[url]
        return RemoteHTTPResponse(
            value,
            self.final_urls.get(url, url),
            self.content_lengths.get(url, len(value)),
        )


class RecordingVerifier:
    def __init__(self) -> None:
        self.hubs = 0
        self.shards: list[str] = []
        self.fail_mounts: set[str] = set()

    def verify_hub(self, manifest: Any, *, fetcher: StrictHTTPSFetcher) -> dict[str, Any]:
        _ = manifest, fetcher
        self.hubs += 1
        return {"verified": True, "authority": "test-hub-root"}

    def verify_shard(self, manifest: Any, *, fetcher: StrictHTTPSFetcher) -> dict[str, Any]:
        _ = fetcher
        mount = str(manifest["identity"]["mount"])
        self.shards.append(str(manifest["identity"]["key"]))
        if mount in self.fail_mounts:
            raise ValueError(f"untrusted publisher for {mount}")
        return {"verified": True, "authority": f"test-publisher:{mount}"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _source(tmp_path: Path, mount: str, marker: str) -> Path:
    source = tmp_path / "frozen" / marker / "mounts" / mount / "latest"
    source.mkdir(parents=True)
    node_id = f"{mount}:latest:guide"
    catalog = {
        "schema_version": 3,
        "channel": "latest",
        "edition": "latest",
        "mount": mount,
        "page_count": 1,
        "pages": [
            {
                "content": {
                    "directives": [],
                    "extensions": [],
                    "headings": [{"anchor": "guide", "level": 1, "line": 1, "text": "Guide"}],
                    "links": [],
                },
                "edition": "latest",
                "mount": mount,
                "node_id": node_id,
                "backlinks": [{"title": f"Backlink {marker}", "href": f"/{mount}/source/"}],
                "sections": [{"depth": 1, "heading": "Guide", "id": "guide", "text": marker}],
                "slug": "guide",
                "title": f"Guide {marker}",
                "url": f"/{mount}/guide/",
            }
        ],
        "edges": [],
        "namespaces": [],
    }
    (source / "catalog.json").write_bytes(_canonical(catalog))
    pages = source / "pages"
    pages.mkdir()
    (pages / "guide.html").write_text(
        f"<article><h1>Guide</h1><p>{marker}</p></article>\n", encoding="utf-8"
    )
    source_fingerprint = hashlib.sha256(f"{mount}:{marker}".encode()).hexdigest()
    (source / "fingerprint.json").write_text(
        json.dumps(
            {
                "mount": mount,
                "edition": "latest",
                "fingerprint": source_fingerprint,
                "contracts": {
                    "dcp": 3,
                    "content_ir": 3,
                    "adapter": 1,
                    "presentation": 1,
                },
                "source": {
                    "provider": "git",
                    "repo": f"https://github.com/example/{mount}.git",
                    "ref": "main",
                    "resolved_ref": hashlib.sha1(marker.encode()).hexdigest(),
                    "path": "docs",
                },
            }
        ),
        encoding="utf-8",
    )
    return source


def _artifact(tmp_path: Path, mount: str, marker: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    artifact, manifest = build_published_shard(
        PublishShardOptions(
            source_shard=_source(tmp_path, mount, marker),
            staging_dir=tmp_path / "published" / marker / mount,
            public_base_url=f"{ORIGIN}/shards",
            verification={
                "signatures": [
                    {
                        "kind": "sigstore-bundle",
                        "url": f"{ORIGIN}/signatures/{mount}-{marker}.json",
                        "sha256": "a" * 64,
                        "issuer": "https://token.actions.githubusercontent.com",
                        "identity": f"https://github.com/example/{mount}/publish@main",
                    }
                ],
                "attestations": [
                    {
                        "predicate_type": "https://slsa.dev/provenance/v1",
                        "url": f"{ORIGIN}/attestations/{mount}-{marker}.json",
                        "sha256": "b" * 64,
                    }
                ],
            },
            lifecycle_status="current",
            retention_days=30,
            pinned_by=(),
        )
    )
    objects = {
        str(manifest["artifact_base_url"]) + "manifest.json": (
            artifact / "manifest.json"
        ).read_bytes()
    }
    objects.update(
        {
            str(manifest["artifact_base_url"]) + str(item["object_url"]): (
                artifact / str(item["object_url"])
            ).read_bytes()
            for item in manifest["inventory"]
        }
    )
    return manifest, objects


def _hub(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    shards: dict[str, Any] = {}
    channels: dict[str, Any] = {}
    for manifest in manifests:
        identity = str(manifest["identity"]["key"])
        manifest_size = len(_canonical(manifest))
        shards[identity] = hub_entry(manifest, manifest_size=manifest_size)
        mount = str(manifest["identity"]["mount"])
        channels[mount] = {"latest": identity, "stable": identity, "editions": [identity]}
    hub = {
        "schema_version": 1,
        "manifest_type": "furatena-federation-hub",
        "generated_at": "2026-08-03T00:00:00Z",
        "contracts": {
            "artifact": 1,
            "dcp_reader_min": 2,
            "dcp_reader_max": 3,
            "content_ir_reader_min": 3,
            "content_ir_reader_max": 3,
        },
        "shards": dict(sorted(shards.items())),
        "channels": dict(sorted(channels.items())),
        "discovery": {
            "channels_field": "remote_federation",
            "hub_manifest_url": HUB_URL,
            "refresh_after_seconds": 300,
            "max_manifest_bytes": 4194304,
            "max_shards": 10000,
            "failure_mode": "last-known-good-or-unavailable",
        },
        "integrity": {"algorithm": "sha256", "payload_sha256": "0" * 64},
        "signatures": [
            {
                "kind": "sigstore-bundle",
                "url": f"{ORIGIN}/signatures/hub.json",
                "sha256": "d" * 64,
                "subject_sha256": "0" * 64,
                "issuer": "https://token.actions.githubusercontent.com",
                "identity": "https://github.com/example/hub/publish@main",
            }
        ],
    }
    digest = hub_payload_digest(hub)
    hub["integrity"]["payload_sha256"] = digest
    hub["signatures"][0]["subject_sha256"] = digest
    return hub


def _registry(
    tmp_path: Path, transport: MemoryTransport, verifier: RecordingVerifier
) -> RemoteShardMountRegistry:
    return RemoteShardMountRegistry(
        hub_url=HUB_URL,
        state_root=tmp_path / "remote-state",
        fetcher=StrictHTTPSFetcher(HUB_URL, transport=transport),
        verifier=verifier,
    )


def _install_hub(transport: MemoryTransport, hub: dict[str, Any]) -> None:
    transport.objects[HUB_URL] = _canonical(hub)


def _presentation_cache_stats(catalog: Any) -> dict[str, int]:
    cache = catalog._remote_presentation_cache
    assert cache is not None
    return cache.stats()


def _remote_docs_app(
    tmp_path: Path,
    registry: RemoteShardMountRegistry,
    *mount_ids: str,
    app_name: str,
) -> Any:
    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.runtime import ServeConfig, ServeMode

    app_root = tmp_path / app_name
    app_root.mkdir()
    (app_root / "docs.yaml").write_text(
        """shell: shell.html
views:
  doc: views/doc.html
  doc_list: views/doc_list.html
  api_reference: views/api_reference.html
  home: views/home.html
  default: views/doc.html
theme:
  use: lagoon
  id: furatena
  templates: theme/templates
mounts: mounts.yaml
""",
        encoding="utf-8",
    )
    mounts = []
    for index, mount in enumerate(mount_ids):
        mounts.append(
            f"""  - id: {mount}
    label: {mount.title()}
    content_root: corpus-{mount}-does-not-exist
    url_prefix: /{mount}
    default: {str(index == 0).lower()}
    source:
      provider: remote-shard
"""
        )
    (app_root / "mounts.yaml").write_text(
        "mounts:\n" + "".join(mounts),
        encoding="utf-8",
    )
    return DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        remote_shards=registry,
    )


def _object_url(manifest: dict[str, Any], role: str) -> str:
    item = next(item for item in manifest["inventory"] if item["role"] == role)
    return str(manifest["artifact_base_url"]) + str(item["object_url"])


def test_strict_fetcher_rejects_cross_origin_privileged_and_unbounded_urls() -> None:
    transport = MemoryTransport({f"{ORIGIN}/ok": b"ok"})
    fetcher = StrictHTTPSFetcher(HUB_URL, transport=transport)

    assert fetcher.fetch(f"{ORIGIN}/ok", max_bytes=2) == b"ok"
    for url in (
        "http://hub.example/unsafe",
        "https://other.example/unsafe",
        "https://user:secret@hub.example/unsafe",
        "https://hub.example/unsafe?token=secret",
        "https://hub.example/a/%2e%2e/private",
        "https://hub.example/a\\private",
        "https://hub.example/a\nprivate",
    ):
        with pytest.raises(RemoteShardFetchError, match="same-origin HTTPS"):
            fetcher.fetch(url, max_bytes=32)

    with pytest.raises(RemoteShardFetchError, match="invalid HTTPS port"):
        fetcher.fetch("https://hub.example:invalid/unsafe", max_bytes=32)
    with pytest.raises(ValueError, match="absolute HTTPS URL"):
        StrictHTTPSFetcher("https://user:secret@hub.example/root", transport=transport)

    transport.content_lengths[f"{ORIGIN}/ok"] = 3
    with pytest.raises(RemoteShardFetchError, match="above the 2 byte bound"):
        fetcher.fetch(f"{ORIGIN}/ok", max_bytes=2)
    transport.content_lengths[f"{ORIGIN}/ok"] = 2
    transport.final_urls[f"{ORIGIN}/ok"] = "https://other.example/redirect"
    with pytest.raises(RemoteShardFetchError, match="same-origin HTTPS"):
        fetcher.fetch(f"{ORIGIN}/ok", max_bytes=2)


def test_zero_local_registry_activates_catalog_then_fetches_node_and_semantic_lazily(
    tmp_path: Path,
) -> None:
    manifest, objects = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects)
    hub = _hub([manifest])
    _install_hub(transport, hub)
    verifier = RecordingVerifier()
    registry = _registry(tmp_path, transport, verifier)

    report = registry.refresh()
    generation = registry.generation("alpha")
    fragment_url = next(
        str(manifest["artifact_base_url"]) + str(item["object_url"])
        for item in manifest["inventory"]
        if item["role"] == "fragment"
    )
    presentation_url = next(
        str(manifest["artifact_base_url"]) + str(item["object_url"])
        for item in manifest["inventory"]
        if item["role"] == "presentation"
    )
    semantic_url = next(
        str(manifest["artifact_base_url"]) + str(item["object_url"])
        for item in manifest["inventory"]
        if item["role"] == "semantic"
    )

    assert report.mounts[0].status == "activated"
    assert registry.mounts() == ("alpha",)
    assert generation.shards[generation.latest].catalog["page_count"] == 1
    assert fragment_url not in transport.calls
    assert presentation_url not in transport.calls
    assert semantic_url not in transport.calls
    assert verifier.hubs == 1
    assert verifier.shards == ["alpha:latest"]

    node = registry.fetch_node("alpha", "latest", "alpha:latest:guide")
    semantic = registry.fetch_semantic_index("alpha", "stable")

    assert node.fragment["sections"][0]["text"] == "v1"
    assert b"<p>v1</p>" in node.presentation
    assert semantic["records"][0]["node_id"] == node.node_id
    assert fragment_url in transport.calls
    assert presentation_url in transport.calls
    assert semantic_url in transport.calls
    receipt = (
        tmp_path
        / "remote-state"
        / "mounts"
        / "alpha"
        / "generations"
        / generation.generation_id
        / "receipt.json"
    )
    assert receipt.is_file()
    assert not (tmp_path / "content").exists()

    generation_id = generation.generation_id
    original_presentation = transport.objects[presentation_url]
    transport.objects[presentation_url] = b"x" * len(original_presentation)
    with pytest.raises(RemoteShardFetchError, match="digest differs"):
        registry.fetch_node("alpha", "latest", "alpha:latest:guide")
    assert registry.generation("alpha").generation_id == generation_id
    transport.objects[presentation_url] = original_presentation

    restarted = _registry(tmp_path, MemoryTransport(objects), RecordingVerifier())
    assert restarted.generation("alpha").generation_id == generation.generation_id


def test_tiered_catalog_residency_routes_before_load_and_reports_churn(tmp_path: Path) -> None:
    alpha, alpha_objects = _artifact(tmp_path, "alpha", "v1")
    beta, beta_objects = _artifact(tmp_path, "beta", "v1")
    transport = MemoryTransport(alpha_objects | beta_objects)
    _install_hub(transport, _hub([alpha, beta]))
    registry = RemoteShardMountRegistry(
        hub_url=HUB_URL,
        state_root=tmp_path / "remote-state",
        fetcher=StrictHTTPSFetcher(HUB_URL, transport=transport),
        verifier=RecordingVerifier(),
        resident_shard_entries=1,
        resident_shard_bytes=8 * 1024 * 1024,
    )
    registry.refresh()
    alpha_catalog = _object_url(alpha, "catalog")
    beta_catalog = _object_url(beta, "catalog")

    assert alpha_catalog not in transport.calls
    assert beta_catalog not in transport.calls
    assert registry.shard("alpha").catalog["page_count"] == 1
    first = registry.residency_status()

    assert alpha_catalog in transport.calls
    assert beta_catalog not in transport.calls
    assert {item["identity"]: item["tier"] for item in first["shards"]} == {
        "alpha:latest": "hot",
        "beta:latest": "cold",
    }
    assert first["resident_entries"] == 1
    assert first["resident_bytes"] <= first["max_resident_bytes"]

    assert registry.shard("beta").catalog["page_count"] == 1
    second = registry.residency_status()
    assert second["evictions"] == 1
    assert {item["identity"]: item["tier"] for item in second["shards"]} == {
        "alpha:latest": "warm",
        "beta:latest": "hot",
    }

    transport.calls.clear()
    assert registry.shard("alpha").catalog["page_count"] == 1
    third = registry.residency_status()
    assert alpha_catalog not in transport.calls
    assert third["warm_loads"] == 1
    assert third["evictions"] == 2


def test_catalog_first_load_coalesces_without_blocking_unrelated_shards(tmp_path: Path) -> None:
    alpha, alpha_objects = _artifact(tmp_path, "alpha", "v1")
    beta, beta_objects = _artifact(tmp_path, "beta", "v1")
    transport = MemoryTransport(alpha_objects | beta_objects)
    _install_hub(transport, _hub([alpha, beta]))
    registry = _registry(tmp_path, transport, RecordingVerifier())
    registry.refresh()
    alpha_catalog = _object_url(alpha, "catalog")
    beta_catalog = _object_url(beta, "catalog")
    started = threading.Event()
    release = threading.Event()
    transport.blockers[alpha_catalog] = (started, release)

    with ThreadPoolExecutor(max_workers=3) as pool:
        alpha_owner = pool.submit(lambda: registry.shard("alpha").catalog["page_count"])
        assert started.wait(timeout=10)
        alpha_waiter = pool.submit(lambda: registry.shard("alpha").catalog["page_count"])
        beta_result = pool.submit(lambda: registry.shard("beta").catalog["page_count"])
        assert beta_result.result(timeout=10) == 1
        release.set()
        assert alpha_owner.result(timeout=10) == 1
        assert alpha_waiter.result(timeout=10) == 1

    status = registry.residency_status()
    assert transport.calls.count(alpha_catalog) == 1
    assert transport.calls.count(beta_catalog) == 1
    assert status["coalesced_loads"] == 1
    assert status["in_flight"] == 0


def test_failed_catalog_flight_wakes_waiters_is_removed_and_can_retry(tmp_path: Path) -> None:
    manifest, objects = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects)
    _install_hub(transport, _hub([manifest]))
    registry = _registry(tmp_path, transport, RecordingVerifier())
    registry.refresh()
    catalog_url = _object_url(manifest, "catalog")
    original = transport.objects[catalog_url]
    started = threading.Event()
    release = threading.Event()
    transport.blockers[catalog_url] = (started, release)

    def load() -> int:
        return int(registry.shard("alpha").catalog["page_count"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(load)
        assert started.wait(timeout=10)
        waiter = pool.submit(load)
        for _ in range(1_000):
            if registry.residency_status()["coalesced_loads"] == 1:
                break
            threading.Event().wait(0.001)
        assert registry.residency_status()["coalesced_loads"] == 1
        transport.objects.pop(catalog_url)
        release.set()
        with pytest.raises(RemoteShardFetchError, match="Remote HTTPS fetch failed"):
            owner.result(timeout=10)
        with pytest.raises(RemoteShardFetchError, match="Remote HTTPS fetch failed"):
            waiter.result(timeout=10)

    failed = registry.residency_status()
    assert failed["in_flight"] == 0
    assert failed["load_failures"] == 1
    transport.blockers.pop(catalog_url)
    transport.objects[catalog_url] = original
    assert load() == 1
    assert registry.residency_status()["cold_loads"] == 1


def test_evicted_generation_remains_safe_and_corrupt_warm_catalog_refetches(
    tmp_path: Path,
) -> None:
    from furatena.catalog.registry import CatalogRegistry, MountConfig
    from furatena.catalog.sources.types import MountSourceConfig

    alpha, alpha_objects = _artifact(tmp_path, "alpha", "v1")
    beta, beta_objects = _artifact(tmp_path, "beta", "v1")
    transport = MemoryTransport(alpha_objects | beta_objects)
    _install_hub(transport, _hub([alpha, beta]))
    remote = RemoteShardMountRegistry(
        hub_url=HUB_URL,
        state_root=tmp_path / "remote-state",
        fetcher=StrictHTTPSFetcher(HUB_URL, transport=transport),
        verifier=RecordingVerifier(),
        resident_shard_entries=1,
        resident_shard_bytes=8 * 1024 * 1024,
    )
    remote.refresh()
    mounts = tuple(
        MountConfig(
            id=mount,
            label=mount.title(),
            content_root=tmp_path / f"missing-{mount}",
            url_prefix=f"/{mount}",
            default=mount == "alpha",
            source=MountSourceConfig(provider="remote-shard"),
        )
        for mount in ("alpha", "beta")
    )
    catalog = CatalogRegistry(
        mounts,
        repo_root=tmp_path,
        app_root=tmp_path,
        autodoc=False,
        remote_shards=remote,
        remote_resident_shard_entries=1,
        remote_resident_shard_bytes=8 * 1024 * 1024,
    )
    alpha_shard = catalog._active_shard("alpha")
    assert alpha_shard is not None
    alpha_node = alpha_shard.get("/alpha/guide/")
    assert alpha_node is not None
    assert catalog._active_shard("beta") is not None

    # LRU eviction drops only the cache's reference. A request that already
    # captured the immutable composed generation can safely finish.
    assert alpha_shard.get("/alpha/guide/") is alpha_node

    catalog_item = next(item for item in alpha["inventory"] if item["role"] == "catalog")
    warm = tmp_path / "remote-state" / "object-cache" / "sha256" / f"{catalog_item['sha256']}.json"
    warm.write_bytes(b"corrupt")
    alpha_catalog_url = _object_url(alpha, "catalog")
    before_calls = transport.calls.count(alpha_catalog_url)

    # Loading beta evicted alpha from both bounded tiers. The corrupt warm
    # candidate is rejected and atomically replaced from the immutable origin.
    assert remote.shard("alpha").catalog["page_count"] == 1
    assert transport.calls.count(alpha_catalog_url) == before_calls + 1
    assert hashlib.sha256(warm.read_bytes()).hexdigest() == catalog_item["sha256"]


def test_refresh_during_cold_compose_serves_captured_generation_without_recaching_it(
    tmp_path: Path,
) -> None:
    from furatena.catalog.registry import CatalogRegistry, MountConfig
    from furatena.catalog.sources.types import MountSourceConfig

    first, first_objects = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(first_objects)
    _install_hub(transport, _hub([first]))
    remote = _registry(tmp_path, transport, RecordingVerifier())
    remote.refresh()
    catalog = CatalogRegistry(
        (
            MountConfig(
                id="alpha",
                label="Alpha",
                content_root=tmp_path / "missing-alpha",
                url_prefix="/alpha",
                default=True,
                source=MountSourceConfig(provider="remote-shard"),
            ),
        ),
        repo_root=tmp_path,
        app_root=tmp_path,
        autodoc=False,
        remote_shards=remote,
    )
    first_catalog_url = _object_url(first, "catalog")
    started = threading.Event()
    release = threading.Event()
    transport.blockers[first_catalog_url] = (started, release)

    with ThreadPoolExecutor(max_workers=2) as pool:
        loading = pool.submit(catalog._active_shard, "alpha")
        assert started.wait(timeout=10)
        second, second_objects = _artifact(tmp_path, "alpha", "v2")
        transport.objects.update(second_objects)
        _install_hub(transport, _hub([second]))
        refreshed = pool.submit(catalog.refresh_remote_shards)
        refreshed.result(timeout=10)
        release.set()
        captured = loading.result(timeout=10)

    assert captured is not None
    captured_node = captured.get("/alpha/guide/")
    assert captured_node is not None
    assert captured_node.title == "Guide v1"
    after_old = catalog.remote_residency_status()
    assert after_old["composed"]["resident_entries"] == 0

    current = catalog._active_shard("alpha")
    assert current is not None
    current_node = current.get("/alpha/guide/")
    assert current_node is not None
    assert current_node.title == "Guide v2"
    assert catalog.remote_residency_status()["composed"]["resident_entries"] == 1


def test_refresh_prunes_stale_identity_but_keeps_process_lifetime_counters(tmp_path: Path) -> None:
    first, first_objects = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(first_objects)
    _install_hub(transport, _hub([first]))
    remote = _registry(tmp_path, transport, RecordingVerifier())
    remote.refresh()
    assert remote.shard("alpha").catalog["page_count"] == 1

    second, second_objects = _artifact(tmp_path, "alpha", "v2")
    transport.objects.update(second_objects)
    _install_hub(transport, _hub([second]))
    remote.refresh()
    status = remote.residency_status()

    assert status["cold_loads"] == 1
    assert status["resident_entries"] == 0
    assert status["shards"] == [{"identity": "alpha:latest", "mount": "alpha", "tier": "cold"}]


def test_catalog_registry_materializes_only_the_routed_remote_mount(tmp_path: Path) -> None:
    from furatena.catalog.registry import CatalogRegistry, MountConfig
    from furatena.catalog.sources.types import MountSourceConfig

    alpha, alpha_objects = _artifact(tmp_path, "alpha", "v1")
    beta, beta_objects = _artifact(tmp_path, "beta", "v1")
    transport = MemoryTransport(alpha_objects | beta_objects)
    _install_hub(transport, _hub([alpha, beta]))
    remote = _registry(tmp_path, transport, RecordingVerifier())
    remote.refresh()
    mounts = tuple(
        MountConfig(
            id=mount,
            label=mount.title(),
            content_root=tmp_path / f"missing-{mount}",
            url_prefix=f"/{mount}",
            default=mount == "alpha",
            source=MountSourceConfig(provider="remote-shard"),
        )
        for mount in ("alpha", "beta")
    )
    catalog = CatalogRegistry(
        mounts,
        repo_root=tmp_path,
        app_root=tmp_path,
        autodoc=False,
        remote_shards=remote,
        remote_resident_shard_entries=1,
        remote_resident_shard_bytes=8 * 1024 * 1024,
    )
    alpha_catalog = _object_url(alpha, "catalog")
    beta_catalog = _object_url(beta, "catalog")

    assert alpha_catalog not in transport.calls
    assert beta_catalog not in transport.calls
    node = catalog.get("/alpha/guide/")

    assert node is not None
    assert node.node_id == "alpha:latest:guide"
    assert alpha_catalog in transport.calls
    assert beta_catalog not in transport.calls
    status = catalog.remote_residency_status()
    assert status["composed"]["resident_entries"] == 1
    assert status["composed"]["resident_bytes"] <= status["composed"]["max_resident_bytes"]
    health = catalog.source_health(mount="alpha")
    assert health["mount_count"] == 1
    assert health["mounts"][0]["status"] == "healthy"
    assert {item["mount"] for item in health["remote_residency"]["objects"]["shards"]} == {"alpha"}
    catalog.refresh_remote_shards()
    refreshed = catalog.remote_residency_status()
    assert refreshed["counter_scope"] == "process_lifetime"
    assert refreshed["composed"]["loads"] == 1
    assert refreshed["composed"]["resident_entries"] == 0


def test_broken_publish_is_mount_local_and_previous_generation_can_be_pinned(
    tmp_path: Path,
) -> None:
    alpha_v1, alpha_v1_objects = _artifact(tmp_path, "alpha", "v1")
    beta_v1, beta_v1_objects = _artifact(tmp_path, "beta", "v1")
    transport = MemoryTransport(alpha_v1_objects | beta_v1_objects)
    _install_hub(transport, _hub([alpha_v1, beta_v1]))
    verifier = RecordingVerifier()
    registry = _registry(tmp_path, transport, verifier)
    first = registry.refresh()
    alpha_old = registry.generation("alpha")
    beta_old = registry.generation("beta")
    assert {item.status for item in first.mounts} == {"activated"}

    alpha_v2, alpha_v2_objects = _artifact(tmp_path, "alpha", "v2")
    beta_v2, beta_v2_objects = _artifact(tmp_path, "beta", "v2")
    transport.objects.update(alpha_v2_objects | beta_v2_objects)
    updated_hub = _hub([alpha_v2, beta_v2])
    _install_hub(transport, updated_hub)
    verifier.fail_mounts.add("beta")

    second = registry.refresh()
    outcomes = {item.mount: item for item in second.mounts}

    assert outcomes["alpha"].status == "activated"
    assert outcomes["beta"].status == "failed"
    assert outcomes["beta"].retained_last_known_good is True
    assert registry.generation("alpha").generation_id != alpha_old.generation_id
    assert registry.generation("beta").generation_id == beta_old.generation_id
    assert b"<p>v2</p>" in registry.fetch_node("alpha", "latest", "alpha:latest:guide").presentation
    assert b"<p>v1</p>" in registry.fetch_node("beta", "latest", "beta:latest:guide").presentation

    rolled_back = registry.rollback("alpha", str(alpha_v1["fingerprint"]))

    assert rolled_back.generation_id == alpha_old.generation_id
    assert rolled_back.rollback_pin == alpha_v1["fingerprint"]
    assert b"<p>v1</p>" in registry.fetch_node("alpha", "latest", "alpha:latest:guide").presentation

    pinned = registry.refresh()
    assert {item.mount: item.status for item in pinned.mounts}["alpha"] == "pinned"
    assert registry.generation("alpha").generation_id == alpha_old.generation_id

    registry.unpin("alpha")
    resumed = registry.refresh()
    assert {item.mount: item.status for item in resumed.mounts}["alpha"] == "activated"
    assert b"<p>v2</p>" in registry.fetch_node("alpha", "latest", "alpha:latest:guide").presentation

    verifier.fail_mounts.clear()
    _install_hub(transport, _hub([alpha_v2]))
    removed = registry.refresh()
    assert {item.mount: item.status for item in removed.mounts}["beta"] == "removed"
    assert registry.mounts() == ("alpha",)
    restarted = _registry(tmp_path, MemoryTransport(transport.objects), RecordingVerifier())
    assert restarted.mounts() == ("alpha",)


def test_unrelated_mount_update_preserves_generation_and_warm_presentation_cache(
    tmp_path: Path,
) -> None:
    import asyncio

    from chirp.testing import TestClient

    alpha_v1, alpha_objects = _artifact(tmp_path, "alpha", "v1")
    beta_v1, beta_objects = _artifact(tmp_path, "beta", "v1")
    transport = MemoryTransport(alpha_objects | beta_objects)
    _install_hub(transport, _hub([alpha_v1, beta_v1]))
    registry = _registry(tmp_path, transport, RecordingVerifier())
    first_report = registry.refresh()
    docs = _remote_docs_app(tmp_path, registry, "alpha", "beta", app_name="locality-app")
    alpha_generation = registry.generation("alpha")
    alpha_catalog = docs.catalog._shards["alpha"]
    composed_generation = docs.catalog.generation
    alpha_presentation_url = next(
        str(alpha_v1["artifact_base_url"]) + str(item["object_url"])
        for item in alpha_v1["inventory"]
        if item["role"] == "presentation"
    )

    async def get(path: str) -> str:
        client = TestClient(docs.create_app())
        async with client:
            response = await client.get(path)
        assert response.status == 200
        return response.text

    assert "v1" in asyncio.run(get("/alpha/guide/"))
    assert transport.calls.count(alpha_presentation_url) == 1

    reused = docs.refresh_remote_shards()
    assert {item.mount: item.status for item in reused.mounts} == {
        "alpha": "reused",
        "beta": "reused",
    }
    assert docs.catalog.generation == composed_generation
    assert docs.catalog._shards["alpha"] is alpha_catalog

    beta_v2, beta_v2_objects = _artifact(tmp_path, "beta", "v2")
    transport.objects.update(beta_v2_objects)
    _install_hub(transport, _hub([alpha_v1, beta_v2]))
    updated = docs.refresh_remote_shards()
    outcomes = {item.mount: item.status for item in updated.mounts}

    assert outcomes == {"alpha": "reused", "beta": "activated"}
    assert registry.generation("alpha").generation_id == alpha_generation.generation_id
    assert registry.generation("alpha").hub_payload_sha256 == first_report.hub_payload_sha256
    assert updated.hub_payload_sha256 != first_report.hub_payload_sha256
    assert docs.catalog._shards["alpha"] is alpha_catalog
    assert _presentation_cache_stats(alpha_catalog)["entries"] == 1
    assert "v1" in asyncio.run(get("/alpha/guide/"))
    assert transport.calls.count(alpha_presentation_url) == 1
    assert "v2" in asyncio.run(get("/beta/guide/"))


def test_same_mount_refreshes_are_serialized_and_publish_one_complete_generation(
    tmp_path: Path,
) -> None:
    manifest, objects = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects)
    _install_hub(transport, _hub([manifest]))
    registry = _registry(tmp_path, transport, RecordingVerifier())

    with ThreadPoolExecutor(max_workers=4) as pool:
        reports = tuple(pool.map(lambda _: registry.refresh(), range(4)))

    generation_ids = {report.mounts[0].generation_id for report in reports}
    generations = list((tmp_path / "remote-state" / "mounts" / "alpha" / "generations").iterdir())
    assert len(generation_ids) == 1
    assert len(generations) == 1
    assert (tmp_path / "remote-state" / "mounts" / "alpha" / "current" / "selection.json").is_file()


def test_invalid_hub_verification_preserves_all_last_known_good_mounts(tmp_path: Path) -> None:
    manifest, objects = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects)
    hub = _hub([manifest])
    _install_hub(transport, hub)
    verifier = RecordingVerifier()
    registry = _registry(tmp_path, transport, verifier)
    registry.refresh()
    generation_id = registry.generation("alpha").generation_id

    corrupted = copy.deepcopy(hub)
    corrupted["generated_at"] = "2026-08-04T00:00:00Z"
    _install_hub(transport, corrupted)

    with pytest.raises(
        RemoteShardVerificationError, match="hub manifest failed contract validation"
    ):
        registry.refresh()
    assert registry.generation("alpha").generation_id == generation_id


def test_verifier_must_return_explicit_serializable_verified_evidence(tmp_path: Path) -> None:
    manifest, objects = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects)
    _install_hub(transport, _hub([manifest]))

    class EmptyVerifier(RecordingVerifier):
        def verify_hub(self, manifest: Any, *, fetcher: StrictHTTPSFetcher) -> dict[str, Any]:
            _ = manifest, fetcher
            return {}

    registry = _registry(tmp_path, transport, EmptyVerifier())
    with pytest.raises(RemoteShardVerificationError, match="explicit verified evidence"):
        registry.refresh()


def test_tampered_local_receipt_is_not_silently_loaded(tmp_path: Path) -> None:
    manifest, objects = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects)
    _install_hub(transport, _hub([manifest]))
    registry = _registry(tmp_path, transport, RecordingVerifier())
    registry.refresh()
    generation = registry.generation("alpha")
    receipt_path = (
        tmp_path
        / "remote-state"
        / "mounts"
        / "alpha"
        / "generations"
        / generation.generation_id
        / "receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.pop("hub_verification")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    restarted = _registry(tmp_path, MemoryTransport(objects), RecordingVerifier())

    assert restarted.mounts() == ()
    assert any("lacks cryptographic evidence" in error for error in restarted.startup_errors)


def test_zero_local_hub_serves_remote_route_and_catalog_without_source_indexing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from chirp.testing import TestClient

    from furatena.catalog.docs_app import DocsApp
    from furatena.catalog.runtime import ServeConfig, ServeMode
    from furatena.catalog.sources.scanner import FilesystemScanner

    manifest_v1, objects_v1 = _artifact(tmp_path, "alpha", "remote-v1")
    transport = MemoryTransport(objects_v1)
    _install_hub(transport, _hub([manifest_v1]))
    registry = _registry(tmp_path, transport, RecordingVerifier())
    registry.refresh()

    app_root = tmp_path / "hub-app"
    app_root.mkdir()
    (app_root / "docs.yaml").write_text(
        """shell: shell.html
views:
  doc: views/doc.html
  doc_list: views/doc_list.html
  api_reference: views/api_reference.html
  home: views/home.html
  default: views/doc.html
theme:
  use: lagoon
  id: furatena
  templates: theme/templates
mounts: mounts.yaml
""",
        encoding="utf-8",
    )
    missing_source = app_root / "corpus-does-not-exist"
    (app_root / "mounts.yaml").write_text(
        f"""mounts:
  - id: alpha
    label: Alpha
    content_root: {missing_source}
    url_prefix: /alpha
    default: true
    source:
      provider: remote-shard
""",
        encoding="utf-8",
    )

    def reject_scan(*args: Any, **kwargs: Any) -> list[Any]:
        _ = args, kwargs
        raise AssertionError("a remote mount attempted local source indexing")

    monkeypatch.setattr(FilesystemScanner, "scan", reject_scan)
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        remote_shards=registry,
    )
    assert not missing_source.exists()
    presentation_url = next(
        str(manifest_v1["artifact_base_url"]) + str(item["object_url"])
        for item in manifest_v1["inventory"]
        if item["role"] == "presentation"
    )
    assert presentation_url not in transport.calls

    async def catalog_export() -> None:
        client = TestClient(docs.create_app())
        async with client:
            exported = await client.get("/catalog.json")
            assert exported.status == 200
            catalog = json.loads(exported.text)
            assert catalog["page_count"] == 1
            assert catalog["pages"][0]["node_id"] == "alpha:latest:guide"

    asyncio.run(catalog_export())
    started = threading.Event()
    release = threading.Event()
    transport.blockers[presentation_url] = (started, release)

    def fetch_page() -> str:
        async def request() -> str:
            client = TestClient(docs.create_app())
            async with client:
                response = await client.get("/alpha/guide/")
            assert response.status == 200
            return response.text

        return asyncio.run(request())

    with ThreadPoolExecutor(max_workers=2) as pool:
        old_request = pool.submit(fetch_page)
        assert started.wait(timeout=10)
        manifest_v2, objects_v2 = _artifact(tmp_path, "alpha", "remote-v2")
        transport.objects.update(objects_v2)
        _install_hub(transport, _hub([manifest_v2]))
        report = docs.refresh_remote_shards()
        assert report.mounts[0].status == "activated"
        release.set()
        old_html = old_request.result(timeout=10)

    assert "Guide remote-v1" in old_html
    assert "remote-v1" in old_html
    assert "Backlink remote-v1" in old_html
    assert "remote-v2" not in old_html
    updated_html = fetch_page()
    assert "Guide remote-v2" in updated_html
    assert "remote-v2" in updated_html
    assert "Backlink remote-v2" in updated_html
    assert "Backlink remote-v1" not in updated_html
    assert transport.calls.count(presentation_url) == 1

    entered_publication = threading.Event()
    finish_publication = threading.Event()
    query_started = threading.Event()
    original_finalize = docs.catalog._finalize_federated

    def paused_finalize() -> None:
        entered_publication.set()
        assert finish_publication.wait(timeout=10)
        original_finalize()

    monkeypatch.setattr(docs.catalog, "_finalize_federated", paused_finalize)
    manifest_v3, objects_v3 = _artifact(tmp_path, "alpha", "remote-v3")
    transport.objects.update(objects_v3)
    _install_hub(transport, _hub([manifest_v3]))

    def read_graph() -> Any:
        query_started.set()
        return docs.catalog.query_graph_snapshot()

    with ThreadPoolExecutor(max_workers=2) as pool:
        refreshing = pool.submit(docs.refresh_remote_shards)
        assert entered_publication.wait(timeout=10)
        reading = pool.submit(read_graph)
        assert query_started.wait(timeout=10)
        assert not reading.done()
        finish_publication.set()
        refreshing.result(timeout=10)
        graph = reading.result(timeout=10)
    assert graph["pages"][0]["title"] == "Guide remote-v3"
    assert graph["namespaces"][0]["page_count"] == 1


def test_read_only_http_request_pins_search_to_one_composed_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from chirp.testing import TestClient

    manifest_v1, objects_v1 = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects_v1)
    _install_hub(transport, _hub([manifest_v1]))
    registry = _registry(tmp_path, transport, RecordingVerifier())
    registry.refresh()
    docs = _remote_docs_app(tmp_path, registry, "alpha", app_name="request-snapshot-app")
    app = docs.create_app()
    catalog = docs.catalog
    nodes_read = threading.Event()
    continue_read = threading.Event()
    generations: list[tuple[str, int]] = []
    original_all_doc_nodes = catalog.all_doc_nodes
    original_ast_documents = catalog.ast_documents

    def paused_all_doc_nodes() -> list[Any]:
        generations.append(("nodes", catalog.generation))
        nodes = original_all_doc_nodes()
        nodes_read.set()
        assert continue_read.wait(timeout=10)
        return nodes

    def observed_ast_documents() -> dict[str, Any]:
        generations.append(("ast", catalog.generation))
        return original_ast_documents()

    monkeypatch.setattr(catalog, "all_doc_nodes", paused_all_doc_nodes)
    monkeypatch.setattr(catalog, "ast_documents", observed_ast_documents)

    def fetch_search() -> dict[str, Any]:
        async def request() -> dict[str, Any]:
            client = TestClient(app)
            async with client:
                response = await client.get("/search.json")
            assert response.status == 200
            return json.loads(response.text)

        return asyncio.run(request())

    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(fetch_search)
        assert nodes_read.wait(timeout=10)
        manifest_v2, objects_v2 = _artifact(tmp_path, "alpha", "v2")
        transport.objects.update(objects_v2)
        _install_hub(transport, _hub([manifest_v2]))
        docs.refresh_remote_shards()
        continue_read.set()
        payload = reading.result(timeout=10)

    assert {kind for kind, _generation in generations} == {"nodes", "ast"}
    assert {generation for _kind, generation in generations} == {1}
    assert catalog.generation == 2
    assert payload["entries"][0]["title"] == "Guide v1"


def test_pinned_remote_catalog_fetches_presentation_from_its_own_generation(
    tmp_path: Path,
) -> None:
    manifest_v1, objects_v1 = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects_v1)
    _install_hub(transport, _hub([manifest_v1]))
    registry = _registry(tmp_path, transport, RecordingVerifier())
    registry.refresh()
    docs = _remote_docs_app(tmp_path, registry, "alpha", app_name="bound-loader-app")

    with docs.catalog.read_snapshot():
        node = docs.catalog.get("/alpha/guide/")
        assert node is not None
        manifest_v2, objects_v2 = _artifact(tmp_path, "alpha", "v2")
        transport.objects.update(objects_v2)
        _install_hub(transport, _hub([manifest_v2]))
        docs.refresh_remote_shards()
        assert "v1" in docs.catalog.body_html(node)

    current = docs.catalog.get("/alpha/guide/")
    assert current is not None
    assert "v2" in docs.catalog.body_html(current)


def test_mcp_resource_and_read_tool_pin_one_composed_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from furatena.catalog.mcp import FuraMCPServer

    manifest_v1, objects_v1 = _artifact(tmp_path, "alpha", "v1")
    transport = MemoryTransport(objects_v1)
    _install_hub(transport, _hub([manifest_v1]))
    registry = _registry(tmp_path, transport, RecordingVerifier())
    registry.refresh()
    docs = _remote_docs_app(tmp_path, registry, "alpha", app_name="mcp-snapshot-app")
    server = FuraMCPServer(docs)
    resource_started = threading.Event()
    continue_resource = threading.Event()
    resource_generations: list[int] = []
    original_resource_payload = server._resource_payload

    def paused_resource_payload(uri: str) -> dict[str, Any]:
        resource_generations.append(docs.catalog.generation)
        resource_started.set()
        assert continue_resource.wait(timeout=10)
        resource_generations.append(docs.catalog.generation)
        return original_resource_payload(uri)

    monkeypatch.setattr(server, "_resource_payload", paused_resource_payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(server.read_resource, "fura://catalog/graph")
        assert resource_started.wait(timeout=10)
        manifest_v2, objects_v2 = _artifact(tmp_path, "alpha", "v2")
        transport.objects.update(objects_v2)
        _install_hub(transport, _hub([manifest_v2]))
        docs.refresh_remote_shards()
        continue_resource.set()
        resource = reading.result(timeout=10)
    assert resource["uri"] == "fura://catalog/graph"
    assert resource_generations == [1, 1]

    monkeypatch.setattr(server, "_resource_payload", original_resource_payload)
    tool_started = threading.Event()
    continue_tool = threading.Event()
    tool_generations: list[int] = []

    def paused_semantic_search(arguments: dict[str, Any]) -> dict[str, Any]:
        _ = arguments
        tool_generations.append(docs.catalog.generation)
        tool_started.set()
        assert continue_tool.wait(timeout=10)
        tool_generations.append(docs.catalog.generation)
        return {"schema_version": 1, "query": "guide", "count": 0, "results": []}

    monkeypatch.setattr(server, "_semantic_search", paused_semantic_search)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(server.call_tool, "semantic_search", {"query": "guide"})
        assert tool_started.wait(timeout=10)
        manifest_v3, objects_v3 = _artifact(tmp_path, "alpha", "v3")
        transport.objects.update(objects_v3)
        _install_hub(transport, _hub([manifest_v3]))
        docs.refresh_remote_shards()
        continue_tool.set()
        result = reading.result(timeout=10)
    assert result["isError"] is False
    assert tool_generations == [2, 2]
    assert docs.catalog.generation == 3


def test_remote_presentation_cache_coalesces_same_node_but_not_unrelated_nodes(
    tmp_path: Path,
) -> None:
    from furatena.catalog.loader import DocCatalog

    raw = {
        "channel": "latest",
        "edition": "latest",
        "edges": [],
        "pages": [
            {
                "edition": "latest",
                "mount": "alpha",
                "node_id": f"alpha:latest:{slug}",
                "slug": slug,
                "title": slug.title(),
                "url": f"/alpha/{slug}/",
            }
            for slug in ("one", "two")
        ],
    }
    barrier = threading.Barrier(2)
    calls: list[str] = []
    calls_lock = threading.Lock()

    def parallel_loader(node_id: str) -> bytes:
        with calls_lock:
            calls.append(node_id)
        barrier.wait(timeout=10)
        return f"<p>{node_id}</p>".encode()

    parallel = DocCatalog.from_remote(
        raw,
        mount="alpha",
        edition="latest",
        presentation_loader=parallel_loader,
        content_root=tmp_path / "missing",
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = tuple(pool.map(parallel.body_html, parallel.nodes))
    assert len(calls) == 2
    assert all("alpha:latest:" in value for value in values)

    started = threading.Event()
    release = threading.Event()
    same_key_calls = 0
    counter_lock = threading.Lock()

    def coalesced_loader(node_id: str) -> bytes:
        nonlocal same_key_calls
        with counter_lock:
            same_key_calls += 1
        started.set()
        assert release.wait(timeout=10)
        return f"<p>{node_id}</p>".encode()

    coalesced = DocCatalog.from_remote(
        raw,
        mount="alpha",
        edition="latest",
        presentation_loader=coalesced_loader,
        content_root=tmp_path / "missing",
    )
    node = coalesced.nodes[0]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(coalesced.body_html, node) for _ in range(8)]
        assert started.wait(timeout=10)
        release.set()
        values = [future.result(timeout=10) for future in futures]
    assert same_key_calls == 1
    assert len(set(values)) == 1
    assert _presentation_cache_stats(coalesced)["flights"] == 0


def test_remote_presentation_cache_is_bounded_evicts_and_never_caches_failures(
    tmp_path: Path,
) -> None:
    from furatena.catalog.loader import DocCatalog

    raw = {
        "channel": "latest",
        "edition": "latest",
        "edges": [],
        "pages": [
            {
                "edition": "latest",
                "mount": "alpha",
                "node_id": f"alpha:latest:{slug}",
                "slug": slug,
                "title": slug.title(),
                "url": f"/alpha/{slug}/",
            }
            for slug in ("one", "two", "three")
        ],
    }
    calls: list[str] = []

    def loader(node_id: str) -> bytes:
        calls.append(node_id)
        return f"<p>{node_id}</p>".encode()

    bounded = DocCatalog.from_remote(
        raw,
        mount="alpha",
        edition="latest",
        presentation_loader=loader,
        content_root=tmp_path / "missing",
        presentation_cache_entries=2,
        presentation_cache_bytes=1_024,
    )
    one, two, three = bounded.nodes
    bounded.body_html(one)
    bounded.body_html(two)
    bounded.body_html(one)
    bounded.body_html(three)
    bounded.body_html(two)
    assert calls.count(one.node_id) == 1
    assert calls.count(two.node_id) == 2
    assert _presentation_cache_stats(bounded) == {
        "entries": 2,
        "bytes": len(f"<p>{three.node_id}</p>".encode()) + len(f"<p>{two.node_id}</p>".encode()),
        "flights": 0,
        "max_entries": 2,
        "max_bytes": 1_024,
    }

    oversized_calls = 0

    def oversized_loader(node_id: str) -> bytes:
        nonlocal oversized_calls
        oversized_calls += 1
        return f"<p>{node_id}</p>".encode()

    oversized = DocCatalog.from_remote(
        raw,
        mount="alpha",
        edition="latest",
        presentation_loader=oversized_loader,
        content_root=tmp_path / "missing",
        presentation_cache_entries=2,
        presentation_cache_bytes=4,
    )
    assert oversized.body_html(oversized.nodes[0])
    assert oversized.body_html(oversized.nodes[0])
    assert oversized_calls == 2
    assert _presentation_cache_stats(oversized)["entries"] == 0

    attempts = 0
    failure_started = threading.Event()
    release_failure = threading.Event()

    def flaky_loader(node_id: str) -> bytes:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failure_started.set()
            assert release_failure.wait(timeout=10)
            raise RemoteShardFetchError(
                "The injected remote presentation fetch failed during testing.",
                mount="alpha",
                operation="remote_shard_read",
            )
        return f"<p>{node_id}</p>".encode()

    flaky = DocCatalog.from_remote(
        raw,
        mount="alpha",
        edition="latest",
        presentation_loader=flaky_loader,
        content_root=tmp_path / "missing",
    )
    flaky_cache = flaky._remote_presentation_cache
    assert flaky_cache is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(flaky.body_html, flaky.nodes[0])
        assert failure_started.wait(timeout=10)
        waiter = pool.submit(flaky.body_html, flaky.nodes[0])
        with flaky_cache._lock:
            flight = flaky_cache._flights[flaky.nodes[0].node_id]
        assert flight.coalesced.wait(timeout=10)
        release_failure.set()
        errors = [future.exception(timeout=10) for future in (owner, waiter)]
    assert all(isinstance(error, RemoteShardFetchError) for error in errors)
    assert {error.code for error in errors if isinstance(error, RemoteShardFetchError)} == {
        "fura.remote_shard.fetch"
    }
    assert {
        tuple(sorted(error.context.items()))
        for error in errors
        if isinstance(error, RemoteShardFetchError)
    } == {(("mount", "alpha"), ("operation", "remote_shard_read"))}
    assert _presentation_cache_stats(flaky)["flights"] == 0
    assert flaky.body_html(flaky.nodes[0])
    assert attempts == 2
