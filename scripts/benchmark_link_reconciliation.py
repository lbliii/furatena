#!/usr/bin/env python3
"""Measure one incremental link delta at a 400-shard synthetic scale."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from furatena.catalog.link_reconciliation_benchmarks import (  # noqa: E402
    run_link_reconciliation_benchmark,
    write_link_reconciliation_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mounts", type=int, default=400)
    parser.add_argument("--pages-per-shard", type=int, default=300)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_link_reconciliation_benchmark(
        mounts=args.mounts,
        pages_per_shard=args.pages_per_shard,
    )
    print(write_link_reconciliation_report(report, args.output), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
