"""Benchmark harness contracts and small-corpus smoke coverage."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from furatena.catalog.benchmarks import (
    BenchmarkCorpus,
    assert_free_threading,
    benchmark_corpus,
    generate_synthetic_corpus,
)


def test_harness_explains_gil_enabled_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "_is_gil_enabled", lambda: True)

    with pytest.raises(RuntimeError, match=r"free-threaded CPython.*PYTHON_GIL=0"):
        assert_free_threading()


def test_synthetic_generator_is_deterministic_and_parameterized(tmp_path: Path) -> None:
    first = generate_synthetic_corpus(tmp_path / "first", page_count=3)
    second = generate_synthetic_corpus(tmp_path / "second", page_count=3)

    first_payloads = [path.read_bytes() for path in sorted(first.glob("*.md"))]
    second_payloads = [path.read_bytes() for path in sorted(second.glob("*.md"))]
    assert first_payloads == second_payloads
    assert len(first_payloads) == 3


def test_benchmark_corpus_reports_all_operations(tmp_path: Path) -> None:
    content_root = generate_synthetic_corpus(tmp_path / "source", page_count=3)
    report = benchmark_corpus(
        BenchmarkCorpus("synthetic", content_root),
        repo_root=Path(__file__).resolve().parents[1],
        workspace=tmp_path / "workspace",
        repeats=1,
        query_iterations=1,
    )

    assert report["page_count"] == 3
    timings = report["timings_ms"]
    assert isinstance(timings, dict)
    assert set(timings) == {
        "full_index",
        "incremental_reindex",
        "freeze",
        "catalog_query",
        "search",
    }
    assert all(
        isinstance(measurement, dict) and measurement["median"] >= 0
        for measurement in timings.values()
    )
