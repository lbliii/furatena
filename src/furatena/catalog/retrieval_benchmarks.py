"""Known-answer quality and cost benchmarks for retrieval algorithms."""

from __future__ import annotations

import json
import platform
import statistics
import sys
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from furatena.catalog.access import AccessPermission, accessible_nodes
from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.embedding_providers import EmbeddingSearchIndex
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.registry import CatalogRegistry, MountConfig
from furatena.catalog.retrieval_dataset import (
    KnownAnswerCase,
    KnownAnswerDataset,
    load_known_answer_dataset,
)
from furatena.catalog.search import search_nodes
from furatena.catalog.semantic import hybrid_search

_ALGORITHMS = ("keyword", "tfidf", "hybrid", "reranked_hybrid")


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """Shared benchmark filter contract for every ranking algorithm."""

    mount: str | None = None
    edition: str | None = None
    tag: str | None = None
    url_prefix: str | None = None
    include_private: bool = False


_NO_FILTERS = RetrievalFilters()


def run_retrieval_algorithm(
    algorithm: str,
    catalog: Any,
    index: EmbeddingSearchIndex,
    query: str,
    *,
    filters: RetrievalFilters = _NO_FILTERS,
    limit: int = 12,
) -> tuple[str, ...]:
    """Return ranked page identities for one benchmark algorithm."""
    if algorithm not in _ALGORITHMS:
        raise ValueError(f"unknown retrieval algorithm: {algorithm}")
    nodes = _filtered_nodes(catalog, filters)
    allowed = {node.node_id for node in nodes}
    if algorithm == "keyword":
        documents = catalog.ast_documents() if hasattr(catalog, "ast_documents") else None
        return tuple(
            hit.node.node_id
            for hit in search_nodes(nodes, query, limit=limit, documents=documents)
        )
    if algorithm == "tfidf":
        ranked: list[str] = []
        for hit in index.search(
            query,
            limit=max(limit * 3, 64),
            mount=filters.mount,
            edition=filters.edition,
        ):
            node_id = hit.chunk.node_id
            if node_id in allowed and node_id not in ranked:
                ranked.append(node_id)
                if len(ranked) == limit:
                    break
        return tuple(ranked)

    result = hybrid_search(
        catalog,
        index,
        query,
        limit=limit,
        mount=filters.mount,
        edition=filters.edition,
        tag=filters.tag,
        url_prefix=filters.url_prefix,
        include_private=filters.include_private,
        ranking="additive" if algorithm == "hybrid" else "keyword_guarded",
    )
    return tuple(hit.node.node_id for hit in result.hits)


def run_retrieval_benchmarks(
    docs_app: Any,
    *,
    dataset: KnownAnswerDataset | None = None,
    repeats: int = 3,
    limit: int = 12,
) -> dict[str, Any]:
    """Compare quality, latency, memory, index size, and filter conformance."""
    assert_free_threading()
    if repeats < 1 or limit < 3:
        raise ValueError("repeats must be positive and limit must be at least 3")
    active_dataset = dataset or load_known_answer_dataset()
    catalog = docs_app.catalog
    index = docs_app.embedding_index
    cases = tuple(
        case
        for case in active_dataset.cases
        if active_dataset.corpus(case.corpus).kind == "repository"
    )
    index_json_bytes = len(
        json.dumps(index.to_json(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    index_memory_bytes = _deep_size(index)

    algorithms: dict[str, Any] = {}
    for algorithm in _ALGORITHMS:
        samples: list[float] = []
        observations: dict[str, tuple[str, ...]] = {}
        for _ in range(repeats):
            started = time.perf_counter()
            for case in cases:
                observations[case.id] = run_retrieval_algorithm(
                    algorithm,
                    catalog,
                    index,
                    case.query,
                    limit=limit,
                )
            samples.append((time.perf_counter() - started) * 1000.0 / max(len(cases), 1))
        algorithms[algorithm] = {
            "quality": _quality_metrics(cases, observations),
            "latency_ms_per_query": _measurement(samples),
            "estimated_index_memory_bytes": 0 if algorithm == "keyword" else index_memory_bytes,
            "serialized_index_bytes": 0 if algorithm == "keyword" else index_json_bytes,
        }

    filters = _filter_conformance(active_dataset, catalog, index)
    return {
        "schema_version": 1,
        "dataset": {"id": active_dataset.dataset_id, "version": active_dataset.version},
        "methodology": {
            "algorithms": list(_ALGORITHMS),
            "clock": "time.perf_counter",
            "latency_statistic": "median",
            "memory_method": "recursive sys.getsizeof estimate",
            "index_size_method": "compact semantic index JSON bytes",
            "repeats": repeats,
            "result_limit": limit,
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "gil_enabled": sys._is_gil_enabled(),
        },
        "algorithms": algorithms,
        "filters": filters,
        "default_algorithm": "reranked_hybrid",
        "tuning_controls": {
            "limit": limit,
            "semantic_candidate_limit": "max(limit * 3, 64)",
            "hybrid_ranking": "keyword_guarded",
            "filters": ["mount", "edition", "tag", "url_prefix", "include_private"],
        },
    }


def write_retrieval_benchmark_report(report: dict[str, Any], output: Path | None) -> str:
    """Serialize a retrieval benchmark report and optionally persist it."""
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    return payload


def _filtered_nodes(catalog: Any, filters: RetrievalFilters) -> list[Any]:
    nodes = accessible_nodes(
        catalog,
        catalog.doc_nodes(),
        permission=AccessPermission.SEARCH,
        include_private=filters.include_private,
    )
    return [node for node in nodes if _matches_filters(node, filters)]


def _matches_filters(node: Any, filters: RetrievalFilters) -> bool:
    if filters.mount is not None and node.mount != filters.mount:
        return False
    if filters.edition is not None and node.edition != filters.edition:
        return False
    if filters.tag is not None and filters.tag not in node.tags:
        return False
    return not (
        filters.url_prefix is not None and not node.url.startswith(filters.url_prefix)
    )


def _quality_metrics(
    cases: tuple[KnownAnswerCase, ...],
    observations: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    retrieval_cases = tuple(case for case in cases if case.result_policy != "no_results")
    negative_cases = tuple(case for case in cases if case.result_policy == "no_results")
    reciprocal_ranks: list[float] = []
    recalled = 0
    details: list[dict[str, Any]] = []
    for case in retrieval_cases:
        relevant = {
            target.node_id for target in (*case.targets, *case.acceptable_alternatives)
        }
        rank = next(
            (
                index
                for index, node_id in enumerate(observations.get(case.id, ()), start=1)
                if node_id in relevant
            ),
            None,
        )
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        recalled += int(rank is not None and rank <= 3)
        details.append({"case_id": case.id, "relevant_rank": rank})
    no_results = sum(not observations.get(case.id) for case in negative_cases)
    return {
        "case_count": len(cases),
        "retrieval_case_count": len(retrieval_cases),
        "negative_case_count": len(negative_cases),
        "recall_at_3": _ratio(recalled, len(retrieval_cases)),
        "mrr": round(statistics.fmean(reciprocal_ranks), 6) if reciprocal_ranks else None,
        "no_result_rate": _ratio(no_results, len(negative_cases)),
        "cases": details,
    }


def _filter_conformance(
    dataset: KnownAnswerDataset,
    catalog: Any,
    index: EmbeddingSearchIndex,
) -> dict[str, Any]:
    public_nodes = _filtered_nodes(catalog, RetrievalFilters())
    repository = next(corpus for corpus in dataset.corpora if corpus.kind == "repository")
    mount_candidate = next(node for node in public_nodes if node.mount == repository.mount)
    edition_candidate = mount_candidate
    tag_candidate = next(node for node in public_nodes if node.tags)
    url_candidate = next(node for node in public_nodes if node.url.startswith("/docs/"))
    specs = {
        "mount": (mount_candidate, RetrievalFilters(mount=mount_candidate.mount)),
        "edition": (edition_candidate, RetrievalFilters(edition=edition_candidate.edition)),
        "tag": (tag_candidate, RetrievalFilters(tag=sorted(tag_candidate.tags)[0])),
        "url": (url_candidate, RetrievalFilters(url_prefix="/docs/")),
    }
    report: dict[str, Any] = {}
    for name, (candidate, filters) in specs.items():
        algorithms: dict[str, Any] = {}
        for algorithm in _ALGORITHMS:
            results = run_retrieval_algorithm(
                algorithm,
                catalog,
                index,
                candidate.title,
                filters=filters,
            )
            resolved = [catalog.get_by_node_id(node_id) for node_id in results]
            passed = bool(resolved) and all(
                node is not None and _matches_filters(node, filters) for node in resolved
            )
            algorithms[algorithm] = {"passed": passed, "result_count": len(results)}
        report[name] = {"filters": _filters_dict(filters), "algorithms": algorithms}
    report["access"] = _access_filter_conformance(dataset)
    report["all_passed"] = all(
        result["passed"]
        for item in report.values()
        if isinstance(item, dict) and "algorithms" in item
        for result in item["algorithms"].values()
    ) and bool(report["access"]["all_passed"])
    return report


def _access_filter_conformance(dataset: KnownAnswerDataset) -> dict[str, Any]:
    corpus = next(item for item in dataset.corpora if item.kind == "synthetic")
    case = next(item for item in dataset.cases if item.corpus == corpus.id)
    target_ids = {target.node_id for target in case.targets}
    resource = resources.files("furatena.catalog").joinpath(corpus.root)
    with resources.as_file(resource) as content_root:
        root = Path(content_root)
        catalog = CatalogRegistry(
            (
                MountConfig(
                    id=corpus.mount,
                    label=corpus.id,
                    content_root=root,
                    url_prefix=corpus.url_prefix,
                    default=True,
                ),
            ),
            repo_root=root,
            app_root=root,
            include_private=True,
            autodoc=False,
        )
        index = EmbeddingIndex.from_nodes(list(catalog.nodes))
        algorithms: dict[str, Any] = {}
        for algorithm in _ALGORITHMS:
            public = run_retrieval_algorithm(algorithm, catalog, index, case.query)
            trusted = run_retrieval_algorithm(
                algorithm,
                catalog,
                index,
                case.query,
                filters=RetrievalFilters(include_private=True),
            )
            algorithms[algorithm] = {
                "passed": not (target_ids & set(public)) and bool(target_ids & set(trusted)),
                "public_target_count": len(target_ids & set(public)),
                "trusted_target_count": len(target_ids & set(trusted)),
            }
    return {
        "filters": {"include_private": False},
        "algorithms": algorithms,
        "all_passed": all(item["passed"] for item in algorithms.values()),
    }


def _filters_dict(filters: RetrievalFilters) -> dict[str, Any]:
    return {
        "mount": filters.mount,
        "edition": filters.edition,
        "tag": filters.tag,
        "url_prefix": filters.url_prefix,
        "include_private": filters.include_private,
    }


def _measurement(samples: list[float]) -> dict[str, Any]:
    return {
        "median": round(statistics.median(samples), 6),
        "samples": [round(value, 6) for value in samples],
        "unit": "ms",
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _deep_size(value: Any, seen: set[int] | None = None) -> int:
    visited = seen if seen is not None else set()
    identity = id(value)
    if identity in visited:
        return 0
    visited.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        return size + sum(
            _deep_size(key, visited) + _deep_size(item, visited)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return size + sum(_deep_size(item, visited) for item in value)
    if hasattr(value, "__dict__"):
        size += _deep_size(vars(value), visited)
    for slot in getattr(type(value), "__slots__", ()):
        if hasattr(value, slot):
            size += _deep_size(getattr(value, slot), visited)
    return size
