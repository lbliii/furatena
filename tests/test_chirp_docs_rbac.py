"""RBAC model tests for mounts and catalog pages."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.access import (
    AccessPermission,
    AccessPolicy,
    AccessRole,
    AccessSubject,
    can_access,
    required_role_for,
)
from furatena.catalog.registry import CatalogRegistry
from tests.support import write_mounts_yaml


def test_access_policy_covers_public_private_team_and_admin_surfaces() -> None:
    anonymous = AccessSubject.anonymous()
    reader = AccessSubject.from_values(actor="reader", roles=["reader"])
    docs_reader = AccessSubject.from_values(actor="docs", roles=["reader"], teams=["docs"])
    publisher = AccessSubject.from_values(actor="publisher", roles=["publisher"])
    admin = AccessSubject.from_values(actor="admin", roles=["admin"])

    assert can_access(AccessPolicy(visibility="public"), anonymous, permission=AccessPermission.READ)
    assert not can_access(AccessPolicy(visibility="private"), anonymous)
    assert can_access(AccessPolicy(visibility="private"), reader)

    team_policy = AccessPolicy(visibility="internal", teams=frozenset({"docs"}))
    assert not can_access(team_policy, reader)
    assert can_access(team_policy, docs_reader)

    admin_policy = AccessPolicy.from_mapping({"access": {"admin_only": True}})
    assert not can_access(admin_policy, publisher, permission=AccessPermission.ADMINISTER)
    assert can_access(admin_policy, admin, permission=AccessPermission.ADMINISTER)
    assert required_role_for(admin_policy, AccessPermission.ADMINISTER) == AccessRole.ADMIN


def test_registry_evaluates_mount_and_page_access(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    content = app_root / "content"
    docs = content / "docs"
    docs.mkdir(parents=True)
    app_root.mkdir(exist_ok=True)
    (content / "_index.md").write_text("---\ntitle: Home\n---\n# Home\n", encoding="utf-8")
    (docs / "public.md").write_text("---\ntitle: Public\n---\n# Public\n", encoding="utf-8")
    (docs / "private.md").write_text(
        "---\ntitle: Private\nvisibility: private\n---\n# Private\n",
        encoding="utf-8",
    )
    (docs / "team.md").write_text(
        "---\ntitle: Team\nvisibility: internal\naccess:\n  teams: [docs]\n---\n# Team\n",
        encoding="utf-8",
    )
    (docs / "admin.md").write_text(
        "---\ntitle: Admin\naccess:\n  admin_only: true\n---\n# Admin\n",
        encoding="utf-8",
    )
    write_mounts_yaml(app_root / "mounts.yaml", content, mount_id="docs")

    registry = CatalogRegistry.from_config(
        app_root / "mounts.yaml",
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
        include_private=True,
    )
    anonymous = AccessSubject.anonymous()
    reader = AccessSubject.from_values(actor="reader", roles=["reader"])
    docs_reader = AccessSubject.from_values(actor="docs", roles=["reader"], teams=["docs"])
    admin = AccessSubject.from_values(actor="admin", roles=["admin"])

    public = registry.get_by_slug("docs/public", mount="docs")
    private = registry.get_by_slug("docs/private", mount="docs")
    team = registry.get_by_slug("docs/team", mount="docs")
    admin_page = registry.get_by_slug("docs/admin", mount="docs")
    assert public is not None
    assert private is not None
    assert team is not None
    assert admin_page is not None

    assert registry.can_access_node(public, anonymous)
    assert not registry.can_access_node(private, anonymous)
    assert registry.can_access_node(private, reader)
    assert not registry.can_access_node(team, reader)
    assert registry.can_access_node(team, docs_reader)
    assert not registry.can_access_node(admin_page, docs_reader)
    assert registry.can_access_node(admin_page, admin, permission=AccessPermission.ADMINISTER)


def test_mount_access_restricts_every_page_in_mount(tmp_path: Path) -> None:
    app_root = tmp_path / "app"
    content = app_root / "content"
    content.mkdir(parents=True)
    app_root.mkdir(exist_ok=True)
    (content / "_index.md").write_text("---\ntitle: Home\n---\n# Home\n", encoding="utf-8")
    write_mounts_yaml(app_root / "mounts.yaml", content, mount_id="docs")
    with (app_root / "mounts.yaml").open("a", encoding="utf-8") as handle:
        handle.write("    access:\n      teams: [platform]\n")

    registry = CatalogRegistry.from_config(
        app_root / "mounts.yaml",
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
    )
    home = registry.get_by_slug("", mount="docs")
    anonymous = AccessSubject.anonymous()
    platform_reader = AccessSubject.from_values(
        actor="platform-reader",
        roles=["reader"],
        teams=["platform"],
    )
    assert home is not None
    assert not registry.can_access_mount("docs", anonymous)
    assert not registry.can_access_node(home, anonymous)
    assert registry.can_access_mount("docs", platform_reader)
    assert registry.can_access_node(home, platform_reader)
