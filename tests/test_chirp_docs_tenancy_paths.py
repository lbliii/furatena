"""Tenant-aware routing and artifact path tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from chirp.testing import TestClient

from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.freeze import FreezeCatalogOptions, freeze_catalog
from furatena.catalog.identity import (
    identity_namespace_parts,
    identity_route_prefix,
    scope_url,
    scoped_frozen_dir,
    strip_identity_route,
)
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.static_export import StaticExportOptions, export_static_site
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml


def _write_tenant_app(
    root: Path,
    *,
    tenant: str,
    workspace: str,
    site: str,
    title: str,
) -> Path:
    app_root = root / site
    content = app_root / "content"
    docs = content / "docs"
    docs.mkdir(parents=True)
    app_root.mkdir(exist_ok=True)
    copy_app_theme(app_root, APP_ROOT)
    write_minimal_docs_yaml(app_root / "docs.yaml")
    with (app_root / "docs.yaml").open("a", encoding="utf-8") as handle:
        handle.write(
            "\nidentity:\n"
            f"  tenant: {tenant}\n"
            f"  workspace: {workspace}\n"
            f"  site: {site}\n"
        )
    write_mounts_yaml(app_root / "mounts.yaml", content, mount_id="docs")
    (content / "_index.md").write_text(
        f"---\ntitle: {title} Home\n---\n\n# {title} Home\n",
        encoding="utf-8",
    )
    (docs / "guide.md").write_text(
        f"---\ntitle: {title} Guide\n---\n\n# {title} Guide\n\nBody.\n",
        encoding="utf-8",
    )
    return app_root


def test_identity_paths_are_safe_and_reversible() -> None:
    identity = {
        "tenant": "Acme, Inc.",
        "workspace": "Platform Docs",
        "site": "../Developer Docs",
    }

    assert identity_namespace_parts(identity) == (
        "tenants",
        "acme-inc",
        "workspaces",
        "platform-docs",
        "sites",
        "developer-docs",
    )
    assert identity_route_prefix(identity) == (
        "/tenants/acme-inc/workspaces/platform-docs/sites/developer-docs"
    )
    assert scope_url("/docs/guide/", identity).endswith("/docs/guide/")
    assert strip_identity_route(scope_url("/docs/guide/", identity), identity) == "/docs/guide/"


def test_freeze_writes_non_default_identity_under_scoped_root(tmp_path: Path) -> None:
    app_root = _write_tenant_app(
        tmp_path,
        tenant="Acme Inc",
        workspace="Platform",
        site="Developer Docs",
        title="Acme",
    )
    config = load_docs_config(app_root / "docs.yaml")
    frozen = app_root / "frozen"

    result = freeze_catalog(
        FreezeCatalogOptions(
            docs_config=app_root / "docs.yaml",
            app_root=app_root,
            repo_root=tmp_path,
            output_dir=frozen,
            autodoc=False,
        )
    )

    scoped = scoped_frozen_dir(frozen, config.identity.to_meta())
    assert result.output_dir == scoped.resolve()
    assert (scoped / "mounts" / "docs" / "catalog.json").is_file()
    assert (scoped / "search.json").is_file()
    assert (scoped / "semantic.json").is_file()
    assert not (frozen / "mounts" / "docs" / "catalog.json").exists()


def test_tenant_route_prefix_allows_same_mount_and_slug_in_two_tenants(tmp_path: Path) -> None:
    acme_root = _write_tenant_app(
        tmp_path / "acme",
        tenant="acme",
        workspace="platform",
        site="docs",
        title="Acme",
    )
    beta_root = _write_tenant_app(
        tmp_path / "beta",
        tenant="beta",
        workspace="platform",
        site="docs",
        title="Beta",
    )

    acme = DocsApp.from_paths(
        acme_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    beta = DocsApp.from_paths(
        beta_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )

    assert acme.catalog.get_by_slug("docs/guide", mount="docs").title == "Acme Guide"
    assert beta.catalog.get_by_slug("docs/guide", mount="docs").title == "Beta Guide"
    assert acme.catalog.scoped_url("/docs/guide/") != beta.catalog.scoped_url("/docs/guide/")

    async def _fetch_title(docs: DocsApp, expected: str) -> None:
        client = TestClient(docs.create_app())
        async with client:
            response = await client.get(docs.catalog.scoped_url("/docs/guide/"))
        assert response.status == 200
        assert expected in response.text

    asyncio.run(_fetch_title(acme, "Acme Guide"))
    asyncio.run(_fetch_title(beta, "Beta Guide"))


def test_static_export_writes_catalog_pages_under_tenant_route(tmp_path: Path) -> None:
    app_root = _write_tenant_app(
        tmp_path,
        tenant="acme",
        workspace="platform",
        site="docs",
        title="Acme",
    )
    frozen = app_root / "frozen"
    freeze_catalog(
        FreezeCatalogOptions(
            docs_config=app_root / "docs.yaml",
            app_root=app_root,
            repo_root=tmp_path,
            output_dir=frozen,
            autodoc=False,
        )
    )
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, frozen, True, False),
    )
    out = tmp_path / "public"

    export_static_site(
        docs,
        StaticExportOptions(
            output_dir=out,
            base_path="",
            site_url="http://127.0.0.1:8080",
            include_index_txt=True,
            include_portal=False,
            include_search=False,
            frozen_dir=frozen,
        ),
    )

    assert (
        out
        / "tenants"
        / "acme"
        / "workspaces"
        / "platform"
        / "sites"
        / "docs"
        / "docs"
        / "guide"
        / "index.html"
    ).is_file()
    assert (out / "catalog.json").is_file()
