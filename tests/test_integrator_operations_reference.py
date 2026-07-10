"""Integrator reference remains exhaustive against runtime-owned contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from furatena.catalog.access import AccessRole
from furatena.catalog.deployment_profiles import DEPLOYMENT_PROFILES
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.docs_inventory import collect_public_surfaces
from furatena.catalog.runtime import ServeConfig, ServeMode

REPO = Path(__file__).resolve().parents[1]
REFERENCE = REPO / "content" / "furatena" / "docs" / "reference" / "integrator-operations.md"


@pytest.fixture(scope="module")
def public_surfaces() -> tuple[object, ...]:
    docs = DocsApp.from_paths(
        REPO / "app" / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    return collect_public_surfaces(docs)


def _row(text: str, name: str) -> str:
    marker = f"| `{name}` |"
    return next(line for line in text.splitlines() if line.startswith(marker))


def test_reference_names_every_operational_surface(public_surfaces: tuple[object, ...]) -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    operational = {
        "deployment_profile",
        "diagnostic",
        "mcp_resource",
        "mcp_tool",
        "sidecar",
    }

    missing = [
        surface.id
        for surface in public_surfaces
        if surface.kind in operational and f"`{surface.name}`" not in text
    ]
    assert missing == []


def test_mcp_rows_cover_live_input_and_output_schemas(public_surfaces: tuple[object, ...]) -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    for surface in public_surfaces:
        if surface.kind != "mcp_tool":
            continue
        row = _row(text, surface.name)
        input_schema = surface.contract["inputSchema"]
        output_schema = surface.contract["outputSchema"]
        for name in input_schema.get("properties", {}):
            assert f"`{name}`" in row, (surface.name, name)
        for name in input_schema.get("required", []):
            assert f"`{name}`" in row, (surface.name, name)
        for name in output_schema.get("required", []):
            assert f"`{name}`" in row, (surface.name, name)


def test_access_deployment_export_and_source_health_contracts_are_explicit() -> None:
    text = REFERENCE.read_text(encoding="utf-8")

    for role in AccessRole:
        assert f"| `{role.value}` |" in text
    for visibility in ("public", "unlisted", "internal", "private", "draft", "archived"):
        assert f"| `{visibility}` |" in text
    for profile in DEPLOYMENT_PROFILES:
        assert f"| `{profile.id}` |" in text
    for output in (
        "/catalog.json",
        "/search.json",
        "/tools.json",
        "/llms.txt",
        "/llms-full.txt",
        "/sitemap.xml",
        "/index.txt",
    ):
        assert f"`{output}`" in text
    for state in ("healthy", "degraded", "unavailable"):
        assert f"| `{state}` |" in text
    for field in ("source.status", "source.stage", "index.status", "index.stage"):
        assert f"`{field}`" in text
    assert "git executable is required for git-backed mounts" in text
    assert "source path does not exist" in text
