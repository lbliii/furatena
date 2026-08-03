"""Railway composer spec, public content starter, and clean-account harness."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

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


def test_clean_account_workflow_proves_lifecycle_and_always_deletes_project() -> None:
    source = (ROOT / ".github" / "workflows" / "railway-template-conformance.yml").read_text()

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
        "if: always() && steps.project.outputs.project_id != ''",
        "delete",
    ):
        assert required in source
    assert "RAILWAY_CLI_VERSION: 5.25.0" in source
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in source
    assert "actions/upload-artifact@bbbca2ddaa5d8feaa63e36b76fdaad77386f024f" in source
