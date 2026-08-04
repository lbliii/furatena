"""Live deployment SLO policy and monitor contracts."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_live_slo.py"


def _module():
    spec = importlib.util.spec_from_file_location("check_live_slo", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config() -> dict:
    return {
        "service": "docs-production",
        "origin": "https://docs.example.com",
        "samples": 3,
        "timeout_seconds": 5,
        "objectives": {
            "probe_availability_percent": 100,
            "ready_p95_milliseconds": 750,
            "home_p95_milliseconds": 1000,
            "freshness_status": "fresh",
            "artifact_integrity_percent": 100,
            "private_image_identity": True,
            "managed_content_identity": True,
        },
        "service_level": {"window_days": 30, "availability_percent": 99.9},
    }


def test_live_slo_script_parses_on_the_hosted_runner_python() -> None:
    ast.parse(SCRIPT.read_text(encoding="utf-8"), feature_version=(3, 12))


def test_live_slo_accepts_complete_digest_identified_service() -> None:
    module = _module()

    def request(origin: str, path: str, *, timeout: float):
        assert origin == "https://docs.example.com"
        assert timeout == 5
        if path == "/meta.json":
            body = json.dumps(
                {
                    "build": {
                        "distribution": "private-image",
                        "image": {"digest": f"sha256:{'a' * 64}"},
                        "content": {"status": "active", "resolved_ref": "b" * 40},
                    }
                }
            ).encode()
            return 200, body, 20.0
        if path == "/healthz":
            return 200, b'{"kind":"health","status":"healthy","ok":true}', 15.0
        if path == "/catalog/freshness.json":
            return 200, b'{"kind":"freshness","status":"fresh","ok":true}', 20.0
        if path == "/readyz":
            return 200, b"ok", 25.0
        return 200, b"<html><body>docs</body></html>", 40.0

    report = module.evaluate_live_slo(
        _config(),
        requester=request,
        artifact_verifier=lambda origin, timeout: {"page_count": 10},
    )

    assert report["ok"]
    assert report["indicators"]["probe_availability_percent"] == 100
    assert report["indicators"]["artifact_integrity_percent"] == 100
    assert report["surface_checks"] == {"health": True, "freshness": True}
    assert report["failures"] == []
    assert report["alerts"] == []


def test_live_slo_fails_closed_on_latency_identity_and_artifact() -> None:
    module = _module()

    def request(origin: str, path: str, *, timeout: float):
        if path == "/meta.json":
            return 200, b'{"build":{"distribution":"source"}}', 20.0
        if path == "/healthz":
            return 200, b'{"kind":"health","status":"healthy","ok":true}', 20.0
        if path == "/catalog/freshness.json":
            return 200, b'{"kind":"freshness","status":"fresh","ok":true}', 20.0
        return (503, b"not ready", 800.0) if path == "/readyz" else (200, b"ok", 1200.0)

    def broken_artifacts(origin: str, *, timeout: float):
        raise RuntimeError("truncated catalog")

    report = module.evaluate_live_slo(
        _config(),
        requester=request,
        artifact_verifier=broken_artifacts,
    )

    assert not report["ok"]
    assert any("availability" in failure for failure in report["failures"])
    assert any("private-image" in failure for failure in report["failures"])
    assert any("managed-content" in failure for failure in report["failures"])
    assert any("artifact integrity" in failure for failure in report["failures"])
    assert {alert["check"] for alert in report["alerts"]} >= {
        "probe_availability",
        "image_identity",
        "content_identity",
        "bulk_artifact_integrity",
    }
    assert all("?" not in alert["summary"] for alert in report["alerts"])


def test_live_slo_fails_closed_on_unreadable_build_identity() -> None:
    module = _module()

    def request(origin: str, path: str, *, timeout: float):
        if path == "/meta.json":
            return 200, b"\xff", 20.0
        if path == "/healthz":
            return 200, b'{"kind":"health","status":"healthy","ok":true}', 20.0
        if path == "/catalog/freshness.json":
            return 200, b'{"kind":"freshness","status":"fresh","ok":true}', 20.0
        if path == "/readyz":
            return 200, b"ok", 25.0
        return 200, b"<html><body>docs</body></html>", 25.0

    report = module.evaluate_live_slo(
        _config(),
        requester=request,
        artifact_verifier=lambda origin, timeout: {"page_count": 10},
    )

    assert not report["ok"]
    assert any("private-image" in failure for failure in report["failures"])
    assert any("managed-content" in failure for failure in report["failures"])


def test_live_slo_fails_closed_on_health_and_freshness_contracts() -> None:
    module = _module()

    def request(origin: str, path: str, *, timeout: float):
        if path == "/meta.json":
            return (
                200,
                json.dumps(
                    {
                        "build": {
                            "distribution": "private-image",
                            "image": {"digest": f"sha256:{'a' * 64}"},
                            "content": {"status": "active", "resolved_ref": "b" * 40},
                        }
                    }
                ).encode(),
                20.0,
            )
        if path == "/healthz":
            return 503, b'{"kind":"health","status":"unhealthy","ok":false}', 20.0
        if path == "/catalog/freshness.json":
            return 200, b'{"kind":"freshness","status":"stale","ok":false}', 20.0
        if path == "/readyz":
            return 200, b"ok", 25.0
        return 200, b"<html><body>docs</body></html>", 25.0

    report = module.evaluate_live_slo(
        _config(),
        requester=request,
        artifact_verifier=lambda origin, timeout: {"page_count": 10},
    )

    assert not report["ok"]
    assert report["surface_checks"] == {"health": False, "freshness": False}
    assert {alert["check"] for alert in report["alerts"]} == {"health", "freshness"}


def test_monitor_preserves_evidence_and_routes_alerts_through_github_issues() -> None:
    workflow = (ROOT / ".github" / "workflows" / "live-slo.yml").read_text(encoding="utf-8")
    policy = json.loads((ROOT / "config" / "live-slo.json").read_text(encoding="utf-8"))

    assert 'cron: "17 */6 * * *"' in workflow
    assert "cancel-in-progress: true" in workflow
    assert "retention-days: 30" in workflow
    assert "issues: write" in workflow
    assert "gh issue create" in workflow
    assert "gh issue comment" in workflow
    assert "gh issue close" in workflow
    assert "jq -r '.service // \"unknown\"'" in workflow
    assert ".build.image.digest" in workflow
    assert ".build.content.resolved_ref" in workflow
    assert ".build.freeze_fingerprint" in workflow
    assert '" + .remediation' in workflow
    assert "always() && steps.probe.outcome == 'failure'" in workflow
    assert "always() && steps.probe.outcome == 'success'" in workflow
    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0" in workflow
    assert "actions/upload-artifact@bbbca2ddaa5d8feaa63e36b76fdaad77386f024f" in workflow
    assert policy["service_level"] == {
        "window_days": 30,
        "availability_percent": 99.9,
        "ready_p95_milliseconds": 750,
        "home_p95_milliseconds": 1000,
        "freshness_percent": 100.0,
        "artifact_integrity_percent": 100.0,
    }
