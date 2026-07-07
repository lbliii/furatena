"""Typed domain error and CLI diagnostic contracts."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pytest

from furatena.catalog.access import author_permission_for
from furatena.catalog.exceptions import (
    AccessDeniedError,
    AccessPolicyError,
    CatalogConfigError,
    CatalogError,
    CatalogLoadError,
    ContentParseError,
    ExportError,
    SourceSyncError,
)
from furatena.catalog.loader import DocCatalog
from furatena.catalog.sources import git as git_source
from furatena.catalog.sources.parse import parse_source_text

cli_main = importlib.import_module("furatena.cli.main")


def test_domain_errors_preserve_generic_catch_compatibility() -> None:
    assert issubclass(CatalogConfigError, ValueError)
    assert issubclass(ContentParseError, ValueError)
    assert issubclass(AccessPolicyError, ValueError)
    assert issubclass(AccessDeniedError, PermissionError)
    assert issubclass(CatalogLoadError, FileNotFoundError)
    assert issubclass(SourceSyncError, RuntimeError)
    assert issubclass(ExportError, RuntimeError)
    assert all(issubclass(item, CatalogError) for item in CatalogError.__subclasses__())


def test_parse_load_access_and_source_errors_include_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ContentParseError) as parse_info:
        parse_source_text("---\ntitle: [\n---\n", content_format="patitas-markdown", path="bad.md")
    assert parse_info.value.context == {
        "path": "bad.md",
        "operation": "parse_frontmatter",
    }

    with pytest.raises(CatalogLoadError) as load_info:
        DocCatalog.from_frozen(tmp_path / "missing", mount="docs")
    assert load_info.value.context["mount"] == "docs"
    assert load_info.value.context["path"].endswith("missing/catalog.json")

    with pytest.raises(AccessPolicyError) as access_info:
        author_permission_for("destroy")
    assert access_info.value.context == {"operation": "destroy"}

    monkeypatch.setattr(git_source.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(SourceSyncError) as sync_info:
        git_source._run_git("status", mount_id="vendor")
    assert sync_info.value.context == {"mount": "vendor", "operation": "git"}


def test_cli_converts_domain_errors_to_stable_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ExportError(
        "page export failed",
        path="public/docs/page/index.html",
        slug="/docs/page/",
        operation="export_page",
    )
    args = argparse.Namespace(command="export", handler=lambda _: (_ for _ in ()).throw(error))
    parser = argparse.Namespace(parse_args=lambda argv: args)
    monkeypatch.setattr(cli_main, "_build_parser", lambda: parser)

    _args, result = cli_main._invoke(["export", "--json"])
    assert result is not None
    assert result.ok is False
    assert int(result.exit_code) == 4
    assert result.diagnostics[0].rule_id == "fura.export"
    assert result.diagnostics[0].source_path == "public/docs/page/index.html"
    assert result.data["error"] == {
        "code": "fura.export",
        "message": "page export failed",
        "context": {
            "path": "public/docs/page/index.html",
            "slug": "/docs/page/",
            "operation": "export_page",
        },
    }
