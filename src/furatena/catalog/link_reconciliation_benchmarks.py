"""Deterministic scale profile for incremental shard link reconciliation."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
import time
import tracemalloc
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.link_reconciliation import (
    IncrementalLinkIndex,
    LinkNode,
    OutboundLink,
    ShardLinkSnapshot,
)


def run_link_reconciliation_benchmark(
    *,
    mounts: int = 400,
    pages_per_shard: int = 300,
) -> dict[str, Any]:
    """Measure one replacement against a deterministic ring of shard links."""
    assert_free_threading()
    if mounts < 2 or pages_per_shard < 1:
        raise ValueError("Link reconciliation requires at least 2 mounts and 1 page per shard.")
    snapshots: dict[str, ShardLinkSnapshot] = {}
    for index in range(mounts):
        snapshot = _snapshot(
            index,
            mounts=mounts,
            pages=pages_per_shard,
            fingerprint="v1",
            target_offset=0,
        )
        snapshots[snapshot.key] = snapshot
    with TemporaryDirectory(prefix="furatena-link-reconciliation-") as temporary:
        state_root = Path(temporary) / "v1"
        index = IncrementalLinkIndex(state_root)
        started = time.perf_counter()
        initial_metrics = index.reconcile(snapshots)
        initial_ms = (time.perf_counter() - started) * 1000

        changed = _snapshot(
            0,
            mounts=mounts,
            pages=pages_per_shard,
            fingerprint="v2",
            target_offset=1,
        )
        tracemalloc.start()
        started = time.perf_counter()
        delta_metrics = index.reconcile({changed.key: changed})
        delta_ms = (time.perf_counter() - started) * 1000
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        state_bytes = sum(path.stat().st_size for path in state_root.rglob("*.json"))
        changed_state_bytes = (
            (state_root / "shards" / f"{hashlib.sha256(changed.key.encode()).hexdigest()}.json")
            .stat()
            .st_size
        )

    total_edges = mounts * pages_per_shard
    return {
        "schema_version": 1,
        "benchmark": "incremental-shard-link-reconciliation",
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "gil_enabled": bool(sys._is_gil_enabled()),
        },
        "workload": {
            "mounts": mounts,
            "pages_per_shard": pages_per_shard,
            "nodes": total_edges,
            "edges": total_edges,
            "topology": "one outbound edge per page to the next mount",
        },
        "initial": {"elapsed_ms": round(initial_ms, 3), **initial_metrics},
        "delta": {
            "elapsed_ms": round(delta_ms, 3),
            "peak_allocated_bytes": peak_bytes,
            "state_record_bytes": changed_state_bytes,
            "neighborhood_fraction": round(delta_metrics["neighborhood_edges"] / total_edges, 6),
            **delta_metrics,
        },
        "persistence": {
            "format": "atomic-sharded-json",
            "total_state_bytes": state_bytes,
        },
    }


def write_link_reconciliation_report(report: dict[str, Any], output: Path | None) -> str:
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    return text


def _snapshot(
    index: int,
    *,
    mounts: int,
    pages: int,
    fingerprint: str,
    target_offset: int,
) -> ShardLinkSnapshot:
    mount = f"m{index:03d}"
    target_mount = f"m{(index + 1) % mounts:03d}"
    nodes = tuple(
        LinkNode(
            node_id=f"{mount}:latest:{page}",
            mount=mount,
            edition="latest",
            title=f"{mount} page {page}",
            url=f"/{mount}/{page}/",
        )
        for page in range(pages)
    )
    outbound = tuple(
        OutboundLink(
            source_id=node.node_id,
            source_mount=mount,
            source_edition="latest",
            source_title=node.title,
            source_url=node.url,
            target_url=f"/{target_mount}/{(page + target_offset) % pages}/",
            target_mount=target_mount,
        )
        for page, node in enumerate(nodes)
    )
    return ShardLinkSnapshot(
        key=f"{mount}:latest",
        mount=mount,
        edition="latest",
        fingerprint=fingerprint,
        nodes=nodes,
        outbound=outbound,
    )
