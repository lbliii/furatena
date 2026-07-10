"""End-to-end contracts for the task-oriented documentation journeys."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from furatena.catalog.config import load_docs_config
from furatena.catalog.registry import CatalogRegistry

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


def _urls(items: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for item in items:
        if href := str(item.get("href") or ""):
            found.add(href)
        found.update(_urls(list(item.get("children") or ())))
    return found


@pytest.fixture(scope="module")
def catalog():
    config = load_docs_config(APP_ROOT / "docs.yaml")
    registry = CatalogRegistry.from_config(
        config.mounts_path,
        repo_root=REPO,
        app_root=APP_ROOT,
        rewrites_path=config.rewrites_path,
        inventories_path=config.inventories_path,
        autodoc=False,
        catalog_nav=config.catalog,
    )
    return registry._shards[next(mount for mount in registry.mounts if mount.default).id]


def test_every_existing_docs_url_has_exactly_one_journey(catalog) -> None:
    journeys = catalog.nav_tree()
    assert [item["title"] for item in journeys[:5]] == [
        "Adopt",
        "Author",
        "Publish",
        "Operate",
        "Integrate",
    ]

    ownership = Counter(
        url
        for journey in journeys[:5]
        for url in _urls(list(journey.get("children") or ())) | {journey["href"]}
        if url.startswith("/docs/")
    )
    public_docs = {
        node.url for node in catalog.nodes if node.url.startswith("/docs/") and node.url != "/docs/"
    }

    assert set(ownership) == public_docs
    assert [url for url, count in ownership.items() if count != 1] == []
    assert all(journey.get("children") for journey in journeys[:5])


def test_adopt_path_reaches_first_edit_and_first_deploy(catalog) -> None:
    adopt = catalog.nav_tree()[0]
    adopt_urls = _urls(list(adopt["children"]))
    assert {
        "/docs/get-started/installation/",
        "/docs/get-started/quickstart/",
        "/docs/get-started/first-github-pages-deploy/",
    } <= adopt_urls

    quickstart = catalog.get_by_slug("docs/get-started/quickstart")
    assert quickstart is not None
    assert "docs/get-started/project-layout" in quickstart.body_md
    assert "docs/get-started/first-github-pages-deploy" in quickstart.body_md


def test_platform_and_agent_entry_paths_are_discoverable(catalog) -> None:
    integrate = catalog.nav_tree()[4]
    assert integrate["href"] == "/docs/operations/consume-agent-outputs/"
    assert {
        "/docs/operations/consume-agent-outputs/",
        "/docs/concepts/platform-proof/",
        "/docs/reference/integrator-operations/",
    } <= _urls(list(integrate["children"]))
