#!/usr/bin/env python3
"""Measure bounded shard residency at the 100-mount synthetic scale."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from furatena.catalog.shard_residency_benchmarks import (  # noqa: E402
    run_shard_residency_benchmark,
    write_shard_residency_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mounts", type=int, default=100)
    parser.add_argument("--editions", type=int, default=4)
    parser.add_argument("--pages-per-shard", type=int, default=300)
    parser.add_argument("--resident-shards", type=int, default=8)
    parser.add_argument("--resident-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_shard_residency_benchmark(
        REPO,
        mounts=args.mounts,
        editions=args.editions,
        pages_per_shard=args.pages_per_shard,
        resident_shards=args.resident_shards,
        resident_bytes=args.resident_bytes,
    )
    print(write_shard_residency_report(report, args.output), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
