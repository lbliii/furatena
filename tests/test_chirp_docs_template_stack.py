"""Template loader stack — project, theme, framework precedence."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_app import DocsApp
from chirp.templating.integration import create_environment


@pytest.fixture(scope="module")
def docs_config():
    return load_docs_config(APP_ROOT / "docs.yaml")


def _app_env(docs_yaml: Path | None = None) -> tuple:
    docs = DocsApp.from_paths(docs_yaml or APP_ROOT / "docs.yaml", repo_root=REPO, autodoc=False)
    app = docs.create_app()
    env = create_environment(
        app.config,
        app._mutable_state.template_filters,
        app._mutable_state.template_globals,
    )
    return app, env


class TestTemplateStack:
    def test_framework_defaults_resolve(self) -> None:
        _, env = _app_env()
        assert env.get_template("partials/head_meta.html")
        assert env.get_template("directives/child_cards.html")
        assert env.get_template("views/doc.html")

    def test_theme_view_wins_over_framework(self) -> None:
        _, env = _app_env()
        _source, filename = env.loader.get_source("views/doc.html")
        assert filename is not None
        assert "theme/views/doc.html" in filename.replace("\\", "/")

    def test_project_override_wins(self, tmp_path) -> None:
        shutil.copytree(APP_ROOT / "theme", tmp_path / "theme")
        shutil.copy(APP_ROOT / "docs.yaml", tmp_path / "docs.yaml")
        shutil.copy(APP_ROOT / "mounts.yaml", tmp_path / "mounts.yaml")
        override_dir = tmp_path / "templates" / "partials"
        override_dir.mkdir(parents=True)
        marker = "{# project override #}"
        (override_dir / "head_meta.html").write_text(marker, encoding="utf-8")

        _, env = _app_env(tmp_path / "docs.yaml")
        source, filename = env.loader.get_source("partials/head_meta.html")
        assert marker in source
        assert str(tmp_path / "templates") in filename

    def test_theme_templates_configured(self, docs_config) -> None:
        assert docs_config.theme.templates == "theme/templates"

    def test_theme_templates_shadow_wins_over_framework(self, tmp_path) -> None:
        shutil.copytree(APP_ROOT / "theme", tmp_path / "theme")
        shutil.copy(APP_ROOT / "docs.yaml", tmp_path / "docs.yaml")
        shutil.copy(APP_ROOT / "mounts.yaml", tmp_path / "mounts.yaml")
        shadow_dir = tmp_path / "theme" / "templates" / "directives"
        shadow_dir.mkdir(parents=True)
        marker = "{# theme shadow override #}"
        (shadow_dir / "child_cards.html").write_text(marker, encoding="utf-8")

        _, env = _app_env(tmp_path / "docs.yaml")
        source, filename = env.loader.get_source("directives/child_cards.html")
        assert marker in source
        assert "theme/templates" in filename.replace("\\", "/")

    def test_framework_dir_is_catalog_builtin(self, docs_config) -> None:
        fw = docs_config.framework_templates_dir
        assert fw.is_dir()
        assert (fw / "partials" / "head_meta.html").is_file()
