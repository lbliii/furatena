"""Advanced author, agent, and migration guides stay complete and executable."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from furatena.cli.main import main

REPO = Path(__file__).resolve().parents[1]
CONTENT = REPO / "content" / "furatena" / "docs"
AUTHOR_GUIDE = CONTENT / "authoring" / "lifecycle-workflow.md"
AGENT_GUIDE = CONTENT / "operations" / "consume-agent-outputs.md"
MIGRATION_GUIDE = CONTENT / "operations" / "migrate-from-js-docs.md"


def test_guides_cover_required_workflow_surfaces() -> None:
    author = AUTHOR_GUIDE.read_text(encoding="utf-8")
    agent = AGENT_GUIDE.read_text(encoding="utf-8")
    migration = MIGRATION_GUIDE.read_text(encoding="utf-8")

    for token in (
        "/docs/_author/studio",
        "author validate",
        "--source-revision",
        "--dry-run",
        "publication_impact",
        "catalog.json",
        "search.json",
        "llms.txt",
    ):
        assert token in author
    for token in (
        "/llms.txt",
        "/llms-full.txt",
        "/docs/PATH/index.txt",
        "/channels.json",
        "/tools.json",
        "/catalog.json",
        "fura mcp --preview --describe --json",
        "fura://catalog/graph",
    ):
        assert token in agent
    for token in (
        "MDX",
        "MyST",
        "RST",
        "openapi:",
        "specs/openapi.yaml",
        "fura migrate --report --json",
        "fura api-diff",
        "/catalog/api-operations.json",
        "list_api_operations",
    ):
        assert token in migration


def test_documented_safe_inspection_commands_run_gil_off(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    assert hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled()
    app_root = tmp_path / "app"
    monkeypatch.setenv("FURA_APP_ROOT", str(app_root))

    main(["init", str(app_root), "--name", "Workflow Guides"])
    capsys.readouterr()
    main(["--app-root", str(app_root), "author", "status", "docs/get-started", "--json"])
    status = json.loads(capsys.readouterr().out)
    assert status["ok"] is True

    main(["--app-root", str(app_root), "author", "validate", "docs/get-started", "--json"])
    validation = json.loads(capsys.readouterr().out)
    assert validation["ok"] is True

    main(
        [
            "--app-root",
            str(app_root),
            "author",
            "draft",
            "docs/get-started",
            "--dry-run",
            "--json",
        ]
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["ok"] is True
    assert preview["data"]["dry_run"] is True

    main(
        [
            "--app-root",
            str(app_root),
            "mcp",
            "--preview",
            "--describe",
            "--no-autodoc",
            "--json",
        ]
    )
    mcp = json.loads(capsys.readouterr().out)
    assert mcp["ok"] is True
    assert {tool["name"] for tool in mcp["data"]["tools"]} >= {
        "semantic_search",
        "query_graph",
    }

    main(["--app-root", str(app_root), "migrate", "--report", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["data"]["report"] is True
