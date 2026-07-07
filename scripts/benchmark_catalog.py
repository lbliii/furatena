#!/usr/bin/env python3
"""Run the Furatena catalog benchmark suite."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from furatena.catalog.benchmarks import run_benchmarks, write_report  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-pages", type=int, default=250)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--query-iterations", type=int, default=10)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run both corpora and print the JSON timing report."""
    args = _parser().parse_args(argv)
    report = run_benchmarks(
        REPO,
        synthetic_pages=args.synthetic_pages,
        repeats=args.repeats,
        query_iterations=args.query_iterations,
    )
    print(write_report(report, args.output), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
