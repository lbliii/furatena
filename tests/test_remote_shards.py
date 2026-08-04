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
