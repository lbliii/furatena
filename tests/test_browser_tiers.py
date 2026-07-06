"""Browser tests must declare a documented risk and runtime tier."""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BROWSER_TEST = REPO / "tests" / "test_author_sse_browser.py"
TIERS = {"browser_smoke", "browser_authoring", "browser_responsive"}


def _mark_name(decorator: ast.expr) -> str | None:
    if not isinstance(decorator, ast.Attribute):
        return None
    owner = decorator.value
    if not isinstance(owner, ast.Attribute) or owner.attr != "mark":
        return None
    if not isinstance(owner.value, ast.Name) or owner.value.id != "pytest":
        return None
    return decorator.attr


def test_every_browser_test_has_a_purpose_tier_and_full_regression_marker() -> None:
    tree = ast.parse(BROWSER_TEST.read_text(encoding="utf-8"), filename=str(BROWSER_TEST))
    tests = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]

    assert tests
    observed_tiers: set[str] = set()
    for test in tests:
        marks = {name for decorator in test.decorator_list if (name := _mark_name(decorator))}
        assert "browser" in marks, f"{test.name} must declare the base browser marker"
        assert "browser_full" in marks, f"{test.name} must run in the full browser tier"
        purpose = marks & TIERS
        assert purpose, f"{test.name} must declare a documented browser purpose tier"
        observed_tiers.update(purpose)

    assert observed_tiers == TIERS
