"""Author-runtime benchmark schema and structural instrumentation contracts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import furatena.catalog.validation as validation_module
from furatena.catalog.author_benchmarks import (
    AUTHOR_RUNTIME_OPERATIONS,
    _write_author_config,
    generate_author_corpus,
    instrument_author_runtime,
    validate_author_runtime_report,
)
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode

REPO = Path(__file__).resolve().parents[1]


def test_author_corpus_is_deterministic_and_has_home_route(tmp_path: Path) -> None:
    first = generate_author_corpus(tmp_path / "first", page_count=3)
    second = generate_author_corpus(tmp_path / "second", page_count=3)

    first_payloads = [path.read_bytes() for path in sorted(first.rglob("*.md"))]
    second_payloads = [path.read_bytes() for path in sorted(second.rglob("*.md"))]
    assert first_payloads == second_payloads
    assert (first / "_index.md").is_file()
    assert len(first_payloads) == 3


def test_runtime_instrumentation_counts_construction_and_validation(tmp_path: Path) -> None:
    content_root = generate_author_corpus(tmp_path / "source", page_count=2)
    docs_yaml = _write_author_config(tmp_path / "app", content_root)

    with instrument_author_runtime() as construction:
        docs = DocsApp.from_paths(
            docs_yaml,
            repo_root=REPO,
            autodoc=False,
            serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
            workers=1,
        )
    assert construction.docs_app_constructions == 1

    node = docs.catalog.get_by_slug("docs/page-00001")
    assert node is not None
    with (
        patch.object(validation_module, "check_catalog_content", return_value=([], [])),
        patch.object(validation_module, "check_catalog_configuration", return_value=([], [])),
        instrument_author_runtime() as request_counts,
    ):
        docs._author_validation_status(node)
    assert request_counts.full_validation_calls == 1
    assert request_counts.docs_app_constructions == 0


def test_author_runtime_report_schema_and_operation_coverage() -> None:
    assert AUTHOR_RUNTIME_OPERATIONS == (
        "docs_app_construction",
        "startup_contract_checks",
        "author_page_request",
        "author_page_status",
        "author_page_validation",
        "docs_validation",
    )

    timing = {"median": 1.0, "samples": [1.0], "unit": "ms"}
    calls = {"median": 0, "samples": [0], "unit": "calls"}
    measurement = {
        "timing_ms": timing,
        "call_counts": {
            "secondary_docs_app_constructions": calls,
            "full_validation_calls": calls,
        },
    }
    report = {
        "schema_version": 1,
        "profile": "author-runtime",
        "environment": {
            "python": "3.14.0",
            "implementation": "CPython",
            "platform": "test",
            "gil_enabled": False,
            "free_threaded": True,
            "cpu_count": 1,
            "dependencies": {},
        },
        "corpus": {
            "kind": "synthetic",
            "page_count": 2,
            "mount_count": 1,
            "source_file_count": 2,
        },
        "methodology": {},
        "operations": {
            "docs_app_construction": {"cold": measurement},
            "startup_contract_checks": {"cold": measurement, "warm": measurement},
            "author_page_request": {"cold": measurement, "warm": measurement},
            "author_page_status": {"cold": measurement, "warm": measurement},
            "author_page_validation": {"cold": measurement, "warm": measurement},
            "docs_validation": {"warm": measurement, "profiled_phases_ms": {}},
        },
        "elapsed_seconds": 1.0,
    }

    validate_author_runtime_report(report, repo_root=REPO)
