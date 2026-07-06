"""Unit tests for shared freeze/static packaging policies."""

from __future__ import annotations

from pathlib import Path

import pytest

from furatena.catalog import packaging
from furatena.catalog.static_export import StaticExportLifecycleError


def test_prefix_root_paths_rewrites_html_and_json_once() -> None:
    source = (
        '<a href="/docs/">Docs</a><img src="//cdn.example/image.png">'
        '{"url":"/api/","self":"/furatena/already/"}'
    )

    rewritten = packaging.prefix_root_paths(source, "/furatena")

    assert 'href="/furatena/docs/"' in rewritten
    assert 'src="//cdn.example/image.png"' in rewritten
    assert '"url":"/furatena/api/"' in rewritten
    assert '"self":"/furatena/already/"' in rewritten
    assert packaging.prefix_root_paths(rewritten, "/furatena") == rewritten


def test_prefix_markdown_links_preserves_code() -> None:
    source = "[Docs](/docs/) and `[raw](/raw/)`\n```md\n[example](/example/)\n```\n"

    rewritten = packaging.prefix_markdown_links(source, "furatena/")

    assert "[Docs](/furatena/docs/)" in rewritten
    assert "`[raw](/raw/)`" in rewritten
    assert "[example](/example/)" in rewritten


def test_prune_stale_files_supports_target_filters_and_preserves(tmp_path: Path) -> None:
    kept = tmp_path / "pages" / "keep.html"
    stale = tmp_path / "pages" / "stale.html"
    unrelated = tmp_path / "pages" / "metadata.json"
    preserved = tmp_path / "pages" / "manifest.html"
    for path in (kept, stale, unrelated, preserved):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")

    removed = packaging.prune_stale_files(
        tmp_path / "pages",
        {kept},
        suffixes=(".html",),
        preserve=frozenset({Path("manifest.html")}),
    )

    assert removed == 1
    assert kept.is_file()
    assert not stale.exists()
    assert unrelated.is_file()
    assert preserved.is_file()


def test_lifecycle_gate_is_shared_and_target_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        packaging,
        "check_lifecycle_sources",
        lambda _catalog: (["docs/bad.md: invalid visibility"], ["docs/warn.md: missing owner"]),
    )

    with pytest.raises(packaging.PackagingLifecycleError) as exc_info:
        packaging.validate_packaging_lifecycle(object(), target="catalog freeze")

    assert exc_info.value.target == "catalog freeze"
    assert exc_info.value.errors == ["docs/bad.md: invalid visibility"]
    assert exc_info.value.warnings == ["docs/warn.md: missing owner"]
    assert str(exc_info.value) == "catalog freeze blocked by lifecycle safety checks"

    assert packaging.validate_packaging_lifecycle(
        object(),
        target="static export",
        allow_errors=True,
    ) == ([], [])


def test_static_lifecycle_error_keeps_legacy_constructor() -> None:
    error = StaticExportLifecycleError(["error"], ["warning"])

    assert error.target == "static export"
    assert error.errors == ["error"]
    assert error.warnings == ["warning"]
