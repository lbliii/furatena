"""RBAC model tests for mounts and catalog pages."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.access import (
    AccessEvaluationService,
    AccessPermission,
    AccessPolicy,
    AccessRole,
    AccessSubject,
    can_access,
    required_role_for,
)
from furatena.catalog.export import catalog_graph, search_json
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.search_experience import build_search_catalog_snapshot
from furatena.catalog.static_export import StaticExportOptions, _collect_routes
from furatena.catalog.structure_index import build_structure_index
from tests.support import write_mounts_yaml


def _write_permission_output_fixture(tmp_path: Path, *, include_private: bool = True) -> CatalogRegistry:
    app_root = tmp_path / "app"
    public_content = app_root / "content" / "public"
    team_content = app_root / "content" / "team"
    public_content.mkdir(parents=True)
    team_content.mkdir(parents=True)
    app_root.mkdir(exist_ok=True)
    (public_content / "_index.md").write_text("---\ntitle: Home\n---\n# Home\n", encoding="utf-8")
    (public_content / "public.md").write_text(
        "---\ntitle: Public\n---\n# Public\n\nVisible body.\n",
        encoding="utf-8",
    )
    (public_content / "private.md").write_text(
        "---\ntitle: Private\nvisibility: private\n---\n# Private\n\nHidden body.\n",
        encoding="utf-8",
    )
    (team_content / "_index.md").write_text(
        "---\ntitle: Team Home\n---\n# Team Home\n",
        encoding="utf-8",
    )
    (team_content / "runbook.md").write_text(
        "---\ntitle: Team Runbook\n---\n# Team Runbook\n\nTeam-only body.\n",
        encoding="utf-8",
    )
    (app_root / "mounts.yaml").write_text(
        f"""
mounts:
  - id: docs
    label: Docs
    content_root: {public_content.relative_to(app_root).as_posix()}
    default: true
  - id: team
    label: Team
    content_root: {team_content.relative_to(app_root).as_posix()}
    url_prefix: /team
    access:
      teams: [platform]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return CatalogRegistry.from_config(
        app_root / "mounts.yaml",
        repo_root=tmp_path,
        app_root=app_root,
        autodoc=False,
        include_private=include_private,
    )


def test_access_policy_covers_public_private_team_and_admin_surfaces() -> None:
    anonymous = AccessSubject.anonymous()
    reader = AccessSubject.from_values(actor="reader", roles=["reader"])
    docs_reader = AccessSubject.from_values(actor="docs", roles=["reader"], teams=["docs"])
    publisher = AccessSubject.from_values(actor="publisher", roles=["publisher"])
    admin = AccessSubject.from_values(actor="admin", roles=["admin"])

    assert can_access(AccessPolicy(visibility="public"), anonymous, permission=AccessPermission.READ)
    assert not can_access(AccessPolicy(visibility="private"), anonymous)
    assert can_access(AccessPolicy(visibility="private"), reader)
    assert not can_access(AccessPolicy.from_page_meta({"draft": True}), anonymous)
    assert can_access(AccessPolicy.from_page_meta({"draft": True}), publisher)

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

    service = AccessEvaluationService()
    assert service.node_decision(registry, public, anonymous).allowed
    assert not service.node_decision(registry, private, anonymous).allowed
    assert service.filter_nodes(registry, [public, private], subject=anonymous) == [public]


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


def test_public_outputs_filter_page_and_mount_access(tmp_path: Path) -> None:
    registry = _write_permission_output_fixture(tmp_path)

    search_payload = search_json(registry)
    search_titles = {entry["title"] for entry in search_payload["entries"]}
    assert {"Home", "Public"} <= search_titles
    assert "Private" not in search_titles
    assert "Team Runbook" not in search_titles

    private_search_payload = search_json(registry, include_private=True)
    private_titles = {entry["title"] for entry in private_search_payload["entries"]}
    assert {"Private", "Team Runbook"} <= private_titles

    graph_titles = {page["title"] for page in catalog_graph(registry)["pages"]}
    assert "Public" in graph_titles
    assert "Private" not in graph_titles
    assert "Team Runbook" not in graph_titles

    structure = build_structure_index(registry)
    heading_titles = {heading["title"] for heading in structure["headings"]}
    assert "Public" in heading_titles
    assert "Private" not in heading_titles
    assert "Team Runbook" not in heading_titles


def test_public_search_snapshot_and_static_routes_filter_access(tmp_path: Path) -> None:
    registry = _write_permission_output_fixture(tmp_path)
    public_registry = _write_permission_output_fixture(tmp_path / "public-registry", include_private=False)

    snapshot = build_search_catalog_snapshot(public_registry)
    snapshot_titles = {node.title for node in snapshot.nodes}
    assert "Public" in snapshot_titles
    assert "Private" not in snapshot_titles
    assert "Team Runbook" not in snapshot_titles

    docs_app = SimpleNamespace(
        catalog=registry,
        config=SimpleNamespace(i18n=SimpleNamespace(enabled=False)),
    )
    routes = set(
        _collect_routes(
            docs_app,
            StaticExportOptions(output_dir=tmp_path / "public", include_portal=False, include_search=False),
        )
    )
    assert "/public/" in routes
    assert "/private/" not in routes
    assert "/team/runbook/" not in routes


def test_public_mcp_resources_filter_access(tmp_path: Path) -> None:
    registry = _write_permission_output_fixture(tmp_path)
    server = FuraMCPServer(SimpleNamespace(catalog=registry, embedding_index=None))

    resources = server.list_resources()
    resource_names = {resource["name"] for resource in resources}
    assert "Public" in resource_names
    assert "Private" not in resource_names
    assert "Team Runbook" not in resource_names

    nodes_payload = server.read_resource("fura://catalog/nodes")
    assert '"Public"' in nodes_payload["text"]
    assert '"Private"' not in nodes_payload["text"]
    assert '"Team Runbook"' not in nodes_payload["text"]
