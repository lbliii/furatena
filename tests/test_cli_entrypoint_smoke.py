"""Thin subprocess coverage for the installed ``fura`` console entry point."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("argument", "expected"),
    (("--version", "Furatena"), ("--help", "hypermedia docs catalog")),
)
def test_installed_fura_entrypoint(argument: str, expected: str) -> None:
    executable = Path(sys.executable).with_name("fura")
    assert executable.is_file(), "the test environment must install the fura console script"

    completed = subprocess.run(
        (str(executable), argument),
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHON_GIL": "0"},
        timeout=10,
    )

    assert completed.returncode == 0
    assert expected in completed.stdout


def test_cli_logic_suite_does_not_spawn_fura_subprocesses() -> None:
    logic_suite = Path(__file__).with_name("test_fura_cli_standalone.py")
    source = logic_suite.read_text(encoding="utf-8")

    assert "import subprocess" not in source
    assert "subprocess.run" not in source
