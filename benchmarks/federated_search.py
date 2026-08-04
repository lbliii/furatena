"""Reproducible pilot and 100-mount federated-search measurements."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.federated_search import (
    FederatedSearchHit,
    FederatedShardSearchIndex,
    build_federated_search_index,
    estimate_federated_search_index_resident_bytes,
    rank_merge_federated_hits,
)

_EDITIONS = (
    ("latest", "current"),
    ("1.2", "legacy"),
    ("next", "preview"),
    ("0.9", "eol"),
)


def _pages(mount: str, edition: str, count: int) -> list[dict[str, Any]]:
    return [
        {
            "node_id": f"{mount}:{edition}:page-{index:04d}",
            "title": f"Shared retrieval guide {index:04d}",
            "description": f"Deterministic federated search example {index:04d} for {mount}.",
            "slug": f"page-{index:04d}",
            "tags": ["retrieval", f"group-{index % 7}"],
            "sections": [
                {
                    "heading": "Query",
                    "text": f"Shared keyword and tfidf retrieval content {index % 13}.",
                }
            ],
        }
        for index in range(count)
    ]


def _query_one(
    item: tuple[str, FederatedShardSearchIndex], query: str, limit: int
) -> tuple[FederatedSearchHit, ...]:
    mount, index = item
    return tuple(
        FederatedSearchHit(
            node_id=hit.node_id,
            title=hit.title,
            snippet=hit.snippet,
            mount=mount,
            edition=index.edition,
            shard_fingerprint=mount,
            score=hit.score,
            keyword_score=hit.keyword_score,
            tfidf_score=hit.tfidf_score,
        )
        for hit in index.search(query, limit=limit)
    )


def _query_all(
    indexes: dict[str, FederatedShardSearchIndex], *, workers: int, limit: int
) -> tuple[FederatedSearchHit, ...]:
    query = "shared retrieval"
    with ThreadPoolExecutor(max_workers=min(workers, len(indexes))) as executor:
        groups = tuple(
            executor.map(
                lambda item: _query_one(item, query, max(limit * 2, 16)),
                sorted(indexes.items()),
            )
        )
    return rank_merge_federated_hits(
        (hit for group in groups for hit in group),
        limit=limit,
    )


def benchmark_scenario(
    *, mounts: int, documents_per_mount: int, repeats: int, workers: int, limit: int
) -> dict[str, Any]:
    registered: dict[str, tuple[str, dict[str, Any]]] = {}
    build_samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        registered = {
            f"{mount}:{edition}": (
                lifecycle,
                build_federated_search_index(
                    _pages(mount, edition, documents_per_mount),
                    mount=mount,
                    edition=edition,
                ),
            )
            for mount in (f"mount-{index:03d}" for index in range(mounts))
            for edition, lifecycle in _EDITIONS
        }
        build_samples.append((time.perf_counter() - started) * 1000)

    # Model the registry's mount/channel/lifecycle selection before any decoded
    # index is parsed or submitted to the worker pool. Default search resolves
    # one current `latest` shard per mount; legacy, preview, and EOL editions
    # remain registered but cannot consume fan-out or cache memory.
    payloads = {
        identity.removesuffix(":latest"): payload
        for identity, (lifecycle, payload) in registered.items()
        if lifecycle == "current" and identity.endswith(":latest")
    }
    if len(payloads) != mounts:
        raise RuntimeError("lifecycle pre-scope did not select exactly one current shard per mount")
    cold_samples: list[float] = []
    warm_samples: list[float] = []
    resident_accounting_samples: list[float] = []
    deterministic_orders: list[tuple[str, ...]] = []
    tracemalloc.start()
    indexes = {mount: FederatedShardSearchIndex(payload) for mount, payload in payloads.items()}
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    for _ in range(repeats):
        cold_started = time.perf_counter()
        indexes = {mount: FederatedShardSearchIndex(payload) for mount, payload in payloads.items()}
        hits = _query_all(indexes, workers=workers, limit=limit)
        cold_samples.append((time.perf_counter() - cold_started) * 1000)
        deterministic_orders.append(tuple(hit.node_id for hit in hits))

        accounting_started = time.perf_counter()
        measured_resident_bytes = sum(
            estimate_federated_search_index_resident_bytes(index) for index in indexes.values()
        )
        resident_accounting_samples.append((time.perf_counter() - accounting_started) * 1000)
        if measured_resident_bytes != sum(index.resident_bytes for index in indexes.values()):
            raise RuntimeError("resident index accounting changed after cold load")

        started = time.perf_counter()
        hits = _query_all(indexes, workers=workers, limit=limit)
        warm_samples.append((time.perf_counter() - started) * 1000)
        deterministic_orders.append(tuple(hit.node_id for hit in hits))
    if len(set(deterministic_orders)) != 1:
        raise RuntimeError("federated rank merge was not deterministic")
    selected_serialized_bytes = sum(
        len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        for payload in payloads.values()
    )
    registered_serialized_bytes = sum(
        len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        for _, payload in registered.values()
    )
    return {
        "mounts": mounts,
        "editions_per_mount": len(_EDITIONS),
        "registered_shards": len(registered),
        "selected_shards": len(payloads),
        "filtered_before_fanout": len(registered) - len(payloads),
        "lifecycle_topology": {edition: lifecycle for edition, lifecycle in _EDITIONS},
        "documents_per_shard": documents_per_mount,
        "fanout": mounts,
        "workers": min(workers, mounts),
        "result_limit": limit,
        "registered_serialized_index_bytes": registered_serialized_bytes,
        "selected_serialized_index_bytes": selected_serialized_bytes,
        "selected_resident_index_bytes": sum(index.resident_bytes for index in indexes.values()),
        "peak_reader_memory_bytes": peak_bytes,
        "resident_accounting_ms": _measurement(resident_accounting_samples),
        "publish_build_ms": _measurement(build_samples),
        "cold_parse_query_merge_ms": _measurement(cold_samples),
        "warm_query_merge_ms": _measurement(warm_samples),
        "deterministic_result_ids": list(deterministic_orders[0]),
    }


def _measurement(samples: list[float]) -> dict[str, Any]:
    return {
        "samples": [round(value, 3) for value in samples],
        "median": round(statistics.median(samples), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents-per-mount", type=int, default=25)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    assert_free_threading()
    report = {
        "schema_version": 1,
        "methodology": {
            "clock": "time.perf_counter",
            "memory": "tracemalloc peak while parsing immutable indexes",
            "transport": "excluded; measures verified decoded-index parse/query/merge",
            "idf": "independent per shard",
            "repeats": args.repeats,
        },
        "pilot": benchmark_scenario(
            mounts=8,
            documents_per_mount=args.documents_per_mount,
            repeats=args.repeats,
            workers=args.workers,
            limit=args.limit,
        ),
        "synthetic_100_mounts": benchmark_scenario(
            mounts=100,
            documents_per_mount=args.documents_per_mount,
            repeats=args.repeats,
            workers=args.workers,
            limit=args.limit,
        ),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
