"""Directive styling tests — chirp-theme visual intent via chirp-ui + Alpine."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.directives.kida_render import render_directive, render_doc_tabs


class TestDirectiveTranslation:
    def test_tabs_use_alpine_and_chirpui_with_theme_skin(self) -> None:
        html = render_doc_tabs(
            [("tab-a", "One", "", True), ("tab-b", "Two", "New", False)],
            ["<p>A</p>", "<p>B</p>"],
        )
        assert "chirp-theme-directive-tabs" in html
        assert "chirpui-tabs" in html
        assert "x-data=" in html
        assert "chirpDocsTabSet(" in html
        assert 'x-data="chirpDocsTabSet(' not in html or "tab-a" in html.split('x-data=')[1][:120]
        assert "x-data='chirpDocsTabSet(" in html
        assert 'chirpDocsTabSet("tab-a"' in html
        assert ':class="{ \'chirpui-tab--active\'' in html
        assert 'class="chirpui-tab chirpui-tab--active"' not in html
        assert 'class="tabs"' not in html
        assert 'data-bengal="tabs"' not in html

    def test_code_tabs_variant(self) -> None:
        html = render_doc_tabs(
            [("code-a", "Python", "", True)],
            ["<pre>print()</pre>"],
            variant="code-tabs",
        )
        assert "chirp-theme-directive-tabs--code" in html

    def test_tab_set_uses_ast_boundaries_with_nested_divs(self) -> None:
        from furatena.catalog.directives.tabs import (
            TabItemHandler,
            TabItemOptions,
            TabSetHandler,
            TabSetOptions,
        )
        from patitas.nodes import Directive
        from patitas.stringbuilder import StringBuilder

        item_one = Directive(
            location=None,  # type: ignore[arg-type]
            name="tab-item",
            title="Nested",
            options=TabItemOptions(selected=True),
            children=(),
        )
        item_two = Directive(
            location=None,  # type: ignore[arg-type]
            name="tab-item",
            title="Plain",
            options=TabItemOptions(),
            children=(),
        )
        tab_set = Directive(
            location=None,  # type: ignore[arg-type]
            name="tab-set",
            title=None,
            options=TabSetOptions(),
            children=(item_one, item_two),
        )

        item_handler = TabItemHandler()
        rendered_parts: list[str] = []
        for item, body in (
            (item_one, '<div class="nested"><div>inner</div></div>'),
            (item_two, "<p>plain</p>"),
        ):
            sb = StringBuilder()
            item_handler.render(item, body, sb)
            rendered_parts.append(sb.build())

        sb = StringBuilder()
        TabSetHandler().render(tab_set, "".join(rendered_parts), sb)
        html = sb.build()
        assert "chirp-theme-directive-tabs" in html
        assert "nested" in html
        assert "inner" in html
        assert "plain" in html
        assert "fura-tab-item" not in html

    def test_tab_set_skips_disabled_items_without_shifting_content(self) -> None:
        from furatena.catalog.directives.tabs import (
            TabItemHandler,
            TabItemOptions,
            TabSetHandler,
            TabSetOptions,
        )
        from patitas.nodes import Directive
        from patitas.stringbuilder import StringBuilder

        disabled = Directive(
            location=None,  # type: ignore[arg-type]
            name="tab-item",
            title="Skip",
            options=TabItemOptions(disabled=True),
            children=(),
        )
        active = Directive(
            location=None,  # type: ignore[arg-type]
            name="tab-item",
            title="Keep",
            options=TabItemOptions(selected=True),
            children=(),
        )
        tab_set = Directive(
            location=None,  # type: ignore[arg-type]
            name="tab-set",
            title=None,
            options=TabSetOptions(),
            children=(disabled, active),
        )

        item_handler = TabItemHandler()
        rendered_parts: list[str] = []
        for item, body in (
            (disabled, "<p>hidden</p>"),
            (active, "<p>visible</p>"),
        ):
            sb = StringBuilder()
            item_handler.render(item, body, sb)
            rendered_parts.append(sb.build())

        sb = StringBuilder()
        TabSetHandler().render(tab_set, "".join(rendered_parts), sb)
        html = sb.build()
        assert html.count("chirp-theme-directive-tabs__label") == 1
        assert "Keep" in html
        assert "visible" in html
        assert "hidden" not in html

    def test_catalog_tab_sets_render_all_tab_items(self) -> None:
        import re

        from furatena.catalog.loader import DocCatalog

        repo = REPO
        catalog = DocCatalog(repo / "content/chirp", autodoc=False, autodoc_config=None)
        tab_item_re = re.compile(r":{3,4}\{tab-item\}\s+([^\n]+)", re.MULTILINE)
        tab_label_re = re.compile(
            r'<span class="chirp-theme-directive-tabs__label">([^<]+)</span>'
        )

        offenders: list[str] = []
        for node in catalog.nodes:
            expected = tab_item_re.findall(node.body_md or "")
            if not expected:
                continue
            labels = tab_label_re.findall(node.body_html or "")
            if labels != expected:
                offenders.append(
                    f"{node.slug}: expected {expected!r}, got {labels!r}",
                )

        assert not offenders, "tab-set labels dropped or reordered:\n" + "\n".join(offenders)

    def test_code_tabs_sync_uses_stable_tab_ids(self) -> None:
        from furatena.catalog.directives.code_tabs import CodeTabsHandler, CodeTabsOptions
        from patitas.stringbuilder import StringBuilder

        handler = CodeTabsHandler()
        source = '```python\na = 1\n```\n\n```python\nb = 2\n```'
        node_a = handler.parse(
            "code-tabs",
            None,
            CodeTabsOptions(sync="flask-chirp"),
            source,
            (),
            location=None,  # type: ignore[arg-type]
        )
        node_b = handler.parse(
            "code-tabs",
            None,
            CodeTabsOptions(sync="flask-chirp"),
            source.replace("a", "x").replace("b", "y"),
            (),
            location=None,  # type: ignore[arg-type]
        )
        sb_a = StringBuilder()
        sb_b = StringBuilder()
        handler.render(node_a, "", sb_a)
        handler.render(node_b, "", sb_b)
        html_a = sb_a.build()
        html_b = sb_b.build()
        assert "code-0-flask-chirp" in html_a
        assert "code-1-flask-chirp" in html_a
        assert "code-0-flask-chirp" in html_b
        assert "code-1-flask-chirp" in html_b
        assert "code-0-python-" not in html_a

    def test_code_tabs_fence_title_labels(self) -> None:
        from furatena.catalog.directives.code_tabs import CodeTabsHandler, CodeTabsOptions
        from patitas.stringbuilder import StringBuilder

        handler = CodeTabsHandler()
        node = handler.parse(
            "code-tabs",
            None,
            CodeTabsOptions(),
            '```bash title="uv"\nuv add bengal-chirp\n```\n\n```bash title="pip"\npip install bengal-chirp\n```',
            (),
            location=None,  # type: ignore[arg-type]
        )
        sb = StringBuilder()
        handler.render(node, "", sb)
        html = sb.build()
        assert "chirp-theme-directive-tabs--code" in html
        assert 'class="rosettes"' in html
        assert "code-block-wrapper" in html
        assert "data-fura-copy-code" in html
        assert ">uv<" in html or ">uv\n" in html
        assert "Title=&quot;Uv&quot;" not in html
        assert ">pip<" in html or ">pip\n" in html

    def test_dropdown_uses_chirpui_collapse_with_theme_skin(self) -> None:
        html = render_directive(
            "accordion",
            title="More",
            body="<p>Hidden</p>",
            open=True,
            description="Extra context",
            badge="New",
            color="info",
            icon="",
            extra_class="",
        )
        assert "chirpui-collapse" in html
        assert "chirp-theme-directive-dropdown" in html
        assert "chirp-theme-directive-dropdown--info" in html
        assert "chirp-theme-directive-dropdown__description" in html
        assert 'class="dropdown"' not in html

    def test_steps_use_theme_step_structure(self) -> None:
        html = render_directive(
            "step",
            title="Install",
            body="<p>Run make install</p>",
            description="One-time setup",
            duration="2 min",
            optional=False,
            step_number=1,
            heading_level=2,
            step_id="install",
            extra_class="",
        )
        assert "step-marker" in html
        assert "step-title" in html
        assert "chirp-theme-directive-steps__step" in html

    def test_version_callout_uses_theme_directive(self) -> None:
        html = render_directive(
            "version_callout",
            kind="since",
            badge_label="Since 0.8.0",
            body="<p>New feature</p>",
        )
        assert "version-directive" in html
        assert "chirp-theme-directive-version" in html
        assert "version-badge-since" in html

    def test_related_handler_limit_and_section_title(self) -> None:
        from furatena.catalog.directives.versioning import RelatedHandler, RelatedOptions
        from patitas.stringbuilder import StringBuilder

        class _Ctx:
            current_slug = "docs/get-started/installation"
            stubs = {"docs/get-started/installation": type("S", (), {"url": "/docs/get-started/installation/"})()}
            def get_backlinks(self, url):
                return [
                    {"href": f"/docs/page-{i}/", "title": f"Page {i}"}
                    for i in range(5)
                ]

        from furatena.catalog import context as ctx_mod

        token = ctx_mod.set_render_context(_Ctx())  # type: ignore[arg-type]
        try:
            handler = RelatedHandler()
            node = handler.parse(
                "related",
                "Related",
                RelatedOptions(limit=3, section_title="Next Steps"),
                "",
                (),
                location=None,  # type: ignore[arg-type]
            )
            sb = StringBuilder()
            handler.render(node, "", sb)
            html = sb.build()
        finally:
            ctx_mod.reset_render_context(token)
        assert "Next Steps" in html
        assert html.count("/docs/page-") == 3

    def test_fenced_code_renders_highlighted_content(self) -> None:
        from furatena.catalog.context import set_render_context, reset_render_context
        from furatena.catalog.render import DocsRenderer

        class _Ctx:
            content_root = REPO / "content" / "chirp"

        renderer = DocsRenderer()
        token = set_render_context(_Ctx())  # type: ignore[arg-type]
        try:
            html = str(
                renderer.render(
                    '```python\nimport chirp\nprint(chirp.__version__)\n```',
                    _Ctx(),  # type: ignore[arg-type]
                )[0]
            )
        finally:
            reset_render_context(token)
        assert "syntax-import" in html or "import" in html
        assert "chirp" in html
        assert "<code></code>" not in html
        assert "code-block-wrapper" in html
        assert "data-fura-copy-code" in html

    def test_all_fenced_blocks_wrapped_when_code_tabs_present(self) -> None:
        from furatena.catalog.context import set_render_context, reset_render_context
        from furatena.catalog.render import DocsRenderer

        class _Ctx:
            content_root = REPO / "content" / "chirp"

        renderer = DocsRenderer()
        source = """::::{code-tabs}
```bash title="uv"
uv add pkg
```

```bash title="pip"
pip install pkg
```
::::

```python
import pkg
```
"""
        token = set_render_context(_Ctx())  # type: ignore[arg-type]
        try:
            html = str(renderer.render(source, _Ctx())[0])  # type: ignore[arg-type]
        finally:
            reset_render_context(token)
        bare = '<div class="rosettes" data-language="python"><pre>'
        assert bare not in html
        assert html.count("code-block-wrapper") >= 3
        assert html.count("data-fura-copy-code") >= 3

    def test_gterm_role_renders_glossary_lookup(self) -> None:
        from furatena.catalog.context import set_render_context, reset_render_context
        from furatena.catalog.render import DocsRenderer

        class _Ctx:
            content_root = REPO / "content" / "chirp"

        renderer = DocsRenderer()
        token = set_render_context(_Ctx())  # type: ignore[arg-type]
        try:
            html = str(renderer.render("Uses {gterm}`Hypermedia` here.", _Ctx())[0])  # type: ignore[arg-type]
        finally:
            reset_render_context(token)
        assert "chirp-theme-glossary-term" in html
        assert "Hypermedia" in html
        assert "title=" in html

    def test_gist_embed_template(self) -> None:
        html = render_directive(
            "gist",
            script_url="https://gist.github.com/user/abc.js",
            gist_url="https://gist.github.com/user/abc",
        )
        assert "chirp-theme-directive-embed--gist" in html

    def test_list_table_theme_class(self) -> None:
        html = render_directive(
            "table",
            headers=["A", "B"],
            body_rows=[["1", "2"]],
            widths=None,
            caption="Options",
            extra_class="",
        )
        assert "chirp-theme-directive-table" in html
        assert "Options" in html

    def test_glossary_handler_renders_terms(self) -> None:
        from furatena.catalog.directives.glossary import GlossaryHandler, GlossaryOptions
        from patitas.stringbuilder import StringBuilder

        class _Ctx:
            content_root = REPO / "content" / "chirp"

        from furatena.catalog import context as ctx_mod

        token = ctx_mod.set_render_context(_Ctx())  # type: ignore[arg-type]
        try:
            handler = GlossaryHandler()
            node = handler.parse(
                "glossary",
                None,
                GlossaryOptions(tags="architecture,docs", sorted=True, show_tags=True),
                "",
                (),
                location=None,  # type: ignore[arg-type]
            )
            sb = StringBuilder()
            handler.render(node, "", sb)
            html = sb.build()
        finally:
            ctx_mod.reset_render_context(token)
        assert "chirp-theme-directive-glossary" in html
        assert "Hypermedia" in html
        assert "DocCatalog" in html
        assert "chirp-theme-directive-glossary__tag" in html


class TestDirectiveManifest:
    def test_manifest_matches_registry(self) -> None:
        from furatena.catalog.directives.manifest import check_manifest_registry_alignment

        assert check_manifest_registry_alignment() == []

    def test_manifest_templates_exist(self) -> None:
        from furatena.catalog.directives.manifest import validate_directive_manifest

        errors, _warnings = validate_directive_manifest()
        assert errors == []


class TestDocsDirectivePages:
    @pytest.fixture(scope="module")
    def docs_client(self):
        from furatena.catalog.docs_app import DocsApp
        from chirp.testing import TestClient

        docs = DocsApp.from_paths(
            APP_ROOT / "docs.yaml",
            repo_root=REPO,
            autodoc=False,
        )
        return TestClient(docs.create_app())

    def test_installation_page_directives(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/get-started/installation/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-directive-tabs" in html
        assert "chirpui-tabs" in html
        assert "x-data" in html
        assert "chirp-theme-directive-dropdown" in html
        assert "/docs-theme/local/directives.css" in html
        assert "/docs-theme/local/js/docs-enhance.js" in html
        assert "code-block-wrapper" in html
        assert "data-fura-copy-code" in html
        assert 'class="rosettes"' in html

    def test_build_apps_nav_and_folder_scripts(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/build-apps/pages-navigation/routes/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-docs-nav__section--has-toggle" in html
        assert 'aria-expanded="true"' in html
        assert "/docs-theme/local/js/fura-nav.js" in html
        assert "Build Apps" in html
        assert "/docs-theme/js/enhancements/tabs.js" not in html
        assert "/docs-theme/js/enhancements/action-bar.js" not in html

    def test_architecture_page_glossary(self, docs_client) -> None:
        import asyncio

        async def _fetch() -> str:
            resp = await docs_client.get("/docs/about/architecture/")
            return resp.text

        html = asyncio.run(_fetch())
        assert "chirp-theme-directive-glossary" in html
        assert "Hypermedia" in html
        assert "chirp-page-actions-" in html
        assert "chirp-theme-glossary-term" in html
