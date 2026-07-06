"""Core coverage policy and failure reporting contracts."""

from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_SPEC = spec_from_file_location(
    "check_core_coverage",
    REPO / "scripts" / "check_core_coverage.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
check_core_coverage = _MODULE.check_core_coverage


def test_policy_tracks_required_core_modules_and_baselines() -> None:
    policy = json.loads((REPO / "config" / "core-coverage.json").read_text(encoding="utf-8"))

    assert set(policy["modules"]) == {
        "src/furatena/catalog/access.py",
        "src/furatena/catalog/export.py",
        "src/furatena/catalog/graph.py",
        "src/furatena/catalog/graph_schema.py",
        "src/furatena/catalog/loader.py",
    }
    for rule in policy["modules"].values():
        assert rule["minimum_percent"] <= rule["baseline_percent"]


def test_ratchet_reports_regressions_and_missing_modules() -> None:
    policy = {
        "modules": {
            "covered.py": {"minimum_percent": 80},
            "missing.py": {"minimum_percent": 70},
        }
    }
    report = {
        "files": {
            "covered.py": {"summary": {"percent_covered": 79.99}},
        }
    }

    assert check_core_coverage(report, policy) == [
        "covered.py: 79.99% is below 80.00%",
        "missing.py: missing from coverage report",
    ]
