"""Versioned presentation-pack manifest and resolution contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from furatena.catalog.application_roots import ApplicationRoots
from furatena.catalog.config import (
    DeliveryConfig,
    DeliveryMountConfig,
    DeliveryThemeConfig,
    DocsConfig,
    PresentationConfig,
    ThemeConfig,
    load_docs_config,
)
from furatena.catalog.delivery import check_delivery_config
from furatena.catalog.deployment_manifest import read_deployment_manifest
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.freeze_incremental import write_freeze_manifest
from furatena.catalog.presentation_pack import (
    PRESENTATION_MANIFEST_NAME,
    PresentationPackError,
    discover_presentation_packs,
    load_presentation_manifest,
    resolve_presentation,
)
from furatena.catalog.renderer_fingerprint import renderer_fingerprint
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from furatena.catalog.theme import DocsTheme
from furatena.catalog.view_kinds import VIEW_KINDS
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "furatena"
    / "catalog"
    / "schemas"
    / "presentation-pack-v1.schema.json"
)


def _manifest(
    root: Path,
    *,
    identity: str,
    kind: str,
    templates: dict[str, str] | None = None,
    scripts: bool = False,
) -> Path:
    root.mkdir(parents=True)
    declared_templates = templates or {}
    for relative in declared_templates.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{% block page_root %}<div id="page-root"></div>{% end %}\n',
            encoding="utf-8",
        )
    assets: list[dict[str, str]] = []
    capabilities: list[str] = []
    requires_trust: list[str] = []
    if kind == "skin":
        for role, relative in (
            ("tokens", "tokens.css"),
            ("styles", "styles.css"),
            ("directives", "directives.css"),
        ):
            (root / relative).write_text(f"/* {role} */\n", encoding="utf-8")
            assets.append({"role": role, "path": relative})
        scripts_dir = root / "js"
        scripts_dir.mkdir()
        if scripts:
            (scripts_dir / "enhance.js").write_text("export {};\n", encoding="utf-8")
        assets.append({"role": "scripts", "path": "js"})
        capabilities.extend(("styles", "scripts"))
        requires_trust.append("scripts")
    if declared_templates:
        capabilities.append("templates")
    payload = {
        "schema_version": 1,
        "id": identity,
        "version": "1.2.3",
        "type": kind,
        "render_context_api_version": "1",
        "runtime": ">=0.1.0,<0.2.0",
        "templates": declared_templates,
        "assets": assets,
        "capabilities": capabilities,
        "requires_trust": requires_trust,
    }
    if kind == "layout":
        payload["contract"] = {
            "render_modes": ["full", "fragment"],
            "slots": ["head", "scripts", "shell", "content"],
            "semantic_hooks": ["main", "page-root"],
            "component_api": "chirp-ui@0.11",
        }
    path = root / PRESENTATION_MANIFEST_NAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _layout_templates() -> dict[str, str]:
    return {spec.kind: spec.default_template for spec in VIEW_KINDS}


def _roots(site: Path, tmp_path: Path, *, managed: bool = False) -> ApplicationRoots:
    return ApplicationRoots(
        site=site.resolve(),
        platform=(tmp_path / "platform").resolve(),
        state=(tmp_path / "state").resolve(),
        output=(tmp_path / "output").resolve(),
        managed=managed,
    )


def test_manifest_schema_is_valid_and_layout_owns_every_view(tmp_path: Path) -> None:
    Draft202012Validator.check_schema(json.loads(SCHEMA.read_text(encoding="utf-8")))
    path = _manifest(
        tmp_path / "layout",
        identity="complete-layout",
        kind="layout",
        templates=_layout_templates(),
    )

    pack = load_presentation_manifest(path, source="local")

    assert pack.kind == "layout"
    assert {name for name, _path in pack.templates} == {spec.kind for spec in VIEW_KINDS}
    assert len(pack.content_digest) == 64


def test_docs_config_parses_site_global_presentation_selection(tmp_path: Path) -> None:
    docs_yaml = tmp_path / "docs.yaml"
    docs_yaml.write_text(
        """
presentation:
  layout: product-layout
  skin: product-skin
  overrides: [first, second]
  trusted_capabilities: [scripts]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_docs_config(docs_yaml)

    assert config.presentation == PresentationConfig(
        layout="product-layout",
        skin="product-skin",
        overrides=("first", "second"),
        trusted_capabilities=frozenset({"scripts"}),
    )


def test_incomplete_layout_fails_with_view_kind_diagnostic(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path / "layout",
        identity="incomplete-layout",
        kind="layout",
        templates={"doc": "views/doc.html"},
    )

    with pytest.raises(PresentationPackError, match="must own every VIEW_KINDS"):
        load_presentation_manifest(path, source="local")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("schema_version", 2, "schema_version"),
        ("version", "1", "version"),
        ("render_context_api_version", "2", "render-context API"),
        ("runtime", ">=99.0.0", "requires Furatena"),
    ),
)
def test_manifest_version_axes_fail_independently(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    path = _manifest(
        tmp_path / field,
        identity="versioned-layout",
        kind="layout",
        templates=_layout_templates(),
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PresentationPackError, match=message):
        load_presentation_manifest(path, source="local")


def test_manifest_rejects_unsafe_and_symlink_asset_paths(tmp_path: Path) -> None:
    path = _manifest(
        tmp_path / "skin",
        identity="unsafe-skin",
        kind="skin",
        scripts=True,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["assets"][0]["path"] = "../tokens.css"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PresentationPackError, match="manifest assets"):
        load_presentation_manifest(path, source="local")

    payload["assets"][0]["path"] = "tokens-link.css"
    path.write_text(json.dumps(payload), encoding="utf-8")
    (path.parent / "tokens-link.css").symlink_to(path.parent / "tokens.css")
    with pytest.raises(PresentationPackError, match="symbolic link"):
        load_presentation_manifest(path, source="local")


def test_discovery_rejects_duplicate_identity(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _manifest(
        site / "presentation" / "one",
        identity="duplicate-layout",
        kind="layout",
        templates=_layout_templates(),
    )
    _manifest(
        site / "presentation" / "two",
        identity="duplicate-layout",
        kind="layout",
        templates=_layout_templates(),
    )
    docs = DocsConfig(root=site, theme=ThemeConfig(use=None))

    with pytest.raises(PresentationPackError, match="duplicate presentation pack identity"):
        discover_presentation_packs(docs, roots=_roots(site, tmp_path))


def test_strict_scripts_require_explicit_trust(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _manifest(
        site / "presentation" / "layout",
        identity="site-layout",
        kind="layout",
        templates=_layout_templates(),
    )
    _manifest(
        site / "presentation" / "skin",
        identity="site-skin",
        kind="skin",
        scripts=True,
    )
    docs = DocsConfig(
        root=site,
        theme=ThemeConfig(use="lagoon"),
        presentation=PresentationConfig(layout="site-layout", skin="site-skin"),
    )

    with pytest.raises(PresentationPackError, match="explicit trust"):
        resolve_presentation(docs, roots=_roots(site, tmp_path))

    trusted = DocsConfig(
        root=site,
        theme=ThemeConfig(use=None),
        presentation=PresentationConfig(
            layout="site-layout",
            skin="site-skin",
            trusted_capabilities=frozenset({"scripts"}),
        ),
    )
    resolved = resolve_presentation(trusted, roots=_roots(site, tmp_path))
    assert resolved.record.skin is not None
    assert resolved.record.trusted_capabilities == frozenset({"scripts"})


def test_ordered_sparse_overrides_precede_complete_layout(tmp_path: Path) -> None:
    site = tmp_path / "site"
    _manifest(
        site / "presentation" / "layout",
        identity="site-layout",
        kind="layout",
        templates=_layout_templates(),
    )
    first = _manifest(
        site / "presentation" / "first",
        identity="first-override",
        kind="override",
        templates={"doc": "views/doc.html"},
    ).parent
    second = _manifest(
        site / "presentation" / "second",
        identity="second-override",
        kind="override",
        templates={"home": "views/home.html"},
    ).parent
    docs = DocsConfig(
        root=site,
        theme=ThemeConfig(use="lagoon"),
        presentation=PresentationConfig(
            layout="site-layout",
            overrides=("first-override", "second-override"),
        ),
    )

    resolved = resolve_presentation(docs, roots=_roots(site, tmp_path))
    theme = DocsTheme.from_docs_config(docs, state_root=tmp_path / "cache")

    assert resolved.template_roots[:2] == (first.resolve(), second.resolve())
    assert theme.template_roots[:2] == resolved.template_roots[:2]
    assert theme.presentation.content_digest == resolved.record.content_digest


def test_managed_discovery_rejects_symlinked_local_pack(tmp_path: Path) -> None:
    site = tmp_path / "site"
    outside = tmp_path / "outside"
    _manifest(
        outside,
        identity="outside-layout",
        kind="layout",
        templates=_layout_templates(),
    )
    (site / "presentation").mkdir(parents=True)
    (site / "presentation" / "linked").symlink_to(outside, target_is_directory=True)
    docs = DocsConfig(root=site, theme=ThemeConfig(use=None))

    with pytest.raises(PresentationPackError, match="symbolic link"):
        discover_presentation_packs(docs, roots=_roots(site, tmp_path, managed=True))


def test_legacy_theme_selection_uses_packaged_docs_with_path_free_record(tmp_path: Path) -> None:
    site = tmp_path / "site"
    docs = DocsConfig(root=site, theme=ThemeConfig(id="furatena", use="lagoon"))

    record = resolve_presentation(docs, roots=_roots(site, tmp_path)).record.to_dict()

    assert record["layout"]["id"] == "docs"
    assert record["layout"]["source"] == "packaged"
    assert record["skin"]["source"] == "compatibility"
    assert "root" not in json.dumps(record)
    assert record["content_digest"]


def test_freeze_manifest_and_renderer_fingerprint_bind_path_free_presentation(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    docs = DocsConfig(root=site, theme=ThemeConfig(id="furatena", use=None))
    record = resolve_presentation(docs, roots=_roots(site, tmp_path)).record.to_dict()
    frozen = tmp_path / "frozen"
    frozen.mkdir()

    write_freeze_manifest(
        frozen,
        dirty_mounts=[],
        total_pages=0,
        presentation=record,
    )

    manifest = read_deployment_manifest(frozen / "freeze.manifest.json", target_hint="freeze")
    assert manifest is not None
    assert manifest.extensions["presentation"] == record
    assert str(tmp_path) not in json.dumps(manifest.extensions["presentation"])
    assert renderer_fingerprint(site, presentation_digest="a" * 64) != renderer_fingerprint(
        site,
        presentation_digest="b" * 64,
    )


def test_differing_per_mount_presentation_has_migration_diagnostic(tmp_path: Path) -> None:
    docs = DocsConfig(
        root=tmp_path,
        theme=ThemeConfig(id="furatena", use="lagoon"),
        delivery=DeliveryConfig(
            mounts={
                "api": DeliveryMountConfig(theme=DeliveryThemeConfig(id="chirp", use="other-skin"))
            }
        ),
    )

    errors, _warnings = check_delivery_config(docs)

    assert any("requires one site-global presentation" in error for error in errors)
    assert any("publish this mount as a separate site" in error for error in errors)


def test_static_manifest_and_html_retain_path_free_presentation_provenance(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    app_root = tmp_path / "app"
    content = app_root / "content"
    content.mkdir(parents=True)
    (content / "_index.md").write_text("---\ntitle: Home\n---\n# Home\n", encoding="utf-8")
    copy_app_theme(app_root, repo / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(app_root / "mounts.yaml", content)
    docs = DocsApp.from_paths(app_root / "docs.yaml", repo_root=tmp_path, autodoc=False)
    output = tmp_path / "public"

    export_static_site(
        docs,
        StaticExportOptions(
            output_dir=output,
            include_index_txt=False,
            include_portal=False,
            include_search=False,
        ),
    )

    manifest = read_deployment_manifest(output / "export.manifest.json", target_hint="static")
    assert manifest is not None
    presentation = manifest.extensions["presentation"]
    assert presentation["content_digest"] == docs.theme.presentation.content_digest
    assert "root" not in json.dumps(presentation)
    html = (output / "index.html").read_text(encoding="utf-8")
    assert 'name="furatena:presentation-digest"' in html
    assert f'data-fura-presentation="{docs.theme.presentation.content_digest}"' in html
    assert str(tmp_path) not in html
