"""Typed Kida component seams and compatibility contracts."""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from chirp.templating.integration import create_environment
from kida.analysis.analyzer import BlockAnalyzer

from furatena.catalog.directives.kida_render import (
    _environment as directive_environment,
)
from furatena.catalog.directives.kida_render import (
    clear_directive_cache,
    render_directive,
)
from furatena.catalog.docs_app import DocsApp
from tests.support import write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


def _static_call_issues(env, template_name: str) -> tuple[list[object], list[object]]:
    template = env.get_template(template_name)
    ast = template._optimized_ast
    assert ast is not None
    imported = env._collect_imported_def_metadata(ast, template_name)
    analyzer = BlockAnalyzer()
    return (
        list(analyzer.validate_calls_with_external_defs(ast, imported)),
        list(analyzer.validate_call_types_with_external_defs(ast, imported)),
    )


def _app_environment(docs_yaml: Path):
    docs = DocsApp.from_paths(docs_yaml, repo_root=REPO, autodoc=False)
    app = docs.create_app()
    return create_environment(
        app.config,
        app._mutable_state.template_filters,
        app._mutable_state.template_globals,
    )


def test_directive_components_expose_typed_props_and_named_content_slots() -> None:
    clear_directive_cache()
    env = directive_environment()

    callout = env.get_template("components/directive_callout.html").def_metadata()[
        "directive_callout"
    ]
    assert {param.name: param.annotation for param in callout.params} == {
        "title": "str | None",
        "variant": "str",
        "kind": "str",
        "modifier": "str",
        "extra_class": "str",
    }
    assert callout.slots == ("content",)

    notice = env.get_template("components/directive_version_notice.html").def_metadata()[
        "directive_version_notice"
    ]
    assert {param.name: param.annotation for param in notice.params} == {
        "kind": "str",
        "badge_label": "str",
        "has_content": "bool",
    }
    assert notice.slots == ("content",)


def test_selected_directive_adapters_pass_external_static_call_validation() -> None:
    clear_directive_cache()
    env = directive_environment()
    for template_name in (
        "components/directive_callout.html",
        "components/directive_version_notice.html",
        "directives/callout.html",
        "directives/related.html",
        "directives/version_callout.html",
    ):
        signature_issues, type_issues = _static_call_issues(env, template_name)
        assert signature_issues == [], template_name
        assert type_issues == [], template_name


def test_directive_component_adapters_preserve_rendered_contracts() -> None:
    clear_directive_cache()
    callout = render_directive(
        "callout",
        admonition_name="warning",
        variant="warning",
        title="Careful",
        extra_class="product-warning",
        body="<p>Trusted renderer body</p>",
    )
    assert "chirp-theme-directive-admonition--warning" in callout
    assert "product-warning" in callout
    assert "<p>Trusted renderer body</p>" in callout

    related = render_directive(
        "related",
        title="Next steps",
        extra_class="",
        body="",
        links=[{"href": "/docs/next/", "title": "Next"}],
    )
    assert "chirp-theme-directive-related" in related
    assert 'href="/docs/next/"' in related

    with_body = render_directive(
        "version_callout",
        kind="since",
        badge_label="Since 0.9",
        body="<p>New behavior</p>",
    )
    without_body = render_directive(
        "version_callout",
        kind="changed",
        badge_label="Changed in 0.9",
        body="",
    )
    assert "version-directive-content" in with_body
    assert "<p>New behavior</p>" in with_body
    assert "version-directive-content" not in without_body


def test_app_shell_components_are_typed_and_calls_validate() -> None:
    env = _app_environment(APP_ROOT / "docs.yaml")
    metadata = env.get_template("partials/shell_nav_mega.html").def_metadata()
    assert {param.name: param.annotation for param in metadata["shell_mega_item"].params} == {
        "href": "str",
        "title": "str",
        "description": "str",
        "icon": "str",
    }
    assert metadata["shell_nav_dropdown"].has_default_slot is True
    assert metadata["shell_nav_dropdown"].params[-1].annotation == "bool"

    for template_name in (
        "partials/shell_nav_mega.html",
        "partials/docs_shell_nav.html",
        "views/home.html",
        "views/develop.html",
    ):
        signature_issues, type_issues = _static_call_issues(env, template_name)
        assert signature_issues == [], template_name
        assert type_issues == [], template_name


def test_marketing_components_are_typed_and_home_calls_validate() -> None:
    env = _app_environment(APP_ROOT / "docs.yaml")
    component_source = (
        APP_ROOT / "theme" / "templates" / "components" / "marketing.html"
    ).read_text(encoding="utf-8")
    assert "chirpui-" not in component_source
    metadata = env.get_template("components/marketing.html").def_metadata()
    assert {param.name: param.annotation for param in metadata["marketing_container"].params} == {
        "max_width": "str",
        "extra_class": "str",
    }
    assert metadata["marketing_container"].has_default_slot is True
    assert {param.name: param.annotation for param in metadata["marketing_button"].params} == {
        "label": "str",
        "href": "str",
        "variant": "str",
    }
    assert metadata["marketing_surface"].has_default_slot is True
    assert {param.name: param.annotation for param in metadata["marketing_page_hero"].params} == {
        "eyebrow": "str",
        "title": "str",
        "description": "str",
    }

    for template_name in (
        "components/marketing.html",
        "views/home.html",
        "views/marketing_page.html",
    ):
        signature_issues, type_issues = _static_call_issues(env, template_name)
        assert signature_issues == [], template_name
        assert type_issues == [], template_name


def test_theme_shadow_can_replace_framework_component_without_wrapper_fork(tmp_path: Path) -> None:
    shutil.copytree(APP_ROOT / "theme", tmp_path / "theme")
    write_minimal_docs_yaml(tmp_path / "docs.yaml")
    content = tmp_path / "content"
    content.mkdir()
    write_mounts_yaml(tmp_path / "mounts.yaml", content)
    component = tmp_path / "theme" / "templates" / "components" / "directive_callout.html"
    component.parent.mkdir(parents=True, exist_ok=True)
    component.write_text(
        """{% def directive_callout(title: str | None = none, variant: str = \"info\", kind: str = \"admonition\", modifier: str = \"\", extra_class: str = \"\") %}
<aside data-shadow-component=\"{{ kind }}\">{% slot content %}</aside>
{% end %}
""",
        encoding="utf-8",
    )

    env = _app_environment(tmp_path / "docs.yaml")
    rendered = env.get_template("directives/callout.html").render(
        title="Shadowed",
        variant="info",
        admonition_name="note",
        extra_class="",
        body="<p>Body</p>",
    )
    assert 'data-shadow-component="admonition"' in rendered
    assert "<p>Body</p>" in rendered


def test_typed_components_are_present_in_installed_package_data() -> None:
    templates = resources.files("furatena.catalog").joinpath("_templates", "components")
    assert templates.joinpath("directive_callout.html").is_file()
    assert templates.joinpath("directive_version_notice.html").is_file()
