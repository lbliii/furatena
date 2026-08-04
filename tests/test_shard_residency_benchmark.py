"""Structural contracts for the tiered shard residency benchmark."""

from __future__ import annotations

from pathlib import Path

from furatena.catalog.shard_residency_benchmarks import run_shard_residency_benchmark


def test_shard_residency_benchmark_routes_before_materialization(tmp_path: Path) -> None:
    report = run_shard_residency_benchmark(
        tmp_path,
        mounts=3,
        editions=2,
        pages_per_shard=5,
        resident_shards=1,
        resident_bytes=2 * 1024 * 1024,
    )

    assert report["corpus"] == {
        "mounts": 3,
        "editions_per_mount": 2,
        "pages_per_shard": 5,
        "total_shards": 6,
        "logical_pages": 30,
    }
    assert report["correctness"] == {
        "materialized_shards_after_first_route": 1,
        "materialized_pages_after_first_route": 5,
        "irrelevant_shards_not_materialized": 5,
        "loaded_identities": ["m001:e1"],
        "resident_entries_within_bound": True,
        "resident_bytes_within_bound": True,
    }
    assert report["measurements"]["cold_first_route_ms"] >= 0
    assert report["measurements"]["admission_accounting_ms"] >= 0
    assert report["measurements"]["accounted_resident_bytes"] > 0
