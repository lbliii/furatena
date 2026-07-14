"""Repository changelog, governance, and actionable-error hygiene contracts."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def test_governance_documents_are_actionable() -> None:
    required = {
        "CONTRIBUTING.md": ("make ci-fast", "changelog.d/issue.type.md", "docs/releasing.md"),
        "SECURITY.md": ("private vulnerability", "supported versions", "never reuse"),
        "AGENTS.md": ("python_gil=0", "changelog.d/issue.type.md", "make ci-fast"),
        "CLAUDE.md": ("agents.md",),
    }
    for relative, phrases in required.items():
        text = (ROOT / relative).read_text(encoding="utf-8").lower()
        for phrase in phrases:
            assert phrase in text


def test_changelog_fragment_contract_and_release_preview() -> None:
    checker = _load_script("check_changelog_fragments.py")
    assert checker.validate_fragments() == ()
    assert checker.has_release_note_intent(("src/furatena/catalog/models.py",)) is False
    assert checker.has_release_note_intent(("changelog.d/365.changed.md",)) is True

    completed = subprocess.run(
        (sys.executable, "-m", "towncrier", "build", "--draft", "--version", "0.1.1"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Added Towncrier release fragments" in completed.stdout
    assert "private-repository releases" in completed.stdout


def test_raise_message_and_silent_exception_ratchets_pass() -> None:
    for command in (
        (sys.executable, "scripts/lint_raise_messages.py"),
        (sys.executable, "-m", "ruff", "check", "src", "--select", "S110,S112"),
    ):
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr


def test_coverage_ratchet_is_exported_as_a_reusable_pattern() -> None:
    guide = (ROOT / "docs" / "COVERAGE_RATCHET.md").read_text(encoding="utf-8").lower()
    for phrase in (
        "per-module coverage ratchet",
        "config/core-coverage.json",
        "scripts/check_core_coverage.py",
        "make ci-coverage",
        "ancestor of `main`",
    ):
        assert phrase in guide
