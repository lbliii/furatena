"""Portable topology contracts for the federated-search profile."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, cast


def test_profile_registers_four_editions_but_fans_out_only_current_shards() -> None:
    namespace = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "benchmarks" / "federated_search.py")
    )
    benchmark_scenario = cast(Any, namespace["benchmark_scenario"])

    report = benchmark_scenario(
        mounts=2,
        documents_per_mount=2,
        repeats=1,
        workers=16,
        limit=4,
    )

    assert report["editions_per_mount"] == 4
    assert report["registered_shards"] == 8
    assert report["selected_shards"] == report["fanout"] == 2
    assert report["filtered_before_fanout"] == 6
    assert report["workers"] == 2
    assert report["selected_resident_index_bytes"] > report["selected_serialized_index_bytes"]
    assert len(report["resident_accounting_ms"]["samples"]) == 1
    assert report["lifecycle_topology"] == {
        "latest": "current",
        "1.2": "legacy",
        "next": "preview",
        "0.9": "eol",
    }
    assert all(":latest:" in node_id for node_id in report["deterministic_result_ids"])
