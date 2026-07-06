"""Enforce per-module coverage ratchets from a coverage.py JSON report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def check_core_coverage(report: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    """Return actionable failures for missing modules or regressed coverage."""

    failures: list[str] = []
    files = report.get("files")
    modules = policy.get("modules")
    if not isinstance(files, dict):
        return ["coverage report does not contain a files mapping"]
    if not isinstance(modules, dict):
        return ["coverage policy does not contain a modules mapping"]

    for source_path, rule in sorted(modules.items()):
        record = files.get(source_path)
        if not isinstance(record, dict):
            failures.append(f"{source_path}: missing from coverage report")
            continue
        summary = record.get("summary")
        if not isinstance(summary, dict):
            failures.append(f"{source_path}: missing coverage summary")
            continue
        actual = float(summary.get("percent_covered", 0.0))
        minimum = float(rule.get("minimum_percent", 0.0))
        if actual + 1e-9 < minimum:
            failures.append(f"{source_path}: {actual:.2f}% is below {minimum:.2f}%")
    return failures


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="coverage.py JSON report")
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("config/core-coverage.json"),
        help="Per-module threshold policy",
    )
    args = parser.parse_args(argv)

    report = _load_json(args.report)
    policy = _load_json(args.policy)
    failures = check_core_coverage(report, policy)
    modules = policy["modules"]
    files = report["files"]
    for source_path, rule in sorted(modules.items()):
        record = files.get(source_path, {})
        summary = record.get("summary", {}) if isinstance(record, dict) else {}
        actual = float(summary.get("percent_covered", 0.0))
        minimum = float(rule["minimum_percent"])
        print(f"{source_path}: {actual:.2f}% (minimum {minimum:.2f}%)")
    if failures:
        print("core coverage ratchet failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("core coverage ratchet passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
