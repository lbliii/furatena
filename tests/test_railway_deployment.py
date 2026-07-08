"""Railway deployment must preserve the frozen, free-threaded production shape."""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_railway_service_is_single_replica_with_readiness_probe() -> None:
    config = tomllib.loads((REPO / "railway.toml").read_text(encoding="utf-8"))

    assert config["build"] == {
        "builder": "dockerfile",
        "dockerfilePath": "Dockerfile",
    }
    assert config["deploy"]["numReplicas"] == 1
    assert config["deploy"]["healthcheckPath"] == "/readyz"
    assert config["deploy"]["startCommand"] == "/app/scripts/railway-start.sh"


def test_container_installs_and_enforces_free_threaded_python() -> None:
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    start = (REPO / "scripts" / "railway-start.sh").read_text(encoding="utf-8")

    assert "uv python install 3.14t" in dockerfile
    assert "PYTHON_GIL=0" in dockerfile
    assert "sys._is_gil_enabled()" in dockerfile
    assert "sys._is_gil_enabled()" in start
    assert "freeze --full --workers 1" in dockerfile
    assert "--preview" in start
    assert "--workers 1" in start
