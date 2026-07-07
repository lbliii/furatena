"""Rendering-context service contracts without app startup."""

from __future__ import annotations

from pathlib import Path

from furatena.catalog.config import load_docs_config
from furatena.catalog.i18n import LocaleResolutionService
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.render_context import RenderContextService
from furatena.catalog.views import ViewRegistry
from tests.support import write_minimal_docs_yaml, write_mounts_yaml


def test_render_context_service_builds_page_and_shell_from_fixture_catalog(
    tmp_path: Path,
) -> None:
    app_root = tmp_path / "app"
    content = app_root / "content"
    content.mkdir(parents=True)
    (content / "_index.md").write_text(
        "---\ntitle: Home\n---\n# Home\n",
        encoding="utf-8",
    )
    (content / "guide.md").write_text(
        "---\ntitle: Guide\n---\n# Guide\n",
        encoding="utf-8",
    )
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(app_root / "mounts.yaml", content)

    config = load_docs_config(app_root / "docs.yaml")
    catalog = CatalogRegistry.from_config(
        app_root / "mounts.yaml",
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
        i18n_config=config.i18n,
    )
    service = RenderContextService(
        config,
        catalog,
        ViewRegistry(config),
        LocaleResolutionService(config.i18n),
    )
    guide = catalog.get_by_slug("guide")

    assert guide is not None
    page = service.page_context(guide)
    shell = service.shell_context()

    assert page["node"] is guide
    assert page["active_view"] == "views/doc.html"
    assert page["canonical_url"].endswith("/guide/")
    assert page["markdown_url"].endswith("/guide.md")
    assert page["llms_url"] == "/llms.txt"
    assert page["rendering_head"].id == "live-shell"
    assert page["html_lang"] == "en"
    assert shell["node"] is None
    assert shell["page_count"] == 2
    assert shell["llms_url"] == "/llms.txt"
    assert shell["html_lang"] == "en"
