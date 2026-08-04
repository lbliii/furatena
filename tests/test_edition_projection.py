"""Executable real-tag contracts for cross-edition projection and switching."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from chirp.testing import TestClient

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.edition_projection import (
    EditionProjection,
    validate_edition_projection_payload,
    write_edition_projection,
)
from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from furatena.catalog.versions import resolve_channel_target
from tests.support import copy_app_theme, write_minimal_docs_yaml

REPO = Path(__file__).resolve().parents[1]
PILOT_FIXTURE = REPO / "tests/fixtures/edition-projection/v1/pilot.json"
CONTRACT_FIXTURE = REPO / "tests/fixtures/edition-projection/v1/projection.json"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_pilot_revision(source: Path, revision: dict) -> None:
    for mount, pages in revision["mounts"].items():
        root = source / mount
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        for slug, record in pages.items():
            target = root / ("_index.md" if not slug else f"{slug}.md")
            if "/" in slug:
                parent, name = slug.rsplit("/", 1)
                target = root / parent / f"{name}.md"
            elif slug == "topic":
                target = root / "topic" / "_index.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            supersedes = f"supersedes: {record['supersedes']}\n" if record.get("supersedes") else ""
            target.write_text(
                "---\n"
                f"title: {record['title']}\n"
                f"{supersedes}"
                "---\n"
                f"# {record['title']}\n\n{record['body']}\n",
                encoding="utf-8",
            )


@pytest.fixture(scope="module")
def pilot(tmp_path_factory: pytest.TempPathFactory) -> tuple[DocsApp, Path, Path]:
    tmp_path = tmp_path_factory.mktemp("edition-projection-pilot")
    fixture = json.loads(PILOT_FIXTURE.read_text(encoding="utf-8"))
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "tests@example.com")
    _git(source, "config", "user.name", "Tests")
    for revision in fixture["editions"]:
        _write_pilot_revision(source, revision)
        _git(source, "add", "docs", "sdk")
        _git(
            source,
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            f"edition {revision['id']}",
        )
        if revision["id"] != "latest":
            _git(source, "tag", f"v{revision['id']}")

    app_root = tmp_path / "app"
    app_root.mkdir()
    copy_app_theme(app_root, REPO / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    (app_root / "mounts.yaml").write_text(
        "mounts:\n"
        "  - id: docs\n"
        "    label: Docs\n"
        "    default: true\n"
        "    source:\n"
        "      provider: git\n"
        f"      repo: {source}\n"
        "      ref: main\n"
        "      path: docs\n"
        "    editions:\n"
        "      source: tags\n"
        "      count: 2\n"
        "  - id: sdk\n"
        "    label: SDK\n"
        "    url_prefix: /sdk\n"
        "    source:\n"
        "      provider: git\n"
        f"      repo: {source}\n"
        "      ref: main\n"
        "      path: sdk\n"
        "    editions:\n"
        "      source: tags\n"
        "      count: 2\n",
        encoding="utf-8",
    )
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    return docs, app_root, tmp_path


def test_real_tags_emit_queryable_edges_and_preserve_shared_provenance(
    pilot: tuple[DocsApp, Path, Path],
) -> None:
    docs, _app_root, _tmp_path = pilot
    projection = docs.catalog.edition_projection()
    payload = projection.to_dict()

    shared_pages = [
        page for page in projection.pages if page.mount == "docs" and page.slug == "shared"
    ]
    assert {page.node_id for page in shared_pages} == {
        "docs:latest:shared",
        "docs:1.1.0:shared",
        "docs:1.0.0:shared",
    }
    assert len({page.shared_node_id for page in shared_pages}) == 1
    shared = next(item for item in payload["shared_nodes"] if item["slug"] == "shared")
    assert len(shared["members"]) == 3
    assert len({item["resolved_ref"] for item in shared["members"]}) == 3

    edges = payload["edges"]
    assert {
        edge["target"]
        for edge in edges
        if edge["kind"] == "available_in" and edge["source"] == "docs:latest:shared"
    } == {
        "release:docs:latest",
        "release:docs:1.1.0",
        "release:docs:1.0.0",
    }
    assert {
        "kind": "supersedes",
        "source": "docs:latest:new-location",
        "target": "docs:1.0.0:old-location",
        "mount": "docs",
        "edition": "latest",
    } in edges
    assert payload["metrics"]["attempts"] > 0
    assert payload["metrics"]["direct_hits"] > 0
    assert payload["metrics"]["resolutions"]["supersedes"] > 0
    assert payload["metrics"]["resolutions"]["ancestor"] > 0
    assert payload["metrics"]["resolutions"]["landing"] > 0


def test_projection_schema_rejects_malformed_contracts(
    pilot: tuple[DocsApp, Path, Path],
) -> None:
    docs, _app_root, _tmp_path = pilot
    fixture = json.loads(CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    actual = docs.catalog.edition_projection().to_dict()
    assert validate_edition_projection_payload(fixture) == []
    assert validate_edition_projection_payload(actual) == []

    unexpected = copy.deepcopy(fixture)
    unexpected["pages"][0]["surprise"] = True
    assert any("surprise" in error for error in validate_edition_projection_payload(unexpected))
    bad_kind = copy.deepcopy(fixture)
    bad_kind["edges"][0]["kind"] = "similar_to"
    assert any("similar_to" in error for error in validate_edition_projection_payload(bad_kind))
    missing_identity = copy.deepcopy(fixture)
    del missing_identity["pages"][0]["node_id"]
    assert any(
        "node_id" in error for error in validate_edition_projection_payload(missing_identity)
    )
    with pytest.raises(ValueError, match="invalid edition projection"):
        EditionProjection.from_dict(unexpected)


def test_resolution_precedence_and_mount_boundary_use_projection_index(
    pilot: tuple[DocsApp, Path, Path],
) -> None:
    docs, _app_root, _tmp_path = pilot
    catalog = docs.catalog

    guide = catalog.get_by_slug("guide", mount="docs")
    moved = catalog.get_by_slug("new-location", mount="docs")
    child = catalog.get_by_slug("topic/new", mount="docs")
    landing_only = catalog.get_by_slug("brand-new", mount="docs")
    mount_collision = catalog.get_by_slug("sdk-only", mount="docs")
    assert all((guide, moved, child, landing_only, mount_collision))

    direct = resolve_channel_target(catalog, guide, "1.0.0")
    assert direct is not None and direct.resolution == "direct"
    replacement = resolve_channel_target(catalog, moved, "1.0.0")
    assert replacement is not None
    assert replacement.resolution == "supersedes"
    assert replacement.resolved_slug == "old-location"
    ancestor = resolve_channel_target(catalog, child, "1.0.0")
    assert ancestor is not None
    assert ancestor.resolution == "ancestor"
    assert ancestor.resolved_slug == "topic"
    landing = resolve_channel_target(catalog, landing_only, "1.0.0")
    assert landing is not None
    assert landing.resolution == "landing"
    assert landing.resolved_slug == ""
    bounded = resolve_channel_target(catalog, mount_collision, "1.0.0")
    assert bounded is not None
    assert bounded.resolution == "landing"
    assert "/sdk/" not in bounded.href


def test_http_mcp_and_release_graph_queries_expose_projection_edges(
    pilot: tuple[DocsApp, Path, Path],
) -> None:
    docs, _app_root, _tmp_path = pilot
    client = TestClient(docs.create_app())

    async def fetch() -> tuple[dict, dict, dict]:
        async with client:
            available = await client.get(
                "/catalog/query.json?edge_kind=available_in&target=release:docs:1.0.0"
            )
            moved = await client.get(
                "/catalog/query.json?edge_kind=supersedes&target=docs:1.0.0:old-location"
            )
            chronology = await client.get(
                "/catalog/query.json?edge_kind=supersedes&source=release:docs:latest"
            )
        assert available.status == moved.status == chronology.status == 200
        return json.loads(available.text), json.loads(moved.text), json.loads(chronology.text)

    available, moved, chronology = asyncio.run(fetch())
    assert any(edge["kind"] == "available_in" for edge in available["edges"])
    assert any(edge["target"] == "docs:1.0.0:old-location" for edge in moved["edges"])
    assert chronology["pages"] == []
    assert chronology["edges"][0]["source"] == "release:docs:latest"

    mcp = FuraMCPServer(docs)
    graph = mcp.call_tool(
        "query_graph",
        {"edge_kind": "available_in", "target": "release:docs:1.0.0"},
    )["structuredContent"]
    assert graph["edges"] == available["edges"]


def test_fallback_notice_survives_htmx_fragment_switch(
    pilot: tuple[DocsApp, Path, Path],
) -> None:
    docs, _app_root, _tmp_path = pilot
    client = TestClient(docs.create_app())

    async def fetch() -> tuple[str, str, str, str]:
        async with client:
            source = await client.get("/topic/new/")
            target = docs.catalog.get_by_slug("topic/new", mount="docs")
            assert target is not None
            fallback = resolve_channel_target(docs.catalog, target, "1.0.0")
            assert fallback is not None
            fragment = await client.get(
                fallback.href,
                headers={"HX-Request": "true"},
            )
            forged = await client.get(
                fallback.href.replace("version_fallback=ancestor", "version_fallback=landing"),
                headers={"HX-Request": "true"},
            )
            replacement = docs.catalog.get_by_slug("new-location", mount="docs")
            assert replacement is not None
            moved = resolve_channel_target(docs.catalog, replacement, "1.0.0")
            assert moved is not None
        assert source.status == fragment.status == forged.status == 200
        return source.text, fragment.text, forged.text, moved.href

    source_html, fragment_html, forged_html, moved_href = asyncio.run(fetch())
    assert "version_fallback=ancestor" in source_html
    assert "version_source=docs%3Alatest%3Atopic%2Fnew" in source_html
    assert 'hx-boost="true"' in source_html
    assert "target.click()" in source_html
    assert 'data-version-fallback="ancestor"' in fragment_html
    assert "Showing the nearest available ancestor." in fragment_html
    assert "data-version-fallback" not in forged_html
    assert "version_fallback" not in moved_href


def test_freeze_writes_projection_and_preview_reuses_exact_contract(
    pilot: tuple[DocsApp, Path, Path],
) -> None:
    docs, app_root, tmp_path = pilot
    frozen = tmp_path / "frozen-projection"
    first_started = time.perf_counter()
    first_freeze = freeze_catalog(
        FreezeCatalogOptions(
            docs_config=app_root / "docs.yaml",
            app_root=app_root,
            repo_root=tmp_path,
            output_dir=frozen,
            autodoc=False,
        )
    )
    first_seconds = time.perf_counter() - first_started
    frozen_payload = json.loads((frozen / "edition-projection.json").read_text(encoding="utf-8"))
    assert frozen_payload == docs.catalog.edition_projection().to_dict()
    first_bytes = (frozen / "edition-projection.json").read_bytes()
    second_started = time.perf_counter()
    second_freeze = freeze_catalog(
        FreezeCatalogOptions(
            docs_config=app_root / "docs.yaml",
            app_root=app_root,
            repo_root=tmp_path,
            output_dir=frozen,
            autodoc=False,
        )
    )
    second_seconds = time.perf_counter() - second_started
    assert first_freeze.frozen_editions
    assert second_freeze.reused_editions
    assert (frozen / "edition-projection.json").read_bytes() == first_bytes
    print(
        "edition-freeze profile: "
        f"cold={first_seconds:.6f}s incremental={second_seconds:.6f}s "
        f"reused={len(second_freeze.reused_editions)}"
    )

    preview = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, frozen, True, False),
    )
    assert preview.catalog.edition_projection().to_dict() == frozen_payload

    output = tmp_path / "static-projection"
    export_static_site(
        preview,
        StaticExportOptions(
            output_dir=output,
            include_index_txt=False,
            include_portal=False,
            include_search=False,
        ),
    )
    assert (
        json.loads((output / "edition-projection.json").read_text(encoding="utf-8"))
        == frozen_payload
    )
    assert (output / "v1.0.0" / "index.md").is_file()
    assert not (output / "v1.0.0.md").exists()
    static_graph = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
    assert any(edge["kind"] == "available_in" for edge in static_graph["edges"])


def test_projection_cache_is_coalesced_and_writes_deterministic_bytes(
    pilot: tuple[DocsApp, Path, Path],
) -> None:
    docs, _app_root, tmp_path = pilot
    with docs.catalog._edition_projection_lock:
        docs.catalog._edition_projection_cache = None
    with docs.catalog._edition_shards_lock:
        docs.catalog._edition_shards.clear()
    cold_started = time.perf_counter()
    cold = docs.catalog.edition_projection()
    cold_seconds = time.perf_counter() - cold_started
    hit_started = time.perf_counter()
    for _index in range(10_000):
        assert docs.catalog.edition_projection() is cold
    hit_seconds = (time.perf_counter() - hit_started) / 10_000
    with docs.catalog._edition_projection_lock:
        docs.catalog._edition_projection_cache = None
    with ThreadPoolExecutor(max_workers=8) as pool:
        projections = list(pool.map(lambda _index: docs.catalog.edition_projection(), range(32)))
    assert all(item is projections[0] for item in projections)
    assert hit_seconds < cold_seconds
    print(
        "edition-projection profile: "
        f"cold={cold_seconds:.6f}s hit={hit_seconds:.9f}s "
        f"speedup={cold_seconds / hit_seconds:.1f}x coalesced={len(projections)}"
    )

    output = tmp_path / "deterministic-projection"
    output.mkdir()
    assert write_edition_projection(docs.catalog, output) is True
    first = (output / "edition-projection.json").read_bytes()
    assert write_edition_projection(docs.catalog, output) is False
    assert (output / "edition-projection.json").read_bytes() == first


def test_packaged_docs_theme_preserves_fragment_version_switching() -> None:
    runtime = (REPO / "src/furatena/themes/docs/partials/docs_runtime_scripts.html").read_text(
        encoding="utf-8"
    )
    version_selector = runtime.split("function setupVersionSelector()", 1)[1].split(
        "function setupSearchPageSync()", 1
    )[0]
    layout = (REPO / "src/furatena/themes/docs/layouts/docs_catalog.html").read_text(
        encoding="utf-8"
    )

    assert "target.click()" in version_selector
    assert "window.location.href = href" not in version_selector
    assert '{% include "partials/version_fallback_notice.html" %}' in layout
