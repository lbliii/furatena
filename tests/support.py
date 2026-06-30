"""Shared test helpers for constructing small Furatena apps."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def write_minimal_docs_yaml(path: Path, *, i18n: bool = False) -> None:
    """Write a small docs.yaml suitable for temporary test apps.

    The production app config references product-specific data files. Tests that
    only need app wiring should use this helper so they declare their fixture
    dependencies explicitly.
    """
    i18n_block = ""
    if i18n:
        i18n_block = """
i18n:
  default_language: en
  languages:
    - { code: en, name: English }
    - { code: es, name: Espanol }
"""
    path.write_text(
        f"""
shell: shell.html
views:
  doc: views/doc.html
  doc_list: views/doc_list.html
  api_reference: views/api_reference.html
  home: views/home.html
  default: views/doc.html
theme:
  use: lagoon
  id: furatena
  templates: theme/templates
mounts: mounts.yaml
{i18n_block}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def write_mounts_yaml(
    path: Path,
    content_root: Path,
    *,
    mount_id: str = "chirp",
    label: str = "Test",
    url_prefix: str = "",
) -> None:
    rel = os.path.relpath(content_root, path.parent)
    prefix_line = f"    url_prefix: {url_prefix}\n" if url_prefix else ""
    path.write_text(
        f"""
mounts:
  - id: {mount_id}
    label: {label}
    content_root: {Path(rel).as_posix()}
{prefix_line}    default: true
""".strip()
        + "\n",
        encoding="utf-8",
    )


def copy_app_theme(app_root: Path, source_app_root: Path) -> None:
    theme_src = source_app_root / "theme"
    if theme_src.is_dir():
        shutil.copytree(theme_src, app_root / "theme")
