"""Public-surface documentation inventory contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.docs_inventory import (
    build_documentation_inventory,
    collect_public_surfaces,
)
from furatena.catalog.runtime import ServeConfig, ServeMode

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


@pytest.fixture(scope="module")
def inventory_docs() -> DocsApp:
    return DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )


def test_inventory_collects_every_public_surface_kind(inventory_docs: DocsApp) -> None:
    surfaces = collect_public_surfaces(inventory_docs)
    by_id = {surface.id: surface for surface in surfaces}

    assert {surface.kind for surface in surfaces} == {
        "cli_command",
        "config_field",
        "deployment_profile",
        "diagnostic",
        "mcp_resource",
        "mcp_tool",
        "route",
        "sidecar",
    }
    assert "cli_command:docs-inventory" in by_id
    assert "config_field:theme.effects.code" in by_id
    assert "mcp_tool:semantic_search" in by_id
    assert "mcp_resource:fura://catalog/graph" in by_id
    assert "sidecar:/catalog.json" in by_id
    assert "deployment_profile:static-pages" in by_id
    assert all(not surface.name.endswith(".") for surface in surfaces if surface.kind == "diagnostic")
    assert str(REPO) not in json.dumps([surface.contract for surface in surfaces])


def test_inventory_links_docs_and_detects_stale_coverage(
    inventory_docs: DocsApp, tmp_path: Path
) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    (docs_root / "deployment.md").write_text(
        "The static-pages deployment profile publishes /catalog.json.\n",
        encoding="utf-8",
    )
    initial = build_documentation_inventory(
        inventory_docs,
        documentation_roots=(docs_root,),
    )
    by_id = {record["id"]: record for record in initial["surfaces"]}
    assert by_id["deployment_profile:static-pages"]["coverage"] == "documented"
    assert by_id["sidecar:/catalog.json"]["coverage"] == "documented"
    assert initial["missing"]

    previous = copy.deepcopy(initial)
    prior = next(
        record
        for record in previous["surfaces"]
        if record["id"] == "deployment_profile:static-pages"
    )
    prior["implementation_fingerprint"] = "prior-contract"
    refreshed = build_documentation_inventory(
        inventory_docs,
        documentation_roots=(docs_root,),
        previous=previous,
    )

    assert "deployment_profile:static-pages" in refreshed["stale"]
    stale = next(
        record
        for record in refreshed["surfaces"]
        if record["id"] == "deployment_profile:static-pages"
    )
    assert stale["coverage"] == "stale"
