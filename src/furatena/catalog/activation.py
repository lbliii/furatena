"""Opt-in, privacy-preserving activation measurement sessions."""

from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
JOURNEYS = frozenset({"new-site", "imported-site"})
EVENTS = frozenset({"first-edit", "first-publish", "clean-migration"})
FIRST_EDIT_TARGET_SECONDS = {"new-site": 600.0, "imported-site": 1800.0}
_WRITE_LOCK = threading.RLock()


def start_activation_session(
    path: Path,
    *,
    journey: str,
    consent: bool,
    replace: bool = False,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    """Create one local session after explicit measurement consent."""
    normalized_journey = _choice(journey, JOURNEYS, "journey")
    if not consent:
        raise ValueError("activation measurement is opt-in; pass explicit consent to start")
    with _WRITE_LOCK:
        if path.exists() and not replace:
            raise FileExistsError(f"activation session already exists: {path}")
        payload: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "consent": True,
            "journey": normalized_journey,
            "session_id": uuid.uuid4().hex,
            "started_monotonic_ns": int(clock_ns()),
            "events": {},
        }
        _write_json(path, payload)
        return payload


def mark_activation_event(
    path: Path,
    *,
    event: str,
    automated_remediation_seconds: float = 0.0,
    manual_remediation_seconds: float = 0.0,
    replace: bool = False,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    """Record one elapsed milestone without collecting source or identity data."""
    normalized_event = _choice(event, EVENTS, "event")
    automated = _non_negative(automated_remediation_seconds, "automated remediation")
    manual = _non_negative(manual_remediation_seconds, "manual remediation")
    with _WRITE_LOCK:
        payload = _read_session(path)
        events = payload["events"]
        if normalized_event in events and not replace:
            raise ValueError(f"activation event already recorded: {normalized_event}")
        elapsed = (int(clock_ns()) - int(payload["started_monotonic_ns"])) / 1_000_000_000
        if elapsed < 0:
            raise ValueError("activation monotonic clock moved backwards; start a new session")
        if automated + manual > elapsed + 1e-9:
            raise ValueError("remediation time cannot exceed total elapsed activation time")
        events[normalized_event] = {
            "elapsed_seconds": round(elapsed, 6),
            "automated_remediation_seconds": round(automated, 6),
            "manual_remediation_seconds": round(manual, 6),
        }
        _write_json(path, payload)
        return payload


def activation_report(paths: Iterable[Path]) -> dict[str, Any]:
    """Aggregate opted-in sessions into a shareable report without identifiers."""
    sessions = [_read_session(path) for path in paths]
    grouped = {journey: [] for journey in sorted(JOURNEYS)}
    for session in sessions:
        grouped[session["journey"]].append(session)
    by_journey = {
        journey: _journey_report(journey, grouped[journey])
        for journey in sorted(grouped)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "collection": "explicit-opt-in-local",
        "privacy": {
            "network_transmission": False,
            "wall_clock_timestamps": False,
            "source_content": False,
            "source_paths": False,
            "actor_or_host_identity": False,
            "session_identifiers_in_report": False,
        },
        "session_count": len(sessions),
        "by_journey": by_journey,
    }


def _journey_report(journey: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for event in sorted(EVENTS):
        samples = [
            float(session["events"][event]["elapsed_seconds"])
            for session in sessions
            if event in session["events"]
        ]
        target = FIRST_EDIT_TARGET_SECONDS.get(journey) if event == "first-edit" else None
        metrics[event] = _metric_summary(samples, target_seconds=target)
    automated = sum(
        float(record["automated_remediation_seconds"])
        for session in sessions
        for record in session["events"].values()
    )
    manual = sum(
        float(record["manual_remediation_seconds"])
        for session in sessions
        for record in session["events"].values()
    )
    remediation_total = automated + manual
    return {
        "session_count": len(sessions),
        "metrics": metrics,
        "remediation": {
            "automated_seconds": round(automated, 6),
            "manual_seconds": round(manual, 6),
            "manual_share": (
                round(manual / remediation_total, 6) if remediation_total else None
            ),
        },
    }


def _metric_summary(samples: list[float], *, target_seconds: float | None) -> dict[str, Any]:
    ordered = sorted(samples)
    median = statistics.median(ordered) if ordered else None
    p95 = ordered[max(math.ceil(len(ordered) * 0.95) - 1, 0)] if ordered else None
    return {
        "sample_count": len(ordered),
        "median_seconds": round(float(median), 6) if median is not None else None,
        "p95_seconds": round(float(p95), 6) if p95 is not None else None,
        "target_seconds": target_seconds,
        "target_met": (
            median <= target_seconds
            if median is not None and target_seconds is not None
            else None
        ),
    }


def _read_session(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"activation session must be a JSON object: {path}")
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("consent") is not True:
        raise ValueError(f"invalid or unconsented activation session: {path}")
    journey = _choice(str(payload.get("journey") or ""), JOURNEYS, "journey")
    events = payload.get("events")
    if not isinstance(events, dict):
        raise ValueError(f"activation session events must be an object: {path}")
    if not isinstance(payload.get("started_monotonic_ns"), int):
        raise ValueError(f"activation session is missing its monotonic origin: {path}")
    for event, record in events.items():
        if _choice(str(event), EVENTS, "event") != event or not isinstance(record, dict):
            raise ValueError(f"invalid activation event record in {path}: {event!r}")
        elapsed = _non_negative(record.get("elapsed_seconds", -1), "elapsed activation")
        automated = _non_negative(
            record.get("automated_remediation_seconds", -1),
            "automated remediation",
        )
        manual = _non_negative(
            record.get("manual_remediation_seconds", -1),
            "manual remediation",
        )
        if automated + manual > elapsed + 1e-9:
            raise ValueError(f"activation remediation exceeds elapsed time in {path}: {event}")
    payload["journey"] = journey
    return payload


def _choice(value: str, choices: frozenset[str], label: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in choices:
        raise ValueError(f"unknown activation {label} {value!r}; expected one of {sorted(choices)}")
    return normalized


def _non_negative(value: float, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{label} seconds must be a finite non-negative value")
    return normalized


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
