"""Incremental ty scope and diagnostic budget contracts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO / "config" / "ty-diagnostics.json"
SCRIPT_PATH = REPO / "scripts" / "check_ty_diagnostics.py"

_SPEC = importlib.util.spec_from_file_location("check_ty_diagnostics", SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

broad_drift = _MODULE.broad_drift
load_baseline = _MODULE.load_baseline
ratchet_violations = _MODULE.ratchet_violations
summarize_diagnostics = _MODULE.summarize_diagnostics


def _diagnostic(path: str, rule: str) -> dict[str, object]:
    return {
        "check_name": rule,
        "location": {
            "path": path,
            "positions": {"begin": {"line": 1, "column": 1}},
        },
    }


def _sample_baseline() -> dict[str, object]:
    return {
        "schema_version": 1,
        "tool_version": "0.0.57",
        "total_diagnostics": 3,
        "owned": {
            "budgeted_modules": ["src/owned.py"],
            "zero_diagnostic_modules": ["src/zero.py"],
        },
        "modules": {
            "src/owned.py": {
                "budget": 2,
                "rules": {"invalid-argument-type": 2},
            },
            "src/unowned.py": {
                "budget": 1,
                "rules": {"unresolved-attribute": 1},
            },
        },
    }


def test_checked_in_baseline_is_complete_and_categorized() -> None:
    baseline = load_baseline(BASELINE_PATH)
    raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    modules = baseline["modules"]

    assert baseline["tool_version"] == "0.0.57"
    assert sum(policy["budget"] for policy in modules.values()) == baseline["total_diagnostics"]

    rules: dict[str, int] = {}
    for path, policy in modules.items():
        assert (REPO / path).is_file()
        assert policy["budget"] == sum(policy["rules"].values())
        assert policy["area"]
        assert policy["root_cause"]
        assert policy["disposition"] in {
            "actionable",
            "mixed",
            "optional-dependency",
            "upstream-candidate",
        }
        assert isinstance(policy["likely_false_positive"], bool)
        assert "upstream_status" in policy
        for rule, count in policy["rules"].items():
            rules[rule] = rules.get(rule, 0) + count
    assert rules == raw["rules"]

    owned = baseline["owned"]
    assert set(owned["budgeted_modules"]) <= set(modules)
    assert set(owned["zero_diagnostic_modules"]).isdisjoint(modules)
    for path in owned["zero_diagnostic_modules"]:
        assert (REPO / path).is_file()


def test_non_owned_drift_is_reported_without_failing_the_ratchet() -> None:
    baseline = _sample_baseline()
    summary = summarize_diagnostics(
        [
            _diagnostic("src/owned.py", "invalid-argument-type"),
            _diagnostic("src/unowned.py", "unresolved-attribute"),
            _diagnostic("src/unowned.py", "unresolved-attribute"),
        ]
    )

    assert ratchet_violations(summary, baseline) == []
    assert broad_drift(summary, baseline) == [
        {
            "module": "src/owned.py",
            "baseline": 2,
            "current": 1,
            "delta": -1,
        },
        {
            "module": "src/unowned.py",
            "baseline": 1,
            "current": 2,
            "delta": 1,
        },
    ]


def test_owned_budgets_reject_new_rules_and_zero_scope_diagnostics() -> None:
    baseline = _sample_baseline()
    summary = summarize_diagnostics(
        [
            _diagnostic("src/owned.py", "invalid-argument-type"),
            _diagnostic("src/owned.py", "unresolved-attribute"),
            _diagnostic("src/owned.py", "unresolved-attribute"),
            _diagnostic("src/zero.py", "invalid-return-type"),
        ]
    )

    violations = ratchet_violations(summary, baseline)
    assert violations == [
        "src/zero.py: expected zero diagnostics, found 1 (invalid-return-type=1)",
        "src/owned.py: diagnostic count 3 exceeds budget 2",
        "src/owned.py: unresolved-attribute count 2 exceeds budget 0",
    ]


def test_makefile_keeps_ty_audit_ratchet_and_first_wave_in_fast_ci() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    assert "ty-audit:" in makefile
    assert "scripts/check_ty_diagnostics.py --report-only --json" in makefile
    assert "ty-ratchet:" in makefile
    assert "$(MAKE) ty-ratchet" in makefile
    assert "tests/test_ty_diagnostic_ratchet.py" in makefile
    for path in (
        "src/furatena/catalog/author_store.py",
        "src/furatena/catalog/lifecycle.py",
        "src/furatena/catalog/models.py",
        "src/furatena/cli/authoring.py",
        "src/furatena/cli/contracts.py",
    ):
        assert path in makefile
