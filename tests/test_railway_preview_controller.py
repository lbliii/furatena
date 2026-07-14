"""Railway preview control is sealed, exact-SHA, and fail-closed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from furatena.catalog.railway_preview_controller import (
    ControllerConfig,
    PreviewControllerError,
    _environment_config,
    _preview_environment_name,
    _redact,
    _trusted_pull_request,
    configure_preview,
)

SHA = "a" * 40
TOKEN = "review-token-" + "x" * 40


def _config() -> ControllerConfig:
    return ControllerConfig(
        project_id="project-1",
        service_id="service-1",
        repository="owner/repo",
        pr_number=424,
        expected_sha=SHA,
        review_url="https://github.com/owner/repo/pull/424",
        timeout_seconds=1,
        poll_seconds=0.01,
    )


class FakeRailway:
    def __init__(self, *, observed_sha: str = SHA, status: str = "SUCCESS") -> None:
        self.observed_sha = observed_sha
        self.status = status
        self.calls: list[tuple[tuple[str, ...], Mapping[str, object] | None]] = []
        self.deployment_lists = 0

    def __call__(
        self,
        arguments: Sequence[str],
        input_payload: Mapping[str, object] | None,
    ) -> Any:
        call = tuple(arguments)
        self.calls.append((call, input_payload))
        if call[0] == "status":
            return {
                "environments": {
                    "edges": [
                        {
                            "node": {
                                "id": "environment-1",
                                "name": "repo-pr-424",
                            }
                        }
                    ]
                }
            }
        if call[:2] == ("environment", "edit"):
            return {"success": True}
        if call[0] == "redeploy":
            return {"success": True}
        if call[:2] == ("deployment", "list"):
            self.deployment_lists += 1
            if self.deployment_lists == 1:
                return [
                    {
                        "id": "deployment-1",
                        "status": "FAILED",
                        "createdAt": "2026-07-14T10:00:00Z",
                        "meta": {"commitHash": self.observed_sha},
                    }
                ]
            return [
                {
                    "id": "deployment-2",
                    "status": self.status,
                    "createdAt": "2026-07-14T10:01:00Z",
                    "meta": {"commitHash": self.observed_sha},
                }
            ]
        if call[:2] == ("domain", "list"):
            return {
                "domains": [
                    {
                        "domain": "pr-424.example.test",
                        "syncStatus": "ACTIVE",
                    }
                ]
            }
        raise AssertionError(call)


class Publisher:
    def __init__(self, *, ready_ok: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        self.ready_ok = ready_ok

    def __call__(self, **values: object) -> Mapping[str, object]:
        self.calls.append(values)
        if values["state"] == "ready" and not self.ready_ok:
            return {"state": "failed", "ok": False}
        return {
            "state": values["state"],
            "ok": values["state"] != "failed",
        }


def test_controller_seals_token_and_publishes_only_exact_sha() -> None:
    railway = FakeRailway()
    publisher = Publisher()

    result = configure_preview(
        _config(),
        preview_token=TOKEN,
        railway=railway,
        publish=publisher,
    )

    assert result.deployment_id == "deployment-2"
    assert [call["state"] for call in publisher.calls] == ["building", "ready"]
    assert railway.calls[0][0] == (
        "status",
        "--project",
        "project-1",
        "--environment",
        "repo-pr-424",
        "--json",
    )
    assert all(call[0] != "link" for call, _payload in railway.calls)
    edit = next(payload for call, payload in railway.calls if call[:2] == ("environment", "edit"))
    assert edit is not None
    variables = edit["services"]["service-1"]["variables"]  # type: ignore[index]
    assert variables["FURA_PREVIEW_AUTH_TOKEN"] == {"value": TOKEN, "isSealed": True}
    assert variables["FURA_PREVIEW_SHA"] == {"value": SHA, "isSealed": False}
    assert all(TOKEN not in " ".join(call) for call, _payload in railway.calls)


def test_controller_rejects_a_superseded_provider_deployment() -> None:
    railway = FakeRailway(observed_sha="b" * 40)
    publisher = Publisher()

    with pytest.raises(PreviewControllerError, match="superseded"):
        configure_preview(
            _config(),
            preview_token=TOKEN,
            railway=railway,
            publish=publisher,
        )

    assert [call["state"] for call in publisher.calls] == ["building", "failed"]
    assert all(call["state"] != "ready" for call in publisher.calls)


def test_redaction_happens_before_diagnostic_truncation() -> None:
    diagnostic = "x" * 490 + TOKEN + "tail"

    assert TOKEN not in _redact(diagnostic, (TOKEN,))
    assert "[REDACTED]" in _redact(diagnostic, (TOKEN,))


def test_conformance_failure_is_not_overwritten_by_generic_failure() -> None:
    publisher = Publisher(ready_ok=False)

    with pytest.raises(PreviewControllerError, match="conformance"):
        configure_preview(
            _config(),
            preview_token=TOKEN,
            railway=FakeRailway(),
            publish=publisher,
        )

    assert [call["state"] for call in publisher.calls] == ["building", "ready"]


def test_environment_config_has_only_one_sealed_value() -> None:
    payload = _environment_config(_config(), TOKEN)
    variables = payload["services"]["service-1"]["variables"]  # type: ignore[index]

    assert {key for key, value in variables.items() if value["isSealed"]} == {
        "FURA_PREVIEW_AUTH_TOKEN"
    }
    assert variables["FURA_PREVIEW_PR_NUMBER"]["value"] == "424"


def test_preview_environment_name_matches_railway_pr_naming() -> None:
    config = _config()

    assert _preview_environment_name(config) == "repo-pr-424"


def test_trusted_pr_requires_current_internal_nonbot_head() -> None:
    payload = {
        "html_url": "https://github.com/owner/repo/pull/424",
        "state": "open",
        "head": {"sha": SHA, "repo": {"full_name": "owner/repo"}},
        "user": {"type": "User"},
    }
    assert _trusted_pull_request(payload, "owner/repo", SHA) == payload["html_url"]

    payload["head"] = {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}}
    with pytest.raises(PreviewControllerError, match="no longer"):
        _trusted_pull_request(payload, "owner/repo", SHA)
