"""Live deployment SLO policy and monitor contracts."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def _artifact_result() -> dict[str, int]:
    return {
        "page_count": 10,
        "query_page_count": 10,
        "search_page_count": 10,
        "semantic_chunk_count": 20,
        "llms_index_bytes": 100,
        "llms_full_bytes": 1000,
    }


def _healthy_request(origin: str, path: str, *, timeout: float):
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
        artifact_verifier=lambda origin, timeout: _artifact_result(),
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


def test_live_slo_preserves_report_when_bulk_artifact_verifier_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    def request(origin: str, path: str, *, timeout: float):
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

    def time_out(*args, **kwargs):
        raise module.subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", time_out)
    report = module.evaluate_live_slo(_config(), requester=request)

    assert not report["ok"]
    assert report["artifact"] == {"error": "bulk-artifact verifier exceeded its 60s timeout"}
    assert {alert["check"] for alert in report["alerts"]} == {"bulk_artifact_integrity"}


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
        artifact_verifier=lambda origin, timeout: _artifact_result(),
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
        artifact_verifier=lambda origin, timeout: _artifact_result(),
    )

    assert not report["ok"]
    assert report["surface_checks"] == {"health": False, "freshness": False}
    assert {alert["check"] for alert in report["alerts"]} == {"health", "freshness"}


@pytest.mark.parametrize(
    ("completed", "expected_error"),
    (
        (
            SimpleNamespace(returncode=9, stdout="", stderr="SECRET?query=response-body"),
            "bulk-artifact verifier exited with status 9",
        ),
        (
            SimpleNamespace(
                returncode=0,
                stdout="not-json SECRET?query=response-body",
                stderr="",
            ),
            "bulk-artifact verifier returned malformed JSON",
        ),
        (
            SimpleNamespace(
                returncode=0,
                stdout='["SECRET?query=response-body"]',
                stderr="",
            ),
            "bulk-artifact verifier returned an invalid result shape",
        ),
        (
            SimpleNamespace(returncode=0, stdout='{"page_count": 10}', stderr=""),
            "bulk-artifact verifier returned an invalid result shape",
        ),
    ),
)
def test_live_slo_redacts_failed_or_invalid_verifier_output(
    monkeypatch: pytest.MonkeyPatch,
    completed: SimpleNamespace,
    expected_error: str,
) -> None:
    module = _module()
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)

    report = module.evaluate_live_slo(_config(), requester=_healthy_request)
    serialized = json.dumps(report)

    assert not report["ok"]
    assert report["artifact"] == {"error": expected_error}
    assert {alert["check"] for alert in report["alerts"]} == {"bulk_artifact_integrity"}
    assert "SECRET" not in serialized
    assert "?query=" not in serialized
    assert "response-body" not in serialized


def test_live_slo_projects_valid_verifier_output_to_safe_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    verifier_result = _artifact_result() | {
        "debug": "SECRET?query=response-body",
        "future_counter": 42,
    }
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(verifier_result),
        stderr="",
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: completed)

    report = module.evaluate_live_slo(_config(), requester=_healthy_request)
    serialized = json.dumps(report)

    assert report["ok"]
    assert report["artifact"] == _artifact_result()
    assert "debug" not in report["artifact"]
    assert "future_counter" not in report["artifact"]
    assert "SECRET" not in serialized
    assert "?query=" not in serialized
    assert "response-body" not in serialized


@pytest.mark.parametrize(
    ("error", "expected_error"),
    (
        (
            OSError("SECRET?query=response-body"),
            "bulk-artifact verifier could not be started",
        ),
        (
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "SECRET?query=response-body"),
            "bulk-artifact verifier returned unreadable output",
        ),
    ),
)
def test_live_slo_redacts_verifier_launch_and_decode_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_error: str,
) -> None:
    module = _module()

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(module.subprocess, "run", fail)
    report = module.evaluate_live_slo(_config(), requester=_healthy_request)
    serialized = json.dumps(report)

    assert not report["ok"]
    assert report["artifact"] == {"error": expected_error}
    assert {alert["check"] for alert in report["alerts"]} == {"bulk_artifact_integrity"}
    assert "SECRET" not in serialized
    assert "?query=" not in serialized
    assert "response-body" not in serialized


def test_live_slo_main_writes_redacted_failure_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _module()
    config_path = tmp_path / "live-slo.json"
    output_path = tmp_path / "report.json"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    evaluate_live_slo = module.evaluate_live_slo
    monkeypatch.setattr(
        module,
        "evaluate_live_slo",
        lambda config: evaluate_live_slo(config, requester=_healthy_request),
    )

    def fail(*args, **kwargs):
        raise OSError("SECRET?query=response-body")

    monkeypatch.setattr(module.subprocess, "run", fail)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [str(SCRIPT), "--config", str(config_path), "--output", str(output_path)],
    )

    assert module.main() == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    serialized = json.dumps(report) + captured.out + captured.err
    assert report["artifact"] == {"error": "bulk-artifact verifier could not be started"}
    assert {alert["check"] for alert in report["alerts"]} == {"bulk_artifact_integrity"}
    assert "SECRET" not in serialized
    assert "?query=" not in serialized
    assert "response-body" not in serialized


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
