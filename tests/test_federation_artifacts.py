"""Published shard and federation hub executable contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from compression import zstd
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from furatena.catalog.federation_artifacts import (
    MAX_PRESENTATION_BYTES,
    federation_hub_manifest_digest,
    hub_payload_digest,
    inventory_digest,
    load_federation_discovery_schema,
    load_federation_hub_schema,
    load_published_shard_schema,
    published_artifact_fingerprint,
    published_manifest_digest,
    validate_federation_discovery,
    validate_federation_hub_manifest,
    validate_published_shard_manifest,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO / "tests" / "fixtures" / "federation" / "v1"
ARTIFACT_ROOT = FIXTURE_ROOT / "artifact"
SHARD_MANIFEST = ARTIFACT_ROOT / "manifest.json"
HUB_MANIFEST = FIXTURE_ROOT / "hub-manifest.json"
CHANNELS_EXTENSION = FIXTURE_ROOT / "channels-extension.json"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _readdress(shard: dict[str, Any]) -> None:
    fingerprint = published_artifact_fingerprint(shard)
    shard["fingerprint"] = fingerprint
    shard["artifact_base_url"] = f"https://artifacts.example.com/shards/sha256/{fingerprint}/"
    for record in (*shard["signatures"], *shard["attestations"]):
        record["subject_sha256"] = fingerprint


def _replace_identity_object(
    artifact: Path,
    shard: dict[str, Any],
    item: dict[str, Any],
    value: dict[str, Any],
) -> None:
    old_path = artifact / item["object_url"]
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    digest = _digest(encoded)
    new_url = f"objects/sha256/{digest}.json"
    old_path.unlink()
    (artifact / new_url).write_bytes(encoded)
    item.update(
        {
            "object_url": new_url,
            "content_encoding": "identity",
            "uncompressed_size": len(encoded),
            "encoded_size": len(encoded),
            "sha256": digest,
            "encoded_sha256": digest,
        }
    )
    shard["totals"]["uncompressed_bytes"] = sum(
        record["uncompressed_size"] for record in shard["inventory"]
    )
    shard["totals"]["encoded_bytes"] = sum(record["encoded_size"] for record in shard["inventory"])
    shard["integrity"]["inventory_sha256"] = inventory_digest(shard["inventory"])
    _readdress(shard)


def _refresh_inventory(shard: dict[str, Any]) -> None:
    roles = [item["role"] for item in shard["inventory"]]
    shard["totals"] = {
        "object_count": len(shard["inventory"]),
        "fragment_count": roles.count("fragment"),
        "presentation_count": roles.count("presentation"),
        "uncompressed_bytes": sum(item["uncompressed_size"] for item in shard["inventory"]),
        "encoded_bytes": sum(item["encoded_size"] for item in shard["inventory"]),
    }
    shard["integrity"]["inventory_sha256"] = inventory_digest(shard["inventory"])
    _readdress(shard)


def _replace_presentation(
    artifact: Path,
    shard: dict[str, Any],
    decoded: bytes,
    *,
    encoding: str = "identity",
    declared_size: int | None = None,
) -> dict[str, Any]:
    item = next(record for record in shard["inventory"] if record["role"] == "presentation")
    old_path = artifact / item["object_url"]
    encoded = zstd.compress(decoded) if encoding == "zstd" else decoded
    digest = _digest(encoded)
    suffix = ".html.zst" if encoding == "zstd" else ".html"
    new_url = f"objects/sha256/{digest}{suffix}"
    old_path.unlink()
    (artifact / new_url).write_bytes(encoded)
    item.update(
        {
            "object_url": new_url,
            "content_encoding": encoding,
            "uncompressed_size": len(decoded) if declared_size is None else declared_size,
            "encoded_size": len(encoded),
            "sha256": _digest(decoded),
            "encoded_sha256": digest,
        }
    )
    _refresh_inventory(shard)
    return item


def test_golden_published_shard_and_hub_pair_validate() -> None:
    shard = _json(SHARD_MANIFEST)
    hub = _json(HUB_MANIFEST)
    discovery = _json(CHANNELS_EXTENSION)

    Draft202012Validator(load_published_shard_schema()).validate(shard)
    Draft202012Validator(load_federation_hub_schema()).validate(hub)
    Draft202012Validator(load_federation_discovery_schema()).validate(discovery)
    assert validate_published_shard_manifest(shard, artifact_root=ARTIFACT_ROOT) == []
    assert (
        validate_federation_hub_manifest(
            hub,
            published_manifests={"chirp:0.10.3": shard},
        )
        == []
    )
    release = hub["shards"]["chirp:0.10.3"]
    assert release["integrity"]["manifest_sha256"] == published_manifest_digest(shard)
    assert release["integrity"]["manifest_bytes"] == SHARD_MANIFEST.stat().st_size
    assert release["source_shard_fingerprint"] == shard["source_shard_fingerprint"]
    assert release["provenance"]["source_path"] == shard["provenance"]["source_path"]
    assert shard["fingerprint"] == published_artifact_fingerprint(shard)
    assert shard["source_shard_fingerprint"] != shard["fingerprint"]
    assert hub["integrity"]["payload_sha256"] == hub_payload_digest(hub)
    assert validate_federation_discovery(discovery) == []
    assert discovery["hub_manifest_sha256"] == federation_hub_manifest_digest(hub)


def test_complete_inventory_rejects_missing_extra_and_corrupt_objects(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    catalog = next(item for item in shard["inventory"] if item["role"] == "catalog")
    catalog_path = artifact / catalog["object_url"]

    catalog_path.write_bytes(catalog_path.read_bytes() + b" ")
    corrupt = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any("exceeds declared encoded_size before read" in item for item in corrupt)

    shutil.copyfile(ARTIFACT_ROOT / catalog["object_url"], catalog_path)
    extra = artifact / "objects" / "sha256" / f"{'f' * 64}.json"
    extra.write_text("{}\n", encoding="utf-8")
    mismatch = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any("object set mismatch" in item and "extra=" in item for item in mismatch)


def test_contract_rejects_traversal_unproven_versions_and_signature_swap() -> None:
    shard = _json(SHARD_MANIFEST)

    traversal = copy.deepcopy(shard)
    traversal["inventory"][0]["logical_path"] = "../catalog.json"
    assert any("does not match" in item for item in validate_published_shard_manifest(traversal))

    old_dcp = copy.deepcopy(shard)
    old_dcp["contracts"]["dcp"] = 1
    assert any("is not one of" in item for item in validate_published_shard_manifest(old_dcp))

    old_ir = copy.deepcopy(shard)
    old_ir["contracts"]["content_ir"] = 2
    assert any("3 was expected" in item for item in validate_published_shard_manifest(old_ir))

    swapped = copy.deepcopy(shard)
    swapped["signatures"][0]["subject_sha256"] = "0" * 64
    assert any("subject_sha256" in item for item in validate_published_shard_manifest(swapped))

    private = copy.deepcopy(shard)
    private["audience"] = "private"
    assert any("was expected" in item for item in validate_published_shard_manifest(private))

    credentialed = copy.deepcopy(shard)
    credentialed["artifact_base_url"] = credentialed["artifact_base_url"].replace(
        "https://", "https://user:secret@"
    )
    assert any(
        "embedded credentials are forbidden" in item
        for item in validate_published_shard_manifest(credentialed)
    )

    unsafe_source = copy.deepcopy(shard)
    unsafe_source["provenance"]["source_path"] = "docs/../private"
    assert any("source_path" in item for item in validate_published_shard_manifest(unsafe_source))

    encoded_traversal = copy.deepcopy(shard)
    encoded_traversal["artifact_base_url"] = encoded_traversal["artifact_base_url"].replace(
        "/shards/", "/shards/%2e%2e/"
    )
    assert any(
        "URL path traversal is forbidden" in item
        for item in validate_published_shard_manifest(encoded_traversal)
    )

    wrong_address = copy.deepcopy(shard)
    wrong_address["inventory"][0]["encoded_sha256"] = "0" * 64
    wrong_address["integrity"]["inventory_sha256"] = inventory_digest(wrong_address["inventory"])
    _readdress(wrong_address)
    assert any(
        "exact encoded_sha256" in item for item in validate_published_shard_manifest(wrong_address)
    )


def test_remote_reader_accepts_real_dcp_v2_and_v3_object_sets(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    assert validate_published_shard_manifest(shard, artifact_root=artifact) == []

    catalog = next(item for item in shard["inventory"] if item["role"] == "catalog")
    catalog_value = _json(artifact / catalog["object_url"])
    catalog_value["schema_version"] = 2
    shard["contracts"]["dcp"] = 2
    _replace_identity_object(artifact, shard, catalog, catalog_value)

    assert validate_published_shard_manifest(shard, artifact_root=artifact) == []


def test_catalog_object_must_match_manifest_identity_and_dcp_contract(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    catalog = next(item for item in shard["inventory"] if item["role"] == "catalog")
    catalog_path = artifact / catalog["object_url"]
    value = _json(catalog_path)
    value["mount"] = "other"
    value["schema_version"] = 2
    catalog_path.write_text(json.dumps(value), encoding="utf-8")
    catalog["encoded_size"] = catalog_path.stat().st_size

    errors = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any("mount identity mismatch" in item for item in errors)
    assert any("DCP contract version mismatch" in item for item in errors)


def test_public_object_set_rejects_restricted_records_and_index_drift(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    fragment = next(item for item in shard["inventory"] if item["role"] == "fragment")
    fragment_path = artifact / fragment["object_url"]
    fragment_value = _json(fragment_path)
    fragment_value["visibility"] = "private"
    fragment_value["source_path"] = "../private.md"
    fragment_value["url"] = "/guide/?token=secret"
    fragment_path.write_text(json.dumps(fragment_value), encoding="utf-8")
    fragment["encoded_size"] = fragment_path.stat().st_size

    errors = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any("non-public visibility is forbidden" in item for item in errors)
    assert any("unsafe source_path" in item for item in errors)
    assert any("unsafe page URL" in item for item in errors)

    shutil.rmtree(artifact)
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    search = next(item for item in shard["inventory"] if item["role"] == "search")
    search_path = artifact / search["object_url"]
    search_value = _json(search_path)
    search_value["documents"].append(
        {"node_id": "chirp:0.10.3:private", "title": "Private", "text": "secret"}
    )
    search_path.write_text(json.dumps(search_value), encoding="utf-8")
    search["encoded_size"] = search_path.stat().st_size

    errors = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any("search node set does not match" in item for item in errors)


def test_presentations_are_required_unique_and_paired_by_node_identity() -> None:
    shard = _json(SHARD_MANIFEST)

    missing = copy.deepcopy(shard)
    missing["inventory"] = [item for item in missing["inventory"] if item["role"] != "presentation"]
    _refresh_inventory(missing)
    assert any("presentation" in item for item in validate_published_shard_manifest(missing))

    duplicate = copy.deepcopy(shard)
    presentation = next(item for item in duplicate["inventory"] if item["role"] == "presentation")
    repeated = copy.deepcopy(presentation)
    repeated["encoded_sha256"] = "c" * 64
    repeated["object_url"] = f"objects/sha256/{'c' * 64}.html"
    duplicate["inventory"].append(repeated)
    duplicate["inventory"].sort(key=lambda item: item["logical_path"])
    _refresh_inventory(duplicate)
    duplicate_errors = validate_published_shard_manifest(duplicate)
    assert any("duplicate presentation node_id" in item for item in duplicate_errors)

    crossed = copy.deepcopy(shard)
    crossed_presentation = next(
        item for item in crossed["inventory"] if item["role"] == "presentation"
    )
    crossed_presentation["node_id"] = "chirp:0.10.3:other"
    _refresh_inventory(crossed)
    crossed_errors = validate_published_shard_manifest(crossed)
    assert any("logical_path must be" in item for item in crossed_errors)
    assert any("node_id sets must match" in item for item in crossed_errors)


def test_per_node_paths_media_types_and_singleton_identity_are_exact() -> None:
    shard = _json(SHARD_MANIFEST)

    wrong_path = copy.deepcopy(shard)
    fragment = next(item for item in wrong_path["inventory"] if item["role"] == "fragment")
    fragment["logical_path"] = "fragments/nodes/guide.json"
    _refresh_inventory(wrong_path)
    assert any(
        "logical_path must be" in item for item in validate_published_shard_manifest(wrong_path)
    )

    wrong_media = copy.deepcopy(shard)
    presentation = next(item for item in wrong_media["inventory"] if item["role"] == "presentation")
    presentation["media_type"] = "application/json"
    presentation["object_url"] = presentation["object_url"].replace(".html", ".json")
    _refresh_inventory(wrong_media)
    wrong_media_errors = validate_published_shard_manifest(wrong_media)
    assert any("text/html" in item for item in wrong_media_errors)

    singleton_identity = copy.deepcopy(shard)
    catalog = next(item for item in singleton_identity["inventory"] if item["role"] == "catalog")
    catalog["node_id"] = "chirp:0.10.3:guide"
    _refresh_inventory(singleton_identity)
    assert any("node_id" in item for item in validate_published_shard_manifest(singleton_identity))


@pytest.mark.parametrize(
    "markup",
    (
        b"<script>alert(1)</script>",
        b"<iframe src='https://example.com'></iframe>",
        b"<object data='/x'></object><embed src='/y'><base href='/'>",
        b"<meta http-equiv='refresh' content='0;url=/private'>",
        b"<p onclick='run()'>unsafe</p>",
        b"<iframe srcdoc='<p>hidden</p>'></iframe>",
        b"<form action='/write'><button formaction='/write'>write</button></form>",
        b"<a hx-get='/private'>load</a><p data-hx-post='/write'>write</p>",
        b"<div x-data='{}'><button @click='run()' :class='active'>run</button></div>",
        b"<p data-action='click->controller#run' data-controller='controller'>run</p>",
        b"<a href='java&#x73;cript:alert(1)'>unsafe</a>",
        b"<a href='java&#x0a;script:alert(1)'>unsafe</a>",
        b"<a href='java\x00script:alert(1)'>unsafe</a>",
        b"<a href='vbscript:msgbox(1)'>unsafe</a><a href='file:///etc/passwd'>file</a>",
        b"<a href='blob:https://example.com/id'>blob</a><a href='//example.com/x'>network</a>",
        b"<img src='data:image/svg+xml;base64,PHN2Zz48L3N2Zz4='>",
        b"<img src='data:image/png;base64,aGVsbG8='>",
        b"<p style='background:url(javascript:alert(1))'>unsafe</p>",
        b"<link rel='stylesheet' href='/active.css'><audio autoplay src='/active.mp3'></audio>",
        b"<svg><foreignObject><p>foreign</p></foreignObject><animate attributeName='x'/></svg>",
    ),
)
def test_presentation_parser_rejects_active_markup_and_unsafe_urls(
    tmp_path: Path, markup: bytes
) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    _replace_presentation(artifact, shard, markup)

    errors = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any("inert HTML" in item for item in errors)


def test_presentation_requires_utf8_but_safe_data_images_remain_inert(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    _replace_presentation(artifact, shard, b"<p>\xff</p>")
    assert any(
        "not valid UTF-8" in item
        for item in validate_published_shard_manifest(shard, artifact_root=artifact)
    )

    shutil.rmtree(artifact)
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    _replace_presentation(
        artifact,
        shard,
        b"<article><a href='../guide'>Relative</a><a href='https://example.com/x'>HTTPS</a>"
        b"<a href='http://example.com/x'>HTTP</a><a href='mailto:docs@example.com'>Mail</a>"
        b"<img src='data:image/png;base64,iVBORw0KGgo='><p>Unrelated prose.</p></article>",
    )
    assert validate_published_shard_manifest(shard, artifact_root=artifact) == []


def test_presentation_encoded_and_decoded_bounds_are_enforced_before_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    presentation = next(item for item in shard["inventory"] if item["role"] == "presentation")
    presentation_path = artifact / presentation["object_url"]
    with presentation_path.open("wb") as stream:
        stream.truncate(shard["layout"]["max_object_bytes"] + 1)
    original_open = Path.open

    def guarded_open(path: Path, *args: Any, **kwargs: Any):
        if path == presentation_path:
            raise AssertionError("oversized presentation must be rejected before open")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    encoded_errors = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any(
        "encoded object exceeds max_object_bytes before read" in item for item in encoded_errors
    )

    monkeypatch.setattr(Path, "open", original_open)
    shutil.copytree(ARTIFACT_ROOT, artifact, dirs_exist_ok=True)
    shard = _json(artifact / "manifest.json")
    bomb = b"<p>" + b"x" * MAX_PRESENTATION_BYTES + b"</p>"
    _replace_presentation(
        artifact,
        shard,
        bomb,
        encoding="zstd",
        declared_size=MAX_PRESENTATION_BYTES,
    )
    decoded_errors = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any(
        f"decoded object exceeds {MAX_PRESENTATION_BYTES} bytes" in item for item in decoded_errors
    )


def test_zstd_object_verification_is_lazy_and_memory_bounded(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    fragment = next(item for item in shard["inventory"] if item["role"] == "fragment")
    old_path = artifact / fragment["object_url"]
    decoded = old_path.read_bytes()
    encoded = zstd.compress(decoded)
    encoded_digest = _digest(encoded)
    new_url = f"objects/sha256/{encoded_digest}.json.zst"
    old_path.unlink()
    (artifact / new_url).write_bytes(encoded)
    fragment.update(
        {
            "object_url": new_url,
            "content_encoding": "zstd",
            "encoded_size": len(encoded),
            "encoded_sha256": encoded_digest,
        }
    )
    shard["totals"]["encoded_bytes"] = sum(item["encoded_size"] for item in shard["inventory"])
    shard["integrity"]["inventory_sha256"] = inventory_digest(shard["inventory"])
    _readdress(shard)

    assert validate_published_shard_manifest(shard, artifact_root=artifact) == []

    shard["layout"]["max_object_bytes"] = 1024 * 1024
    bomb = b" " * (shard["layout"]["max_object_bytes"] + 1)
    encoded_bomb = zstd.compress(bomb)
    encoded_bomb_digest = _digest(encoded_bomb)
    (artifact / new_url).unlink()
    bomb_url = f"objects/sha256/{encoded_bomb_digest}.json.zst"
    (artifact / bomb_url).write_bytes(encoded_bomb)
    fragment.update(
        {
            "object_url": bomb_url,
            "uncompressed_size": len(bomb),
            "encoded_size": len(encoded_bomb),
            "sha256": _digest(bomb),
            "encoded_sha256": encoded_bomb_digest,
        }
    )
    shard["totals"]["uncompressed_bytes"] = sum(
        item["uncompressed_size"] for item in shard["inventory"]
    )
    shard["totals"]["encoded_bytes"] = sum(item["encoded_size"] for item in shard["inventory"])
    shard["integrity"]["inventory_sha256"] = inventory_digest(shard["inventory"])
    _readdress(shard)
    errors = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any("decoded object exceeds 1048576 bytes" in item for item in errors)


def test_encoded_object_bound_is_enforced_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact"
    shutil.copytree(ARTIFACT_ROOT, artifact)
    shard = _json(artifact / "manifest.json")
    fragment = next(item for item in shard["inventory"] if item["role"] == "fragment")
    oversized = artifact / fragment["object_url"]
    with oversized.open("wb") as stream:
        stream.truncate(shard["layout"]["max_object_bytes"] + 1)

    original_open = Path.open

    def guarded_open(path: Path, *args: Any, **kwargs: Any):
        if path == oversized:
            raise AssertionError("oversized object must be rejected before open")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    errors = validate_published_shard_manifest(shard, artifact_root=artifact)
    assert any("encoded object exceeds max_object_bytes before read" in item for item in errors)

    shutil.copyfile(ARTIFACT_ROOT / fragment["object_url"], oversized)
    shard["layout"]["max_object_bytes"] = 1024 * 1024
    requested_sizes: list[int] = []

    class GrowingObject:
        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            requested_sizes.append(size)
            return b"x" * size

    def growing_open(path: Path, *args: Any, **kwargs: Any):
        if path == oversized:
            return GrowingObject()
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", growing_open)
    errors = validate_published_shard_manifest(shard, artifact_root=artifact)

    assert requested_sizes == [shard["layout"]["max_object_bytes"] + 1]
    assert any(
        "encoded object exceeds max_object_bytes during bounded read" in item for item in errors
    )


def test_hub_fails_closed_on_identity_lifecycle_channel_and_pair_drift() -> None:
    hub = _json(HUB_MANIFEST)
    shard = _json(SHARD_MANIFEST)

    wrong_key = copy.deepcopy(hub)
    wrong_key["shards"]["chirp:0.10.3"]["edition"] = "0.10.2"
    wrong_key["integrity"]["payload_sha256"] = hub_payload_digest(wrong_key)
    assert any("key must equal" in item for item in validate_federation_hub_manifest(wrong_key))

    eol_stable = copy.deepcopy(hub)
    eol_stable["shards"]["chirp:0.10.3"]["lifecycle"]["status"] = "eol"
    eol_stable["integrity"]["payload_sha256"] = hub_payload_digest(eol_stable)
    assert any("cannot be stable" in item for item in validate_federation_hub_manifest(eol_stable))

    bad_pair = copy.deepcopy(hub)
    bad_pair["shards"]["chirp:0.10.3"]["integrity"]["manifest_sha256"] = "0" * 64
    bad_pair["integrity"]["payload_sha256"] = hub_payload_digest(bad_pair)
    errors = validate_federation_hub_manifest(
        bad_pair,
        published_manifests={"chirp:0.10.3": shard},
    )
    assert any("published manifest digest mismatch" in item for item in errors)

    crossed = copy.deepcopy(hub)
    crossed["channels"]["other"] = crossed["channels"].pop("chirp")
    crossed["integrity"]["payload_sha256"] = hub_payload_digest(crossed)
    assert any("crosses mounts" in item for item in validate_federation_hub_manifest(crossed))

    provenance_drift = copy.deepcopy(hub)
    provenance_drift["shards"]["chirp:0.10.3"]["provenance"]["source_path"] = "other"
    provenance_drift["integrity"]["payload_sha256"] = hub_payload_digest(provenance_drift)
    errors = validate_federation_hub_manifest(
        provenance_drift,
        published_manifests={"chirp:0.10.3": shard},
    )
    assert any("provenance mismatch" in item for item in errors)

    nondeterministic = copy.deepcopy(hub)
    nondeterministic["shards"] = dict(reversed(nondeterministic["shards"].items()))
    nondeterministic["integrity"]["payload_sha256"] = hub_payload_digest(nondeterministic)
    assert any(
        "identity keys must be sorted" in item
        for item in validate_federation_hub_manifest(nondeterministic)
    )


def test_hub_signature_payload_binds_envelope_and_generation_time() -> None:
    hub = _json(HUB_MANIFEST)
    original = hub_payload_digest(hub)
    for field, value in (
        ("schema_version", 2),
        ("manifest_type", "different"),
        ("generated_at", "2026-08-04T00:00:00Z"),
    ):
        changed = copy.deepcopy(hub)
        changed[field] = value
        assert hub_payload_digest(changed) != original
