#!/usr/bin/env python3
"""Benchmark known-answer retrieval algorithms and filter contracts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.docs_app import DocsApp  # noqa: E402
from furatena.catalog.retrieval_benchmarks import (  # noqa: E402
    run_retrieval_benchmarks,
    write_retrieval_benchmark_report,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    docs = DocsApp.from_paths(
        REPO / "app" / "docs.yaml",
        repo_root=REPO,
        autodoc_config=REPO / "config" / "autodoc.yaml",
        autodoc=True,
    )
    report = run_retrieval_benchmarks(docs, repeats=args.repeats, limit=args.limit)
    print(write_retrieval_benchmark_report(report, args.output), end="")


if __name__ == "__main__":
    main()
