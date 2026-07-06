"""Cross-transport author role and lifecycle authorization matrix."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from chirp.testing import TestClient

from furatena.catalog.access import AccessRole, AccessSubject
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.mcp import FuraMCPServer, MCPAccessPolicy
from furatena.catalog.registry import load_mounts
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.cli.authoring import author_apply_edit, author_new, author_transition
from furatena.cli.main import main

_EXPECTED = {
    AccessRole.ANONYMOUS: frozenset(),
    AccessRole.READER: frozenset(),
    AccessRole.CONTRIBUTOR: frozenset({"draft", "edit", "create"}),
    AccessRole.PUBLISHER: frozenset({"draft", "publish", "unpublish", "edit", "create"}),
    AccessRole.ADMIN: frozenset(
        {"draft", "publish", "unpublish", "archive", "edit", "create"}
    ),
}
_OPERATIONS = ("draft", "publish", "unpublish", "archive", "edit", "create")


def _subject(role: AccessRole) -> AccessSubject:
    return AccessSubject.from_values(actor=f"test-{role.value}", roles=[role])


def _csrf_context(response) -> tuple[str, str]:
    match = re.search(r'<meta name="csrf-token" content="([^"]+)">', response.text)
    assert match is not None
    headers = {str(key).lower(): str(value) for key, value in response.headers}
    return match.group(1), headers["set-cookie"].split(";", 1)[0]


def test_cli_author_engine_enforces_role_operation_matrix(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Authorization Matrix"])
    mounts = load_mounts(app_root / "mounts.yaml", repo_root=app_root)
    target = app_root / "content" / "docs" / "get-started.md"
    source = target.read_text(encoding="utf-8")

    for role, allowed in _EXPECTED.items():
        subject = _subject(role)
        for operation in _OPERATIONS:
            if operation == "create":
                result = author_new(
                    f"docs/{role.value}-draft",
                    mounts=mounts,
                    subject=subject,
                    dry_run=True,
                )
            elif operation == "edit":
                result = author_apply_edit(
                    "docs/get-started",
                    mounts=mounts,
                    subject=subject,
                    old_text="# Get started",
                    new_text="# Get started safely",
                    dry_run=True,
                )
            else:
                result = author_transition(
                    operation,
                    "docs/get-started",
                    mounts=mounts,
                    subject=subject,
                    dry_run=True,
                )
            assert result.ok is (operation in allowed), (role, operation, result.to_dict())
            if not result.ok:
                assert result.diagnostics[0].rule_id == "fura.author.authorization"

    assert target.read_text(encoding="utf-8") == source


def test_browser_enforces_same_matrix_for_htmx_and_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Authorization Matrix"])
    monkeypatch.setenv("CHIRP_SKIP_CONTRACT_CHECKS", "1")

    for role, allowed in _EXPECTED.items():
        docs = DocsApp.from_paths(
            app_root / "docs.yaml",
            repo_root=app_root,
            autodoc=False,
            serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
            author_subject=_subject(role),
        )
        client = TestClient(docs.create_app())
        page = asyncio.run(client.get("/docs/get-started/"))
        token, cookie = _csrf_context(page)
        base_headers = {"Cookie": cookie, "X-CSRF-Token": token}

        for operation in ("draft", "publish", "unpublish", "archive"):
            data = {
                "slug": "docs/get-started",
                "operation": operation,
                "dry_run": "1",
                "confirmed": "0",
                "actor": "forged-admin",
                "roles": "admin",
            }
            fallback = asyncio.run(
                client.post("/docs/_author/transition", headers=base_headers, data=data)
            )
            htmx = asyncio.run(
                client.post(
                    "/docs/_author/transition",
                    headers={**base_headers, "HX-Request": "true"},
                    data=data,
                )
            )
            expected_allowed = operation in allowed
            assert fallback.status == (303 if expected_allowed else 403), (role, operation)
            assert htmx.status == (200 if expected_allowed else 403), (role, operation)

        source = (app_root / "content" / "docs" / "get-started.md").read_text(encoding="utf-8")
        edit = asyncio.run(
            client.post(
                "/docs/_author/studio/save",
                headers=base_headers,
                data={
                    "slug": "docs/get-started",
                    "mode": "edit",
                    "title": "Get started",
                    "source": source,
                },
            )
        )
        assert edit.status == (200 if "edit" in allowed else 403), role

        create_slug = f"docs/browser-{role.value}"
        create = asyncio.run(
            client.post(
                "/docs/_author/studio/save",
                headers=base_headers,
                data={
                    "slug": create_slug,
                    "mode": "create",
                    "title": role.value.title(),
                    "source": f"# {role.value.title()}\n",
                },
            )
        )
        assert create.status == (200 if "create" in allowed else 403), role
        assert (app_root / "content" / "docs" / f"browser-{role.value}.md").exists() is (
            "create" in allowed
        )


def test_mcp_enforces_same_trusted_policy_matrix(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Authorization Matrix"])
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )

    calls = {
        "create": (
            "author_create_draft",
            {"slug": "docs/mcp-matrix", "title": "Matrix"},
        ),
        "edit": (
            "author_apply_edit",
            {
                "target": "docs/get-started",
                "old_text": "# Get started",
                "new_text": "# Get started safely",
            },
        ),
        "publish": ("author_publish", {"target": "docs/get-started"}),
        "unpublish": ("author_unpublish", {"target": "docs/get-started"}),
        "archive": ("author_archive", {"target": "docs/get-started"}),
    }

    for role, allowed in _EXPECTED.items():
        policy = MCPAccessPolicy(
            transport="remote",
            actor=f"mcp-{role.value}",
            allow_private=True,
            roles=frozenset({role}),
            privileged_tokens=frozenset({"trusted"}),
        )
        server = FuraMCPServer(docs, include_private=True, policy=policy)
        for operation, (tool, arguments) in calls.items():
            response = server.call_tool(
                tool,
                {
                    **arguments,
                    "actor": "forged-admin",
                    "roles": ["admin"],
                    "privileged_token": "trusted",
                },
            )
            assert response["isError"] is (operation not in allowed), (
                role,
                operation,
                response,
            )
            if operation not in allowed:
                assert response["structuredContent"]["diagnostics"][0]["rule_id"] == (
                    "fura.author.authorization"
                )
            assert response["structuredContent"]["audit"]["actor"] == f"mcp-{role.value}"


def test_remote_author_serve_fails_closed_without_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Authorization Matrix"])
    monkeypatch.setenv("FURA_MODE", "preview")

    with pytest.raises(SystemExit, match="loopback host"):
        main(
            [
                "--app-root",
                str(app_root),
                "serve",
                "--author",
                "--no-autodoc",
                "--host",
                "0.0.0.0",
            ]
        )
