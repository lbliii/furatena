"""Tenant, role, and team isolation across browser, export, and MCP."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from chirp.errors import NotFound
from chirp.testing import TestClient

from furatena.catalog.access import AccessSubject
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.export import catalog_graph, search_json
from furatena.catalog.gateway_identity import GatewayIdentityError, map_gateway_claims
from furatena.catalog.mcp import FuraMCPServer, MCPAccessPolicy
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.semantic import semantic_index_json
from furatena.cli.main import main

_TEAM_CANARY = "alpha-orchid-team-canary"
_ADMIN_CANARY = "velvet-cipher-admin-canary"


@pytest.fixture()
def isolated_app(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Isolation Docs"])
    capsys.readouterr()
    docs = app_root / "content" / "docs"
    (docs / "public.md").write_text(
        "---\ntitle: Public guide\n---\n\nPublic content.\n",
        encoding="utf-8",
    )
    (docs / "team-alpha.md").write_text(
        "---\n"
        "title: Team Alpha runbook\n"
        "visibility: private\n"
        "access:\n"
        "  teams: [alpha]\n"
        "---\n\n"
        f"{_TEAM_CANARY}\n",
        encoding="utf-8",
    )
    (docs / "admin.md").write_text(
        "---\n"
        "title: Admin runbook\n"
        "visibility: private\n"
        "access:\n"
        "  roles: [admin]\n"
        "---\n\n"
        f"{_ADMIN_CANARY}\n",
        encoding="utf-8",
    )
    return app_root


def _subject(role: str, *, team: str | None = None) -> AccessSubject:
    return AccessSubject.from_values(
        actor=f"{role}-{team or 'none'}",
        roles=[role],
        teams=[team] if team else [],
    )


def _docs(app_root: Path, subject: AccessSubject) -> DocsApp:
    return DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        author_subject=subject,
    )


async def _fetch_browser_surfaces(
    client: TestClient,
    *,
    fetch_direct: bool,
) -> tuple[int | None, str, str]:
    direct = await client.get("/docs/team-alpha/") if fetch_direct else None
    graph = await client.get("/catalog.json?include_private=1")
    search = await client.get(
        "/search.json?q=canary&include_private=1&roles=admin&teams=alpha"
    )
    return direct.status if direct is not None else None, graph.text, search.text


def _surface_texts(docs: DocsApp, subject: AccessSubject) -> dict[str, str]:
    graph = json.dumps(catalog_graph(docs.catalog, subject=subject), sort_keys=True)
    search = json.dumps(search_json(docs.catalog, subject=subject), sort_keys=True)
    semantic = json.dumps(
        semantic_index_json(docs.catalog, docs.embedding_index, subject=subject),
        sort_keys=True,
    )
    policy = MCPAccessPolicy(
        actor=subject.actor,
        tenant="default",
        site="default",
        allow_private=True,
        roles=subject.roles,
        teams=subject.teams,
    )
    server = FuraMCPServer(docs, include_private=True, policy=policy)
    mcp_graph = server.read_resource("fura://catalog/graph")["text"]
    mcp_search = json.dumps(
        server.call_tool("semantic_search", {"query": "canary"})[
            "structuredContent"
        ],
        sort_keys=True,
    )
    return {
        "catalog": graph,
        "search": search,
        "semantic": semantic,
        "mcp_graph": mcp_graph,
        "mcp_search": mcp_search,
    }


def test_role_and_team_canaries_are_isolated_across_export_and_mcp(
    isolated_app: Path,
) -> None:
    alpha = _subject("reader", team="alpha")
    beta = _subject("reader", team="beta")
    admin = _subject("admin")
    anonymous = AccessSubject.anonymous()
    docs = _docs(isolated_app, admin)

    alpha_outputs = _surface_texts(docs, alpha)
    beta_outputs = _surface_texts(docs, beta)
    admin_outputs = _surface_texts(docs, admin)
    anonymous_outputs = _surface_texts(docs, anonymous)

    for payload in alpha_outputs.values():
        assert _TEAM_CANARY in payload
        assert _ADMIN_CANARY not in payload
    for payload in beta_outputs.values():
        assert _TEAM_CANARY not in payload
        assert _ADMIN_CANARY not in payload
    for payload in anonymous_outputs.values():
        assert _TEAM_CANARY not in payload
        assert _ADMIN_CANARY not in payload
    for payload in admin_outputs.values():
        assert _TEAM_CANARY in payload
        assert _ADMIN_CANARY in payload


def test_browser_direct_and_json_routes_use_signed_session_subject(
    isolated_app: Path,
) -> None:
    cases = (
        (_subject("reader", team="alpha"), 200, True, False),
        (_subject("reader", team="beta"), None, False, False),
        (_subject("admin"), 200, True, True),
        (AccessSubject.anonymous(), None, False, False),
    )

    for subject, direct_status, has_team, has_admin in cases:
        client = TestClient(_docs(isolated_app, subject).create_app())
        status, graph_text, search_text = asyncio.run(
            _fetch_browser_surfaces(client, fetch_direct=direct_status == 200)
        )
        combined = f"{graph_text}\n{search_text}"
        assert status == direct_status
        assert (_TEAM_CANARY in combined) is has_team
        assert (_ADMIN_CANARY in combined) is has_admin


def test_browser_direct_page_fails_closed_before_rendering(
    isolated_app: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = _docs(isolated_app, _subject("reader", team="beta"))
    monkeypatch.setattr("furatena.catalog.docs_app.get_session", lambda: {})

    with pytest.raises(NotFound, match="Page not found"):
        docs._render_catalog_page(SimpleNamespace(path="/docs/team-alpha/"))


def test_spoofed_mcp_arguments_do_not_escalate_policy_subject(
    isolated_app: Path,
) -> None:
    beta = _subject("reader", team="beta")
    docs = _docs(isolated_app, beta)
    server = FuraMCPServer(
        docs,
        include_private=True,
        policy=MCPAccessPolicy(
            actor=beta.actor,
            allow_private=True,
            roles=beta.roles,
            teams=beta.teams,
        ),
    )
    result = server.call_tool(
        "semantic_search",
        {
            "query": "canary",
            "roles": ["admin"],
            "teams": ["alpha"],
            "tenant": "other",
        },
    )
    payload = json.dumps(result["structuredContent"], sort_keys=True)
    assert _TEAM_CANARY not in payload
    assert _ADMIN_CANARY not in payload


def test_cross_tenant_or_missing_gateway_context_fails_before_surface_access() -> None:
    claims = {
        "sub": "reader",
        "roles": ["reader"],
        "teams": ["alpha"],
        "tenant": "other",
        "workspace": "platform",
        "site": "docs",
    }
    with pytest.raises(GatewayIdentityError) as cross_tenant:
        map_gateway_claims(
            claims,
            trusted_transport=True,
            expected_identity={
                "tenant": "acme",
                "workspace": "platform",
                "site": "docs",
            },
        )
    assert cross_tenant.value.code == "identity_conflict"

    with pytest.raises(GatewayIdentityError) as unavailable:
        map_gateway_claims(claims, trusted_transport=False)
    assert unavailable.value.code == "untrusted_transport"
