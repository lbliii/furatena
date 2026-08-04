"""Platform/site composition and managed generation-selection contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from furatena.catalog.application_roots import ApplicationRootError, ApplicationRoots
from furatena.catalog.config import DocsConfig, ThemeConfig
from furatena.catalog.content_deployment import ContentDeploymentError, GenerationSelection
from furatena.catalog.exceptions import CatalogConfigError
from furatena.catalog.theme import DocsTheme
from furatena.cli.commands._shared import _docs_yaml
from furatena.themes.docs import ROOT as PACKAGED_DOCS_ROOT


def test_local_single_root_profile_preserves_compatible_defaults(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()

    roots = ApplicationRoots.from_environment(site, environ={})

    assert roots.site == site
    assert roots.platform == site
    assert roots.state == site / ".docs-cache"
    assert roots.output == site
    assert not roots.managed
    external = tmp_path / "shared-content"
    assert roots.require_site_path(external, label="mount") == external


def test_managed_profile_requires_disjoint_explicit_writable_roots(tmp_path: Path) -> None:
    site = tmp_path / "generations" / "one" / "source" / "app"
    platform = tmp_path / "image" / "app"
    state = tmp_path / "runtime-state"
    output = tmp_path / "runtime-output"
    site.mkdir(parents=True)
    platform.mkdir(parents=True)
    roots = ApplicationRoots.from_environment(
        site,
        environ={
            "FURA_CONTENT_REPOSITORY": "https://github.com/example/docs.git",
            "FURA_PLATFORM_ROOT": str(platform),
            "FURA_RUNTIME_STATE_ROOT": str(state),
            "FURA_OUTPUT_ROOT": str(output),
        },
    )

    roots.ensure_writable_roots()

    assert roots.managed
    assert state.is_dir()
    assert output.is_dir()
    with pytest.raises(ApplicationRootError, match="protected application namespace"):
        roots.require_site_path(site / "frozen" / "catalog.json", label="mount")
    with pytest.raises(ApplicationRootError, match="beneath the active site root"):
        roots.require_site_path(tmp_path / "outside", label="mount")


def test_managed_profile_rejects_implicit_or_cross_root_writes(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    base = {
        "FURA_CONTENT_REPOSITORY": "https://github.com/example/docs.git",
        "FURA_PLATFORM_ROOT": str(tmp_path / "platform"),
        "FURA_RUNTIME_STATE_ROOT": str(site / ".docs-cache"),
        "FURA_OUTPUT_ROOT": str(tmp_path / "output"),
    }

    with pytest.raises(ApplicationRootError, match="outside the read-only site generation"):
        ApplicationRoots.from_environment(site, environ=base)

    base.pop("FURA_RUNTIME_STATE_ROOT")
    with pytest.raises(ApplicationRootError, match="explicit immutable and writable roots"):
        ApplicationRoots.from_environment(site, environ=base)


def test_config_must_be_directly_beneath_selected_app_root(tmp_path: Path) -> None:
    app = tmp_path / "app"
    other = tmp_path / "other"
    app.mkdir()
    other.mkdir()
    args = argparse.Namespace(app_root=str(app), config=str(other / "docs.yaml"))

    with pytest.raises(CatalogConfigError, match=r"--config must name docs\.yaml directly beneath"):
        _docs_yaml(args)


def test_platform_layouts_follow_site_theme_roots(tmp_path: Path) -> None:
    site = tmp_path / "generation" / "site"
    platform = tmp_path / "image" / "app"
    state = tmp_path / "state"
    (site / "theme").mkdir(parents=True)
    (platform / "theme").mkdir(parents=True)
    docs = DocsConfig(root=site, theme=ThemeConfig(use="lagoon"))

    theme = DocsTheme.from_docs_config(
        docs,
        platform_root=platform,
        state_root=state,
    )

    assert theme.template_roots == (
        site / "theme",
        PACKAGED_DOCS_ROOT / "templates",
        PACKAGED_DOCS_ROOT,
        platform / "theme",
    )


def test_generation_selection_binds_all_roots_to_one_receipt(tmp_path: Path) -> None:
    generation = tmp_path / "generation-one"
    site = generation / "source" / "app"
    frozen = generation / "frozen"
    site.mkdir(parents=True)
    frozen.mkdir()
    (site / "docs.yaml").write_text("site: {name: Docs}\n", encoding="utf-8")
    (frozen / "catalog.json").write_text("{}\n", encoding="utf-8")
    (generation / "receipt.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generation": generation.name,
                "selection": {
                    "checkout": "source",
                    "site": "source/app",
                    "frozen": "frozen",
                    "receipt": "receipt.json",
                },
            }
        ),
        encoding="utf-8",
    )

    selection = GenerationSelection.from_generation(generation)

    selection.require_runtime(site_root=site, frozen_root=frozen)
    with pytest.raises(ContentDeploymentError, match="do not match"):
        selection.require_runtime(site_root=site, frozen_root=tmp_path / "other")


def test_generation_selection_rejects_symlink_escape(tmp_path: Path) -> None:
    generation = tmp_path / "generation-one"
    generation.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (generation / "source").symlink_to(outside, target_is_directory=True)
    (generation / "receipt.json").write_text(
        json.dumps(
            {
                "generation": generation.name,
                "selection": {
                    "checkout": "source",
                    "site": "source/app",
                    "frozen": "frozen",
                    "receipt": "receipt.json",
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ContentDeploymentError, match="escapes its generation"):
        GenerationSelection.from_generation(generation)
