"""Generation-scoped validation snapshot behavior and surface agreement."""

from __future__ import annotations

import asyncio
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from chirp.testing import TestClient

import furatena.catalog.validation as validation_module
from furatena.catalog.author_benchmarks import (
    _write_author_config,
    generate_author_corpus,
    instrument_author_runtime,
)
from furatena.catalog.check import check_catalog
from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.validation import ValidationSnapshotService
from tests.support import copy_app_theme

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


class _Catalog:
    def __init__(self, root: Path) -> None:
        self.generation = 1
        self.inventory_store = None
        self.repo_root = root


def _service_fixture(tmp_path: Path) -> tuple[_Catalog, ValidationSnapshotService, Path]:
    content_root = generate_author_corpus(tmp_path / "source", page_count=2)
    docs_yaml = _write_author_config(tmp_path / "app", content_root)
    config = load_docs_config(docs_yaml)
    template = config.theme_dir / "views" / "doc.html"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("{% block page_root %}doc{% endblock %}\n", encoding="utf-8")
    catalog = _Catalog(tmp_path)
    service = ValidationSnapshotService(
        catalog,
        views=SimpleNamespace(),
        docs=config,
        theme=SimpleNamespace(template_roots=(config.theme_dir,)),
    )
    return catalog, service, template


def test_snapshot_reuses_content_and_config_until_their_keys_change(tmp_path: Path) -> None:
    catalog, service, template = _service_fixture(tmp_path)
    content_results = [(["content-1"], []), (["content-2"], [])]
    config_results = [([], ["config-1"]), ([], ["config-2"])]

    with (
        patch.object(
            validation_module,
            "check_catalog_content",
            side_effect=content_results,
        ) as content_check,
        patch.object(
            validation_module,
            "check_catalog_configuration",
            side_effect=config_results,
        ) as config_check,
    ):
        first = service.snapshot()
        assert service.snapshot() is first
        assert content_check.call_count == 1
        assert config_check.call_count == 1

        catalog.generation += 1
        content_changed = service.snapshot()
        assert content_changed.catalog_generation == 2
        assert content_changed.errors == ("content-2",)
        assert content_check.call_count == 2
        assert config_check.call_count == 1

        template.write_text("{% block page_root %}changed{% endblock %}\n", encoding="utf-8")
        stamp = max(template.stat().st_mtime_ns + 1_000_000, os.stat(template).st_mtime_ns)
        os.utime(template, ns=(stamp, stamp))
        config_changed = service.snapshot()
        assert config_changed.configuration_fingerprint != first.configuration_fingerprint
        assert config_changed.warnings == ("config-2",)
        assert content_check.call_count == 2
        assert config_check.call_count == 2


def test_snapshot_publication_is_single_compute_under_concurrency(tmp_path: Path) -> None:
    _catalog, service, _template = _service_fixture(tmp_path)
    with (
        patch.object(
            validation_module,
            "check_catalog_content",
            return_value=([], []),
        ) as content_check,
        patch.object(
            validation_module,
            "check_catalog_configuration",
            return_value=([], []),
        ) as config_check,
        ThreadPoolExecutor(max_workers=8) as executor,
    ):
        snapshots = tuple(executor.map(lambda _index: service.snapshot(), range(16)))

    assert all(snapshot is snapshots[0] for snapshot in snapshots)
    assert content_check.call_count == 1
    assert config_check.call_count == 1


def test_author_requests_reuse_snapshot_and_content_edit_refreshes_diagnostics(
    tmp_path: Path,
) -> None:
    content_root = generate_author_corpus(tmp_path / "source", page_count=3)
    app_root = tmp_path / "app"
    docs_yaml = _write_author_config(app_root, content_root)
    copy_app_theme(app_root, APP_ROOT)
    docs = DocsApp.from_paths(
        docs_yaml,
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
        workers=1,
    )
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        docs.app.freeze()
    if docs.catalog._watcher is not None:
        docs.catalog._watcher.stop()
    for shard in docs.catalog._shards.values():
        shard._watcher = None
    client = TestClient(docs.create_app())

    async def _request(path: str):
        return await client.get(path)

    with instrument_author_runtime() as cached_counts:
        page = asyncio.run(_request("/docs/page-00001/"))
        first_status = asyncio.run(_request("/docs/_author/page.json?slug=docs/page-00001"))
        second_status = asyncio.run(_request("/docs/_author/page.json?slug=docs/page-00001"))
    assert page.status == first_status.status == second_status.status == 200
    assert cached_counts.docs_app_constructions == 0
    assert cached_counts.full_validation_calls == 0

    with instrument_author_runtime() as forced_counts:
        forced = asyncio.run(_request("/docs/_author/page.json?slug=docs/page-00001&validate=1"))
    assert forced.status == 200
    assert forced_counts.docs_app_constructions == 0
    assert forced_counts.full_validation_calls == 1

    before = json.loads(second_status.text)
    source = content_root / "docs" / "page-00001.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n[Missing](/docs/does-not-exist/).\n",
        encoding="utf-8",
    )
    stamp = source.stat().st_mtime_ns + 1_000_000
    os.utime(source, ns=(stamp, stamp))
    refreshed_response = asyncio.run(_request("/docs/_author/page.json?slug=docs/page-00001"))
    refreshed = json.loads(refreshed_response.text)
    assert (
        refreshed["validation"]["catalog_generation"] > before["validation"]["catalog_generation"]
    )
    assert refreshed["validation"]["error_count"] >= 1

    snapshot = docs.validation.snapshot()
    mcp_report = FuraMCPServer(docs).validation_report()
    dashboard_response = asyncio.run(_request("/docs/_author/dashboard?json=1"))
    dashboard = json.loads(dashboard_response.text)["data"]
    direct_errors, direct_warnings = check_catalog(
        docs.catalog,
        views=docs.views,
        docs=docs.config,
        theme=docs.theme,
        inventory_store=docs.catalog.inventory_store,
        template_env=docs._validation_template_env(),
    )
    assert tuple(direct_errors) == snapshot.errors
    assert tuple(direct_warnings) == snapshot.warnings
    assert [item["message"] for item in mcp_report["errors"]] == list(snapshot.errors)
    assert [item["message"] for item in mcp_report["warnings"]] == list(snapshot.warnings)
    assert dashboard["catalog_generation"] == snapshot.catalog_generation
    assert {item["raw_message"] for item in dashboard["diagnostics"]} == {
        *snapshot.errors,
        *snapshot.warnings,
    }
