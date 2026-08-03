"""Railway preview control is sealed, exact-SHA, and fail-closed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from furatena.catalog.railway_preview_controller import (
    ControllerConfig,
    PreviewControllerError,
    RailwayCLI,
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
    def __init__(
        self,
        *,
        observed_sha: str = SHA,
        status: str = "SUCCESS",
        preview_has_service: bool = True,
        mutate_production: bool = False,
        production_commit: str = "c" * 40,
    ) -> None:
        self.observed_sha = observed_sha
        self.status = status
        self.preview_has_service = preview_has_service
        self.mutate_production = mutate_production
        self.production_commit = production_commit
        self.calls: list[tuple[tuple[str, ...], Mapping[str, object] | None]] = []
        self.deployment_lists = 0
        self.production_reads = 0

    def __call__(
        self,
        arguments: Sequence[str],
        input_payload: Mapping[str, object] | None,
    ) -> Any:
        call = tuple(arguments)
        self.calls.append((call, input_payload))
        if call[0] == "status":
            environment_name = call[call.index("--environment") + 1]
            if environment_name == "production":
                self.production_reads += 1
                branch = (
                    "feature/escaped-preview"
                    if self.mutate_production and self.production_reads > 1
                    else "main"
                )
                commit = SHA if branch != "main" else self.production_commit
                return _status_payload(
                    environment_id="production-1",
                    environment_name="production",
                    branch=branch,
                    commit=commit,
                )
            services = (
                {
                    "edges": [
                        {
                            "node": {
                                "id": "instance-1",
                                "serviceId": "service-1",
                            }
                        }
                    ]
                }
                if self.preview_has_service
                else {"edges": []}
            )
            return {
                "environments": {
                    "edges": [
                        {
                            "node": {
                                "id": "environment-1",
                                "name": "repo-pr-424",
                                "serviceInstances": services,
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


def _status_payload(
    *, environment_id: str, environment_name: str, branch: str, commit: str
) -> dict[str, object]:
    return {
        "environments": {
            "edges": [
                {
                    "node": {
                        "id": environment_id,
                        "name": environment_name,
                        "serviceInstances": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "instance-1",
                                        "serviceId": "service-1",
                                        "source": {"repo": "owner/repo", "image": None},
                                        "latestDeployment": {
                                            "id": "production-deployment",
                                            "meta": {
                                                "repo": "owner/repo",
                                                "branch": branch,
                                                "commitHash": commit,
                                            },
                                        },
                                    }
                                }
                            ]
                        },
                    }
                }
            ]
        }
    }


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


def test_controller_fails_before_mutation_when_preview_service_is_missing() -> None:
    railway = FakeRailway(preview_has_service=False)
    publisher = Publisher()

    with pytest.raises(PreviewControllerError, match="duplicate the base service"):
        configure_preview(
            _config(),
            preview_token=TOKEN,
            railway=railway,
            publish=publisher,
        )

    assert all(call[:2] != ("environment", "edit") for call, _payload in railway.calls)
    assert all(call[0] != "redeploy" for call, _payload in railway.calls)


def test_controller_fails_if_production_leaves_main() -> None:
    railway = FakeRailway(mutate_production=True)
    publisher = Publisher()

    with pytest.raises(PreviewControllerError, match="must remain deployed"):
        configure_preview(
            _config(),
            preview_token=TOKEN,
            railway=railway,
            publish=publisher,
        )

    assert [call["state"] for call in publisher.calls] == ["building", "failed"]


def test_controller_fails_if_production_received_the_pr_head() -> None:
    railway = FakeRailway(production_commit=SHA)

    with pytest.raises(PreviewControllerError, match="received the pull-request head"):
        configure_preview(
            _config(),
            preview_token=TOKEN,
            railway=railway,
            publish=Publisher(),
        )


def test_cli_rejects_source_link_operations_without_running_them(tmp_path: Path) -> None:
    cli = RailwayCLI(cwd=tmp_path)

    with pytest.raises(PreviewControllerError, match="sealed preview command set"):
        cli(("service", "source", "connect", "--repo", "owner/repo"), None)


def test_redaction_happens_before_diagnostic_truncation() -> None:
    diagnostic = "x" * 490 + TOKEN + "tail"

    assert TOKEN not in _redact(diagnostic, (TOKEN,))
    assert "[REDACTED]" in _redact(diagnostic, (TOKEN,))


def test_redaction_removes_provider_credentials_without_explicit_values() -> None:
    provider_token = "github_" + "pat_" + "secret123"
    diagnostic = (
        f'{{"registryCredentials":{{"password":"{provider_token}"}},'
        '"Authorization":"Bearer-sensitive"}'
    )

    redacted = _redact(diagnostic, ())

    assert provider_token not in redacted
    assert "Bearer-sensitive" not in redacted
    assert redacted.count("[REDACTED]") == 2


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
