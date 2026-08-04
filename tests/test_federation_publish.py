"""Deterministic, atomic, and resumable shard publication tests."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from furatena.catalog.federation_artifacts import (
    hub_payload_digest,
    validate_federation_hub_manifest,
    validate_published_shard_manifest,
)
from furatena.catalog.federation_publish import (
    PublishShardOptions,
    ShardPublishConflictError,
    ShardPublishPartialError,
    StoredObject,
    build_published_shard,
    publish_shard,
)

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "fixtures" / "federation" / "v1" / "artifact"


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "frozen" / "mounts" / "chirp" / "0.10.3"
    source.mkdir(parents=True, exist_ok=True)
    golden_manifest = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    catalog_item = next(item for item in golden_manifest["inventory"] if item["role"] == "catalog")
    (source / "catalog.json").write_bytes((GOLDEN / catalog_item["object_url"]).read_bytes())
    (source / "fingerprint.json").write_text(
        json.dumps(
            {
                "mount": "chirp",
                "edition": "0.10.3",
                "fingerprint": "b84016cc7512e8aea0435ebb5991e662c6778ae55ca3afc1662036cd809fdac3",
                "contracts": {"dcp": 3, "content_ir": 3, "adapter": 1},
                "source": {
                    "provider": "git",
                    "repo": "https://github.com/example/chirp.git",
                    "ref": "v0.10.3",
                    "resolved_ref": "e20ae88a77558d708e527b883dd655c0c0c656c2",
                    "path": "site/content",
                },
            }
        ),
        encoding="utf-8",
    )
    return source


def _options(tmp_path: Path, *, staging: str = "stage") -> PublishShardOptions:
    return PublishShardOptions(
        source_shard=_source(tmp_path),
        staging_dir=tmp_path / staging,
        public_base_url="https://artifacts.example.com/shards",
        verification={
            "signatures": [
                {
                    "kind": "sigstore-bundle",
                    "url": "https://artifacts.example.com/signatures/chirp.sigstore.json",
                    "sha256": "a" * 64,
                    "issuer": "https://token.actions.githubusercontent.com",
                    "identity": "https://github.com/example/chirp/.github/workflows/publish.yml@refs/tags/v0.10.3",
                }
            ],
            "attestations": [
                {
                    "predicate_type": "https://slsa.dev/provenance/v1",
                    "url": "https://artifacts.example.com/attestations/chirp.slsa.json",
                    "sha256": "b" * 64,
                }
            ],
        },
    )


def _restage(options: PublishShardOptions, path: Path) -> PublishShardOptions:
    return replace(options, staging_dir=path)


class RecordingBackend:
    def __init__(self, *, fail_after: int | None = None, metadata: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.opens: list[str] = []
        self.fail_after = fail_after
        self.metadata = metadata

    def inspect(self, key: str) -> StoredObject | None:
        value = self.objects.get(key)
        if value is None:
            return None
        return StoredObject(
            size=len(value),
            sha256=hashlib.sha256(value).hexdigest() if self.metadata else None,
        )

    def put(
        self,
        key: str,
        source: Path,
        *,
        media_type: str,
        sha256: str,
        create_only: bool,
    ) -> None:
        if self.fail_after is not None and len(self.puts) == self.fail_after:
            raise ShardPublishPartialError("injected partial upload")
        self.puts.append(key)
        _ = media_type
        value = source.read_bytes()
        if create_only and key in self.objects:
            raise ShardPublishConflictError(f"object already exists: {key}")
        assert hashlib.sha256(value).hexdigest() == sha256
        self.objects[key] = value

    def open(self, key: str) -> AbstractContextManager[BinaryIO]:
        self.opens.append(key)
        return BytesIO(self.objects[key])


def test_package_is_deterministic_and_validates_complete_object_set(tmp_path: Path) -> None:
    first_root, first = build_published_shard(_options(tmp_path, staging="first"))
    second_root, second = build_published_shard(_options(tmp_path, staging="second"))

    assert first == second
    assert (first_root / "manifest.json").read_bytes() == (
        second_root / "manifest.json"
    ).read_bytes()
    assert validate_published_shard_manifest(first, artifact_root=first_root) == []
    assert [item["logical_path"] for item in first["inventory"]] == sorted(
        item["logical_path"] for item in first["inventory"]
    )


def test_manifest_commits_last_and_exact_retry_is_a_noop(tmp_path: Path) -> None:
    backend = RecordingBackend()
    options = _options(tmp_path)

    first = publish_shard(options, backend)
    first_puts = tuple(backend.puts)
    second = publish_shard(_restage(options, tmp_path / "retry"), backend)

    assert first.status == "published"
    assert backend.puts[-1].endswith("/manifest.json")
    assert all(not key.endswith("/manifest.json") for key in backend.puts[:-1])
    assert second.status == "up_to_date"
    assert tuple(backend.puts) == first_puts
    assert first.hub_entry == second.hub_entry
    assert len(backend.opens) >= len(first.manifest["inventory"]) * 2 + 2
    assert first.hub_entry["artifact_url"].endswith(f"/sha256/{first.fingerprint}/manifest.json")
    hub = json.loads(
        (REPO / "tests" / "fixtures" / "federation" / "v1" / "hub-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    hub["shards"]["chirp:0.10.3"] = first.hub_entry
    hub["integrity"]["payload_sha256"] = hub_payload_digest(hub)
    hub["signatures"][0]["subject_sha256"] = hub["integrity"]["payload_sha256"]
    assert (
        validate_federation_hub_manifest(
            hub,
            published_manifests={"chirp:0.10.3": first.manifest},
        )
        == []
    )


def test_committed_manifest_rechecks_objects_and_uses_trusted_metadata(
    tmp_path: Path,
) -> None:
    backend = RecordingBackend(metadata=True)
    options = _options(tmp_path)
    publish_shard(options, backend)
    opens_after_upload = len(backend.opens)

    retry = publish_shard(_restage(options, tmp_path / "retry"), backend)
    assert retry.status == "up_to_date"
    assert len(backend.opens) == opens_after_upload

    object_key = next(key for key in backend.objects if "/objects/" in key)
    del backend.objects[object_key]
    with pytest.raises(ShardPublishPartialError, match="missing from remote storage"):
        publish_shard(_restage(options, tmp_path / "missing"), backend)


def test_metadata_mismatch_fails_without_downloading_remote_bytes(tmp_path: Path) -> None:
    class MismatchedBackend(RecordingBackend):
        mismatch_key: str | None = None

        def inspect(self, key: str) -> StoredObject | None:
            result = super().inspect(key)
            if key == self.mismatch_key and result is not None:
                return StoredObject(size=result.size, sha256="0" * 64)
            return result

    backend = MismatchedBackend(metadata=True)
    options = _options(tmp_path)
    publish_shard(options, backend)
    object_key = next(key for key in backend.objects if "/objects/" in key)
    backend.mismatch_key = object_key
    opens_before = len(backend.opens)
    with pytest.raises(ShardPublishConflictError, match="digest metadata differs"):
        publish_shard(_restage(options, tmp_path / "mismatch"), backend)
    assert len(backend.opens) == opens_before


def test_partial_upload_never_exposes_manifest_and_retry_resumes(tmp_path: Path) -> None:
    backend = RecordingBackend(fail_after=2)
    options = _options(tmp_path)

    with pytest.raises(ShardPublishPartialError, match="injected partial"):
        publish_shard(options, backend)
    assert backend.objects
    assert not any(key.endswith("/manifest.json") for key in backend.objects)

    prior_count = len(backend.objects)
    backend.fail_after = None
    result = publish_shard(_restage(options, tmp_path / "retry"), backend)

    assert result.status == "published"
    assert result.reused_objects == prior_count
    assert any(key.endswith("/manifest.json") for key in backend.objects)


def test_retry_rejects_conflicting_remote_object_before_manifest(tmp_path: Path) -> None:
    options = _options(tmp_path)
    artifact, manifest = build_published_shard(options)
    first = manifest["inventory"][0]
    prefix = f"sha256/{manifest['fingerprint']}"
    backend = RecordingBackend()
    backend.objects[f"{prefix}/{first['object_url']}"] = b"different"

    with pytest.raises(ShardPublishConflictError, match="differs from the immutable inventory"):
        publish_shard(_restage(options, tmp_path / "publish"), backend)

    assert artifact.is_dir()
    assert not any(key.endswith("/manifest.json") for key in backend.objects)


def test_staging_collision_preserves_caller_data(tmp_path: Path) -> None:
    options = _options(tmp_path)
    artifact = options.staging_dir / "artifact"
    artifact.mkdir(parents=True)
    sentinel = artifact / "caller-data.txt"
    sentinel.write_text("keep\n", encoding="utf-8")

    with pytest.raises(ShardPublishConflictError, match="will not be replaced"):
        build_published_shard(options)
    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_optional_repository_assertion_rejects_provenance_mismatch(tmp_path: Path) -> None:
    options = _options(tmp_path)
    asserted = PublishShardOptions(
        source_shard=options.source_shard,
        staging_dir=options.staging_dir,
        public_base_url=options.public_base_url,
        verification=options.verification,
        repository_url="https://github.com/other/docs.git",
    )

    with pytest.raises(ShardPublishConflictError, match="does not match frozen shard"):
        build_published_shard(asserted)


def test_concurrent_identical_create_is_verified_and_counted_as_reused(tmp_path: Path) -> None:
    class ConcurrentBackend(RecordingBackend):
        first = True

        def put(self, key: str, source: Path, **kwargs: Any) -> None:
            if self.first and "/objects/" in key:
                self.first = False
                super().put(key, source, **kwargs)
                raise ShardPublishConflictError("injected concurrent writer")
            super().put(key, source, **kwargs)

    backend = ConcurrentBackend()
    result = publish_shard(_options(tmp_path), backend)

    assert result.status == "published"
    assert result.reused_objects == 1


def test_verification_subject_mismatch_is_a_stable_conflict(tmp_path: Path) -> None:
    options = _options(tmp_path)
    verification: dict[str, Any] = dict(options.verification)
    verification["signatures"] = [
        {**options.verification["signatures"][0], "subject_sha256": "f" * 64}
    ]
    mismatched = PublishShardOptions(
        source_shard=options.source_shard,
        staging_dir=options.staging_dir,
        public_base_url=options.public_base_url,
        verification=verification,
    )

    with pytest.raises(ShardPublishConflictError) as raised:
        build_published_shard(mismatched)
    assert getattr(raised.value, "code", None) == "fura.publish_shard.conflict"
