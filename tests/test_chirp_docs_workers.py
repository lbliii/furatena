"""Tests for parallel docs catalog workers."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

ROOT = APP_ROOT


def test_resolve_workers_explicit():
    from furatena.catalog.workers import resolve_workers

    assert resolve_workers(4) == 4
    assert resolve_workers(0) == 1
    assert resolve_workers(-1) == 1


def test_resolve_workers_env(monkeypatch):
    from furatena.catalog.workers import resolve_workers

    monkeypatch.setenv("FURA_WORKERS", "3")
    assert resolve_workers(None) == 3
    monkeypatch.delenv("FURA_WORKERS", raising=False)
    assert resolve_workers(None) >= 1


def test_parallel_registry_matches_sequential(monkeypatch):
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.registry import CatalogRegistry

    monkeypatch.chdir(ROOT)
    docs_config = load_docs_config(ROOT / "docs.yaml")
    mounts_path = ROOT / "mounts.yaml"
    autodoc_config = REPO / "config" / "autodoc.yaml"
    kwargs = {
        "repo_root": REPO,
        "app_root": ROOT,
        "rewrites_path": docs_config.rewrites_path,
        "inventories_path": docs_config.inventories_path,
        "autodoc_config": autodoc_config,
        "autodoc": False,
    }

    sequential = CatalogRegistry.from_config(mounts_path, workers=1, **kwargs)
    parallel = CatalogRegistry.from_config(mounts_path, workers=4, **kwargs)

    seq_urls = sorted(node.url for node in sequential.nodes)
    par_urls = sorted(node.url for node in parallel.nodes)
    assert seq_urls == par_urls
    assert len(sequential.nodes) == len(parallel.nodes)


def test_dcp_schema_validates_live_registry():
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.dcp_validate import validate_catalog_graph
    from furatena.catalog.registry import CatalogRegistry

    docs_config = load_docs_config(ROOT / "docs.yaml")
    registry = CatalogRegistry.from_config(
        ROOT / "mounts.yaml",
        repo_root=REPO,
        app_root=ROOT,
        rewrites_path=docs_config.rewrites_path,
        inventories_path=docs_config.inventories_path,
        autodoc=False,
        workers=1,
    )
    errors = validate_catalog_graph(registry)
    assert errors == []


def test_dcp_compatibility_fixtures_validate():
    from furatena.catalog.dcp_validate import dcp_fixture_paths, validate_catalog_json_file

    fixtures = dcp_fixture_paths()
    assert {path.name for path in fixtures} == {"catalog-v2.json", "catalog-v3.json"}
    for path in fixtures:
        assert validate_catalog_json_file(path) == []


def test_dcp_validator_rejects_unsupported_version(tmp_path: Path):
    from furatena.catalog.dcp_validate import validate_catalog_json_file

    sample = tmp_path / "catalog.json"
    sample.write_text(
        '{"schema_version": 99, "channel": "latest", "page_count": 0, '
        '"pages": [], "edges": [], "namespaces": []}',
        encoding="utf-8",
    )

    errors = validate_catalog_json_file(sample)
    assert errors
    assert "unsupported DCP schema_version" in errors[0]


def test_html_format_bridge_indexed():
    from furatena.catalog.config import load_docs_config
    from furatena.catalog.registry import CatalogRegistry

    docs_config = load_docs_config(ROOT / "docs.yaml")
    registry = CatalogRegistry.from_config(
        ROOT / "mounts.yaml",
        repo_root=REPO,
        app_root=ROOT,
        rewrites_path=docs_config.rewrites_path,
        inventories_path=docs_config.inventories_path,
        autodoc=False,
        workers=1,
    )
    node = registry.get_by_slug("formats/html-bridge", mount="shared")
    assert node is not None
    assert node.content_format == "html"
    assert "HTML format bridge" in node.title or "html" in node.slug
