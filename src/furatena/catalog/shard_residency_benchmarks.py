"""Deterministic synthetic scale measurement for tiered shard residency."""

from __future__ import annotations

import json
import platform
import sys
import time
import tracemalloc
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.registry import (
    CatalogRegistry,
    MountConfig,
    _doc_catalog_resident_size,
)
from furatena.catalog.sources.types import MountSourceConfig


class _SyntheticCatalog(Mapping[str, Any]):
    """Cold immutable object-store stand-in materialized only on first read."""

    def __init__(self, mount: str, edition: str, pages: int, loads: list[str]) -> None:
        self.mount = mount
        self.edition = edition
        self.pages = pages
        self.loads = loads
        self._value: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._value is None:
            self.loads.append(f"{self.mount}:{self.edition}")
            self._value = {
                "schema_version": 3,
                "channel": self.edition,
                "edition": self.edition,
                "mount": self.mount,
                "page_count": self.pages,
                "pages": [
                    {
                        "edition": self.edition,
                        "mount": self.mount,
                        "node_id": f"{self.mount}:{self.edition}:page-{index:03d}",
                        "slug": f"page-{index:03d}",
                        "title": f"{self.mount} page {index:03d}",
                        "url": f"/{self.mount}/page-{index:03d}/",
                        "body_text": f"Synthetic immutable page {index:03d}.",
                        "content": {
                            "directives": [],
                            "extensions": [],
                            "headings": [],
                            "links": [],
                        },
                    }
                    for index in range(self.pages)
                ],
                "edges": [],
                "namespaces": [],
            }
        return self._value

    def __getitem__(self, key: str) -> Any:
        return self._load()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._load())

    def __len__(self) -> int:
        return len(self._load())


@dataclass(frozen=True, slots=True)
class _SyntheticGeneration:
    generation_id: str
    shards: Mapping[str, Any]


class _SyntheticRemoteRegistry:
    def __init__(self, *, mounts: int, editions: int, pages: int) -> None:
        self.loads: list[str] = []
        self._mounts = tuple(f"m{index:03d}" for index in range(mounts))
        self._shards: dict[str, Any] = {}
        for mount in self._mounts:
            for edition_index in range(editions):
                edition = f"e{edition_index}"
                identity = f"{mount}:{edition}"
                self._shards[identity] = SimpleNamespace(
                    identity=identity,
                    mount=mount,
                    edition=edition,
                    fingerprint=f"synthetic-{identity}",
                    catalog=_SyntheticCatalog(mount, edition, pages, self.loads),
                )

    def mounts(self) -> tuple[str, ...]:
        return self._mounts

    def shard(self, mount: str, edition: str = "latest") -> Any:
        selected = "e0" if edition in {"latest", "stable"} else edition
        return self._shards[f"{mount}:{selected}"]

    def generation(self, mount: str) -> _SyntheticGeneration:
        return _SyntheticGeneration(
            f"synthetic:{mount}",
            {
                identity: shard
                for identity, shard in self._shards.items()
                if identity.startswith(f"{mount}:")
            }
        )

    def fetch_presentation_from(self, shard: Any, node_id: str) -> bytes:
        _ = shard, node_id
        return b"<article>synthetic</article>"

    def residency_status(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "resident_entries": 0,
            "resident_bytes": 0,
            "max_resident_entries": 0,
            "max_resident_bytes": 0,
            "in_flight": 0,
            "hot_hits": 0,
            "warm_loads": 0,
            "cold_loads": len(self.loads),
            "evictions": 0,
            "coalesced_loads": 0,
            "load_failures": 0,
            "shards": [],
        }


def run_shard_residency_benchmark(
    repo_root: Path,
    *,
    mounts: int = 100,
    editions: int = 4,
    pages_per_shard: int = 300,
    resident_shards: int = 8,
    resident_bytes: int = 64 * 1024 * 1024,
) -> dict[str, Any]:
    """Measure descriptor scale and one cold O(1) mount:edition route."""
    assert_free_threading()
    if min(mounts, editions, pages_per_shard, resident_shards, resident_bytes) <= 0:
        raise ValueError("Shard-residency benchmark dimensions and bounds must be positive.")
    tracemalloc.start()
    started = time.perf_counter_ns()
    remote = _SyntheticRemoteRegistry(
        mounts=mounts,
        editions=editions,
        pages=pages_per_shard,
    )
    mount_configs = tuple(
        MountConfig(
            id=mount,
            label=mount,
            content_root=repo_root / ".synthetic-remote" / mount,
            url_prefix=f"/{mount}",
            default=index == 0,
            source=MountSourceConfig(provider="remote-shard"),
        )
        for index, mount in enumerate(remote.mounts())
    )
    registry = CatalogRegistry(
        mount_configs,
        repo_root=repo_root,
        app_root=repo_root,
        autodoc=False,
        remote_shards=cast(Any, remote),
        remote_resident_shard_entries=resident_shards,
        remote_resident_shard_bytes=resident_bytes,
    )
    descriptor_ns = time.perf_counter_ns() - started
    descriptor_current, descriptor_peak = tracemalloc.get_traced_memory()
    total_shards = mounts * editions

    target_mount = remote.mounts()[mounts // 2]
    target_edition = f"e{editions - 1}"
    node_id = f"{target_mount}:{target_edition}:page-000"
    cold_started = time.perf_counter_ns()
    node = registry.get_by_node_id(node_id)
    cold_ns = time.perf_counter_ns() - cold_started
    if node is None or node.node_id != node_id:
        raise RuntimeError(f"Synthetic shard route did not resolve {node_id}.")
    first_route_loads = tuple(remote.loads)
    after_cold_current, after_cold_peak = tracemalloc.get_traced_memory()

    hot_started = time.perf_counter_ns()
    hot_node = registry.get_by_node_id(node_id)
    hot_ns = time.perf_counter_ns() - hot_started
    if hot_node is not node:
        raise RuntimeError("Hot shard route did not reuse its immutable node.")

    with registry.use_edition(target_edition):
        shard = registry._active_shard(target_mount)
    if shard is None:
        raise RuntimeError(
            "Synthetic resident shard disappeared after routing; inspect residency status."
        )
    accounting_started = time.perf_counter_ns()
    accounted_bytes = _doc_catalog_resident_size(shard)
    accounting_ns = time.perf_counter_ns() - accounting_started
    status = registry.remote_residency_status()["composed"]
    tracemalloc.stop()
    return {
        "schema_version": 1,
        "environment": {
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "gil_enabled": bool(sys._is_gil_enabled()),
        },
        "corpus": {
            "mounts": mounts,
            "editions_per_mount": editions,
            "pages_per_shard": pages_per_shard,
            "total_shards": total_shards,
            "logical_pages": total_shards * pages_per_shard,
        },
        "methodology": {
            "clock": "time.perf_counter_ns",
            "memory": "tracemalloc Python allocations plus cycle-safe sys.getsizeof admission accounting",
            "origin": "deterministic in-process immutable object-store stand-in; network excluded",
            "route": "mount:edition:node split plus O(1) mount and shard lookup before catalog mapping access",
            "resident_shard_limit": resident_shards,
            "resident_byte_limit": resident_bytes,
        },
        "measurements": {
            "descriptor_setup_ms": round(descriptor_ns / 1_000_000, 6),
            "descriptor_current_bytes": descriptor_current,
            "descriptor_peak_bytes": descriptor_peak,
            "cold_first_route_ms": round(cold_ns / 1_000_000, 6),
            "hot_route_ms": round(hot_ns / 1_000_000, 6),
            "after_cold_current_bytes": after_cold_current,
            "after_cold_peak_bytes": after_cold_peak,
            "admission_accounting_ms": round(accounting_ns / 1_000_000, 6),
            "accounted_resident_bytes": accounted_bytes,
        },
        "correctness": {
            "materialized_shards_after_first_route": len(first_route_loads),
            "materialized_pages_after_first_route": len(first_route_loads) * pages_per_shard,
            "irrelevant_shards_not_materialized": total_shards - len(first_route_loads),
            "loaded_identities": list(first_route_loads),
            "resident_entries_within_bound": status["resident_entries"] <= resident_shards,
            "resident_bytes_within_bound": status["resident_bytes"] <= resident_bytes,
        },
    }


def write_shard_residency_report(report: Mapping[str, Any], output: Path | None) -> str:
    """Serialize one report and optionally persist the exact printed bytes."""
    encoded = json.dumps(dict(report), indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    return encoded
