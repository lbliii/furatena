"""Railway composer spec, public content starter, and clean-account harness."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
from pathlib import Path

from chirp.testing.client import TestClient

from furatena.catalog.config import load_docs_config
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.view_kinds import VIEW_KINDS
from furatena.cli.main import run_command

ROOT = Path(__file__).resolve().parents[1]


def _validator():
    path = ROOT / "scripts" / "verify_template_spec.py"
    spec = importlib.util.spec_from_file_location("verify_template_spec", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_composer_spec_has_private_image_volume_secrets_and_gates() -> None:
    payload = json.loads((ROOT / "config" / "railway-template-spec.json").read_text())
    failures = _validator().validate_template_spec(payload)

    assert failures == []
    template = payload["template"]
    assert template["hidden_registry_credentials"]
    assert template["volume"] == {
        "name": "furatena-data",
        "mount_path": "/data/furatena",
        "required": True,
    }
    assert template["replicas"] == 1
    variables = {item["name"]: item for item in payload["variables"]}
    assert variables["RAILWAY_RUN_UID"] == {
        "name": "RAILWAY_RUN_UID",
        "required": True,
        "secret": False,
        "default": "0",
        "description": (
            "Start only the volume ownership bootstrap as root; Furatena immediately drops "
            "to uid/gid 65532"
        ),
    }


def test_public_content_starter_has_no_proprietary_package_dependency() -> None:
    starter = ROOT / "examples" / "railway-content-starter"
    assert (starter / "app/docs.yaml").is_file()
    assert (starter / "app/mounts.yaml").is_file()
    assert list((starter / "app/content").rglob("*.md"))
    assert not (starter / "pyproject.toml").exists()
    text = "\n".join(path.read_text() for path in starter.rglob("*") if path.is_file())
    assert "pip install furatena" not in text
    assert "furatena==" not in text
    refresh = (starter / ".github/workflows/refresh.yml").read_text()
    assert "FURATENA_REFRESH_TOKEN" in refresh
    assert "Authorization: Bearer" in refresh


def test_public_content_starter_has_realistic_owned_content_and_operations_guidance() -> None:
    starter = ROOT / "examples" / "railway-content-starter"
    content = starter / "app" / "content" / "docs"

    for relative_path in (
        "getting-started/first-request.md",
        "concepts/project-identity.md",
        "guides/publish-a-change.md",
        "reference/projects.md",
        "troubleshooting/refresh-failures.md",
        "contributing.md",
    ):
        assert (content / relative_path).is_file()

    readme = (starter / "README.md").read_text(encoding="utf-8").lower()
    for required in (
        "edit in github",
        "edit locally",
        "pull request",
        "authenticated refresh",
        "roll back content",
        "runtime updates",
        "application source",
        "image digest",
    ):
        assert required in readme


def test_public_content_starter_uses_packaged_presentation_and_owned_branding() -> None:
    starter = ROOT / "examples" / "railway-content-starter"
    app_root = starter / "app"
    config = load_docs_config(app_root / "docs.yaml")

    assert config.presentation.layout == "docs"
    assert config.presentation.skin == "lagoon"
    assert config.presentation.trusted_capabilities == frozenset({"scripts"})
    assert config.identity.to_meta() == {
        "tenant": "acme",
        "workspace": "developer-platform",
        "site": "documentation",
    }
    assert not (app_root / "theme" / "shell.html").exists()
    assert not (app_root / "theme" / "views").exists()
    branding = app_root / "theme" / "assets" / "branding"
    assert {path.relative_to(app_root).as_posix() for path in branding.iterdir()} == {
        "theme/assets/branding/favicon.svg",
        "theme/assets/branding/site.webmanifest",
    }

    config_source = (app_root / "docs.yaml").read_text(encoding="utf-8")
    for obsolete in ("shell:", "views:", "templates: theme", "use: lagoon"):
        assert obsolete not in config_source

    forbidden_suffixes = {".css", ".html", ".js", ".py", ".woff", ".woff2"}
    assert not [path for path in app_root.rglob("*") if path.suffix in forbidden_suffixes]
    forbidden_directories = {
        ".docs-cache",
        ".preview",
        "__pycache__",
        "frozen",
        "public",
    }
    assert not [
        path for path in starter.rglob("*") if path.is_dir() and path.name in forbidden_directories
    ]


def test_public_content_starter_checks_freezes_serves_exports_and_keeps_agent_outputs(
    tmp_path: Path,
) -> None:
    source = ROOT / "examples" / "railway-content-starter" / "app"
    app_root = tmp_path / "app"
    frozen = tmp_path / "frozen"
    public = tmp_path / "public"
    shutil.copytree(source, app_root)

    checked = run_command(
        [
            "--app-root",
            str(app_root),
            "check",
            "--content-only",
            "--warnings-as-errors",
            "--json",
        ]
    )
    run_command(["--app-root", str(app_root), "freeze", str(frozen), "--json"])
    run_command(
        [
            "--app-root",
            str(app_root),
            "export",
            str(public),
            "--frozen",
            str(frozen),
            "--base-path",
            "",
            "--json",
        ]
    )

    assert checked is not None and checked.ok
    frozen_site = (
        frozen
        / "tenants"
        / "acme"
        / "workspaces"
        / "developer-platform"
        / "sites"
        / "documentation"
    )
    public_site = (
        public
        / "tenants"
        / "acme"
        / "workspaces"
        / "developer-platform"
        / "sites"
        / "documentation"
    )
    assert (frozen_site / "freeze.manifest.json").is_file()

    config = load_docs_config(app_root / "docs.yaml")
    docs = DocsApp(
        config,
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, False, False),
    )

    async def live_outputs():
        client = TestClient(docs.create_app())
        return {
            route: await client.get(route)
            for route in (
                "/",
                "/docs/quickstart/",
                "/docs/reference/projects/",
                "/search?q=quickstart",
                "/catalog.json",
                "/llms.txt",
            )
        }

    responses = asyncio.run(live_outputs())
    assert all(response.status == 200 for response in responses.values())
    assert "chirp-theme-docs-layout" in responses["/docs/quickstart/"].text
    assert "Projects API" in responses["/docs/reference/projects/"].text
    assert "Quickstart" in responses["/search?q=quickstart"].text
    assert (public_site / "docs" / "quickstart" / "index.html").is_file()
    assert (public_site / "docs" / "reference" / "projects" / "index.html").is_file()
    assert (public / "catalog.json").read_bytes() == (frozen_site / "catalog.json").read_bytes()
    assert (public / "llms.txt").read_bytes() == (frozen_site / "llms.txt").read_bytes()

    manifest = json.loads((frozen_site / "freeze.manifest.json").read_text(encoding="utf-8"))
    assert manifest["presentation"]["layout"]["id"] == "docs"
    assert manifest["presentation"]["skin"]["id"] == "lagoon"
    assert {name for name, _template in docs.theme.view_templates} == {
        spec.kind for spec in VIEW_KINDS
    }


def test_public_content_starter_composes_under_managed_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = ROOT / "examples" / "railway-content-starter" / "app"
    app_root = tmp_path / "generation" / "source" / "app"
    platform_root = tmp_path / "platform" / "app"
    state_root = tmp_path / "runtime-state"
    output_root = tmp_path / "runtime-output"
    shutil.copytree(source, app_root)
    platform_root.mkdir(parents=True)
    monkeypatch.setenv("FURA_CONTENT_REPOSITORY", "https://github.com/example/docs.git")
    monkeypatch.setenv("FURA_PLATFORM_ROOT", str(platform_root))
    monkeypatch.setenv("FURA_RUNTIME_STATE_ROOT", str(state_root))
    monkeypatch.setenv("FURA_OUTPUT_ROOT", str(output_root))

    config = load_docs_config(app_root / "docs.yaml")
    docs = DocsApp(
        config,
        repo_root=app_root.parent,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, False, False),
    )

    assert docs.roots.managed
    assert docs.roots.site == app_root
    assert docs.roots.platform == platform_root
    assert docs.roots.state == state_root
    assert docs.roots.output == output_root
    assert docs.theme.presentation.layout.id == "docs"
    assert docs.theme.presentation.skin.id == "lagoon"

    async def managed_output():
        return await TestClient(docs.create_app()).get("/docs/quickstart/")

    response = asyncio.run(managed_output())
    assert response.status == 200
    assert "chirp-theme-docs-layout" in response.text


def test_clean_account_workflow_proves_lifecycle_and_always_deletes_project() -> None:
    source = (ROOT / ".github" / "workflows" / "railway-template-conformance.yml").read_text()

    assert "examples/railway-content-starter/**" in source
    for required in (
        "@railway/cli@${RAILWAY_CLI_VERSION}",
        "deploy --template",
        "check_live_slo.py",
        "Reject unauthenticated content refresh without changing generations",
        "content-auth-denied.json",
        "content-after-auth-denial.json",
        "refs/heads/furatena-conformance-missing",
        "content-after-bad-ref.json",
        "redeploy",
        "/_fura/content/rollback",
        "rollback_hold",
        "verify-live-artifacts.py",
        "if: always() && steps.project.outputs.project_id != ''",
        "delete",
    ):
        assert required in source
    assert "RAILWAY_CLI_VERSION: 5.25.0" in source
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in source
    assert "actions/upload-artifact@bbbca2ddaa5d8feaa63e36b76fdaad77386f024f" in source


def test_clean_account_workflow_proves_refresh_auth_denial_without_mutation() -> None:
    source = (ROOT / ".github" / "workflows" / "railway-template-conformance.yml").read_text()
    denial = source.split(
        "      - name: Reject unauthenticated content refresh without changing generations\n",
        1,
    )[1].split("\n      - name:", 1)[0]

    assert "--request POST" in denial
    assert 'test "$status" = 401' in denial
    assert "content operation authorization failed" in denial
    assert "Authorization:" not in denial
    assert "content-after-auth-denial.json" in denial
    assert "active_generation" in denial
