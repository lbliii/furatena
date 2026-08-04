"""Packaged docs and vanilla complete-layout conformance."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from chirp.testing.client import TestClient

from furatena.catalog.config import PresentationConfig, load_docs_config
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.pdf_export import PDFExportOptions, export_pdfs
from furatena.catalog.presentation_pack import discover_presentation_packs
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from furatena.catalog.theme_lint import check_theme_assets
from furatena.catalog.view_kinds import VIEW_KINDS
from furatena.catalog.view_lint import check_view_templates
from tests.support import write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]
DOCS_PACK = REPO / "src" / "furatena" / "themes" / "docs"
APP_THEME = REPO / "app" / "theme"


def _write_site(tmp_path: Path):
    app_root = tmp_path / "app"
    content = app_root / "content"
    (content / "section").mkdir(parents=True)
    pages = {
        "_index.md": "---\ntitle: Home\nlayout: home\n---\n# Welcome\n",
        "doc.md": "---\ntitle: Document\nlayout: doc\n---\n# Document\n\n## Details\n",
        "section/_index.md": ("---\ntitle: Section\nlayout: doc_list\n---\n# Section\n"),
        "collection.md": (
            "---\ntitle: Collection\nlayout: collection\ncollection: sample\n---\n# Collection\n"
        ),
        "changelog.md": "---\ntitle: Changelog\nlayout: changelog\n---\n# Changelog\n",
        "api.md": "---\ntitle: API\nlayout: api_reference\n---\n# API\n",
        "page.md": "---\ntitle: Page\nlayout: page\n---\n# Page\n",
    }
    for relative, source in pages.items():
        path = content / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    with (app_root / "docs.yaml").open("a", encoding="utf-8") as handle:
        handle.write("compose:\n  collection:\n    data: collections.yaml\n")
    (app_root / "collections.yaml").write_text(
        "sample:\n  title: Sample\n  nodes: [doc, page]\n",
        encoding="utf-8",
    )
    write_mounts_yaml(app_root / "mounts.yaml", content)
    return app_root, load_docs_config(app_root / "docs.yaml")


def _app(config, tmp_path: Path) -> DocsApp:
    return DocsApp(
        config,
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, False, False),
    )


def test_packaged_layout_manifests_own_every_view_kind(tmp_path: Path) -> None:
    _app_root, config = _write_site(tmp_path)
    packs = {pack.id: pack for pack in discover_presentation_packs(config)}
    expected = {spec.kind for spec in VIEW_KINDS}

    assert {"docs", "vanilla"} <= packs.keys()
    for identity in ("docs", "vanilla"):
        pack = packs[identity]
        assert pack.kind == "layout"
        assert pack.source == "packaged"
        assert {name for name, _path in pack.templates} == expected


def test_docs_pack_is_extracted_from_proven_app_templates() -> None:
    pairs = [
        (APP_THEME / "shell.html", DOCS_PACK / "shell.html"),
        (APP_THEME / "search.html", DOCS_PACK / "search.html"),
        (
            APP_THEME / "partials" / "docs_head_assets.html",
            DOCS_PACK / "partials" / "docs_head_assets.html",
        ),
        (
            APP_THEME / "partials" / "docs_runtime_scripts.html",
            DOCS_PACK / "partials" / "docs_runtime_scripts.html",
        ),
    ]
    for directory in ("layouts", "views", "templates"):
        for source in sorted((APP_THEME / directory).rglob("*.html")):
            pairs.append((source, DOCS_PACK / source.relative_to(APP_THEME)))

    assert pairs
    for source, packaged in pairs:
        assert packaged.read_bytes() == source.read_bytes(), source


def test_site_without_templates_renders_every_layout_surface(tmp_path: Path) -> None:
    app_root, docs_config = _write_site(tmp_path)
    vanilla_config = replace(
        docs_config,
        presentation=PresentationConfig(layout="vanilla"),
    )
    assert not (app_root / "theme").exists()
    assert not (app_root / "templates").exists()

    routes = (
        "/",
        "/doc/",
        "/section/",
        "/collection/",
        "/changelog/",
        "/api/",
        "/page/",
        "/portal/",
        "/search?q=document",
        "/missing/",
    )

    async def render(config):
        docs = _app(config, tmp_path)
        client = TestClient(docs.create_app())
        responses = [await client.get(route) for route in routes]
        fragment = await client.get("/doc/", headers={"HX-Request": "true"})
        return docs, responses, fragment

    docs_app, docs_responses, docs_fragment = asyncio.run(render(docs_config))
    vanilla_app, vanilla_responses, vanilla_fragment = asyncio.run(render(vanilla_config))

    for responses in (docs_responses, vanilla_responses):
        assert [response.status for response in responses] == [
            200,
            200,
            200,
            200,
            200,
            200,
            200,
            200,
            200,
            404,
        ]
        assert all('id="page-root"' in response.text for response in responses)
    assert "chirp-theme-docs-layout" in docs_responses[1].text
    assert '<form action="/search" method="get"' in vanilla_responses[1].text
    assert all("/docs-static/" not in response.text for response in vanilla_responses)
    assert docs_fragment.status == 200 and "chirp-theme-docs-layout" in docs_fragment.text
    assert "<html" not in docs_fragment.text
    assert vanilla_fragment.status == 200 and "<article" in vanilla_fragment.text
    assert "<html" not in vanilla_fragment.text
    assert vanilla_app.theme.stylesheet_hrefs == ("/docs-presentation/vanilla/styles/vanilla.css",)
    assert docs_app.theme.presentation.layout.id == "docs"


def test_both_layouts_pass_existing_typed_view_contract(tmp_path: Path) -> None:
    _app_root, docs_config = _write_site(tmp_path)
    for identity in ("docs", "vanilla"):
        config = replace(
            docs_config,
            presentation=(
                PresentationConfig(layout=identity)
                if identity == "vanilla"
                else PresentationConfig()
            ),
        )
        docs = _app(config, tmp_path)
        errors, _warnings = check_view_templates(
            config,
            docs.theme,
            strict=True,
            repo_root=tmp_path,
            catalog=docs.catalog,
        )
        assert not errors, f"{identity}:\n" + "\n".join(errors)


def test_vanilla_passes_theme_asset_lint_without_a_skin(tmp_path: Path) -> None:
    _app_root, docs_config = _write_site(tmp_path)
    config = replace(
        docs_config,
        presentation=PresentationConfig(layout="vanilla"),
    )

    errors, _warnings = check_theme_assets(config)

    assert errors == []


def test_project_template_shadows_packaged_layout_without_mutating_it(
    tmp_path: Path,
) -> None:
    app_root, docs_config = _write_site(tmp_path)
    override = app_root / "templates" / "views" / "doc.html"
    override.parent.mkdir(parents=True)
    override.write_text(
        '{% extends "layouts/docs_catalog.html" %}\n'
        "{% block catalog_article %}"
        '<article data-adopter-override="true"><h1>{{ node.title }}</h1></article>'
        "{% end %}\n",
        encoding="utf-8",
    )

    async def render() -> str:
        client = TestClient(_app(docs_config, tmp_path).create_app())
        return (await client.get("/doc/")).text

    before = (DOCS_PACK / "views" / "doc.html").read_bytes()
    html = asyncio.run(render())

    assert 'data-adopter-override="true"' in html
    assert (DOCS_PACK / "views" / "doc.html").read_bytes() == before


def test_visibility_agent_static_and_pdf_outputs_are_layout_independent(
    tmp_path: Path,
) -> None:
    _app_root, docs_config = _write_site(tmp_path)
    vanilla_config = replace(
        docs_config,
        presentation=PresentationConfig(layout="vanilla"),
    )
    docs_app = _app(docs_config, tmp_path)
    vanilla_app = _app(vanilla_config, tmp_path)

    async def agent_payloads(app: DocsApp):
        client = TestClient(app.create_app())
        catalog = await client.get("/catalog.json")
        llms = await client.get("/llms.txt")
        return json.loads(catalog.text), llms.text

    assert asyncio.run(agent_payloads(docs_app)) == asyncio.run(agent_payloads(vanilla_app))

    docs_static = tmp_path / "docs-public"
    vanilla_static = tmp_path / "vanilla-public"
    export_static_site(
        docs_app,
        StaticExportOptions(
            output_dir=docs_static,
            include_index_txt=False,
            include_portal=False,
            include_search=False,
        ),
    )
    export_static_site(
        vanilla_app,
        StaticExportOptions(
            output_dir=vanilla_static,
            include_index_txt=False,
            include_portal=False,
            include_search=False,
        ),
    )
    assert (docs_static / "catalog.json").read_bytes() == (
        vanilla_static / "catalog.json"
    ).read_bytes()
    assert "/docs-static/" not in (vanilla_static / "doc" / "index.html").read_text(
        encoding="utf-8"
    )

    docs_pdf = export_pdfs(
        docs_app.catalog,
        config=docs_config,
        options=PDFExportOptions(output_dir=tmp_path / "docs-pdf", update_channel_manifest=False),
    )
    vanilla_pdf = export_pdfs(
        vanilla_app.catalog,
        config=vanilla_config,
        options=PDFExportOptions(
            output_dir=tmp_path / "vanilla-pdf",
            update_channel_manifest=False,
        ),
    )
    assert docs_pdf.node_count == vanilla_pdf.node_count
    assert docs_pdf.page_count == vanilla_pdf.page_count
