"""Release support policy and public-surface contracts."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "docs" / "support-policy.json"


def _policy() -> dict[str, object]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_python_support_metadata_matches_free_threaded_release_runtime() -> None:
    policy = _policy()
    python_policy = policy["python"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["requires-python"] == python_policy["requires_python"]
    assert sys.implementation.name == python_policy["implementation"].lower()
    assert f"{sys.version_info.major}.{sys.version_info.minor}" == python_policy["version"]
    assert os.environ.get("PYTHON_GIL") == "0"
    gil_probe = getattr(sys, "_is_gil_enabled", None)
    assert callable(gil_probe), "support policy requires a free-threaded CPython build"
    assert gil_probe() is python_policy["gil_enabled"]


def test_machine_readable_public_python_exports_match_modules() -> None:
    public_python = _policy()["public_python"]

    for module_name, expected_symbols in public_python.items():
        module = importlib.import_module(module_name)
        assert sorted(module.__all__) == sorted(expected_symbols)
        for symbol in expected_symbols:
            assert hasattr(module, symbol), f"{module_name}.{symbol} is not importable"


def test_policy_defines_every_required_contract_and_deprecation_window() -> None:
    policy = _policy()
    documents = policy["contract_documents"]
    assert set(documents) == {
        "cli",
        "config",
        "dcp",
        "mcp",
        "source_adapters",
        "themes",
    }
    for relative_path in documents.values():
        assert (ROOT / relative_path).is_file()

    deprecation = policy["deprecation"]
    assert deprecation["minimum_days"] >= 90
    assert deprecation["minimum_minor_releases"] >= 2
