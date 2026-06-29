from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from chirp.testing import TestClient

from furatena.catalog.develop_exports import develop_export
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.mcp import FuraMCPServer, MCPAccessPolicy
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.cli.main import main


def test_init_scaffolds_standalone_app(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])

    assert (app_root / "docs.yaml").is_file()
    assert (app_root / "mounts.yaml").is_file()
    assert (app_root / "content" / "docs" / "get-started.md").is_file()
    assert (app_root / "theme" / "views" / "doc.html").is_file()
    assert (app_root / "theme" / "search.html").is_file()


def test_init_json_emits_written_files(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "init"
    assert payload["data"]["count"] > 0
    assert "docs.yaml" in payload["data"]["written"]


def test_init_app_passes_strict_content_check(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main([
        "--app-root",
        str(app_root),
        "check",
        "--content-only",
        "--warnings-as-errors",
    ])


def test_check_json_emits_standard_result(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main([
        "--app-root",
        str(app_root),
        "check",
        "--content-only",
        "--warnings-as-errors",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "check"
    assert payload["exit_code"] == 0
    assert payload["diagnostics"] == []
    assert payload["data"]["content_only"] is True


def test_check_json_validates_bundled_dcp_fixtures(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main([
        "--app-root",
        str(app_root),
        "check",
        "--content-only",
        "--dcp-fixtures",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "check"
    assert payload["data"]["dcp_fixtures"] is True
    assert payload["data"]["dcp_file_count"] == 2


def test_check_agent_only_json_lints_mcp_contracts(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main([
        "--app-root",
        str(app_root),
        "check",
        "--agent-only",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "check"
    assert payload["data"]["agent"] is True
    assert payload["data"]["agent_only"] is True
    assert payload["data"]["agent_error_count"] == 0
    assert payload["diagnostics"] == []


def test_agent_lint_reports_missing_mcp_parameter_description(tmp_path: Path) -> None:
    from furatena.catalog.agent_lint import check_agent_contracts

    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )

    class BrokenServer(FuraMCPServer):
        def list_tools(self):
            tools = super().list_tools()
            search = next(tool for tool in tools if tool["name"] == "semantic_search")
            del search["inputSchema"]["properties"]["query"]["description"]
            return tools

    errors, warnings = check_agent_contracts(BrokenServer(docs))

    assert any(
        finding.rule_id == "fura.agent.parameter_description"
        and "semantic_search parameter query" in finding.message
        for finding in errors
    )
    assert not warnings


def test_agent_evals_json_reports_golden_path_categories(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    api_page = app_root / "content" / "docs" / "api.md"
    api_page.write_text(
        "---\n"
        "title: API Reference\n"
        "description: API operation reference.\n"
        "tags:\n"
        "  - api\n"
        "---\n"
        "# API Reference\n\nCall the example operation.\n",
        encoding="utf-8",
    )
    secret = app_root / "content" / "docs" / "secret.md"
    secret.write_text(
        "---\n"
        "title: Secret Draft\n"
        "description: Private draft fixture.\n"
        "draft: true\n"
        "---\n"
        "# Secret Draft\n\nPrivate notes.\n",
        encoding="utf-8",
    )
    shared = app_root / "shared" / "docs"
    shared.mkdir(parents=True)
    (shared / "hub.md").write_text(
        "---\ntitle: Shared Hub\ndescription: Shared mounted hub.\n---\n# Shared Hub\n",
        encoding="utf-8",
    )
    (app_root / "mounts.yaml").write_text(
        "\n".join(
            [
                "mounts:",
                "  - id: docs",
                "    label: Documentation",
                "    content_root: content",
                "    default: true",
                "    extensions: ['.md', '.html']",
                "  - id: shared",
                "    label: Shared",
                "    content_root: shared",
                "    url_prefix: /shared",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    main([
        "--app-root",
        str(app_root),
        "evals",
        "--include-private",
        "--json",
        "--no-autodoc",
    ])
    payload = json.loads(capsys.readouterr().out)
    results = {item["id"]: item for item in payload["data"]["results"]}

    assert payload["ok"] is True
    assert payload["command"] == "evals"
    assert payload["data"]["fail_count"] == 0
    assert {
        "prose_docs",
        "api_operations",
        "private_content",
        "versioned_content",
        "stale_content",
        "multi_mount_hubs",
        "tool_selection",
    } <= set(payload["data"]["categories"])
    assert results["api-operation-discovery"]["status"] == "pass"
    assert results["private-content-boundary"]["status"] == "pass"
    assert results["multi-mount-hub-discovery"]["status"] == "pass"
    assert results["tool-selection-search"]["expected"]["tool"] == "semantic_search"
    assert results["tool-selection-author-edit"]["expected"]["tool"] == "author_propose_edit"


def test_query_json_uses_standard_result_envelope(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main([
        "--app-root",
        str(app_root),
        "query",
        "--heading",
        "Get started",
        "--json",
        "--no-autodoc",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "query"
    assert payload["data"]["count"] == 1
    assert payload["data"]["results"][0]["title"] == "Get started"


def test_freeze_and_export_json_report_outputs(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main(["--app-root", str(app_root), "freeze", "--json"])
    freeze_payload = json.loads(capsys.readouterr().out)
    main(["--app-root", str(app_root), "export", "--fresh", "--base-path", "", "--json"])
    export_payload = json.loads(capsys.readouterr().out)

    assert freeze_payload["ok"] is True
    assert freeze_payload["command"] == "freeze"
    assert freeze_payload["data"]["page_count"] >= 1
    assert export_payload["ok"] is True
    assert export_payload["command"] == "export"
    assert export_payload["data"]["base_path"] == "/"
    assert (app_root / "public" / "docs" / "get-started" / "index.html").is_file()


def test_export_json_blocks_lifecycle_errors_without_override(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    public = app_root / "content" / "docs" / "get-started.md"
    public.write_text(
        public.read_text(encoding="utf-8") + "\n\nSee [Secret](/docs/secret/).\n",
        encoding="utf-8",
    )
    secret = app_root / "content" / "docs" / "secret.md"
    secret.write_text(
        "---\ntitle: Secret\ndraft: true\n---\n# Secret\n",
        encoding="utf-8",
    )
    broken = app_root / "content" / "docs" / "broken.md"
    broken.write_text("---\ntitle: [broken\n---\n# Broken\n", encoding="utf-8")
    capsys.readouterr()

    try:
        main(["--app-root", str(app_root), "export", "--fresh", "--base-path", "", "--json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("export should fail on lifecycle errors")
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["command"] == "export"
    assert payload["exit_code"] == 2
    assert all(diagnostic["rule_id"] == "fura.lifecycle" for diagnostic in payload["diagnostics"])
    assert any(
        diagnostic["source_path"] == "docs/broken.md"
        and "source frontmatter could not be parsed" in diagnostic["message"]
        for diagnostic in payload["diagnostics"]
    )
    assert any(
        diagnostic["source_path"] == "docs/get-started.md"
        and "public page links to draft/private target" in diagnostic["message"]
        for diagnostic in payload["diagnostics"]
    )

    main([
        "--app-root",
        str(app_root),
        "export",
        "--fresh",
        "--base-path",
        "",
        "--allow-lifecycle-errors",
        "--json",
    ])
    override_payload = json.loads(capsys.readouterr().out)
    assert override_payload["ok"] is True


def test_export_excludes_unlinked_draft_pages(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    secret = app_root / "content" / "docs" / "secret.md"
    secret.write_text(
        "---\ntitle: Secret\ndraft: true\n---\n# Secret\n\nPrivate notes.\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    main(["--app-root", str(app_root), "export", "--fresh", "--base-path", "", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert not (app_root / "public" / "docs" / "secret" / "index.html").exists()
    assert not (app_root / "public" / "docs" / "secret" / "index.txt").exists()
    catalog_payload = json.loads((app_root / "public" / "catalog.json").read_text(encoding="utf-8"))
    search_payload = json.loads((app_root / "public" / "search.json").read_text(encoding="utf-8"))
    assert all(entry["title"] != "Secret" for entry in search_payload["entries"])
    for sidecar in ("catalog.json", "llms.txt", "llms-full.txt", "meta.json", "sitemap.xml"):
        assert "Secret" not in (app_root / "public" / sidecar).read_text(encoding="utf-8")
    tools_payload = json.loads((app_root / "public" / "tools.json").read_text(encoding="utf-8"))
    assert tools_payload["page_count"] == catalog_payload["page_count"]


def test_author_mode_indexes_drafts_with_public_output_filtering(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    secret = app_root / "content" / "docs" / "secret.md"
    secret.write_text(
        "---\ntitle: Secret\ndraft: true\n---\n# Secret\n\nPrivate launch notes.\n",
        encoding="utf-8",
    )
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    node = docs.catalog.get_path("/docs/secret/")
    assert node is not None
    client = TestClient(docs.create_app())

    def json_body(text: str):
        return json.loads(text.split("<script", 1)[0])

    async def _fetch() -> dict[str, object]:
        direct = await client.get("/docs/secret/")
        catalog_public = await client.get("/catalog.json")
        catalog_private = await client.get("/catalog.json?include_private=1")
        search_public = await client.get("/search.json?q=private%20launch")
        search_private = await client.get("/search.json?q=private%20launch&include_private=1")
        llms_public = await client.get("/llms.txt")
        llms_private = await client.get("/llms.txt?include_private=1")
        llms_full_public = await client.get("/llms-full.txt")
        llms_full_private = await client.get("/llms-full.txt?include_private=1")
        tools_public = await client.get("/tools.json")
        tools_private = await client.get("/tools.json?include_private=1")
        sitemap_public = await client.get("/sitemap.xml")
        sitemap_private = await client.get("/sitemap.xml?include_private=1")
        meta_public = await client.get("/meta.json")
        meta_private = await client.get("/meta.json?include_private=1")
        retrieve_public = await client.get(f"/catalog/retrieve?id={node.node_id}")
        retrieve_private = await client.get(f"/catalog/retrieve?id={node.node_id}&include_private=1")
        return {
            "direct_status": direct.status,
            "direct_text": direct.text,
            "catalog_public": json_body(catalog_public.text),
            "catalog_private": json_body(catalog_private.text),
            "search_public": json_body(search_public.text),
            "search_private": json_body(search_private.text),
            "llms_public": llms_public.text,
            "llms_private": llms_private.text,
            "llms_full_public": llms_full_public.text,
            "llms_full_private": llms_full_private.text,
            "tools_public": json_body(tools_public.text),
            "tools_private": json_body(tools_private.text),
            "sitemap_public": sitemap_public.text,
            "sitemap_private": sitemap_private.text,
            "meta_public": json_body(meta_public.text),
            "meta_private": json_body(meta_private.text),
            "retrieve_public_status": retrieve_public.status,
            "retrieve_private_status": retrieve_private.status,
            "retrieve_private": json_body(retrieve_private.text),
        }

    payload = asyncio.run(_fetch())
    public_mcp = FuraMCPServer(docs)
    private_mcp = FuraMCPServer(docs, include_private=True)
    mcp_public_titles = {resource["name"] for resource in public_mcp.list_resources()}
    mcp_private_titles = {resource["name"] for resource in private_mcp.list_resources()}
    mcp_public_retrieve = public_mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "retrieve_node", "arguments": {"node_id": node.node_id}},
        }
    )
    mcp_private_retrieve = private_mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "retrieve_node", "arguments": {"node_id": node.node_id}},
        }
    )

    assert payload["direct_status"] == 200
    assert "Secret" in payload["direct_text"]
    assert all(page["title"] != "Secret" for page in payload["catalog_public"]["pages"])
    assert any(page["title"] == "Secret" for page in payload["catalog_private"]["pages"])
    assert payload["search_public"]["count"] == 0
    assert payload["search_private"]["count"] >= 1
    assert "Secret" not in payload["llms_public"]
    assert "Secret" in payload["llms_private"]
    assert "Secret" not in payload["llms_full_public"]
    assert "Secret" in payload["llms_full_private"]
    assert payload["tools_private"]["page_count"] == payload["tools_public"]["page_count"] + 1
    assert "/docs/secret/" not in payload["sitemap_public"]
    assert "/docs/secret/" in payload["sitemap_private"]
    assert all(page["title"] != "Secret" for page in payload["meta_public"]["pages"])
    assert any(page["title"] == "Secret" for page in payload["meta_private"]["pages"])
    assert payload["retrieve_public_status"] == 404
    assert payload["retrieve_private_status"] == 200
    assert payload["retrieve_private"]["node_id"] == node.node_id
    assert "Secret" not in mcp_public_titles
    assert "Secret" in mcp_private_titles
    assert "error" in mcp_public_retrieve
    assert mcp_private_retrieve["result"]["structuredContent"]["node_id"] == node.node_id
    assert "Secret" not in docs._develop_export_sample(develop_export("llms"))


def test_author_page_chrome_routes_and_status_model(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    def parse_json(response):
        return json.loads(response.text.split("<script", 1)[0])

    main(["init", str(app_root), "--name", "Acme Docs"])
    private = app_root / "content" / "docs" / "private.md"
    private.write_text(
        "---\ntitle: Private\nvisibility: private\n---\n# Private\n\nPrivate notes.\n",
        encoding="utf-8",
    )
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
    )
    author_client = TestClient(docs.create_app())

    public_docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, True, False),
    )
    public_client = TestClient(public_docs.create_app())

    async def _fetch_author() -> dict[str, object]:
        page = await author_client.get("/docs/get-started/")
        boosted_page = await author_client.get(
            "/docs/get-started/",
            headers={"HX-Request": "true", "HX-Boosted": "true"},
        )
        status = await author_client.get("/docs/_author/page.json?slug=docs/get-started")
        source = await author_client.get("/docs/_author/source?slug=docs/get-started")
        private_page = await author_client.get("/docs/private/")
        private_status = await author_client.get("/docs/_author/page.json?slug=docs/private")
        preview = await author_client.get(
            "/docs/_author/transition?slug=docs/get-started&operation=publish&dry_run=1"
        )
        return {
            "page": page,
            "boosted_page": boosted_page,
            "status": status,
            "source": source,
            "private_page": private_page,
            "private_status": private_status,
            "preview": preview,
        }

    author_payload = asyncio.run(_fetch_author())
    status_payload = parse_json(author_payload["status"])
    private_payload = parse_json(author_payload["private_status"])
    preview_payload = parse_json(author_payload["preview"])

    assert author_payload["page"].status == 200
    assert 'data-fura-author-chrome' in author_payload["page"].text
    assert 'id="fura-author-sse"' in author_payload["page"].text
    assert 'sse-connect="/docs/_author/events?slug=docs/get-started"' in author_payload["page"].text
    assert 'hx-trigger="sse:author-invalidate"' in author_payload["page"].text
    assert 'HX-Docs-Author-Reload' in author_payload["page"].text
    assert author_payload["boosted_page"].status == 200
    assert 'data-fura-author-chrome' in author_payload["boosted_page"].text
    assert "Open source" in author_payload["page"].text
    assert "Copy source path" in author_payload["page"].text
    assert "Inspect public output" in author_payload["page"].text
    assert 'data-action="copy-source-path"' in author_payload["page"].text
    assert "/docs/_author/page.json?slug=docs/get-started&amp;inspect_public=1" in author_payload[
        "page"
    ].text
    assert status_payload["source_path"].endswith("content/docs/get-started.md")
    assert status_payload["content_format"] == "patitas-markdown"
    assert {"public", "valid", "clean", "public-output"} <= set(status_payload["states"])
    assert status_payload["export_impact"]["included"] is True
    assert author_payload["source"].status == 200
    assert "# Get started" in author_payload["source"].text
    assert 'data-author-state="private"' in author_payload["private_page"].text
    assert {"private", "excluded-output"} <= set(private_payload["states"])
    assert preview_payload["ok"] is True
    assert preview_payload["data"]["dry_run"] is True

    target = app_root / "content" / "docs" / "get-started.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Run the local docs server:",
            "Run the local author preview:",
        ),
        encoding="utf-8",
    )
    future = time.time() + 5
    os.utime(target, (future, future))
    time.sleep(1.1)

    stale_status = asyncio.run(author_client.get("/docs/_author/stale?slug=docs/get-started"))
    stale_payload = parse_json(stale_status)
    assert stale_payload["event"] == "author-invalidate"
    assert stale_payload["generation"]
    assert stale_payload["current"]["slug"] == "docs/get-started"
    assert "page-root" in stale_payload["current"]["target_hints"]
    assert stale_payload["current"]["dirty_paths"] == ["docs/get-started.md"]
    dirty_status = asyncio.run(author_client.get("/docs/_author/page.json?slug=docs/get-started"))
    dirty_payload = parse_json(dirty_status)
    assert {"stale", "valid"} <= set(dirty_payload["states"])
    assert dirty_payload["validation"]["error_count"] == 0

    reload_page = asyncio.run(
        author_client.get(
            "/docs/get-started/",
            headers={
                "HX-Request": "true",
                "HX-Docs-Author-Reload": "1",
            },
        )
    )
    assert reload_page.status == 200
    assert "Run the local author preview:" in reload_page.text
    clean_stale_status = asyncio.run(author_client.get("/docs/_author/stale?slug=docs/get-started"))
    clean_stale_payload = parse_json(clean_stale_status)
    assert clean_stale_payload["current"] is None

    target.write_text(
        target.read_text(encoding="utf-8").replace("weight: 20", "visibility: invalid"),
        encoding="utf-8",
    )
    os.utime(target, (future + 1, future + 1))
    time.sleep(1.1)
    dirty_status = asyncio.run(author_client.get("/docs/_author/page.json?slug=docs/get-started"))
    dirty_payload = parse_json(dirty_status)
    assert {"stale", "invalid"} <= set(dirty_payload["states"])
    assert dirty_payload["validation"]["error_count"] >= 1

    async def _fetch_public() -> dict[str, object]:
        page = await public_client.get("/docs/get-started/")
        status = await public_client.get("/docs/_author/page.json?slug=docs/get-started")
        source = await public_client.get("/docs/_author/source?slug=docs/get-started")
        return {"page": page, "status": status, "source": source}

    public_payload = asyncio.run(_fetch_public())
    assert 'data-fura-author-chrome' not in public_payload["page"].text
    assert 'id="fura-author-sse"' not in public_payload["page"].text
    assert "/docs/_author/page.json?slug=docs/get-started&amp;inspect_public=1" not in public_payload[
        "page"
    ].text
    assert public_payload["status"].status == 404
    assert public_payload["source"].status == 404


def test_author_studio_save_create_and_route_gating(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    author_client = TestClient(docs.create_app())
    public_docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, True, False),
    )
    public_client = TestClient(public_docs.create_app())

    target = app_root / "content" / "docs" / "get-started.md"
    original = target.read_text(encoding="utf-8")
    edited = original.replace("Run the local docs server:", "Updated in studio.")

    async def _exercise_author() -> dict[str, object]:
        studio = await author_client.get("/docs/_author/studio?slug=docs/get-started")
        saved = await author_client.post(
            "/docs/_author/studio/save",
            headers={"HX-Request": "true"},
            data={
                "slug": "docs/get-started",
                "mode": "edit",
                "title": "Get started",
                "source": edited,
            },
        )
        page = await author_client.get("/docs/get-started/")
        invalid = await author_client.post(
            "/docs/_author/studio/save",
            headers={"HX-Request": "true"},
            data={
                "slug": "docs/get-started",
                "mode": "edit",
                "title": "Get started",
                "source": "",
            },
        )
        create = await author_client.post(
            "/docs/_author/studio/save",
            headers={"HX-Request": "true"},
            data={
                "slug": "docs/studio-draft",
                "mode": "create",
                "title": "Studio draft",
                "source": "# Studio draft\n\nPrivate draft body.\n",
            },
        )
        created_page = await author_client.get("/docs/studio-draft/")
        return {
            "studio": studio,
            "saved": saved,
            "page": page,
            "invalid": invalid,
            "create": create,
            "created_page": created_page,
        }

    payload = asyncio.run(_exercise_author())
    draft = app_root / "content" / "docs" / "studio-draft.md"

    assert payload["studio"].status == 200
    assert 'id="author-studio-workspace"' in payload["studio"].text
    assert 'name="source"' in payload["studio"].text
    assert "Run the local docs server:" in payload["studio"].text
    assert payload["saved"].status == 200
    assert "Updated in studio." in payload["saved"].text
    assert 'data-author-studio-saved="true"' in payload["saved"].text
    assert "Updated in studio." in payload["page"].text
    assert target.read_text(encoding="utf-8") == edited
    assert payload["invalid"].status == 200
    assert "source text must not be empty" in payload["invalid"].text
    assert 'data-rule-id="fura.author"' in payload["invalid"].text
    assert payload["create"].status == 200
    assert "Private draft body." in payload["create"].text
    assert draft.is_file()
    draft_source = draft.read_text(encoding="utf-8")
    assert "visibility: draft" in draft_source
    assert "draft: true" in draft_source
    assert payload["created_page"].status == 200
    assert 'data-author-state="draft"' in payload["created_page"].text

    async def _exercise_public() -> dict[str, object]:
        studio = await public_client.get("/docs/_author/studio?slug=docs/get-started")
        save = await public_client.post(
            "/docs/_author/studio/save",
            data={
                "slug": "docs/get-started",
                "mode": "edit",
                "title": "Get started",
                "source": edited,
            },
        )
        return {"studio": studio, "save": save}

    public_payload = asyncio.run(_exercise_public())
    assert public_payload["studio"].status == 404
    assert public_payload["save"].status == 404


def test_author_new_status_and_publish_json_contract(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main([
        "--app-root",
        str(app_root),
        "author",
        "new",
        "docs/release-notes",
        "--title",
        "Release notes",
        "--dry-run",
        "--json",
    ])
    dry_payload = json.loads(capsys.readouterr().out)
    target = app_root / "content" / "docs" / "release-notes.md"
    assert dry_payload["ok"] is True
    assert dry_payload["command"] == "author new"
    assert dry_payload["data"]["operation_id"].startswith("author.new.")
    assert dry_payload["data"]["resulting_visibility"] == "draft"
    assert dry_payload["data"]["dry_run"] is True
    assert dry_payload["data"]["changed_files"] == []
    assert not target.exists()

    main([
        "--app-root",
        str(app_root),
        "author",
        "new",
        "docs/release-notes",
        "--title",
        "Release notes",
        "--yes",
        "--json",
    ])
    create_payload = json.loads(capsys.readouterr().out)
    assert create_payload["ok"] is True
    assert create_payload["data"]["changed_files"] == [str(target)]
    assert "draft: true" in target.read_text(encoding="utf-8")

    main(["--app-root", str(app_root), "author", "status", "docs/release-notes", "--json"])
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["ok"] is True
    assert status_payload["data"]["resulting_visibility"] == "draft"

    main(["--app-root", str(app_root), "author", "validate", "docs/release-notes", "--json"])
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["ok"] is True
    assert validate_payload["command"] == "author validate"
    assert validate_payload["data"]["operation_id"].startswith("author.validate.")
    assert validate_payload["data"]["target_path"] == str(target)
    assert validate_payload["data"]["resulting_visibility"] == "draft"
    assert validate_payload["data"]["changed_files"] == []

    try:
        main(["--app-root", str(app_root), "author", "publish", "docs/release-notes", "--json"])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("publish should require --yes or --dry-run")
    confirm_payload = json.loads(capsys.readouterr().out)
    assert confirm_payload["ok"] is False
    assert confirm_payload["diagnostics"][0]["rule_id"] == "fura.author"

    main([
        "--app-root",
        str(app_root),
        "author",
        "publish",
        "docs/release-notes",
        "--dry-run",
        "--json",
    ])
    publish_dry = json.loads(capsys.readouterr().out)
    assert publish_dry["ok"] is True
    assert publish_dry["data"]["previous_visibility"] == "draft"
    assert publish_dry["data"]["resulting_visibility"] == "public"
    assert "visibility: public" in publish_dry["data"]["diff"]
    impact = publish_dry["data"]["publication_impact"]
    assert impact["previous_public"] is False
    assert impact["resulting_public"] is True
    assert impact["change"] == "added_to_public_output"
    assert impact["affected_surfaces"] == ["navigation", "search", "export", "agent"]
    assert all(surface["affected"] is True for surface in impact["surfaces"])
    assert "visibility: public" not in target.read_text(encoding="utf-8")

    main([
        "--app-root",
        str(app_root),
        "author",
        "publish",
        "docs/release-notes",
        "--yes",
        "--json",
    ])
    publish_payload = json.loads(capsys.readouterr().out)
    source = target.read_text(encoding="utf-8")
    assert publish_payload["ok"] is True
    assert publish_payload["data"]["changed_files"] == [str(target)]
    assert publish_payload["data"]["resulting_visibility"] == "public"
    assert publish_payload["data"]["publication_impact"]["resulting_public"] is True
    assert "visibility: public" in source
    assert "published_at:" in source

    main([
        "--app-root",
        str(app_root),
        "author",
        "unpublish",
        "docs/release-notes",
        "--dry-run",
        "--json",
    ])
    unpublish_dry = json.loads(capsys.readouterr().out)
    assert unpublish_dry["ok"] is True
    assert unpublish_dry["data"]["publication_impact"]["previous_public"] is True
    assert unpublish_dry["data"]["publication_impact"]["resulting_public"] is False
    assert unpublish_dry["data"]["publication_impact"]["change"] == "removed_from_public_output"
    assert "visibility: draft" not in target.read_text(encoding="utf-8")


def test_author_validate_reports_lifecycle_failure(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    target = app_root / "content" / "docs" / "get-started.md"
    source = target.read_text(encoding="utf-8")
    target.write_text(source.replace("---\n", "---\nvisibility: invalid\n", 1), encoding="utf-8")

    try:
        main(["--app-root", str(app_root), "author", "validate", "docs/get-started", "--json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("author validate should fail for invalid lifecycle frontmatter")
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert payload["data"]["operation"] == "validate"
    assert payload["data"]["target_path"] == str(target)
    assert payload["data"]["diagnostics"][0]["severity"] == "error"
    assert "visibility must be one of" in payload["data"]["diagnostics"][0]["message"]
    assert payload["diagnostics"][0]["source_path"] == str(target)


def test_author_edit_json_contract_and_confirmation_gate(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    target = app_root / "content" / "docs" / "get-started.md"
    original = target.read_text(encoding="utf-8")
    old_text = "Run the local docs server:"
    new_text = "Run the local author preview:"

    main([
        "--app-root",
        str(app_root),
        "author",
        "edit",
        "docs/get-started",
        "--old-text",
        old_text,
        "--new-text",
        new_text,
        "--dry-run",
        "--json",
    ])
    dry_payload = json.loads(capsys.readouterr().out)
    assert dry_payload["ok"] is True
    assert dry_payload["command"] == "author edit"
    assert dry_payload["data"]["operation_id"].startswith("author.apply_edit.")
    assert dry_payload["data"]["dry_run"] is True
    assert dry_payload["data"]["changed_files"] == []
    assert new_text in dry_payload["data"]["diff"]
    assert target.read_text(encoding="utf-8") == original

    try:
        main([
            "--app-root",
            str(app_root),
            "author",
            "edit",
            "docs/get-started",
            "--old-text",
            old_text,
            "--new-text",
            new_text,
            "--json",
        ])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("edit should require --yes or --dry-run")
    confirm_payload = json.loads(capsys.readouterr().out)
    assert confirm_payload["ok"] is False
    assert confirm_payload["diagnostics"][0]["rule_id"] == "fura.author"
    assert target.read_text(encoding="utf-8") == original

    main([
        "--app-root",
        str(app_root),
        "author",
        "edit",
        "docs/get-started",
        "--old-text",
        old_text,
        "--new-text",
        new_text,
        "--yes",
        "--json",
    ])
    edit_payload = json.loads(capsys.readouterr().out)
    assert edit_payload["ok"] is True
    assert edit_payload["data"]["changed_files"] == [str(target)]
    assert edit_payload["data"]["previous_visibility"] == "public"
    assert edit_payload["data"]["resulting_visibility"] == "public"
    assert new_text in target.read_text(encoding="utf-8")

    try:
        main([
            "--app-root",
            str(app_root),
            "author",
            "edit",
            "docs/get-started",
            "--old-text",
            old_text,
            "--new-text",
            "Should not apply.",
            "--dry-run",
            "--json",
        ])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("stale edit span should fail")
    stale_payload = json.loads(capsys.readouterr().out)
    assert stale_payload["ok"] is False
    assert "old_text was not found" in stale_payload["diagnostics"][0]["message"]


def test_author_lifecycle_reports_missing_mount_and_ambiguous_slug(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    shared = app_root / "shared"
    (shared / "docs").mkdir(parents=True)
    (shared / "docs" / "same.md").write_text("---\ntitle: Shared Same\n---\n# Shared\n", encoding="utf-8")
    (app_root / "content" / "docs" / "same.md").write_text(
        "---\ntitle: Default Same\n---\n# Default\n",
        encoding="utf-8",
    )
    (app_root / "mounts.yaml").write_text(
        "\n".join(
            [
                "mounts:",
                "  - id: main",
                "    label: Main",
                "    content_root: content",
                "    default: true",
                "  - id: shared",
                "    label: Shared",
                "    content_root: shared",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    try:
        main([
            "--app-root",
            str(app_root),
            "author",
            "status",
            "docs/same",
            "--mount",
            "missing",
            "--json",
        ])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("missing mount should fail")
    missing_payload = json.loads(capsys.readouterr().out)
    assert missing_payload["ok"] is False
    assert "unknown mount" in missing_payload["diagnostics"][0]["message"]

    try:
        main(["--app-root", str(app_root), "author", "status", "docs/same", "--json"])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("ambiguous slug should fail")
    ambiguous_payload = json.loads(capsys.readouterr().out)
    assert ambiguous_payload["ok"] is False
    assert "ambiguous author target" in ambiguous_payload["diagnostics"][0]["message"]

    main([
        "--app-root",
        str(app_root),
        "author",
        "status",
        "docs/same",
        "--mount",
        "shared",
        "--json",
    ])
    shared_payload = json.loads(capsys.readouterr().out)
    assert shared_payload["ok"] is True
    assert shared_payload["data"]["mount"] == "shared"


def test_migrate_json_reports_validation_errors(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing.txt"

    try:
        main(["migrate", str(missing), "--json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("migrate should fail for a non-MDX path")
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["command"] == "migrate"
    assert payload["exit_code"] == 2
    assert payload["diagnostics"][0]["severity"] == "error"
    assert payload["diagnostics"][0]["source_path"] == str(missing.resolve())


def test_stop_json_reports_no_listener(capsys) -> None:
    main(["stop", "--port", "65534", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "stop"
    assert payload["data"]["stopped"] is False


def test_recipes_json_lists_agent_workflows(capsys) -> None:
    main(["recipes", "--json"])
    payload = json.loads(capsys.readouterr().out)
    recipe_ids = {recipe["id"] for recipe in payload["data"]["recipes"]}

    assert payload["ok"] is True
    assert payload["command"] == "recipes"
    assert {
        "init",
        "inspect",
        "validate",
        "query",
        "publish",
        "repair",
        "author-draft",
        "author-edit-publish",
        "author-stale-repair",
        "author-publish-remediation",
        "source-sync",
    } <= recipe_ids


def test_recipe_json_reports_single_workflow(capsys) -> None:
    main(["recipes", "publish", "--json"])
    payload = json.loads(capsys.readouterr().out)
    recipe = payload["data"]["recipes"][0]

    assert payload["ok"] is True
    assert payload["data"]["count"] == 1
    assert recipe["id"] == "publish"
    assert [step["id"] for step in recipe["steps"]] == ["freeze", "export", "verify-preview"]

    main(["recipes", "validate", "--json"])
    validate_payload = json.loads(capsys.readouterr().out)
    validate_recipe = validate_payload["data"]["recipes"][0]
    assert "agent-evals" in [step["id"] for step in validate_recipe["steps"]]


def test_unknown_recipe_json_reports_config_error(capsys) -> None:
    try:
        main(["recipes", "missing", "--json"])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("unknown recipe should fail")
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["exit_code"] == 3
    assert payload["diagnostics"][0]["rule_id"] == "fura.recipes"


def test_mcp_describe_json_reports_resources_and_tools(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main(["--app-root", str(app_root), "mcp", "--describe", "--json", "--no-autodoc"])
    payload = json.loads(capsys.readouterr().out)
    resource_uris = {resource["uri"] for resource in payload["data"]["resources"]}
    tool_names = {tool["name"] for tool in payload["data"]["tools"]}

    assert payload["ok"] is True
    assert payload["command"] == "mcp"
    assert payload["data"]["transport"] == "milo-stdio"
    assert payload["data"]["policy"]["transport"] == "local"
    assert "fura://catalog/nodes" in resource_uris
    assert "fura://catalog/api-operations" in resource_uris
    assert "fura://reports/validation" in resource_uris
    assert "fura://reports/audit" in resource_uris
    assert {
        "semantic_search",
        "retrieve_node",
        "traverse_graph",
        "run_checks",
        "author_create_draft",
        "author_read_source",
        "author_apply_edit",
        "author_publish",
    } <= tool_names


def test_mcp_json_rpc_tools_return_structured_content(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    server = FuraMCPServer(docs)
    node = docs.catalog.doc_nodes()[0]

    init_response = server.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    tools_response = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    resource_response = server.handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "fura://catalog/nodes"}}
    )
    search_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "semantic_search",
                "arguments": {"query": "Get started", "limit": 5},
            },
        }
    )
    retrieve_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "retrieve_node", "arguments": {"node_id": node.node_id}},
        }
    )
    graph_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "traverse_graph", "arguments": {"url": node.url}},
        }
    )

    assert init_response["result"]["protocolVersion"] == "2025-06-18"
    assert any(tool["outputSchema"] for tool in tools_response["result"]["tools"])
    catalog_payload = json.loads(resource_response["result"]["contents"][0]["text"])
    assert catalog_payload["count"] >= 1
    search_payload = search_response["result"]["structuredContent"]
    assert search_payload["count"] >= 1
    assert search_response["result"]["content"][0]["type"] == "text"
    assert retrieve_response["result"]["structuredContent"]["node_id"] == node.node_id
    assert graph_response["result"]["structuredContent"]["node"]["url"] == node.url


def test_mcp_milo_adapter_exposes_resources_and_structured_tools(tmp_path: Path) -> None:
    from milo.testing import MCPClient

    from furatena.catalog.mcp import build_milo_cli

    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    server = FuraMCPServer(docs)
    client = MCPClient(build_milo_cli(server))
    node = docs.catalog.doc_nodes()[0]

    init = client.initialize()
    resources = client.list_resources()
    tools = client.list_tools()
    search = client.call("semantic_search", query="Get started", limit=5)
    retrieve = client.call("retrieve_node", node_id=node.node_id)

    assert init["serverInfo"]["name"] == "furatena-catalog"
    assert "fura://catalog/nodes" in {resource["uri"] for resource in resources}
    assert "milo://stats" in {resource["uri"] for resource in resources}
    tool_by_name = {tool.name: tool for tool in tools}
    assert "semantic_search" in tool_by_name
    assert tool_by_name["semantic_search"].output_schema is not None
    assert search.is_error is False
    assert search.structured["count"] >= 1
    assert retrieve.is_error is False
    assert retrieve.structured["node_id"] == node.node_id


def test_mcp_remote_policy_denies_sensitive_tools_and_audits(tmp_path: Path) -> None:
    from milo.testing import MCPClient

    from furatena.catalog.mcp import build_milo_cli

    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    policy = MCPAccessPolicy(
        transport="remote",
        actor="agent-ci",
        tenant="acme",
        site="docs",
        allow_private=True,
        privileged_tokens=frozenset({"secret"}),
        rate_limit_per_minute=10,
        max_output_chars=200_000,
    )
    server = FuraMCPServer(docs, include_private=True, policy=policy)

    def call(name: str, arguments: dict[str, object]) -> dict[str, object]:
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return response["result"]

    denied = call("author_read_source", {"target": "docs/get-started"})
    search = call("semantic_search", {"query": "Get started", "limit": 3})
    allowed = call("author_read_source", {"target": "docs/get-started", "privileged_token": "secret"})
    milo_allowed = MCPClient(build_milo_cli(server)).call(
        "author_read_source",
        target="docs/get-started",
        privileged_token="secret",
    )
    audit = json.loads(server.read_resource("fura://reports/audit")["text"])
    audit_json = json.dumps(audit)

    assert denied["isError"] is True
    assert denied["structuredContent"]["diagnostics"][0]["rule_id"] == "fura.mcp.privileged_token"
    assert search["isError"] is False
    assert allowed["isError"] is False
    assert "# Get started" in allowed["structuredContent"]["source"]
    assert milo_allowed.is_error is False
    assert "# Get started" in milo_allowed.structured["source"]
    assert audit["policy"]["transport"] == "remote"
    assert audit["count"] == 4
    assert {entry["tool"] for entry in audit["entries"]} == {"author_read_source", "semantic_search"}
    assert audit["entries"][0]["actor"] == "agent-ci"
    assert audit["entries"][0]["tenant"] == "acme"
    assert audit["entries"][0]["site"] == "docs"
    assert audit["entries"][0]["status"] == "denied"
    assert audit["entries"][-1]["inputs"]["privileged_token"] == "<redacted>"
    assert "secret" not in audit_json


def test_mcp_remote_policy_rate_limits_tools(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    server = FuraMCPServer(
        docs,
        policy=MCPAccessPolicy(
            transport="remote",
            actor="agent-ci",
            rate_limit_per_minute=1,
        ),
    )

    first = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "semantic_search", "arguments": {"query": "Get started"}},
        }
    )
    second = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "semantic_search", "arguments": {"query": "Get started"}},
        }
    )
    audit = json.loads(server.read_resource("fura://reports/audit")["text"])

    assert first["result"]["isError"] is False
    assert second["error"]["code"] == -32029
    assert second["error"]["data"]["diagnostics"][0]["rule_id"] == "fura.mcp.rate_limit"
    assert audit["entries"][-1]["status"] == "rate_limited"
    assert audit["entries"][-1]["result_status"] == "error"


def test_mcp_authoring_tools_are_private_structured_and_confirmation_gated(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    public_server = FuraMCPServer(docs)
    private_server = FuraMCPServer(docs, include_private=True)
    target = app_root / "content" / "docs" / "mcp-draft.md"

    def call(server: FuraMCPServer, name: str, arguments: dict[str, object]) -> dict[str, object]:
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return response["result"]

    public_create = call(public_server, "author_create_draft", {"slug": "docs/mcp-draft"})
    assert public_create["isError"] is True
    assert public_create["structuredContent"]["ok"] is False
    assert "include-private" in public_create["structuredContent"]["diagnostics"][0]["message"]

    dry_create = call(
        private_server,
        "author_create_draft",
        {"slug": "docs/mcp-draft", "title": "MCP Draft", "actor": "agent-test"},
    )
    dry_payload = dry_create["structuredContent"]
    assert dry_create["isError"] is False
    assert dry_payload["ok"] is True
    assert dry_payload["dry_run"] is True
    assert dry_payload["changed_files"] == []
    assert dry_payload["audit"]["actor"] == "agent-test"
    assert not target.exists()

    create = call(
        private_server,
        "author_create_draft",
        {
            "slug": "docs/mcp-draft",
            "title": "MCP Draft",
            "dry_run": False,
            "confirmed": True,
        },
    )
    assert create["isError"] is False
    assert create["structuredContent"]["changed_files"] == [str(target)]
    assert "visibility: draft" in target.read_text(encoding="utf-8")

    read = call(private_server, "author_read_source", {"target": "docs/mcp-draft"})
    assert read["isError"] is False
    assert "# MCP Draft" in read["structuredContent"]["source"]

    propose = call(
        private_server,
        "author_propose_edit",
        {
            "target": "docs/mcp-draft",
            "old_text": "# MCP Draft\n",
            "new_text": "# MCP Draft\n\nDraft body.\n",
        },
    )
    assert propose["isError"] is False
    assert propose["structuredContent"]["dry_run"] is True
    assert "Draft body" in propose["structuredContent"]["diff"]
    assert "Draft body" not in target.read_text(encoding="utf-8")

    unsafe_edit = call(
        private_server,
        "author_apply_edit",
        {
            "target": "docs/mcp-draft",
            "old_text": "# MCP Draft\n",
            "new_text": "# MCP Draft\n\nDraft body.\n",
            "dry_run": False,
        },
    )
    assert unsafe_edit["isError"] is True
    assert "require --yes or --dry-run" in unsafe_edit["structuredContent"]["diagnostics"][0]["message"]

    edit = call(
        private_server,
        "author_apply_edit",
        {
            "target": "docs/mcp-draft",
            "old_text": "# MCP Draft\n",
            "new_text": "# MCP Draft\n\nDraft body.\n",
            "dry_run": False,
            "confirmed": True,
        },
    )
    assert edit["isError"] is False
    assert "Draft body" in target.read_text(encoding="utf-8")

    unsafe_publish = call(
        private_server,
        "author_publish",
        {"target": "docs/mcp-draft", "dry_run": False},
    )
    assert unsafe_publish["isError"] is True
    assert "visibility: public" not in target.read_text(encoding="utf-8")

    publish_preview = call(private_server, "author_publish", {"target": "docs/mcp-draft"})
    assert publish_preview["isError"] is False
    assert publish_preview["structuredContent"]["dry_run"] is True
    assert publish_preview["structuredContent"]["resulting_visibility"] == "public"
    assert publish_preview["structuredContent"]["publication_impact"]["affected_surfaces"] == [
        "navigation",
        "search",
        "export",
        "agent",
    ]
    assert "visibility: public" not in target.read_text(encoding="utf-8")

    validation = call(private_server, "author_validate", {"target": "docs/mcp-draft"})
    impact = call(private_server, "author_inspect_publication_impact", {"target": "docs/mcp-draft"})
    assert validation["structuredContent"]["audit"]["command"] == "author_validate"
    assert "status" in impact["structuredContent"]
    assert "stale_impact" in impact["structuredContent"]

    publish = call(
        private_server,
        "author_publish",
        {"target": "docs/mcp-draft", "dry_run": False, "confirmed": True},
    )
    assert publish["isError"] is False
    assert publish["structuredContent"]["audit"]["previous_state"] == "draft"
    assert publish["structuredContent"]["audit"]["resulting_state"] == "public"
    assert "visibility: public" in target.read_text(encoding="utf-8")


def test_init_app_freezes_and_exports_static_site(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main(["--app-root", str(app_root), "freeze"])
    main(["--app-root", str(app_root), "export", "--fresh", "--base-path", ""])

    assert (app_root / "frozen" / "catalog.json").is_file()
    assert (app_root / "frozen" / "search.json").is_file()
    assert (app_root / "public" / "docs" / "get-started" / "index.html").is_file()
    assert (app_root / "public" / "search.json").is_file()


def test_theme_inspect_and_eject_framework_template(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main(["--app-root", str(app_root), "theme", "inspect", "directives/callout.html"])
    inspect_output = capsys.readouterr().out

    assert "directives/callout.html" in inspect_output
    assert "framework" in inspect_output
    assert "theme/templates/directives/callout.html" in inspect_output

    main(["--app-root", str(app_root), "theme", "eject", "directives/callout.html"])
    eject_output = capsys.readouterr().out
    target = app_root / "theme" / "templates" / "directives" / "callout.html"

    assert target.is_file()
    assert "Ejected from framework:directives/callout.html" in target.read_text(encoding="utf-8")
    assert "ejected directives/callout.html" in eject_output

    main(["--app-root", str(app_root), "theme", "eject", "directives/callout.html"])
    skip_output = capsys.readouterr().out

    assert "skip directives/callout.html" in skip_output


def test_theme_inspect_json_reports_resolution(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main([
        "--app-root",
        str(app_root),
        "theme",
        "inspect",
        "directives/callout.html",
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["command"] == "theme inspect"
    assert payload["data"]["files"][0]["logical_path"] == "directives/callout.html"


def test_theme_diff_reports_override_drift(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main(["--app-root", str(app_root), "theme", "eject", "directives/callout.html"])
    capsys.readouterr()

    main(["--app-root", str(app_root), "theme", "diff", "directives/callout.html"])
    clean_diff = capsys.readouterr().out
    assert "no differences" in clean_diff

    target = app_root / "theme" / "templates" / "directives" / "callout.html"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n{# local edit #}\n",
        encoding="utf-8",
    )

    main(["--app-root", str(app_root), "theme", "diff", "directives/callout.html"])
    diff_output = capsys.readouterr().out

    assert "--- framework:directives/callout.html" in diff_output
    assert "+++ project:directives/callout.html" in diff_output
    assert "local edit" in diff_output


def test_ejected_template_keeps_check_and_export_working(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    main(["--app-root", str(app_root), "theme", "eject", "directives/callout.html"])
    main([
        "--app-root",
        str(app_root),
        "check",
        "--content-only",
        "--warnings-as-errors",
    ])
    main(["--app-root", str(app_root), "export", "--fresh", "--base-path", ""])

    assert (app_root / "public" / "docs" / "get-started" / "index.html").is_file()
