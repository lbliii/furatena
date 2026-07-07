"""Known-answer retrieval algorithm and filter benchmark contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.retrieval_benchmarks import (
    RetrievalFilters,
    run_retrieval_algorithm,
    run_retrieval_benchmarks,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def docs() -> DocsApp:
    return DocsApp.from_paths(
        REPO / "app" / "docs.yaml",
        repo_root=REPO,
        autodoc_config=REPO / "config" / "autodoc.yaml",
        autodoc=True,
    )


def test_benchmark_compares_quality_cost_and_all_required_filters(docs: DocsApp) -> None:
    report = run_retrieval_benchmarks(docs, repeats=1, limit=12)

    assert report["environment"]["gil_enabled"] is False
    assert report["default_algorithm"] == "reranked_hybrid"
    assert set(report["algorithms"]) == {
        "keyword",
        "tfidf",
        "hybrid",
        "reranked_hybrid",
    }
    for result in report["algorithms"].values():
        assert result["quality"]["recall_at_3"] is not None
        assert result["quality"]["mrr"] is not None
        assert result["latency_ms_per_query"]["median"] >= 0
        assert result["estimated_index_memory_bytes"] >= 0
        assert result["serialized_index_bytes"] >= 0
    assert (
        report["algorithms"]["reranked_hybrid"]["quality"]["mrr"]
        >= report["algorithms"]["hybrid"]["quality"]["mrr"]
    )
    assert report["filters"]["all_passed"] is True
    assert set(report["filters"]) >= {"mount", "edition", "tag", "url", "access"}
    for name in ("mount", "edition", "tag", "url", "access"):
        assert all(
            item["passed"] for item in report["filters"][name]["algorithms"].values()
        )


def test_url_filter_applies_to_keyword_tfidf_and_both_hybrid_modes(docs: DocsApp) -> None:
    filters = RetrievalFilters(url_prefix="/docs/")
    for algorithm in ("keyword", "tfidf", "hybrid", "reranked_hybrid"):
        node_ids = run_retrieval_algorithm(
            algorithm,
            docs.catalog,
            docs.embedding_index,
            "installation",
            filters=filters,
        )
        assert node_ids
        nodes = [docs.catalog.get_by_node_id(node_id) for node_id in node_ids]
        assert all(node is not None for node in nodes)
        assert all(node.url.startswith("/docs/") for node in nodes if node is not None)


def test_unknown_algorithm_and_invalid_benchmark_bounds_fail_loudly(docs: DocsApp) -> None:
    with pytest.raises(ValueError, match="unknown retrieval algorithm"):
        run_retrieval_algorithm("mystery", docs.catalog, docs.embedding_index, "query")
    with pytest.raises(ValueError, match="repeats must be positive"):
        run_retrieval_benchmarks(docs, repeats=0)
