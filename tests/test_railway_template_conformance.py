"""Railway composer spec, public content starter, and clean-account harness."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml
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
    assert template["public_networking"]
    assert template["volume"] == {
        "name": "furatena-data",
        "mount_path": "/data/furatena",
        "required": True,
    }
    assert template["replicas"] == 1
    variables = {item["name"]: item for item in payload["variables"]}
    assert all(item["description"].strip() for item in variables.values())
    assert variables["FURA_BASE_URL"]["default"] == "https://${{RAILWAY_PUBLIC_DOMAIN}}"
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


def test_composer_spec_fails_closed_on_variable_and_network_metadata_drift() -> None:
    payload = json.loads((ROOT / "config" / "railway-template-spec.json").read_text())
    validator = _validator()

    missing_description = copy.deepcopy(payload)
    missing_description["variables"][0]["description"] = " "

    missing_optional_default = copy.deepcopy(payload)
    content_ref = next(
        item for item in missing_optional_default["variables"] if item["name"] == "FURA_CONTENT_REF"
    )
    content_ref.pop("default")

    literal_secret = copy.deepcopy(payload)
    refresh_token = next(
        item for item in literal_secret["variables"] if item["name"] == "FURA_CONTENT_REFRESH_TOKEN"
    )
    refresh_token["default"] = "committed-value"

    disabled_network = copy.deepcopy(payload)
    disabled_network["template"]["public_networking"] = False

    detached_base_url = copy.deepcopy(payload)
    base_url = next(
        item for item in detached_base_url["variables"] if item["name"] == "FURA_BASE_URL"
    )
    base_url["default"] = "invalid"

    for changed, expected in (
        (missing_description, "template variable FURA_CONTENT_REPOSITORY must have a description"),
        (
            missing_optional_default,
            "optional template variable FURA_CONTENT_REF must have a safe default",
        ),
        (
            literal_secret,
            "FURA_CONTENT_REFRESH_TOKEN must use a generated secret of at least 32 characters",
        ),
        (disabled_network, "public networking must be enabled"),
        (detached_base_url, "FURA_BASE_URL must derive from RAILWAY_PUBLIC_DOMAIN"),
    ):
        assert expected in validator.validate_template_spec(changed)


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
        "refs/heads/furatena-conformance-missing",
        "content-after-bad-ref.json",
        "redeploy",
        "/_fura/content/rollback",
        "rollback_hold",
        "verify-live-artifacts.py",
        "Delete the disposable project with bounded retries",
        "if: always()",
        "for attempt in 1 2 3",
        "timeout 120s",
        "conformance-evidence/cleanup.json",
        "delete",
    ):
        assert required in source
    assert "RAILWAY_CLI_VERSION: 5.25.0" in source
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in source
    assert "actions/upload-artifact@bbbca2ddaa5d8feaa63e36b76fdaad77386f024f" in source


def test_clean_account_evidence_excludes_raw_control_plane_identifiers() -> None:
    source = (ROOT / ".github" / "workflows" / "railway-template-conformance.yml").read_text()
    upload = source.split("      - name: Upload conformance evidence\n", 1)[1]

    assert "conformance-evidence/railway-terminal.json" in upload
    assert "conformance-evidence/cleanup.json" in upload
    for raw_control_plane_file in (
        "project.json",
        "domains.json",
        "domain-created.json",
        "deployments-initial.json",
        "final-deployments.json",
        "final-status.json",
    ):
        assert raw_control_plane_file not in upload
    assert "path: conformance-evidence/" not in upload
    assert "latest_deployment_status" in source
    assert "deployment_statuses" in source
    assert 'rm -f "${RUNNER_TEMP}/furatena-final-deployments.json"' in source


def test_clean_account_masks_generated_session_secret_before_use() -> None:
    source = (ROOT / ".github" / "workflows" / "railway-template-conformance.yml").read_text()

    generated = source.index("session_secret=$(openssl rand -hex 32)")
    masked = source.index('echo "::add-mask::$session_secret"')
    deployed = source.index("FURA_SESSION_SECRET=$session_secret")

    assert generated < masked < deployed


def test_clean_account_persists_name_fallback_before_project_init() -> None:
    source = (ROOT / ".github" / "workflows" / "railway-template-conformance.yml").read_text()

    name = source.index('project_name="furatena-conformance-${GITHUB_RUN_ID}"')
    persisted = source.index(
        'printf \'%s\\n\' "$project_name" > "${RUNNER_TEMP}/furatena-project-name"'
    )
    initialized = source.index('npx --yes "@railway/cli@${RAILWAY_CLI_VERSION}" init')

    assert name < persisted < initialized


def test_clean_account_removes_disposable_origin_from_uploaded_slo() -> None:
    source = (ROOT / ".github" / "workflows" / "railway-template-conformance.yml").read_text()
    activation = source.split(
        "      - name: Prove first activation and private-image identity\n", 1
    )[1].split("      - name: Inject an unreachable ref", 1)[0]
    upload = source.split("      - name: Upload conformance evidence\n", 1)[1]

    assert 'raw_slo="${RUNNER_TEMP}/furatena-initial-slo.json"' in activation
    assert "jq 'del(.origin)' \"$raw_slo\" > conformance-evidence/initial-slo.json" in activation
    assert "trap 'rm -f \"$raw_slo\"' EXIT" in activation
    assert "conformance-evidence/initial-slo.json" in upload
    assert "furatena-initial-slo.json" not in upload


def test_clean_account_cleanup_uses_local_id_and_name_fallbacks(tmp_path: Path) -> None:
    workflow_path = ROOT / ".github" / "workflows" / "railway-template-conformance.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    cleanup = next(
        step["run"]
        for step in workflow["jobs"]["clean-account"]["steps"]
        if step.get("name") == "Delete the disposable project with bounded retries"
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_npx = fake_bin / "npx"
    fake_npx.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n',
        encoding="utf-8",
    )
    fake_npx.chmod(0o755)

    for fallback_file, project_target in (
        ("furatena-project-id", "project-id-fallback"),
        ("furatena-project-name", "furatena-conformance-name-fallback"),
    ):
        case_root = tmp_path / fallback_file
        runner_temp = case_root / "runner-temp"
        runner_temp.mkdir(parents=True)
        (runner_temp / fallback_file).write_text(project_target + "\n", encoding="utf-8")
        call_log = case_root / "calls.log"
        completed = subprocess.run(
            ["bash", "-c", cleanup],
            cwd=case_root,
            env={
                **os.environ,
                "CALL_LOG": str(call_log),
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "PROJECT_ID": "",
                "RAILWAY_CLI_VERSION": "5.25.0",
                "RUNNER_TEMP": str(runner_temp),
            },
            check=False,
            capture_output=True,
            text=True,
        )

        assert completed.returncode == 0, completed.stderr
        assert f"--project {project_target} --yes --json" in call_log.read_text(encoding="utf-8")
        receipt = json.loads(
            (case_root / "conformance-evidence" / "cleanup.json").read_text(encoding="utf-8")
        )
        assert receipt == {"schema_version": 1, "cleanup": {"status": "deleted", "attempts": 1}}
        assert not (runner_temp / fallback_file).exists()
