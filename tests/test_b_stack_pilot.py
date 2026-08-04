"""Isolated b-stack pilot configuration and remote-evidence contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from furatena.catalog.config import load_docs_config
from furatena.catalog.registry import load_mounts

REPO = Path(__file__).resolve().parents[1]
PILOT_ROOT = REPO / "config" / "pilots" / "b-stack"
MOUNTS_PATH = PILOT_ROOT / "mounts.yaml"
EVIDENCE_PATH = REPO / "docs" / "b-stack-pilot-v1.json"
PRODUCTION_MOUNTS = REPO / "app" / "mounts.yaml"
EXPECTED_IDS = (
    "bengal",
    "chirp",
    "chirp-ui",
    "kida",
    "patitas",
    "pounce",
    "rosettes",
    "zoomies",
)
EXPECTED_RELEASES = {
    "bengal": ("0.5.1", "0.5.0", "0.4.3"),
    "chirp": ("0.10.3", "0.10.2", "0.10.1"),
    "chirp-ui": ("0.11.4", "0.11.3", "0.11.2"),
    "kida": ("0.12.1", "0.12.0", "0.11.0"),
    "patitas": ("0.4.0", "0.3.5", "0.3.4"),
    "pounce": ("0.9.2", "0.9.1", "0.9.0"),
    "rosettes": ("0.2.0",),
    "zoomies": ("0.3.3", "0.3.1", "0.3.0"),
}
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def test_pilot_config_is_exact_public_and_production_isolated() -> None:
    mounts_text = MOUNTS_PATH.read_text(encoding="utf-8")
    raw = yaml.safe_load(mounts_text)
    configured = raw["mounts"]
    mounts = load_mounts(MOUNTS_PATH, repo_root=REPO)

    assert tuple(item.id for item in mounts) == EXPECTED_IDS
    assert len({item.id for item in mounts}) == 8
    assert len({item.url_prefix for item in mounts}) == 8
    assert [item.id for item in mounts if item.default] == ["bengal"]
    assert "purr" not in mounts_text.lower()
    assert "/Users/" not in mounts_text
    assert "~/" not in mounts_text
    assert "git@" not in mounts_text
    assert "token" not in mounts_text.lower()
    production = yaml.safe_load(PRODUCTION_MOUNTS.read_text(encoding="utf-8"))
    assert {item["id"] for item in production["mounts"]}.isdisjoint(EXPECTED_IDS)

    for item, mount in zip(configured, mounts, strict=True):
        expected_repo = f"https://github.com/lbliii/{mount.id}.git"
        assert item["source"] == {
            "provider": "git",
            "repo": expected_repo,
            "ref": "main",
            "path": "site/content",
        }
        parsed = urlsplit(expected_repo)
        assert parsed.scheme == "https"
        assert parsed.hostname == "github.com"
        assert parsed.username is None
        assert mount.editions is not None
        assert mount.editions.source == "tags"
        assert mount.editions.count == 3
        assert mount.editions.pattern == "v*"
        assert mount.editions.strip_prefix == "v"
        assert mount.editions.sort == "semver-desc"
        assert mount.editions.include_prereleases is False
        assert mount.source.git is not None
        assert mount.source.git.sync_root is None

    docs = load_docs_config(PILOT_ROOT / "docs.yaml")
    assert docs.mounts_path == MOUNTS_PATH
    assert docs.mounts_path != load_docs_config(REPO / "app" / "docs.yaml").mounts_path
    assert (PILOT_ROOT / docs.theme.templates).resolve() == REPO / "app" / "theme"


def test_discovery_evidence_matches_bounded_config_and_peeled_shas() -> None:
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    records = evidence["mounts"]

    assert evidence["configuration"] == "config/pilots/b-stack/mounts.yaml"
    assert evidence["discovery"]["bounded"] is True
    assert evidence["discovery"]["release_limit_per_mount"] == 3
    assert tuple(item["id"] for item in records) == EXPECTED_IDS
    assert evidence["expected_edition_shards"] == sum(
        1 + len(EXPECTED_RELEASES[mount_id]) for mount_id in EXPECTED_IDS
    )
    assert evidence["expected_edition_shards"] == 30

    for record in records:
        mount_id = record["id"]
        assert record["repository"] == f"https://github.com/lbliii/{mount_id}.git"
        assert record["default_ref"] == "main"
        assert SHA.fullmatch(record["latest_resolved_ref"])
        releases = record["releases"]
        assert tuple(item["edition"] for item in releases) == EXPECTED_RELEASES[mount_id]
        assert len(releases) <= evidence["discovery"]["release_limit_per_mount"]
        for release in releases:
            assert release["ref"] == f"v{release['edition']}"
            assert SHA.fullmatch(release["resolved_ref"])

    serialized = json.dumps(evidence)
    assert "/Users/" not in serialized
    assert "~/" not in serialized
    assert "token" not in serialized.lower()
    assert "credential" not in serialized.lower()


def test_runtime_proof_covers_every_configured_edition_context() -> None:
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    proof = evidence["proof"]
    freeze = proof["freeze"]
    preview = proof["preview"]
    shard_bytes = freeze["shard_bytes"]

    assert proof["content_check"] == {
        "ok": False,
        "error_count": 20,
        "warning_count": 219,
        "classification": "upstream source diagnostics",
        "pilot_template_error_count": 0,
        "error_categories": {
            "broken_internal_link": 16,
            "unresolved_reference": 1,
            "unknown_collection": 1,
            "directive_nesting": 2,
        },
    }
    assert freeze["ok"] is True
    assert freeze["current_mount_count"] == len(EXPECTED_IDS)
    assert freeze["release_shard_count"] == sum(
        len(EXPECTED_RELEASES[mount_id]) for mount_id in EXPECTED_IDS
    )
    assert freeze["total_edition_context_count"] == evidence["expected_edition_shards"]
    assert set(shard_bytes) == set(EXPECTED_IDS)
    for mount_id in EXPECTED_IDS:
        assert set(shard_bytes[mount_id]) == {"latest", *EXPECTED_RELEASES[mount_id]}
        assert all(size > 0 for size in shard_bytes[mount_id].values())
    assert freeze["output_bytes"] >= sum(
        size for mount in shard_bytes.values() for size in mount.values()
    )
    assert SHA256.fullmatch(freeze["channels_sha256"])
    assert SHA256.fullmatch(freeze["manifest_sha256"])

    assert preview["preflight_ok"] is True
    assert preview["health_status"] == "healthy"
    assert preview["health_http_status"] == 200
    assert preview["mount_count"] == len(EXPECTED_IDS)
    assert preview["page_count"] == freeze["current_page_count"]
    assert preview["stale_freeze"] is False
    assert preview["rss_kib"] > 0
