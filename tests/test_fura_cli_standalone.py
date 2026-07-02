from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
from chirp.testing import TestClient

from furatena.catalog.develop_exports import develop_export
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.mcp import FuraMCPServer, MCPAccessPolicy
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.cli.main import main

_UNSET = object()


def _assert_author_json_envelope(
    payload: dict[str, object],
    *,
    command: str,
    operation: str,
    ok: bool = True,
    target_path: str | None | object = _UNSET,
    resulting_visibility: str | None | object = _UNSET,
) -> dict[str, object]:
    assert payload["ok"] is ok
    assert payload["command"] == f"author {command}"
    assert isinstance(payload["exit_code"], int)
    assert isinstance(payload["summary"], str)
    assert payload["summary"]
    assert isinstance(payload["diagnostics"], list)

    data = payload["data"]
    assert isinstance(data, dict)
    assert data["ok"] is ok
    assert data["operation"] == operation
    assert isinstance(data["operation_id"], str)
    assert data["operation_id"].startswith(f"author.{operation}.")
    assert "target_path" in data
    assert "mount" in data
    assert "previous_visibility" in data
    assert "resulting_visibility" in data
    assert isinstance(data["changed_files"], list)
    assert isinstance(data["diagnostics"], list)
    assert isinstance(data["next_actions"], list)
    if target_path is not _UNSET:
        assert data["target_path"] == target_path
    if resulting_visibility is not _UNSET:
        assert data["resulting_visibility"] == resulting_visibility
    return data


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


def test_migrate_report_json_groups_risks(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    page = app_root / "content" / "docs" / "legacy.mdx"
    page.write_text(
        "---\ntitle: Legacy\n---\n\n"
        "# Legacy\n\n"
        "<ApiTable endpoint=\"/v1\" />\n\n"
        "[Missing](/docs/missing/)\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    with pytest.raises(SystemExit) as excinfo:
        main(["--app-root", str(app_root), "migrate", "--report", "--json"])
    assert excinfo.value.code == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["command"] == "migrate"
    report = payload["data"]["migration_report"]
    assert report["summary"]["error_count"] >= 1
    assert report["summary"]["warning_count"] >= 1
    assert "error" in report["groups"]["by_severity"]
    assert "docs/legacy.mdx" in report["groups"]["by_source_path"]
    assert "internal link" in report["groups"]["by_construct"]
    assert any(
        finding["construct"] == "MDX JSX component <ApiTable>"
        and finding["severity"] == "warning"
        for finding in report["findings"]
    )


def test_migrate_report_text_is_readable(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    page = app_root / "content" / "docs" / "legacy.mdx"
    page.write_text(
        "---\ntitle: Legacy\n---\n\n<ApiTable endpoint=\"/v1\" />\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    main(["--app-root", str(app_root), "migrate", "--report"])
    output = capsys.readouterr().out

    assert "Migration readiness report" in output
    assert "WARNING" in output
    assert "MDX JSX component <ApiTable>" in output


def test_check_reports_openapi_governance_findings(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    config_dir = app_root / "config"
    specs_dir = app_root / "specs"
    config_dir.mkdir()
    specs_dir.mkdir()
    (config_dir / "autodoc.yaml").write_text(
        """
autodoc:
  python:
    enabled: false
  openapi:
    enabled: true
    specs:
      - specs/openapi.yaml
""".lstrip(),
        encoding="utf-8",
    )
    (specs_dir / "openapi.yaml").write_text(
        """
openapi: 3.1.0
info:
  title: Acme API
  version: 1.0.0
paths:
  /users:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Missing'
            examples:
              broken:
                summary: no value
      responses:
        '200':
          description: OK
""".lstrip(),
        encoding="utf-8",
    )
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc:
        main(["--app-root", str(app_root), "check", "--content-only", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert int(exc.value.code) == 2
    assert payload["ok"] is False
    messages = [item["message"] for item in payload["diagnostics"]]
    assert any("missing operationId" in message for message in messages)
    assert any("broken example" in message for message in messages)
    assert any("unresolved schema reference" in message for message in messages)
    api_diagnostics = [
        item
        for item in payload["diagnostics"]
        if "OpenAPI" in item["message"]
        or "operationId" in item["message"]
        or "schema reference" in item["message"]
        or "broken example" in item["message"]
    ]
    assert {item["rule_id"] for item in api_diagnostics} == {"fura.api"}


def test_api_diff_json_reports_operation_changes(tmp_path: Path, capsys) -> None:
    old = tmp_path / "old.yaml"
    new = tmp_path / "new.yaml"
    old.write_text(
        """
openapi: 3.1.0
info:
  title: Acme API
  version: 1.0.0
paths:
  /users:
    get:
      operationId: listUsers
      summary: List users
      responses:
        '200':
          description: OK
        '404':
          description: Missing
    post:
      operationId: createUser
      responses:
        '201':
          description: Created
""".lstrip(),
        encoding="utf-8",
    )
    new.write_text(
        """
openapi: 3.1.0
info:
  title: Acme API
  version: 1.1.0
paths:
  /users:
    get:
      operationId: listUsers
      summary: List users v2
      responses:
        '200':
          description: OK
    delete:
      operationId: deleteUsers
      responses:
        '204':
          description: Deleted
""".lstrip(),
        encoding="utf-8",
    )

    main(["api-diff", str(old), str(new), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    data = payload["data"]
    assert data["summary"] == {"added": 1, "removed": 1, "changed": 1, "breaking": 2}
    assert data["added"][0]["operation_id"] == "deleteUsers"
    assert data["removed"][0]["operation_id"] == "createUser"
    assert data["changed"][0]["changes"] == ["summary changed", "response codes changed"]
    assert any("response codes removed: 404" in item["changes"] for item in data["breaking"])


def test_check_reports_stale_public_output_and_deploy_fails(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main(["--app-root", str(app_root), "freeze", "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] is True

    target = app_root / "content" / "docs" / "get-started.md"
    target.write_text(
        target.read_text(encoding="utf-8") + "\n\nFresh public edit.\n",
        encoding="utf-8",
    )
    future = time.time() + 5
    os.utime(target, (future, future))

    main(["--app-root", str(app_root), "check", "--content-only", "--json"])
    local_payload = json.loads(capsys.readouterr().out)
    stale_diagnostics = [
        diagnostic
        for diagnostic in local_payload["diagnostics"]
        if diagnostic["rule_id"] == "fura.lifecycle"
        and "public output is stale" in diagnostic["message"]
    ]

    assert local_payload["ok"] is True
    assert stale_diagnostics
    assert stale_diagnostics[0]["severity"] == "warning"
    assert stale_diagnostics[0]["source_path"] == "docs/get-started.md"

    try:
        main(["--app-root", str(app_root), "check", "--content-only", "--deploy", "--json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("deploy check should fail on stale public output")
    deploy_payload = json.loads(capsys.readouterr().out)
    deploy_stale = [
        diagnostic
        for diagnostic in deploy_payload["diagnostics"]
        if diagnostic["rule_id"] == "fura.lifecycle"
        and "public output is stale" in diagnostic["message"]
    ]

    assert deploy_payload["ok"] is False
    assert deploy_stale
    assert deploy_stale[0]["severity"] == "error"
    assert "fura freeze" in deploy_stale[0]["next_action"]

    main(["--app-root", str(app_root), "impact", "--slug", "docs/get-started", "--json"])
    impact_payload = json.loads(capsys.readouterr().out)
    impact = impact_payload["data"]["impact"][0]
    repair_task = impact_payload["data"]["repair_tasks"][0]

    assert impact_payload["ok"] is True
    assert impact_payload["command"] == "impact"
    assert impact_payload["data"]["stale_count"] == 1
    assert impact["mode"] == "frozen-output"
    assert impact["slug"] == "docs/get-started"
    assert "export" in impact["refresh_targets"]
    assert impact["affected_chunks"]
    assert impact["graph_context"]["node_id"] == impact["provenance"]["node_id"]
    assert repair_task["source_paths"] == ["docs/get-started.md"]
    assert repair_task["dcp_node_id"] == impact["provenance"]["node_id"]
    assert "Refresh stale docs output" in impact_payload["data"]["task_markdown"]

    main(["--app-root", str(app_root), "check", "--content-only", "--report-format", "github"])
    github_report = capsys.readouterr().out
    assert "::warning file=docs/get-started.md,title=fura.lifecycle::" in github_report
    assert "public output is stale" in github_report

    main(["--app-root", str(app_root), "check", "--content-only", "--report-format", "markdown"])
    markdown_report = capsys.readouterr().out
    assert "## Furatena check Report" in markdown_report
    assert "| warning | fura.lifecycle | docs/get-started.md |" in markdown_report

    for report_format, marker in (
        ("junit", "<testsuite"),
        ("checkstyle", "<checkstyle"),
    ):
        try:
            main([
                "--app-root",
                str(app_root),
                "check",
                "--content-only",
                "--deploy",
                "--report-format",
                report_format,
            ])
        except SystemExit as exc:
            assert exc.code == 2
        else:  # pragma: no cover - defensive assertion
            raise AssertionError(f"{report_format} deploy report should fail on stale output")
        report_text = capsys.readouterr().out
        assert marker in report_text
        assert "public output is stale" in report_text

    main(["--app-root", str(app_root), "check", "--content-only", "--agent", "--json"])
    agent_payload = json.loads(capsys.readouterr().out)
    assert agent_payload["data"]["agent_warning_count"] >= 1
    assert any(
        diagnostic["rule_id"] == "fura.agent_safety.stale_context"
        for diagnostic in agent_payload["diagnostics"]
    )


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
        "author_workflows",
    } <= set(payload["data"]["categories"])
    assert results["api-operation-discovery"]["status"] == "pass"
    assert results["private-content-boundary"]["status"] == "pass"
    assert results["multi-mount-hub-discovery"]["status"] == "pass"
    assert results["tool-selection-search"]["expected"]["tool"] == "semantic_search"
    assert results["tool-selection-author-edit"]["expected"]["tool"] == "author_propose_edit"
    assert results["author-draft-dry-run"]["status"] == "pass"
    assert results["author-draft-dry-run"]["observed"]["changed_files"] == []
    assert results["author-publish-dry-run"]["status"] == "pass"
    assert results["author-publish-dry-run"]["observed"]["publication_change"] == "added_to_public_output"
    assert results["author-publish-remediation"]["status"] == "pass"
    assert results["author-publish-remediation"]["observed"]["source_unchanged_after_failed_publish"] is True
    assert results["author-validation-repair"]["status"] == "pass"
    assert results["author-validation-repair"]["observed"]["invalid_validation_is_error"] is True
    assert results["author-validation-repair"]["observed"]["clean_validation_is_error"] is False
    assert results["author-validation-repair"]["observed"]["source_restored"] is True
    assert results["author-publish-round-trip"]["status"] == "pass"
    assert results["author-publish-round-trip"]["observed"]["public_before_is_error"] is True
    assert results["author-publish-round-trip"]["observed"]["public_after_publish_is_error"] is False
    assert results["author-publish-round-trip"]["observed"]["public_after_unpublish_is_error"] is True
    assert results["author-publish-round-trip"]["observed"]["restore_is_error"] is False


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
    frozen_channels = json.loads((app_root / "frozen" / "channels.json").read_text(encoding="utf-8"))
    public_channels = json.loads((app_root / "public" / "channels.json").read_text(encoding="utf-8"))
    assert frozen_channels["mode"] == "freeze"
    assert public_channels["mode"] == "static"
    assert {item["id"] for item in public_channels["channels"]} >= {"static", "agent", "pdf"}
    assert public_channels["channels"][3]["status"] == "planned"


def test_pdf_export_supports_page_collection_and_site(tmp_path: Path, capsys) -> None:
    from pypdf import PdfReader

    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    page = app_root / "content" / "docs" / "pdf-source.md"
    page.write_text(
        "---\n"
        "title: PDF Source\n"
        "description: PDF export fixture.\n"
        "---\n"
        "# PDF Source\n\n"
        "Intro paragraph with [Example](https://example.com).\n\n"
        "## Heading One\n\n"
        "Body under heading.\n\n"
        "```python\n"
        "print('pdf code')\n"
        "```\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    main([
        "--app-root",
        str(app_root),
        "pdf",
        "--page",
        "/docs/pdf-source/",
        "--json",
        "--no-autodoc",
    ])
    page_payload = json.loads(capsys.readouterr().out)
    main([
        "--app-root",
        str(app_root),
        "pdf",
        "--collection",
        "docs",
        "--json",
        "--no-autodoc",
    ])
    collection_payload = json.loads(capsys.readouterr().out)
    main(["--app-root", str(app_root), "pdf", "--json", "--no-autodoc"])
    site_payload = json.loads(capsys.readouterr().out)

    page_pdf = Path(page_payload["data"]["paths"][0])
    collection_pdf = Path(collection_payload["data"]["paths"][0])
    site_pdf = Path(site_payload["data"]["paths"][0])
    assert page_payload["ok"] is True
    assert page_payload["data"]["target"] == "page"
    assert collection_payload["data"]["target"] == "collection"
    assert site_payload["data"]["target"] == "site"
    assert page_pdf.stat().st_size > 1000
    assert collection_pdf.stat().st_size > page_pdf.stat().st_size
    assert site_pdf.stat().st_size > 1000
    assert site_payload["data"]["page_count"] >= collection_payload["data"]["page_count"]

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(page_pdf)).pages)
    assert "PDF Source" in text
    assert "Heading One" in text
    assert "print('pdf code')" in text
    assert "https://example.com" in text
    assert "Page 1" in text

    channels = json.loads((app_root / "public" / "channels.json").read_text(encoding="utf-8"))
    pdf_channel = next(item for item in channels["channels"] if item["id"] == "pdf")
    assert pdf_channel["status"] == "available"
    assert any(output["url"].endswith(".pdf") for output in pdf_channel["outputs"])


def test_freeze_records_source_sync_state_and_drift_reasons(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    main(["--app-root", str(app_root), "freeze", "--json"])
    initial_payload = json.loads(capsys.readouterr().out)
    assert initial_payload["ok"] is True

    registry = json.loads((app_root / "frozen" / "registry.json").read_text(encoding="utf-8"))
    manifest = json.loads((app_root / "frozen" / "freeze.manifest.json").read_text(encoding="utf-8"))
    mount = registry["mounts"][0]
    status = mount["source_status"]
    manifest_status = manifest["mount_status"][0]
    assert status["provider"] == "filesystem"
    assert status["status"] == "frozen"
    assert "missing_source_fingerprint" in status["drift_reasons"]
    assert "source_root" not in status
    assert manifest["schema_version"] == 2
    assert manifest_status["content_fingerprint"] == status["content_fingerprint"]
    assert "source_root" not in manifest_status
    assert manifest["renderer"]["fingerprint"] == status["renderer_fingerprint"]

    main(["--app-root", str(app_root), "freeze", "--json"])
    skipped_payload = json.loads(capsys.readouterr().out)
    skipped_manifest = json.loads((app_root / "frozen" / "freeze.manifest.json").read_text(encoding="utf-8"))
    skipped_status = skipped_manifest["mount_status"][0]
    assert skipped_payload["data"]["status"] == "up_to_date"
    assert skipped_manifest["dirty_mounts"] == []
    assert skipped_status["status"] == "skipped"
    assert skipped_status["drift_reasons"] == []

    source = app_root / "content" / "docs" / "get-started.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\n\nNew source-sync state.\n",
        encoding="utf-8",
    )
    main(["--app-root", str(app_root), "freeze", "--json"])
    content_payload = json.loads(capsys.readouterr().out)
    content_manifest = json.loads((app_root / "frozen" / "freeze.manifest.json").read_text(encoding="utf-8"))
    content_status = content_manifest["mount_status"][0]
    assert content_payload["data"]["status"] == "updated"
    assert content_status["status"] == "frozen"
    assert content_status["drift_reasons"] == ["content"]

    (app_root / "frozen" / "renderer.fingerprint").write_text("stale-renderer\n", encoding="utf-8")
    main(["--app-root", str(app_root), "freeze", "--json"])
    renderer_payload = json.loads(capsys.readouterr().out)
    renderer_manifest = json.loads((app_root / "frozen" / "freeze.manifest.json").read_text(encoding="utf-8"))
    renderer_status = renderer_manifest["mount_status"][0]
    assert renderer_payload["data"]["status"] == "updated"
    assert renderer_status["status"] == "frozen"
    assert renderer_status["drift_reasons"] == ["renderer"]


def test_freeze_records_failed_mount_status_without_refreshing_renderer(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()

    from furatena.catalog import freeze as freeze_module

    def fail_shard(*_args, **_kwargs) -> int:
        raise RuntimeError("synthetic shard failure")

    monkeypatch.setattr(freeze_module, "_freeze_shard", fail_shard)

    with pytest.raises(RuntimeError, match="synthetic shard failure"):
        main(["--app-root", str(app_root), "freeze", "--json"])

    registry = json.loads((app_root / "frozen" / "registry.json").read_text(encoding="utf-8"))
    manifest = json.loads((app_root / "frozen" / "freeze.manifest.json").read_text(encoding="utf-8"))
    registry_status = registry["mounts"][0]["source_status"]
    manifest_status = manifest["mount_status"][0]
    assert registry_status["status"] == "failed"
    assert registry_status["error"] == "synthetic shard failure"
    assert manifest_status["status"] == "failed"
    assert "missing_source_fingerprint" in manifest_status["drift_reasons"]
    assert not (app_root / "frozen" / "renderer.fingerprint").exists()


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
    for sidecar in (
        "catalog.json",
        "catalog/api-operations.json",
        "llms.txt",
        "llms-full.txt",
        "meta.json",
        "sitemap.xml",
    ):
        assert "Secret" not in (app_root / "public" / sidecar).read_text(encoding="utf-8")
    tools_payload = json.loads((app_root / "public" / "tools.json").read_text(encoding="utf-8"))
    assert tools_payload["page_count"] == catalog_payload["page_count"]


def test_freeze_excludes_draft_pages_from_frozen_ir_and_preview(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    secret = app_root / "content" / "docs" / "secret.md"
    secret.write_text(
        "---\ntitle: Secret\n---\n# Secret\n\nPrivate notes.\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    main(["--app-root", str(app_root), "freeze", "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] is True

    frozen = app_root / "frozen"
    assert list(frozen.glob("mounts/*/pages/docs/secret.html"))
    assert "Secret" in (frozen / "search.json").read_text(encoding="utf-8")

    secret.write_text(
        "---\ntitle: Secret\ndraft: true\n---\n# Secret\n\nPrivate notes.\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    main(["--app-root", str(app_root), "freeze", "--json"])
    payload = json.loads(capsys.readouterr().out)

    frozen_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            frozen / "catalog.json",
            frozen / "search.json",
            frozen / "semantic.json",
            frozen / "tools.json",
        )
    )
    secret_pages = list(frozen.glob("mounts/*/pages/docs/secret.html"))
    secret_ast = list(frozen.glob("mounts/*/ast/docs/secret.json"))
    preview_docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, frozen, True, False),
    )
    preview_client = TestClient(preview_docs.create_app())

    async def _fetch() -> dict[str, object]:
        catalog = await preview_client.get("/catalog.json")
        search = await preview_client.get("/search.json?q=private%20notes")
        return {
            "catalog": json.loads(catalog.text.split("<script", 1)[0]),
            "search": json.loads(search.text.split("<script", 1)[0]),
        }

    preview_payload = asyncio.run(_fetch())

    assert payload["ok"] is True
    assert "Secret" not in frozen_text
    assert secret_pages == []
    assert secret_ast == []
    assert preview_docs.catalog.get_path("/docs/secret/") is None
    assert all(page["title"] != "Secret" for page in preview_payload["catalog"]["pages"])
    assert preview_payload["search"]["count"] == 0


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

    from furatena.catalog.agent_lint import check_agent_safety

    safety_errors, safety_warnings = check_agent_safety(public_mcp)
    assert safety_errors == []
    assert safety_warnings == []

    class LeakyServer(FuraMCPServer):
        def list_resources(self):
            return [
                *super().list_resources(),
                {
                    "uri": "fura://leak",
                    "name": "Secret",
                    "description": "Leaked private resource.",
                    "mimeType": "application/json",
                },
            ]

    leaky_errors, _leaky_warnings = check_agent_safety(LeakyServer(docs))
    assert any(error.rule_id == "fura.agent_safety.private_leak" for error in leaky_errors)


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
        htmx_validate = await author_client.get(
            "/docs/_author/page.json?slug=docs/get-started&validate=1",
            headers={"HX-Request": "true"},
        )
        return {
            "page": page,
            "boosted_page": boosted_page,
            "status": status,
            "source": source,
            "private_page": private_page,
            "private_status": private_status,
            "preview": preview,
            "htmx_validate": htmx_validate,
        }

    author_payload = asyncio.run(_fetch_author())
    status_payload = parse_json(author_payload["status"])
    private_payload = parse_json(author_payload["private_status"])
    preview_payload = parse_json(author_payload["preview"])

    assert author_payload["page"].status == 200
    assert 'data-fura-author-chrome' in author_payload["page"].text
    assert 'id="fura-author-sse"' in author_payload["page"].text
    assert 'sse-connect="/docs/_author/events?slug=docs/get-started"' in author_payload["page"].text
    assert 'sse-swap="author-invalidate"' in author_payload["page"].text
    assert 'hx-disinherit="hx-target hx-swap"' in author_payload["page"].text
    assert 'hx-swap="none"' in author_payload["page"].text
    assert 'hx-trigger="sse:author-invalidate"' in author_payload["page"].text
    assert 'HX-Docs-Author-Reload' in author_payload["page"].text
    assert author_payload["boosted_page"].status == 200
    assert 'data-fura-author-chrome' in author_payload["boosted_page"].text
    assert "Open source" in author_payload["page"].text
    assert "Copy source path" in author_payload["page"].text
    assert "Inspect public output" in author_payload["page"].text
    assert 'data-author-surface="local"' in author_payload["page"].text
    assert "Not exported" in author_payload["page"].text
    assert 'data-action="copy-source-path"' in author_payload["page"].text
    assert 'hx-target="#fura-author-chrome"' in author_payload["page"].text
    assert "operation=draft&amp;dry_run=0&amp;confirmed=1" in author_payload["page"].text
    assert "operation=publish&amp;dry_run=0&amp;confirmed=1" in author_payload["page"].text
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
    assert author_payload["htmx_validate"].status == 200
    assert 'id="fura-author-chrome"' in author_payload["htmx_validate"].text
    assert "Author controls" in author_payload["htmx_validate"].text
    assert "Local only" in author_payload["htmx_validate"].text
    assert '"ok":' not in author_payload["htmx_validate"].text

    draft_response = asyncio.run(
        author_client.get(
            "/docs/_author/transition?slug=docs/private&operation=draft&dry_run=0&confirmed=1",
            headers={"HX-Request": "true"},
        )
    )
    assert draft_response.status == 200
    assert 'id="fura-author-chrome"' in draft_response.text
    assert 'data-author-state="draft"' in draft_response.text
    assert '"ok":' not in draft_response.text

    archive_response = asyncio.run(
        author_client.get(
            "/docs/_author/transition?slug=docs/private&operation=archive&dry_run=0&confirmed=1"
        )
    )
    archive_payload = parse_json(archive_response)
    assert archive_payload["ok"] is True
    assert archive_payload["data"]["resulting_visibility"] == "archived"
    archive_status = asyncio.run(author_client.get("/docs/_author/page.json?slug=docs/private"))
    archive_status_payload = parse_json(archive_status)
    assert {"archived", "valid", "excluded-output"} <= set(archive_status_payload["states"])
    assert "public-output" not in archive_status_payload["states"]
    assert archive_status_payload["visibility"] == "archived"
    assert archive_status_payload["export_impact"]["included"] is False

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
    assert "author-invalidate" not in public_payload["page"].text
    assert "HX-Docs-Author-Reload" not in public_payload["page"].text
    assert "/docs/_author/page.json?slug=docs/get-started&amp;inspect_public=1" not in public_payload[
        "page"
    ].text
    assert public_payload["status"].status == 404
    assert public_payload["source"].status == 404
    assert "Author controls" not in public_payload["page"].text


def test_author_dashboard_lists_mount_status_and_lint_drilldown(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    broken = app_root / "content" / "docs" / "broken.md"
    broken.write_text(
        "---\ntitle: Broken\n---\n# Broken\n\n[Missing](/docs/missing/)\n",
        encoding="utf-8",
    )
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
    )
    author_client = TestClient(docs.create_app())

    target = app_root / "content" / "docs" / "get-started.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Run the local docs server:",
            "Run the local author dashboard:",
        ),
        encoding="utf-8",
    )
    future = time.time() + 5
    os.utime(target, (future, future))
    time.sleep(1.1)

    async def _fetch_author() -> dict[str, object]:
        dashboard = await author_client.get("/docs/_author/dashboard")
        data = await author_client.get("/docs/_author/dashboard?json=1")
        page = await author_client.get("/docs/get-started/")
        return {"dashboard": dashboard, "data": data, "page": page}

    author_payload = asyncio.run(_fetch_author())
    dashboard = author_payload["dashboard"]
    data_payload = json.loads(author_payload["data"].text.split("<script", 1)[0])

    assert dashboard.status == 200
    assert 'id="author-dashboard"' in dashboard.text
    assert 'data-mount-id="docs"' in dashboard.text
    assert 'data-source-format="patitas-markdown"' in dashboard.text
    assert "Top blocking errors" in dashboard.text
    assert "broken internal link" in dashboard.text
    assert "/docs/_author/studio?slug=docs/broken" in dashboard.text
    assert author_payload["page"].status == 200
    assert "Dashboard" in author_payload["page"].text
    assert data_payload["ok"] is True
    summary = data_payload["data"]["summary"]
    assert summary["mount_count"] == 1
    assert summary["page_count"] >= 3
    assert summary["error_count"] >= 1
    assert summary["freshness_count"] >= 1
    mount = data_payload["data"]["mounts"][0]
    assert mount["id"] == "docs"
    assert mount["status"] in {"blocked", "stale"}
    assert mount["dirty_count"] + mount["stale_count"] >= 1
    assert any(item["format"] == "patitas-markdown" for item in mount["formats"])
    assert any("broken internal link" in item["message"] for item in data_payload["data"]["blocking"])

    public_docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, True, False),
    )
    public_client = TestClient(public_docs.create_app())
    public_dashboard = asyncio.run(public_client.get("/docs/_author/dashboard"))
    public_page = asyncio.run(public_client.get("/docs/get-started/"))
    assert public_dashboard.status == 404
    assert 'id="author-dashboard"' not in public_page.text
    assert "Dashboard" not in public_page.text


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
    dry_data = _assert_author_json_envelope(
        dry_payload,
        command="new",
        operation="new",
        target_path=str(target),
        resulting_visibility="draft",
    )
    assert dry_payload["ok"] is True
    assert dry_payload["command"] == "author new"
    assert dry_data["dry_run"] is True
    assert dry_data["changed_files"] == []
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
    create_data = _assert_author_json_envelope(
        create_payload,
        command="new",
        operation="new",
        target_path=str(target),
        resulting_visibility="draft",
    )
    assert create_payload["ok"] is True
    assert create_data["changed_files"] == [str(target)]
    assert "draft: true" in target.read_text(encoding="utf-8")

    main(["--app-root", str(app_root), "author", "status", "docs/release-notes", "--json"])
    status_payload = json.loads(capsys.readouterr().out)
    status_data = _assert_author_json_envelope(
        status_payload,
        command="status",
        operation="status",
        target_path=str(target),
        resulting_visibility="draft",
    )
    assert status_payload["ok"] is True
    assert status_data["resulting_visibility"] == "draft"

    main(["--app-root", str(app_root), "author", "validate", "docs/release-notes", "--json"])
    validate_payload = json.loads(capsys.readouterr().out)
    validate_data = _assert_author_json_envelope(
        validate_payload,
        command="validate",
        operation="validate",
        target_path=str(target),
        resulting_visibility="draft",
    )
    assert validate_payload["ok"] is True
    assert validate_payload["command"] == "author validate"
    assert validate_data["changed_files"] == []

    try:
        main(["--app-root", str(app_root), "author", "publish", "docs/release-notes", "--json"])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("publish should require --yes or --dry-run")
    confirm_payload = json.loads(capsys.readouterr().out)
    _assert_author_json_envelope(
        confirm_payload,
        command="publish",
        operation="publish",
        ok=False,
        target_path=str(target),
        resulting_visibility=None,
    )
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
    publish_dry_data = _assert_author_json_envelope(
        publish_dry,
        command="publish",
        operation="publish",
        target_path=str(target),
        resulting_visibility="public",
    )
    assert publish_dry["ok"] is True
    assert publish_dry_data["previous_visibility"] == "draft"
    assert "visibility: public" in publish_dry_data["diff"]
    impact = publish_dry_data["publication_impact"]
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
    publish_data = _assert_author_json_envelope(
        publish_payload,
        command="publish",
        operation="publish",
        target_path=str(target),
        resulting_visibility="public",
    )
    source = target.read_text(encoding="utf-8")
    assert publish_payload["ok"] is True
    assert publish_data["changed_files"] == [str(target)]
    assert publish_data["publication_impact"]["resulting_public"] is True
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
    unpublish_data = _assert_author_json_envelope(
        unpublish_dry,
        command="unpublish",
        operation="unpublish",
        target_path=str(target),
        resulting_visibility="draft",
    )
    assert unpublish_dry["ok"] is True
    assert unpublish_data["publication_impact"]["previous_public"] is True
    assert unpublish_data["publication_impact"]["resulting_public"] is False
    assert unpublish_data["publication_impact"]["change"] == "removed_from_public_output"
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
    data = _assert_author_json_envelope(
        payload,
        command="validate",
        operation="validate",
        ok=False,
        target_path=str(target),
    )
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert data["diagnostics"][0]["severity"] == "error"
    assert "visibility must be one of" in data["diagnostics"][0]["message"]
    assert payload["diagnostics"][0]["source_path"] == str(target)


def test_author_publish_clears_archived_visibility_conflict(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    target = app_root / "content" / "docs" / "get-started.md"

    main(["--app-root", str(app_root), "author", "archive", "docs/get-started", "--yes", "--json"])
    archive_payload = json.loads(capsys.readouterr().out)
    archive_data = _assert_author_json_envelope(
        archive_payload,
        command="archive",
        operation="archive",
        target_path=str(target),
        resulting_visibility="archived",
    )
    assert archive_payload["ok"] is True
    assert archive_data["changed_files"] == [str(target)]
    archived_source = target.read_text(encoding="utf-8")
    assert "visibility: archived" in archived_source
    assert "archived_at:" in archived_source

    main(["--app-root", str(app_root), "author", "publish", "docs/get-started", "--dry-run", "--json"])
    preview_payload = json.loads(capsys.readouterr().out)
    preview_data = _assert_author_json_envelope(
        preview_payload,
        command="publish",
        operation="publish",
        target_path=str(target),
        resulting_visibility="public",
    )
    assert preview_payload["ok"] is True
    assert preview_data["previous_visibility"] == "archived"
    assert "-archived_at:" in preview_data["diff"]
    assert "archived_at:" in target.read_text(encoding="utf-8")

    main(["--app-root", str(app_root), "author", "publish", "docs/get-started", "--yes", "--json"])
    publish_payload = json.loads(capsys.readouterr().out)
    _assert_author_json_envelope(
        publish_payload,
        command="publish",
        operation="publish",
        target_path=str(target),
        resulting_visibility="public",
    )
    assert publish_payload["ok"] is True
    published_source = target.read_text(encoding="utf-8")
    assert "visibility: public" in published_source
    assert "published_at:" in published_source
    assert "archived_at:" not in published_source

    main(["--app-root", str(app_root), "author", "validate", "docs/get-started", "--json"])
    validate_payload = json.loads(capsys.readouterr().out)
    _assert_author_json_envelope(
        validate_payload,
        command="validate",
        operation="validate",
        target_path=str(target),
        resulting_visibility="public",
    )
    assert validate_payload["ok"] is True


def test_author_edit_json_contract_and_confirmation_gate(tmp_path: Path, capsys) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    capsys.readouterr()
    target = app_root / "content" / "docs" / "get-started.md"
    original = target.read_text(encoding="utf-8") + "\nRepeat marker.\nRepeat marker.\n"
    target.write_text(original, encoding="utf-8")
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
    dry_data = _assert_author_json_envelope(
        dry_payload,
        command="edit",
        operation="apply_edit",
        target_path=str(target),
        resulting_visibility="public",
    )
    assert dry_payload["ok"] is True
    assert dry_payload["command"] == "author edit"
    assert dry_data["dry_run"] is True
    assert dry_data["changed_files"] == []
    assert new_text in dry_data["diff"]
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
    _assert_author_json_envelope(
        confirm_payload,
        command="edit",
        operation="apply_edit",
        ok=False,
        target_path=str(target),
        resulting_visibility=None,
    )
    assert confirm_payload["ok"] is False
    assert confirm_payload["diagnostics"][0]["rule_id"] == "fura.author"
    assert target.read_text(encoding="utf-8") == original

    try:
        main([
            "--app-root",
            str(app_root),
            "author",
            "edit",
            "docs/get-started",
            "--old-text",
            "Repeat marker.",
            "--new-text",
            "Unique marker.",
            "--dry-run",
            "--json",
        ])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("edit should reject multiple matching source spans")
    multiple_payload = json.loads(capsys.readouterr().out)
    _assert_author_json_envelope(
        multiple_payload,
        command="edit",
        operation="apply_edit",
        ok=False,
        target_path=str(target),
        resulting_visibility=None,
    )
    assert multiple_payload["ok"] is False
    assert "old_text matches multiple source spans" in multiple_payload["diagnostics"][0]["message"]
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
    edit_data = _assert_author_json_envelope(
        edit_payload,
        command="edit",
        operation="apply_edit",
        target_path=str(target),
        resulting_visibility="public",
    )
    assert edit_payload["ok"] is True
    assert edit_data["changed_files"] == [str(target)]
    assert edit_data["previous_visibility"] == "public"
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
    _assert_author_json_envelope(
        stale_payload,
        command="edit",
        operation="apply_edit",
        ok=False,
        target_path=str(target),
        resulting_visibility=None,
    )
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
    _assert_author_json_envelope(
        missing_payload,
        command="status",
        operation="status",
        ok=False,
        target_path=None,
        resulting_visibility=None,
    )
    assert missing_payload["ok"] is False
    assert "unknown mount" in missing_payload["diagnostics"][0]["message"]

    try:
        main([
            "--app-root",
            str(app_root),
            "author",
            "new",
            "../outside",
            "--title",
            "Outside",
            "--yes",
            "--json",
        ])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("escaping author slug should fail")
    escape_payload = json.loads(capsys.readouterr().out)
    outside = app_root / "outside.md"
    _assert_author_json_envelope(
        escape_payload,
        command="new",
        operation="new",
        ok=False,
        target_path=str(outside),
        resulting_visibility=None,
    )
    assert escape_payload["ok"] is False
    assert "escapes the selected content root" in escape_payload["diagnostics"][0]["message"]
    assert escape_payload["diagnostics"][0]["source_path"] == str(outside)
    assert not outside.exists()

    try:
        main(["--app-root", str(app_root), "author", "status", "docs/same", "--json"])
    except SystemExit as exc:
        assert exc.code == 3
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("ambiguous slug should fail")
    ambiguous_payload = json.loads(capsys.readouterr().out)
    _assert_author_json_envelope(
        ambiguous_payload,
        command="status",
        operation="status",
        ok=False,
        target_path=None,
        resulting_visibility=None,
    )
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
    shared_target = shared / "docs" / "same.md"
    shared_data = _assert_author_json_envelope(
        shared_payload,
        command="status",
        operation="status",
        target_path=str(shared_target),
        resulting_visibility="public",
    )
    assert shared_payload["ok"] is True
    assert shared_data["mount"] == "shared"


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
        "author-archive",
        "source-sync",
    } <= recipe_ids


def test_author_recipes_encode_safe_mutation_flow(capsys) -> None:
    main(["recipes", "--json"])
    payload = json.loads(capsys.readouterr().out)
    recipes = {recipe["id"]: recipe for recipe in payload["data"]["recipes"]}

    draft_steps = {step["id"]: step for step in recipes["author-draft"]["steps"]}
    assert draft_steps["preview-draft"]["dry_run"] is True
    assert draft_steps["create-draft"]["requires_confirmation"] is True
    assert "--dry-run" in draft_steps["preview-draft"]["command"]
    assert "--yes" in draft_steps["create-draft"]["command"]

    edit_publish_steps = {step["id"]: step for step in recipes["author-edit-publish"]["steps"]}
    assert edit_publish_steps["read-source"]["command"].startswith("MCP author_read_source")
    assert edit_publish_steps["preview-edit"]["dry_run"] is True
    assert edit_publish_steps["apply-edit"]["requires_confirmation"] is True
    assert "author_apply_edit" in edit_publish_steps["apply-edit"]["command"]
    assert "confirmed=true dry_run=false" in edit_publish_steps["apply-edit"]["command"]
    assert edit_publish_steps["publish-dry-run"]["dry_run"] is True
    assert "author_publish" in edit_publish_steps["publish-dry-run"]["command"]
    assert "dry_run=true" in edit_publish_steps["publish-dry-run"]["command"]
    assert edit_publish_steps["publish"]["requires_confirmation"] is True
    assert "confirmed=true dry_run=false" in edit_publish_steps["publish"]["command"]

    repair_steps = {step["id"]: step for step in recipes["author-stale-repair"]["steps"]}
    assert repair_steps["inspect-impact"]["command"].startswith("MCP author_inspect_publication_impact")
    assert repair_steps["preview-fix"]["dry_run"] is True
    assert repair_steps["apply-fix"]["requires_confirmation"] is True
    assert "confirmed=true dry_run=false" in repair_steps["apply-fix"]["command"]
    assert "check --content-only --json" in repair_steps["validate"]["command"]

    remediation_steps = {step["id"]: step for step in recipes["author-publish-remediation"]["steps"]}
    assert remediation_steps["publish-preview"]["dry_run"] is True
    assert "author_validate" in remediation_steps["validate-target"]["command"]
    assert remediation_steps["repair-preview"]["dry_run"] is True
    assert remediation_steps["repair-write"]["requires_confirmation"] is True
    assert "confirmed=true dry_run=false" in remediation_steps["repair-write"]["command"]
    assert remediation_steps["retry-publish"]["requires_confirmation"] is True
    assert "author_publish" in remediation_steps["retry-publish"]["command"]
    assert "confirmed=true dry_run=false" in remediation_steps["retry-publish"]["command"]

    archive_steps = {step["id"]: step for step in recipes["author-archive"]["steps"]}
    assert archive_steps["inspect-impact"]["command"].startswith(
        "MCP author_inspect_publication_impact"
    )
    assert archive_steps["archive-dry-run"]["dry_run"] is True
    assert "author_archive" in archive_steps["archive-dry-run"]["command"]
    assert "dry_run=true" in archive_steps["archive-dry-run"]["command"]
    assert archive_steps["archive"]["requires_confirmation"] is True
    assert "confirmed=true dry_run=false" in archive_steps["archive"]["command"]
    assert "check --content-only --json" in archive_steps["validate"]["command"]


def test_query_recipe_covers_dcp_and_mcp_graph_queries(capsys) -> None:
    main(["recipes", "query", "--json"])
    payload = json.loads(capsys.readouterr().out)
    recipe = payload["data"]["recipes"][0]
    steps = {step["id"]: step for step in recipe["steps"]}

    assert {"by-heading", "by-directive", "by-namespace", "by-dcp-edge", "by-mcp-graph"} <= set(steps)
    assert "/catalog/query.json" in steps["by-dcp-edge"]["command"]
    assert "edge_kind=<EDGE_KIND>" in steps["by-dcp-edge"]["command"]
    assert "target=<TARGET>" in steps["by-dcp-edge"]["command"]
    assert steps["by-mcp-graph"]["command"].startswith("MCP query_graph")
    assert "structuredContent" in " ".join(recipe["verifies"])
    assert "graph/query.json" in recipe["related_commands"]


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
    assert "fura://catalog/graph" in resource_uris
    assert "fura://catalog/api-operations" in resource_uris
    assert "fura://reports/validation" in resource_uris
    assert "fura://reports/audit" in resource_uris
    assert {
        "semantic_search",
        "retrieve_node",
        "query_graph",
        "traverse_graph",
        "run_checks",
        "author_create_draft",
        "author_read_source",
        "author_apply_edit",
        "author_publish",
        "author_archive",
    } <= tool_names


def test_mcp_json_rpc_tools_return_structured_content(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs_dir = app_root / "content" / "docs"
    (docs_dir / "mcp-query-target.md").write_text(
        "---\ntitle: MCP Query Target\ntags: [agent-query]\n---\n# MCP Query Target\n",
        encoding="utf-8",
    )
    (docs_dir / "mcp-query-source.md").write_text(
        "---\n"
        "title: MCP Query Source\n"
        "tags: [agent-query]\n"
        "owner: docs-platform\n"
        "api_schemas: [User]\n"
        "---\n"
        "# MCP Query Source\n\n"
        "[Target](/docs/mcp-query-target/)\n",
        encoding="utf-8",
    )
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
    query_response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "query_graph",
                "arguments": {
                    "tag": "agent-query",
                    "edge": "api_schema",
                    "target": "schema:User",
                },
            },
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
    query_payload = query_response["result"]["structuredContent"]
    assert query_payload["page_count"] == 1
    assert query_payload["edge_count"] == 1
    assert query_payload["edges"][0]["target"] == "schema:User"
    assert query_payload["graph_nodes"] == [
        {
            "id": "schema:User",
            "kind": "api_schema",
            "label": "User",
            "mount": "docs",
            "edition": "latest",
        }
    ]


def test_mcp_agent_contract_covers_required_resources_tools_and_schemas(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    docs_dir = app_root / "content" / "docs"
    (docs_dir / "contract-source.md").write_text(
        "---\n"
        "title: Contract Source\n"
        "tags: [contract]\n"
        "owner: docs-platform\n"
        "api_schemas: [Invoice]\n"
        "---\n"
        "# Contract Source\n\n"
        "[Get started](/docs/get-started/)\n",
        encoding="utf-8",
    )
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    server = FuraMCPServer(docs)
    required_resources = {
        "fura://catalog/nodes": ("count", "nodes"),
        "fura://catalog/graph": ("pages", "edges"),
        "fura://catalog/api-operations": ("count", "operations"),
        "fura://catalog/sources": ("mount_count", "mounts"),
        "fura://catalog/channels": ("active_channel", "mounts"),
        "fura://reports/validation": ("ok", "errors", "warnings"),
        "fura://reports/stale-impact": ("stale_count", "entries"),
    }
    resource_uris = {resource["uri"] for resource in server.list_resources()}

    assert set(required_resources) <= resource_uris
    for uri, keys in required_resources.items():
        payload = json.loads(server.read_resource(uri)["text"])
        assert isinstance(payload["schema_version"], int)
        assert payload["schema_version"] >= 1
        for key in keys:
            assert key in payload

    tools = {tool["name"]: tool for tool in server.list_tools()}
    contract_node = docs.catalog.get_by_slug("docs/contract-source")
    assert contract_node is not None
    required_tools = {
        "semantic_search": {"query": "contract", "limit": 5},
        "retrieve_node": {"node_id": contract_node.node_id},
        "query_graph": {"owner": "docs-platform", "edge_kind": "api_schema", "target": "schema:Invoice"},
        "traverse_graph": {"url": "/docs/contract-source/"},
        "inspect_source_health": {},
        "run_checks": {},
        "explain_stale_impact": {},
    }
    structured_by_tool = {}

    assert set(required_tools) <= set(tools)
    for name, arguments in required_tools.items():
        tool = tools[name]
        assert tool["inputSchema"]["type"] == "object"
        assert tool["outputSchema"]["type"] == "object"
        assert tool["outputSchema"]["required"]

        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        result = response["result"]
        assert result["isError"] is False
        assert isinstance(result["structuredContent"], dict)
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        structured_by_tool[name] = result["structuredContent"]

    assert structured_by_tool["query_graph"]["graph_nodes"] == [
        {
            "id": "schema:Invoice",
            "kind": "api_schema",
            "label": "Invoice",
            "mount": "docs",
            "edition": "latest",
        }
    ]


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
    graph_query = client.call("query_graph", mount=node.mount)
    author_denied = client.call("author_read_source", target="docs/get-started")

    assert init["serverInfo"]["name"] == "furatena-catalog"
    assert "fura://catalog/nodes" in {resource["uri"] for resource in resources}
    assert "milo://stats" in {resource["uri"] for resource in resources}
    tool_by_name = {tool.name: tool for tool in tools}
    assert "semantic_search" in tool_by_name
    assert "query_graph" in tool_by_name
    assert tool_by_name["semantic_search"].output_schema is not None
    assert tool_by_name["query_graph"].output_schema is not None
    assert search.is_error is False
    assert search.structured["count"] >= 1
    assert retrieve.is_error is False
    assert retrieve.structured["node_id"] == node.node_id
    assert graph_query.is_error is False
    assert graph_query.structured["page_count"] >= 1
    assert author_denied.is_error is False
    assert author_denied.structured["ok"] is False
    assert author_denied.structured["diagnostics"][0]["rule_id"] == "fura.mcp.author"


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


def test_mcp_stale_impact_groups_by_provenance(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    target = app_root / "content" / "docs" / "get-started.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "---\n",
            (
                "---\n"
                "owner: docs-platform\n"
                "source_provider: git\n"
                "source_repo: lbliii/furatena\n"
                "source_ref: main\n"
                "tenant: default\n"
                "workspace: platform\n"
                "site: docs\n"
            ),
            1,
        ),
        encoding="utf-8",
    )
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    docs.catalog._shards["docs"]._last_invalidations["docs/get-started"] = (
        "page-root",
        "search",
    )
    server = FuraMCPServer(docs, include_private=True)
    response = server.handle_request(
        {
            "jsonrpc": "2.0",
            "id": "stale",
            "method": "tools/call",
            "params": {
                "name": "explain_stale_impact",
                "arguments": {"slug": "docs/get-started"},
            },
        }
    )
    payload = response["result"]["structuredContent"]
    impact = payload["impact"][0]

    assert payload["stale_count"] == 1
    assert impact["owner"] == "docs-platform"
    assert impact["source_key"] == "git:lbliii/furatena@main:docs/get-started.md"
    assert impact["tenant"] == "default"
    assert impact["workspace"] == "platform"
    assert impact["site"] == "docs"
    assert impact["provenance"]["tenant"] == "default"
    assert impact["provenance"]["workspace"] == "platform"
    assert impact["provenance"]["site"] == "docs"
    assert payload["groups"]["by_owner"] == [
        {
            "key": "docs-platform",
            "count": 1,
            "slugs": ["docs/get-started"],
            "refresh_targets": ["page-root", "search"],
        }
    ]
    assert payload["groups"]["by_source"][0]["key"] == impact["source_key"]
    assert payload["groups"]["by_mount"][0]["key"] == "docs"
    assert payload["groups"]["by_tenant"][0]["key"] == "default"
    assert payload["groups"]["by_workspace"][0]["key"] == "platform"
    assert payload["groups"]["by_site"][0]["key"] == "docs"
    assert payload["groups"]["by_channel"][0]["key"] == "latest"
    assert payload["groups"]["by_output_channel"] == payload["groups"]["by_channel"]
    assert impact["affected_chunks"]
    assert impact["graph_context"]["node_id"] == impact["provenance"]["node_id"]
    assert "changed_graph_edges" in impact
    assert impact["recommended_remediation"]
    assert payload["repair_tasks"][0]["owner"] == "docs-platform"
    assert payload["repair_tasks"][0]["source_paths"] == ["docs/get-started.md"]
    assert payload["repair_tasks"][0]["dcp_node_id"] == impact["provenance"]["node_id"]
    assert "Refresh stale docs output" in payload["task_markdown"]


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

    def raw_call(server: FuraMCPServer, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": name,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    def call(server: FuraMCPServer, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return raw_call(server, name, arguments)["result"]

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
    draft_node = docs.catalog.get_by_slug("docs/mcp-draft")
    assert draft_node is not None
    public_draft_retrieve = raw_call(public_server, "retrieve_node", {"node_id": draft_node.node_id})
    assert public_draft_retrieve["error"]["code"] == -32602

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
    published_node = docs.catalog.get_by_slug("docs/mcp-draft")
    assert published_node is not None
    public_published_retrieve = raw_call(
        public_server,
        "retrieve_node",
        {"node_id": published_node.node_id},
    )
    assert public_published_retrieve["result"]["structuredContent"]["node_id"] == published_node.node_id

    unsafe_unpublish = call(
        private_server,
        "author_unpublish",
        {"target": "docs/mcp-draft", "dry_run": False},
    )
    assert unsafe_unpublish["isError"] is True
    assert "visibility: public" in target.read_text(encoding="utf-8")

    unpublish_preview = call(private_server, "author_unpublish", {"target": "docs/mcp-draft"})
    assert unpublish_preview["isError"] is False
    assert unpublish_preview["structuredContent"]["dry_run"] is True
    assert unpublish_preview["structuredContent"]["resulting_visibility"] == "draft"
    assert unpublish_preview["structuredContent"]["publication_impact"]["change"] == "removed_from_public_output"

    unpublish = call(
        private_server,
        "author_unpublish",
        {"target": "docs/mcp-draft", "dry_run": False, "confirmed": True},
    )
    assert unpublish["isError"] is False
    assert unpublish["structuredContent"]["audit"]["previous_state"] == "public"
    assert unpublish["structuredContent"]["audit"]["resulting_state"] == "draft"
    assert "visibility: draft" in target.read_text(encoding="utf-8")
    draft_again_node = docs.catalog.get_by_slug("docs/mcp-draft")
    assert draft_again_node is not None
    public_unpublished_retrieve = raw_call(
        public_server,
        "retrieve_node",
        {"node_id": draft_again_node.node_id},
    )
    assert public_unpublished_retrieve["error"]["code"] == -32602

    archive_preview = call(private_server, "author_archive", {"target": "docs/mcp-draft"})
    assert archive_preview["isError"] is False
    assert archive_preview["structuredContent"]["dry_run"] is True
    assert archive_preview["structuredContent"]["resulting_visibility"] == "archived"
    assert archive_preview["structuredContent"]["publication_impact"]["change"] == "private_metadata_updated"
    assert "visibility: archived" not in target.read_text(encoding="utf-8")

    unsafe_archive = call(
        private_server,
        "author_archive",
        {"target": "docs/mcp-draft", "dry_run": False},
    )
    assert unsafe_archive["isError"] is True
    assert "visibility: archived" not in target.read_text(encoding="utf-8")

    archive = call(
        private_server,
        "author_archive",
        {"target": "docs/mcp-draft", "dry_run": False, "confirmed": True},
    )
    assert archive["isError"] is False
    assert archive["structuredContent"]["audit"]["previous_state"] == "draft"
    assert archive["structuredContent"]["audit"]["resulting_state"] == "archived"
    assert "visibility: archived" in target.read_text(encoding="utf-8")
    archived_node = docs.catalog.get_by_slug("docs/mcp-draft")
    assert archived_node is not None
    public_archived_retrieve = raw_call(
        public_server,
        "retrieve_node",
        {"node_id": archived_node.node_id},
    )
    assert public_archived_retrieve["error"]["code"] == -32602

    audit = json.loads(private_server.read_resource("fura://reports/audit")["text"])
    draft_preview_entry = next(
        entry
        for entry in audit["entries"]
        if entry["tool"] == "author_create_draft" and entry["actor"] == "agent-test"
    )
    unsafe_publish_entry = next(
        entry
        for entry in audit["entries"]
        if entry["tool"] == "author_publish" and entry["status"] == "error"
    )
    publish_entry = next(
        entry
        for entry in audit["entries"]
        if entry["tool"] == "author_publish"
        and entry["status"] == "ok"
        and entry["resulting_state"] == "public"
        and entry["confirmed"] is True
    )
    assert draft_preview_entry["command"] == "author_create_draft"
    assert draft_preview_entry["target"] == "docs/mcp-draft"
    assert draft_preview_entry["target_path"] == str(target)
    assert draft_preview_entry["dry_run"] is True
    assert draft_preview_entry["confirmed"] is False
    assert unsafe_publish_entry["command"] == "author_publish"
    assert unsafe_publish_entry["target_path"] == str(target)
    assert unsafe_publish_entry["dry_run"] is False
    assert unsafe_publish_entry["confirmed"] is False
    assert unsafe_publish_entry["diagnostics"][0]["rule_id"] == "fura.author"
    assert publish_entry["previous_state"] == "draft"
    assert publish_entry["resulting_state"] == "public"
    assert publish_entry["dry_run"] is False
    assert publish_entry["confirmed"] is True


def test_mcp_author_validate_scopes_lifecycle_errors_to_target(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"

    main(["init", str(app_root), "--name", "Acme Docs"])
    target = app_root / "content" / "docs" / "get-started.md"
    source = target.read_text(encoding="utf-8")
    target.write_text(source.replace("---\n", "---\nvisibility: invalid\n", 1), encoding="utf-8")
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    server = FuraMCPServer(docs, include_private=True)

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

    validation = call("author_validate", {"target": "docs/get-started"})
    payload = validation["structuredContent"]
    assert validation["isError"] is True
    assert payload["operation"] == "validate"
    assert payload["target_path"] == str(target)
    assert payload["error_count"] == 1
    assert payload["errors"][0]["source_path"] == str(target)
    assert "visibility must be one of" in payload["errors"][0]["message"]
    assert payload["audit"]["command"] == "author_validate"
    assert payload["audit"]["target_path"] == str(target)
    assert payload["audit"]["diagnostics"][0]["source_path"] == str(target)

    impact = call("author_inspect_publication_impact", {"target": "docs/get-started"})
    impact_payload = impact["structuredContent"]
    assert impact["isError"] is True
    assert impact_payload["validation"]["error_count"] == 1
    assert impact_payload["validation"]["errors"][0]["source_path"] == str(target)


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
