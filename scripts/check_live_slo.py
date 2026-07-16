#!/usr/bin/env python3
"""Probe the live docs service and emit one machine-readable SLO receipt."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _request(origin: str, path: str, *, timeout: float) -> tuple[int, bytes, float]:
    request = urllib.request.Request(
        f"{origin.rstrip('/')}{path}",
        headers={"Accept-Encoding": "identity", "User-Agent": "furatena-slo/1"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = int(exc.code)
    elapsed = (time.perf_counter() - started) * 1000
    return status, body, elapsed


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return math.inf
    ordered = sorted(values)
    rank = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[rank]


def evaluate_live_slo(
    config: dict[str, Any],
    *,
    requester=_request,
    artifact_verifier: Any | None = None,
) -> dict[str, Any]:
    """Collect point-in-time indicators and compare them with committed objectives."""
    origin = str(config["origin"]).rstrip("/")
    samples = int(config.get("samples") or 5)
    timeout = float(config.get("timeout_seconds") or 30)
    objectives = dict(config["objectives"])
    observations: dict[str, list[dict[str, Any]]] = {"ready": [], "home": []}
    failures: list[str] = []
    for name, path in (("ready", "/readyz"), ("home", "/")):
        for _ in range(samples):
            try:
                status, body, elapsed = requester(origin, path, timeout=timeout)
                ok = status == 200 and bool(body)
                observations[name].append(
                    {
                        "status": status,
                        "bytes": len(body),
                        "milliseconds": round(elapsed, 3),
                        "ok": ok,
                    }
                )
            except OSError as exc:
                observations[name].append(
                    {"status": 0, "bytes": 0, "milliseconds": None, "ok": False, "error": str(exc)}
                )
    all_probes = [item for values in observations.values() for item in values]
    availability = 100 * sum(bool(item["ok"]) for item in all_probes) / len(all_probes)
    latencies = {
        name: [float(item["milliseconds"]) for item in values if item["milliseconds"] is not None]
        for name, values in observations.items()
    }
    ready_p95 = _percentile(latencies["ready"], 0.95)
    home_p95 = _percentile(latencies["home"], 0.95)
    if availability < float(objectives["probe_availability_percent"]):
        failures.append(
            f"probe availability {availability:.3f}% is below {objectives['probe_availability_percent']}%"
        )
    if ready_p95 > float(objectives["ready_p95_milliseconds"]):
        failures.append(
            f"ready p95 {ready_p95:.3f}ms exceeds {objectives['ready_p95_milliseconds']}ms"
        )
    if home_p95 > float(objectives["home_p95_milliseconds"]):
        failures.append(
            f"home p95 {home_p95:.3f}ms exceeds {objectives['home_p95_milliseconds']}ms"
        )

    status, meta_body, _elapsed = requester(origin, "/meta.json", timeout=timeout)
    try:
        meta = json.loads(meta_body) if status == 200 else {}
    except UnicodeDecodeError, json.JSONDecodeError:
        meta = {}
    build = meta.get("build") if isinstance(meta, dict) else None
    build = build if isinstance(build, dict) else {}
    if objectives.get("private_image_identity"):
        digest = str((build.get("image") or {}).get("digest") or "")
        if build.get("distribution") != "private-image" or not (
            digest.startswith("sha256:") and len(digest) == 71
        ):
            failures.append("/meta.json does not prove an exact private-image digest")
    if objectives.get("managed_content_identity"):
        content = build.get("content") or {}
        if content.get("status") != "active" or not content.get("resolved_ref"):
            failures.append("/meta.json does not prove an active managed-content generation")

    if artifact_verifier is None:
        completed = subprocess.run(
            (
                sys.executable,
                str(ROOT / "scripts" / "verify-live-artifacts.py"),
                origin,
                "--timeout",
                str(timeout),
            ),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(timeout * 6, 60),
        )
        artifact_ok = completed.returncode == 0
        artifact = (
            json.loads(completed.stdout) if artifact_ok else {"error": completed.stderr.strip()}
        )
    else:
        try:
            artifact = artifact_verifier(origin, timeout=timeout)
            artifact_ok = True
        except (OSError, RuntimeError) as exc:
            artifact = {"error": str(exc)}
            artifact_ok = False
    artifact_integrity = 100.0 if artifact_ok else 0.0
    if artifact_integrity < float(objectives["artifact_integrity_percent"]):
        failures.append(f"artifact integrity failed: {artifact.get('error', 'unknown error')}")

    return {
        "schema_version": 1,
        "origin": origin,
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ok": not failures,
        "indicators": {
            "probe_availability_percent": round(availability, 3),
            "ready_p95_milliseconds": round(ready_p95, 3),
            "home_p95_milliseconds": round(home_p95, 3),
            "artifact_integrity_percent": artifact_integrity,
        },
        "objectives": objectives,
        "service_level": config.get("service_level") or {},
        "observations": observations,
        "build": build,
        "artifact": artifact,
        "failures": failures,
        "latency_median_milliseconds": {
            name: round(statistics.median(values), 3) if values else None
            for name, values in latencies.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "live-slo.json")
    parser.add_argument("--origin")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.origin:
        config["origin"] = args.origin
    report = evaluate_live_slo(config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
