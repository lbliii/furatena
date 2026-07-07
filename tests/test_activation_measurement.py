"""Opt-in activation timing and privacy contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from furatena.catalog.activation import (
    activation_report,
    mark_activation_event,
    start_activation_session,
)
from furatena.catalog.benchmarks import assert_free_threading
from furatena.cli.main import run_command


def _clock(seconds: float):
    return lambda: int(seconds * 1_000_000_000)


def _completed_session(
    path: Path,
    *,
    journey: str,
    first_edit: float,
    first_publish: float,
    clean_migration: float | None = None,
) -> None:
    start_activation_session(path, journey=journey, consent=True, clock_ns=_clock(100))
    mark_activation_event(
        path,
        event="first-edit",
        automated_remediation_seconds=10,
        manual_remediation_seconds=20,
        clock_ns=_clock(100 + first_edit),
    )
    mark_activation_event(
        path,
        event="first-publish",
        automated_remediation_seconds=30,
        manual_remediation_seconds=40,
        clock_ns=_clock(100 + first_publish),
    )
    if clean_migration is not None:
        mark_activation_event(
            path,
            event="clean-migration",
            automated_remediation_seconds=15,
            manual_remediation_seconds=25,
            clock_ns=_clock(100 + clean_migration),
        )


def test_start_requires_explicit_consent_and_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "session.json"

    with pytest.raises(ValueError, match="opt-in"):
        start_activation_session(path, journey="new-site", consent=False)

    assert not path.exists()


def test_report_rejects_non_object_session_json(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        activation_report((path,))


def test_report_separates_journeys_targets_and_remediation(tmp_path: Path) -> None:
    new = tmp_path / "new.json"
    imported = tmp_path / "imported.json"
    _completed_session(new, journey="new-site", first_edit=500, first_publish=900)
    _completed_session(
        imported,
        journey="imported-site",
        first_edit=1500,
        first_publish=2400,
        clean_migration=1200,
    )

    report = activation_report((new, imported))

    new_report = report["by_journey"]["new-site"]
    imported_report = report["by_journey"]["imported-site"]
    assert new_report["metrics"]["first-edit"] == {
        "sample_count": 1,
        "median_seconds": 500.0,
        "p95_seconds": 500.0,
        "target_seconds": 600.0,
        "target_met": True,
    }
    assert imported_report["metrics"]["first-edit"]["target_seconds"] == 1800.0
    assert imported_report["metrics"]["clean-migration"]["median_seconds"] == 1200.0
    assert new_report["remediation"] == {
        "automated_seconds": 40.0,
        "manual_seconds": 60.0,
        "manual_share": 0.6,
    }


def test_shareable_report_omits_identity_paths_and_wall_clock(tmp_path: Path) -> None:
    session = tmp_path / "private-name.json"
    _completed_session(session, journey="new-site", first_edit=60, first_publish=120)

    serialized = json.dumps(activation_report((session,)), sort_keys=True)

    assert '"session_id":' not in serialized
    assert "started_monotonic_ns" not in serialized
    assert str(session) not in serialized
    assert "private-name" not in serialized
    assert '"network_transmission": false' in serialized


def test_concurrent_sessions_are_safe_with_gil_disabled(tmp_path: Path) -> None:
    assert_free_threading()
    paths = [tmp_path / f"session-{index}.json" for index in range(64)]

    def create(index: int) -> None:
        start_activation_session(
            paths[index],
            journey="new-site" if index % 2 == 0 else "imported-site",
            consent=True,
            clock_ns=_clock(100),
        )
        mark_activation_event(
            paths[index],
            event="first-edit",
            clock_ns=_clock(101 + index),
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(create, range(len(paths))))

    report = activation_report(paths)
    assert report["session_count"] == 64
    assert report["by_journey"]["new-site"]["session_count"] == 32
    assert report["by_journey"]["imported-site"]["session_count"] == 32


def test_cli_protocol_emits_sanitized_report(tmp_path: Path) -> None:
    session = tmp_path / "session.json"
    output = tmp_path / "report.json"

    started = run_command(
        [
            "activation",
            "start",
            "--journey",
            "new-site",
            "--session",
            str(session),
            "--consent",
            "--json",
        ]
    )
    marked = run_command(
        [
            "activation",
            "mark",
            "--session",
            str(session),
            "--event",
            "first-edit",
            "--json",
        ]
    )
    reported = run_command(
        [
            "activation",
            "report",
            "--session",
            str(session),
            "--output",
            str(output),
            "--json",
        ]
    )

    assert started is not None and started.ok
    assert marked is not None and marked.ok
    assert reported is not None and reported.ok
    assert started.command == "activation start"
    assert marked.command == "activation mark"
    assert reported.command == "activation report"
    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8"))["session_count"] == 1
