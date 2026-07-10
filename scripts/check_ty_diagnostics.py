#!/usr/bin/env python3
"""Report and enforce incremental ty diagnostic budgets."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO / "config" / "ty-diagnostics.json"


def _positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def load_baseline(path: Path) -> dict[str, Any]:
    """Load and validate the stable parts of the ratchet policy."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("ty baseline must use schema_version 1")
    if not isinstance(raw.get("tool_version"), str):
        raise ValueError("ty baseline requires tool_version")
    _positive_int(raw.get("total_diagnostics"), field="total_diagnostics")

    modules = raw.get("modules")
    if not isinstance(modules, dict):
        raise ValueError("ty baseline requires a modules object")
    for module, policy in modules.items():
        if not isinstance(module, str) or not isinstance(policy, dict):
            raise ValueError("ty baseline module entries must be objects")
        _positive_int(policy.get("budget"), field=f"modules.{module}.budget")
        rules = policy.get("rules")
        if not isinstance(rules, dict):
            raise ValueError(f"modules.{module}.rules must be an object")
        for rule, count in rules.items():
            if not isinstance(rule, str):
                raise ValueError(f"modules.{module}.rules keys must be strings")
            _positive_int(count, field=f"modules.{module}.rules.{rule}")

    owned = raw.get("owned")
    if not isinstance(owned, dict):
        raise ValueError("ty baseline requires an owned object")
    for key in ("budgeted_modules", "zero_diagnostic_modules"):
        values = owned.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise ValueError(f"owned.{key} must be a list of paths")
    missing = sorted(set(owned["budgeted_modules"]) - set(modules))
    if missing:
        raise ValueError(f"owned budgeted modules missing from baseline: {', '.join(missing)}")
    return raw


def summarize_diagnostics(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate GitLab-format ty diagnostics by stable module and rule keys."""
    rule_counts: Counter[str] = Counter()
    module_rules: dict[str, Counter[str]] = defaultdict(Counter)
    for item in diagnostics:
        if not isinstance(item, dict):
            raise ValueError("ty report entries must be objects")
        rule = item.get("check_name")
        location = item.get("location")
        path = location.get("path") if isinstance(location, dict) else None
        if not isinstance(rule, str) or not isinstance(path, str):
            raise ValueError("ty report entry is missing check_name or location.path")
        rule_counts[rule] += 1
        module_rules[path][rule] += 1

    modules = {
        path: {
            "count": sum(rules.values()),
            "rules": dict(sorted(rules.items())),
        }
        for path, rules in sorted(module_rules.items())
    }
    return {
        "total": len(diagnostics),
        "rules": dict(sorted(rule_counts.items())),
        "modules": modules,
    }


def ratchet_violations(summary: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Return owned-scope regressions; non-owned drift remains report-only."""
    violations: list[str] = []
    current_modules = summary["modules"]
    module_policies = baseline["modules"]
    owned = baseline["owned"]

    for path in owned["zero_diagnostic_modules"]:
        current = current_modules.get(path, {"count": 0, "rules": {}})
        if current["count"]:
            rules = ", ".join(f"{rule}={count}" for rule, count in current["rules"].items())
            violations.append(
                f"{path}: expected zero diagnostics, found {current['count']} ({rules})"
            )

    for path in owned["budgeted_modules"]:
        current = current_modules.get(path, {"count": 0, "rules": {}})
        policy = module_policies[path]
        if current["count"] > policy["budget"]:
            violations.append(
                f"{path}: diagnostic count {current['count']} exceeds budget {policy['budget']}"
            )
        for rule, count in current["rules"].items():
            budget = policy["rules"].get(rule, 0)
            if count > budget:
                violations.append(f"{path}: {rule} count {count} exceeds budget {budget}")
    return violations


def broad_drift(summary: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, object]]:
    """Describe all module-count drift without turning non-owned work into a gate."""
    paths = sorted(set(summary["modules"]) | set(baseline["modules"]))
    drift: list[dict[str, object]] = []
    for path in paths:
        current = summary["modules"].get(path, {"count": 0})["count"]
        expected = baseline["modules"].get(path, {"budget": 0})["budget"]
        if current != expected:
            drift.append(
                {
                    "module": path,
                    "baseline": expected,
                    "current": current,
                    "delta": current - expected,
                }
            )
    return drift


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHON_GIL"] = "0"
    return subprocess.run(
        command,
        cwd=REPO,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def run_ty(paths: list[str]) -> tuple[str, dict[str, Any]]:
    """Run the pinned ty and return its version plus structured summary."""
    executable = shutil.which("ty")
    if executable is None:
        raise RuntimeError("ty is not installed; run `uv sync --group dev`")

    version_result = _run([executable, "--version"])
    if version_result.returncode != 0:
        raise RuntimeError(version_result.stderr.strip() or "failed to read ty version")
    version_text = version_result.stdout.strip()
    version_parts = version_text.split()
    if len(version_parts) < 2:
        raise RuntimeError(f"unexpected ty version output: {version_text!r}")

    result = _run(
        [
            executable,
            "check",
            *paths,
            "--output-format",
            "gitlab",
            "--exit-zero",
            "--color",
            "never",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ty audit failed to execute")
    try:
        diagnostics = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ty did not emit valid GitLab JSON: {exc}") from exc
    if not isinstance(diagnostics, list):
        raise RuntimeError("ty GitLab report must be a JSON list")
    return version_parts[1], summarize_diagnostics(diagnostics)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("paths", nargs="*", default=["src"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
        tool_version, summary = run_ty(args.paths)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ty diagnostic ratchet failed: {exc}", file=sys.stderr)
        return 2

    violations = ratchet_violations(summary, baseline)
    if tool_version != baseline["tool_version"]:
        violations.insert(
            0,
            f"ty version {tool_version} does not match baseline {baseline['tool_version']}",
        )
    drift = broad_drift(summary, baseline)
    payload = {
        "schema_version": 1,
        "mode": "report" if args.report_only else "ratchet",
        "tool_version": tool_version,
        "baseline": str(args.baseline),
        "baseline_total": baseline["total_diagnostics"],
        "current": summary,
        "broad_drift": drift,
        "violations": violations,
        "ok": not violations or args.report_only,
    }

    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            "ty diagnostic "
            f"{payload['mode']}: {summary['total']} current repo-wide, "
            f"{baseline['total_diagnostics']} baseline, {len(violations)} violation(s)"
        )
        for violation in violations:
            print(f"  - {violation}")
        if drift:
            print(f"  broad drift: {len(drift)} module(s); non-owned changes are report-only")

    if args.report_only:
        return 0
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main())
