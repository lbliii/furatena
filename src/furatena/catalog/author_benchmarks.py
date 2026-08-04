"""Author-runtime benchmark and structural regression instrumentation."""

from __future__ import annotations

import asyncio
import cProfile
import io
import json
import os
import platform
import pstats
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, TypedDict, cast
from unittest.mock import patch

from chirp.testing import TestClient
from jsonschema import Draft202012Validator

import furatena.catalog.check as check_module
import furatena.catalog.mcp as mcp_module
import furatena.catalog.render_context as render_context_module
from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.dev_banner import compose_serve_preflight
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.validation import ValidationSnapshotService

AUTHOR_RUNTIME_SCHEMA_VERSION = 1
AUTHOR_RUNTIME_OPERATIONS = (
    "docs_app_construction",
    "startup_contract_checks",
    "author_page_request",
    "author_page_status",
    "author_page_validation",
    "docs_validation",
)
_DEPENDENCIES = (
    "furatena",
    "bengal-chirp",
    "chirp-ui",
    "kida-templates",
    "milo-cli",
    "patitas",
)
_VALIDATION_PHASES = {
    "broken_internal_links": "check_broken_internal_links",
    "unresolved_references": "check_unresolved_references",
    "directive_manifest": "check_directive_manifest",
    "content_lint": "check_content_lint",
    "front_matter": "check_front_matter",
    "lifecycle": "check_lifecycle_sources",
    "cross_edition_links": "check_cross_edition_links",
    "body_link_boost": "check_body_link_boost",
    "directive_template_hrefs": "check_directive_template_hrefs",
    "ast_roundtrip": "check_ast_roundtrip",
    "rendering_heads": "check_rendering_head_contracts",
    "view_config": "check_view_config",
    "view_templates": "check_view_templates_for_config",
    "theme_assets": "check_theme_assets",
    "delivery_config": "check_delivery_config",
    "dcp_schema": "check_dcp_schema",
}


@dataclass(slots=True)
class RuntimeCallCounts:
    """Structural work performed during one measured operation."""

    docs_app_constructions: int = 0
    full_validation_calls: int = 0


class _OperationSample(TypedDict):
    timing_ms: float
    secondary_docs_app_constructions: int
    full_validation_calls: int


@contextmanager
def instrument_author_runtime() -> Iterator[RuntimeCallCounts]:
    """Count ``DocsApp`` construction and full validation across runtime consumers."""
    counts = RuntimeCallCounts()
    original_init = DocsApp.__init__
    original_check = check_module.check_catalog
    original_snapshot_compute = ValidationSnapshotService._compute_snapshot

    def counted_init(self: DocsApp, *args: Any, **kwargs: Any) -> None:
        counts.docs_app_constructions += 1
        original_init(self, *args, **kwargs)

    def counted_check(*args: Any, **kwargs: Any):
        counts.full_validation_calls += 1
        return original_check(*args, **kwargs)

    def counted_snapshot_compute(self: ValidationSnapshotService, *args: Any, **kwargs: Any):
        counts.full_validation_calls += 1
        return original_snapshot_compute(self, *args, **kwargs)

    with (
        patch.object(DocsApp, "__init__", counted_init),
        patch.object(check_module, "check_catalog", counted_check),
        patch.object(render_context_module, "check_catalog", counted_check),
        patch.object(mcp_module, "check_catalog", counted_check),
        patch.object(
            ValidationSnapshotService,
            "_compute_snapshot",
            counted_snapshot_compute,
        ),
    ):
        yield counts


def generate_author_corpus(root: Path, *, page_count: int) -> Path:
    """Create a deterministic corpus with a home route and representative docs pages."""
    if page_count < 2:
        raise ValueError("page_count must be at least 2")
    content_root = root / "content"
    docs_root = content_root / "docs"
    docs_root.mkdir(parents=True, exist_ok=True)
    (content_root / "_index.md").write_text(
        "---\ntitle: Benchmark home\nlayout: home\n---\n\n# Benchmark home\n\n"
        "[Open the measured page](/docs/page-00001/).\n",
        encoding="utf-8",
    )
    for index in range(1, page_count):
        following = 1 if index + 1 == page_count else index + 1
        (docs_root / f"page-{index:05d}.md").write_text(
            "\n".join(
                (
                    "---",
                    f"title: Author benchmark page {index}",
                    "description: Deterministic author-runtime benchmark content.",
                    "tags: [benchmark, author-runtime]",
                    "---",
                    "",
                    f"# Author benchmark page {index}",
                    "",
                    "This page exercises author chrome, validation, and status JSON.",
                    "",
                    f"[Next](/docs/page-{following:05d}/)",
                    "",
                )
            ),
            encoding="utf-8",
        )
    return content_root


def run_author_runtime_benchmark(
    repo_root: Path,
    *,
    synthetic_pages: int = 12,
    repeats: int = 3,
    dogfood: bool = False,
) -> dict[str, object]:
    """Measure author construction, startup, requests, and validation."""
    assert_free_threading()
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="furatena-author-benchmark-") as raw_workspace:
        workspace = Path(raw_workspace).resolve()
        if dogfood:
            docs_yaml = repo_root / "app" / "docs.yaml"
            source_roots = (
                repo_root / "content" / "furatena",
                repo_root / "app" / "content" / "shared",
            )
            page_path = "/docs/get-started/installation/"
            page_slug = "get-started/installation"
            corpus_kind = "dogfood"
        else:
            content_root = generate_author_corpus(workspace / "source", page_count=synthetic_pages)
            docs_yaml = _write_author_config(
                workspace / "app",
                content_root,
                theme_root=repo_root / "app" / "theme",
            )
            source_roots = (content_root,)
            page_path = "/docs/page-00001/"
            page_slug = "docs/page-00001"
            corpus_kind = "synthetic"

        def build_docs() -> DocsApp:
            with patch.dict(os.environ, {"CHIRP_SKIP_CONTRACT_CHECKS": "1"}):
                return DocsApp.from_paths(
                    docs_yaml,
                    repo_root=repo_root,
                    autodoc=False,
                    serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
                    workers=1,
                )

        def composed_preflight(docs: DocsApp) -> None:
            result = compose_serve_preflight(
                docs.app,
                docs.serve,
                page_count=len(docs.catalog.nodes),
                mount_count=len(docs.catalog.mounts),
                configured_url="http://127.0.0.1:8001/",
                run_contract_checks=True,
            )
            if not result.ok:
                raise RuntimeError(
                    "Author benchmark serve preflight failed its composed contract checks."
                )

        construction = _measure_sync(build_docs, repeats=repeats, primary_construction=True)
        startup_cold_samples: list[_OperationSample] = []
        warm_docs: DocsApp | None = None
        for _ in range(repeats):
            docs = build_docs()
            startup_cold_samples.append(_sample_sync(lambda docs=docs: composed_preflight(docs)))
            warm_docs = docs
        assert warm_docs is not None
        startup_warm = _measure_sync(lambda: composed_preflight(warm_docs), repeats=repeats)

        client = TestClient(warm_docs.create_app())
        page_cold, page_warm = _measure_async_request(
            lambda: client.get(page_path), repeats=repeats
        )
        status_cold, status_warm = _measure_async_request(
            lambda: client.get("/docs/_author/page.json", query={"slug": page_slug}),
            repeats=repeats,
        )
        validation_cold, validation_warm = _measure_async_request(
            lambda: client.get(
                "/docs/_author/page.json",
                query={"slug": page_slug, "validate": "1"},
            ),
            repeats=repeats,
        )
        docs_validation = _measure_sync(
            lambda: check_module.check_catalog(
                warm_docs.catalog,
                views=warm_docs.views,
                docs=warm_docs.config,
                theme=warm_docs.theme,
                inventory_store=warm_docs.catalog.inventory_store,
            ),
            repeats=repeats,
        )
        validation_phases = _profile_validation_phases(warm_docs)

        source_files = tuple(
            path
            for root in source_roots
            if root.is_dir()
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".mdx", ".rst", ".html"}
        )
        report: dict[str, object] = {
            "schema_version": AUTHOR_RUNTIME_SCHEMA_VERSION,
            "profile": "author-runtime",
            "environment": _environment_metadata(),
            "corpus": {
                "kind": corpus_kind,
                "page_count": len(warm_docs.catalog.nodes),
                "mount_count": len(warm_docs.catalog.mounts),
                "source_file_count": len(source_files),
            },
            "methodology": {
                "clock": "time.perf_counter",
                "statistic": "median",
                "repeats": repeats,
                "workers": 1,
                "autodoc": False,
                "cold_definition": "first operation on fresh state",
                "warm_definition": "same operation repeated after one completed sample",
                "wall_clock_policy": "informational across machines",
            },
            "operations": {
                "docs_app_construction": {"cold": construction},
                "startup_contract_checks": {
                    "cold": _operation_report(startup_cold_samples),
                    "warm": startup_warm,
                },
                "author_page_request": {"cold": page_cold, "warm": page_warm},
                "author_page_status": {"cold": status_cold, "warm": status_warm},
                "author_page_validation": {
                    "cold": validation_cold,
                    "warm": validation_warm,
                },
                "docs_validation": {
                    "warm": docs_validation,
                    "profiled_phases_ms": validation_phases,
                },
            },
            "elapsed_seconds": round(time.time() - started, 6),
        }
    validate_author_runtime_report(report, repo_root=repo_root)
    return report


def validate_author_runtime_report(report: dict[str, object], *, repo_root: Path) -> None:
    """Validate an author-runtime report against its checked-in schema."""
    schema_path = (
        repo_root
        / "src"
        / "furatena"
        / "catalog"
        / "schemas"
        / "author-runtime-benchmark-v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)


def write_author_runtime_report(report: dict[str, object], output: Path | None = None) -> str:
    """Serialize an author-runtime report and optionally persist it."""
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    return payload


def _write_author_config(
    app_root: Path,
    content_root: Path,
    *,
    theme_root: Path | None = None,
) -> Path:
    app_root.mkdir(parents=True, exist_ok=True)
    if theme_root is not None:
        shutil.copytree(theme_root, app_root / "theme")
    docs_yaml = app_root / "docs.yaml"
    docs_yaml.write_text(
        """shell: shell.html
views:
  doc: views/doc.html
  doc_list: views/doc_list.html
  page: views/page.html
  home: views/home.html
  changelog: views/changelog.html
  collection: views/collection.html
  api_reference: views/api_reference.html
  portal: views/portal.html
  default: views/doc.html
theme:
  use: lagoon
  id: furatena
  templates: theme/templates
mounts: mounts.yaml
""",
        encoding="utf-8",
    )
    (app_root / "mounts.yaml").write_text(
        "mounts:\n"
        "  - id: benchmark\n"
        "    label: Author benchmark\n"
        f"    content_root: {json.dumps(str(content_root))}\n"
        "    default: true\n",
        encoding="utf-8",
    )
    return docs_yaml


def _measure_sync(
    operation: Callable[[], object],
    *,
    repeats: int,
    primary_construction: bool = False,
) -> dict[str, object]:
    samples = [
        _sample_sync(operation, primary_construction=primary_construction) for _ in range(repeats)
    ]
    return _operation_report(samples)


def _sample_sync(
    operation: Callable[[], object], *, primary_construction: bool = False
) -> _OperationSample:
    with instrument_author_runtime() as counts:
        started = time.perf_counter()
        operation()
        elapsed = (time.perf_counter() - started) * 1000.0
    secondary = counts.docs_app_constructions - int(primary_construction)
    return {
        "timing_ms": elapsed,
        "secondary_docs_app_constructions": max(0, secondary),
        "full_validation_calls": counts.full_validation_calls,
    }


def _measure_async_request(
    operation: Callable[[], Any], *, repeats: int
) -> tuple[dict[str, object], dict[str, object]]:
    cold = _sample_sync(lambda: _run_request(operation))
    warm = _measure_sync(lambda: _run_request(operation), repeats=repeats)
    return _operation_report([cold]), warm


def _run_request(operation: Callable[[], Any]) -> None:
    response = asyncio.run(operation())
    if response.status != 200:
        raise RuntimeError(f"author benchmark request failed with HTTP {response.status}")


def _silent_call(operation: Callable[[], object]) -> object:
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        return operation()


def _operation_report(samples: list[_OperationSample]) -> dict[str, object]:
    return {
        "timing_ms": _measurement([sample["timing_ms"] for sample in samples]),
        "call_counts": {
            "secondary_docs_app_constructions": _count_measurement(
                [sample["secondary_docs_app_constructions"] for sample in samples]
            ),
            "full_validation_calls": _count_measurement(
                [sample["full_validation_calls"] for sample in samples]
            ),
        },
    }


def _measurement(samples: list[float]) -> dict[str, object]:
    return {
        "median": round(statistics.median(samples), 6),
        "samples": [round(sample, 6) for sample in samples],
        "unit": "ms",
    }


def _count_measurement(samples: list[int]) -> dict[str, object]:
    return {
        "median": statistics.median(samples),
        "samples": samples,
        "unit": "calls",
    }


def _profile_validation_phases(docs: DocsApp) -> dict[str, float]:
    profiler = cProfile.Profile()
    profiler.runcall(
        check_module.check_catalog,
        docs.catalog,
        views=docs.views,
        docs=docs.config,
        theme=docs.theme,
        inventory_store=docs.catalog.inventory_store,
    )
    stats = pstats.Stats(profiler)
    cumulative_by_name: dict[str, float] = {}
    raw_stats = cast(Any, stats).stats
    for (_filename, _line, function_name), values in raw_stats.items():
        cumulative_by_name[function_name] = cumulative_by_name.get(function_name, 0.0) + values[3]
    return {
        phase: round(cumulative_by_name.get(function_name, 0.0) * 1000.0, 6)
        for phase, function_name in _VALIDATION_PHASES.items()
    }


def _environment_metadata() -> dict[str, object]:
    probe = getattr(sys, "_is_gil_enabled", None)
    gil_enabled = probe() if probe is not None else True
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "gil_enabled": gil_enabled,
        "free_threaded": not gil_enabled,
        "cpu_count": os.cpu_count(),
        "dependencies": {name: _package_version(name) for name in _DEPENDENCIES},
    }


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"
