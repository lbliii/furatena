"""Repeatable performance benchmarks for catalog indexing and delivery paths."""

from __future__ import annotations

import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog
from furatena.catalog.loader import DocCatalog
from furatena.catalog.query import query_catalog_graph
from furatena.catalog.search import search_nodes


@dataclass(frozen=True, slots=True)
class BenchmarkCorpus:
    """One isolated documentation corpus measured by the harness."""

    name: str
    content_root: Path


def generate_synthetic_corpus(root: Path, *, page_count: int) -> Path:
    """Generate a deterministic, linked Markdown corpus of ``page_count`` pages."""
    if page_count < 1:
        raise ValueError("page_count must be at least 1")
    content_root = root / "content"
    content_root.mkdir(parents=True, exist_ok=True)
    for index in range(page_count):
        previous = (index - 1) % page_count
        following = (index + 1) % page_count
        (content_root / f"page-{index:05d}.md").write_text(
            "\n".join(
                (
                    "---",
                    f"title: Benchmark page {index}",
                    "description: Deterministic synthetic benchmark content.",
                    "tags: [benchmark, synthetic]",
                    f"weight: {index}",
                    "---",
                    "",
                    f"# Benchmark page {index}",
                    "",
                    "This page exercises catalog parsing, graph edges, query filtering, and search.",
                    "",
                    f"[Previous](page-{previous:05d}.md) · [Next](page-{following:05d}.md)",
                    "",
                    "## Stable section",
                    "",
                    f"Synthetic documentation payload number {index} for repeatable measurements.",
                    "",
                )
            ),
            encoding="utf-8",
        )
    return content_root


def assert_free_threading() -> None:
    """Fail clearly when the harness is not running with the GIL disabled."""
    probe = getattr(sys, "_is_gil_enabled", None)
    if probe is None or probe():
        raise RuntimeError(
            "catalog benchmarks require a free-threaded CPython build with PYTHON_GIL=0"
        )


def benchmark_corpus(
    corpus: BenchmarkCorpus,
    *,
    repo_root: Path,
    workspace: Path,
    repeats: int = 3,
    query_iterations: int = 10,
) -> dict[str, object]:
    """Measure cold index/freeze and warm incremental/query/search paths."""
    if repeats < 1 or query_iterations < 1:
        raise ValueError("repeats and query_iterations must be at least 1")

    def build_catalog() -> DocCatalog:
        return DocCatalog(
            corpus.content_root,
            auto_reload=True,
            autodoc=False,
            mount=corpus.name,
            workers=1,
        )

    index_samples: list[float] = []
    catalog: DocCatalog | None = None
    for _ in range(repeats):
        elapsed, catalog = _timed(build_catalog)
        index_samples.append(elapsed)
    assert catalog is not None

    timings = {
        "full_index": _measurement(index_samples),
        "incremental_reindex": _measurement(
            _measure_incremental(catalog, corpus.content_root, repeats=repeats)
        ),
        "freeze": _measurement(
            _measure_freeze(
                corpus,
                repo_root=repo_root,
                workspace=workspace,
                repeats=repeats,
            )
        ),
        "catalog_query": _measurement(
            _measure_repeated(
                lambda: query_catalog_graph(catalog, mount=corpus.name),
                repeats=repeats,
                iterations=query_iterations,
            )
        ),
        "search": _measurement(
            _measure_repeated(
                lambda: search_nodes(
                    list(catalog.nodes),
                    "documentation",
                    documents=catalog.ast_documents(),
                ),
                repeats=repeats,
                iterations=query_iterations,
            )
        ),
    }
    return {
        "name": corpus.name,
        "page_count": len(catalog.nodes),
        "source_file_count": len(tuple(corpus.content_root.rglob("*.md"))),
        "timings_ms": timings,
    }


def run_benchmarks(
    repo_root: Path,
    *,
    synthetic_pages: int = 250,
    repeats: int = 3,
    query_iterations: int = 10,
) -> dict[str, object]:
    """Benchmark isolated dogfood and synthetic corpora and return a JSON-safe report."""
    assert_free_threading()
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="furatena-benchmark-") as raw_workspace:
        workspace = Path(raw_workspace).resolve()
        dogfood_root = workspace / "dogfood"
        shutil.copytree(repo_root / "content" / "furatena", dogfood_root)
        synthetic_root = generate_synthetic_corpus(
            workspace / "synthetic", page_count=synthetic_pages
        )
        corpora = (
            BenchmarkCorpus("dogfood", dogfood_root),
            BenchmarkCorpus("synthetic", synthetic_root),
        )
        results = [
            benchmark_corpus(
                corpus,
                repo_root=repo_root,
                workspace=workspace / corpus.name,
                repeats=repeats,
                query_iterations=query_iterations,
            )
            for corpus in corpora
        ]

    return {
        "schema_version": 1,
        "methodology": {
            "clock": "time.perf_counter",
            "statistic": "median",
            "cold_operations": ["full_index", "freeze"],
            "warm_operations": ["incremental_reindex", "catalog_query", "search"],
            "repeats": repeats,
            "query_iterations_per_repeat": query_iterations,
            "workers": 1,
            "autodoc": False,
            "corpus_isolation": "temporary copy",
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "gil_enabled": sys._is_gil_enabled(),
            "cpu_count": os.cpu_count(),
        },
        "elapsed_seconds": round(time.time() - started, 6),
        "corpora": results,
    }


def write_report(report: dict[str, object], output: Path | None = None) -> str:
    """Serialize a benchmark report and optionally persist it."""
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    return payload


def _timed[T](function: Callable[[], T]) -> tuple[float, T]:
    started = time.perf_counter()
    result = function()
    return (time.perf_counter() - started) * 1000.0, result


def _measurement(samples: list[float]) -> dict[str, object]:
    return {
        "median": round(statistics.median(samples), 6),
        "samples": [round(sample, 6) for sample in samples],
        "unit": "ms",
    }


def _bump_mtime(path: Path) -> None:
    stamp = max(time.time_ns(), path.stat().st_mtime_ns + 1_000_000)
    os.utime(path, ns=(stamp, stamp))


def _measure_incremental(catalog: DocCatalog, content_root: Path, *, repeats: int) -> list[float]:
    source = next(iter(sorted(content_root.rglob("*.md"))))
    original = source.read_text(encoding="utf-8")
    samples: list[float] = []
    try:
        for index in range(repeats):
            source.write_text(f"{original}\nBenchmark edit {index}.\n", encoding="utf-8")
            _bump_mtime(source)
            elapsed, refreshed = _timed(catalog.refresh_if_stale)
            if not refreshed:
                raise RuntimeError(f"incremental benchmark did not detect {source}")
            samples.append(elapsed)
            source.write_text(original, encoding="utf-8")
            _bump_mtime(source)
            catalog.refresh_if_stale()
    finally:
        source.write_text(original, encoding="utf-8")
    return samples


def _measure_repeated(
    operation: Callable[[], object], *, repeats: int, iterations: int
) -> list[float]:
    operation()
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(iterations):
            operation()
        samples.append((time.perf_counter() - started) * 1000.0 / iterations)
    return samples


def _measure_freeze(
    corpus: BenchmarkCorpus, *, repo_root: Path, workspace: Path, repeats: int
) -> list[float]:
    app_root = workspace / "app"
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "docs.yaml").write_text("mounts: mounts.yaml\n", encoding="utf-8")
    (app_root / "mounts.yaml").write_text(
        "\n".join(
            (
                "mounts:",
                f"  - id: {corpus.name}",
                f"    label: {corpus.name.title()} benchmark",
                f"    content_root: {json.dumps(str(corpus.content_root))}",
                "    default: true",
                "",
            )
        ),
        encoding="utf-8",
    )
    samples: list[float] = []
    for index in range(repeats):
        output = workspace / f"frozen-{index}"
        options = FreezeCatalogOptions(
            docs_config=app_root / "docs.yaml",
            app_root=app_root,
            repo_root=repo_root,
            output_dir=output,
            full_rebuild=True,
            workers=1,
            autodoc=False,
            allow_lifecycle_errors=True,
        )
        elapsed, _ = _timed(partial(freeze_catalog, options))
        samples.append(elapsed)
    return samples
