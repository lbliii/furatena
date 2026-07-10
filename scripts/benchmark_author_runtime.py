#!/usr/bin/env python3
"""Benchmark author-mode startup, requests, and validation structure."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from furatena.catalog.author_benchmarks import (  # noqa: E402
    run_author_runtime_benchmark,
    write_author_runtime_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-pages", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--dogfood", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the author-runtime profile and print its JSON report."""
    args = _parser().parse_args(argv)
    report = run_author_runtime_benchmark(
        REPO,
        synthetic_pages=args.synthetic_pages,
        repeats=args.repeats,
        dogfood=args.dogfood,
    )
    print(write_author_runtime_report(report, args.output), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
