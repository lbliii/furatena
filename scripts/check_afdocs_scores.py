#!/usr/bin/env python3
"""Normalize dual-channel afdocs reports and enforce the committed score baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CHANNELS = ("static", "live")
EXPECTED_SPLIT = {
    "check_id": "content-negotiation",
    "static": "not-pass",
    "live": "pass",
}
_STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2, "skip": 3, "error": 4}


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), label=str(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read {path}: {exc}") from exc


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _normalized_channel(report: dict[str, Any], *, channel: str) -> dict[str, Any]:
    results = report.get("results")
    summary = report.get("summary")
    scoring = report.get("scoring")
    if not isinstance(results, list) or not results:
        raise ValueError(f"{channel} afdocs report has no check results")
    if not isinstance(summary, dict):
        raise ValueError(f"{channel} afdocs report has no summary")
    if not isinstance(scoring, dict) or not isinstance(scoring.get("overall"), int):
        raise ValueError(f"{channel} afdocs report has no integer scoring.overall")

    normalized_results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(results):
        result = _object(value, label=f"{channel}.results[{index}]")
        check_id = result.get("id")
        status = result.get("status")
        if not isinstance(check_id, str) or not check_id:
            raise ValueError(f"{channel}.results[{index}] has no check id")
        if check_id in seen:
            raise ValueError(f"{channel} afdocs report repeats check {check_id!r}")
        if status not in _STATUS_RANK:
            raise ValueError(f"{channel} check {check_id!r} has unknown status {status!r}")
        seen.add(check_id)
        normalized_results.append(result)

    normalized_results.sort(key=lambda item: str(item["id"]))
    return {
        "url": report.get("url"),
        "timestamp": report.get("timestamp"),
        "summary": summary,
        "scoring": scoring,
        "results": normalized_results,
        "sampling_strategy": report.get("samplingStrategy"),
        "tested_pages": report.get("testedPages"),
        "discovery_sources": report.get("discoverySources", []),
    }


def build_evidence(
    static_report: dict[str, Any],
    live_report: dict[str, Any],
    *,
    afdocs_version: str,
) -> dict[str, Any]:
    """Build the stable JSON artifact retained by CI."""
    static = _normalized_channel(static_report, channel="static")
    live = _normalized_channel(live_report, channel="live")
    spec_urls = {static_report.get("specUrl"), live_report.get("specUrl")}
    if None in spec_urls or len(spec_urls) != 1:
        raise ValueError("static and live reports must use the same non-empty specUrl")
    timestamps = [
        value for value in (static["timestamp"], live["timestamp"]) if isinstance(value, str)
    ]
    if len(timestamps) != 2:
        raise ValueError("static and live reports must include timestamps")
    return {
        "schema_version": SCHEMA_VERSION,
        "afdocs_version": afdocs_version,
        "spec_url": spec_urls.pop(),
        "generated_at": max(timestamps),
        "channels": {"static": static, "live": live},
    }


def _statuses(channel: dict[str, Any]) -> dict[str, str]:
    return {str(item["id"]): str(item["status"]) for item in channel["results"]}


def build_baseline(evidence: dict[str, Any]) -> dict[str, Any]:
    """Reduce one evidence artifact to the reviewable regression baseline."""
    channels = _object(evidence.get("channels"), label="evidence.channels")
    baseline_channels: dict[str, Any] = {}
    for name in CHANNELS:
        channel = _object(channels.get(name), label=f"evidence.channels.{name}")
        scoring = _object(channel.get("scoring"), label=f"evidence.channels.{name}.scoring")
        baseline_channels[name] = {
            "url": channel.get("url"),
            "overall": scoring.get("overall"),
            "grade": scoring.get("grade"),
            "checks": dict(sorted(_statuses(channel).items())),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "afdocs_version": evidence.get("afdocs_version"),
        "spec_url": evidence.get("spec_url"),
        "recorded_at": evidence.get("generated_at"),
        "required_split": EXPECTED_SPLIT,
        "channels": baseline_channels,
    }


def _split_regressions(evidence: dict[str, Any]) -> list[str]:
    channels = _object(evidence.get("channels"), label="evidence.channels")
    static_status = _statuses(
        _object(channels.get("static"), label="evidence.channels.static")
    ).get("content-negotiation")
    live_status = _statuses(_object(channels.get("live"), label="evidence.channels.live")).get(
        "content-negotiation"
    )
    regressions: list[str] = []
    if static_status in {None, "pass"}:
        regressions.append(
            "expected static content-negotiation to remain a documented non-pass, "
            f"observed {static_status!r}"
        )
    if live_status != "pass":
        regressions.append(f"expected live content-negotiation to pass, observed {live_status!r}")
    return regressions


def find_regressions(evidence: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Return score, status, schema, and expected-channel-split regressions."""
    regressions: list[str] = []
    if baseline.get("schema_version") != SCHEMA_VERSION:
        regressions.append(
            f"baseline schema_version must be {SCHEMA_VERSION}, got {baseline.get('schema_version')!r}"
        )
    if baseline.get("afdocs_version") != evidence.get("afdocs_version"):
        regressions.append(
            "afdocs version changed from "
            f"{baseline.get('afdocs_version')!r} to {evidence.get('afdocs_version')!r}; "
            "record and review a new baseline"
        )
    if baseline.get("spec_url") != evidence.get("spec_url"):
        regressions.append("afdocs spec URL changed; record and review a new baseline")
    if baseline.get("required_split") != EXPECTED_SPLIT:
        regressions.append("baseline does not preserve the required static/live negotiation split")
    regressions.extend(_split_regressions(evidence))

    current_channels = _object(evidence.get("channels"), label="evidence.channels")
    baseline_channels = _object(baseline.get("channels"), label="baseline.channels")
    for name in CHANNELS:
        current = _object(current_channels.get(name), label=f"evidence.channels.{name}")
        expected = _object(baseline_channels.get(name), label=f"baseline.channels.{name}")
        if current.get("url") != expected.get("url"):
            regressions.append(
                f"{name} URL changed from {expected.get('url')!r} to {current.get('url')!r}"
            )

        scoring = _object(current.get("scoring"), label=f"evidence.channels.{name}.scoring")
        current_score = scoring.get("overall")
        baseline_score = expected.get("overall")
        if not isinstance(current_score, int) or not isinstance(baseline_score, int):
            regressions.append(f"{name} baseline and current scores must be integers")
        elif current_score < baseline_score:
            regressions.append(f"{name} score regressed from {baseline_score} to {current_score}")

        current_statuses = _statuses(current)
        expected_statuses = expected.get("checks")
        if not isinstance(expected_statuses, dict):
            regressions.append(f"{name} baseline checks must be an object")
            continue
        for check_id, baseline_status in expected_statuses.items():
            current_status = current_statuses.get(str(check_id))
            if current_status is None:
                regressions.append(f"{name} check {check_id!r} disappeared from afdocs output")
                continue
            if baseline_status not in _STATUS_RANK:
                regressions.append(
                    f"{name} baseline check {check_id!r} has unknown status {baseline_status!r}"
                )
                continue
            if _STATUS_RANK[current_status] > _STATUS_RANK[str(baseline_status)]:
                regressions.append(
                    f"{name} check {check_id!r} regressed from {baseline_status} to {current_status}"
                )
        for check_id in sorted(current_statuses.keys() - {str(key) for key in expected_statuses}):
            regressions.append(
                f"{name} check {check_id!r} is new; record and review a new baseline"
            )
    return regressions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", type=Path, required=True, help="Static-channel afdocs JSON")
    parser.add_argument("--live", type=Path, required=True, help="Live-channel afdocs JSON")
    parser.add_argument("--afdocs-version", required=True)
    parser.add_argument("--output", type=Path, required=True, help="Combined evidence JSON")
    parser.add_argument("--regressions", type=Path, required=True, help="Regression result JSON")
    parser.add_argument("--baseline", type=Path, help="Committed baseline for check mode")
    parser.add_argument(
        "--record-baseline",
        type=Path,
        help="Write a candidate baseline instead of enforcing an existing one",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        evidence = build_evidence(
            _load_json(args.static),
            _load_json(args.live),
            afdocs_version=args.afdocs_version,
        )
        _write_json(args.output, evidence)
        if args.record_baseline is not None:
            baseline = build_baseline(evidence)
            regressions = _split_regressions(evidence)
            _write_json(args.record_baseline, baseline)
        else:
            if args.baseline is None:
                raise ValueError("--baseline is required unless --record-baseline is used")
            baseline = _load_json(args.baseline)
            regressions = find_regressions(evidence, baseline)
        _write_json(
            args.regressions,
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": evidence["generated_at"],
                "ok": not regressions,
                "regressions": regressions,
            },
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    channels = evidence["channels"]
    print(
        "afdocs scores: "
        f"static={channels['static']['scoring']['overall']} "
        f"live={channels['live']['scoring']['overall']}"
    )
    if regressions:
        for regression in regressions:
            print(f"regression: {regression}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
