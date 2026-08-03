"""Trusted exact-SHA controller for Railway pull-request environments."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from furatena.catalog.preview_reporting import report_preview_state

_SHA = re.compile(r"[0-9a-f]{40}")
_TERMINAL_FAILURES = {"CANCELED", "CRASHED", "FAILED", "NEEDS_APPROVAL", "SKIPPED"}
_ALLOWED_RAILWAY_COMMANDS = {
    ("deployment", "list"),
    ("domain", "list"),
    ("environment", "edit"),
    ("redeploy",),
    ("status",),
}
_SENSITIVE_DIAGNOSTIC_VALUE = re.compile(
    r'(?i)(["\']?(?:authorization|password|secret|token|api[_-]?key)["\']?\s*[:=]\s*["\']?)'
    r"([^\s,}\"']+)"
)
_GITHUB_TOKEN = re.compile(r"\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+\b")


class PreviewControllerError(RuntimeError):
    """A sanitized, operator-actionable provider control failure."""


class _PreviewEnvironmentPending(PreviewControllerError):
    """The deterministic Railway PR environment does not exist yet."""


@dataclass(frozen=True, slots=True)
class ControllerConfig:
    project_id: str
    service_id: str
    repository: str
    pr_number: int
    expected_sha: str
    review_url: str
    timeout_seconds: float = 1_200
    poll_seconds: float = 10


@dataclass(frozen=True, slots=True)
class PreviewDeployment:
    environment_id: str
    deployment_id: str
    domain: str


@dataclass(frozen=True, slots=True)
class _ProtectedServiceState:
    source_repo: str
    source_image: str
    deployed_repo: str
    deployed_branch: str
    deployed_sha: str


RailwayCall = Callable[[Sequence[str], Mapping[str, object] | None], Any]


class Publisher(Protocol):
    def __call__(
        self,
        *,
        state: str,
        origin: str,
        remediation: str | None,
        details_url: str,
    ) -> Mapping[str, object]: ...


class RailwayCLI:
    """Small JSON-only Railway CLI boundary that never includes secrets in arguments."""

    def __init__(self, *, cwd: Path, secrets_to_redact: Sequence[str] = ()) -> None:
        self.cwd = cwd
        self.secrets_to_redact = tuple(value for value in secrets_to_redact if value)

    def __call__(
        self,
        arguments: Sequence[str],
        input_payload: Mapping[str, object] | None = None,
    ) -> Any:
        if not _railway_command_allowed(arguments):
            command_name = " ".join(arguments[:3]) or "<empty>"
            raise PreviewControllerError(
                f"Railway command {command_name!r} is outside the sealed preview command set."
            )
        command = ["railway", *arguments]
        completed = subprocess.run(
            command,
            cwd=self.cwd,
            input=json.dumps(input_payload) if input_payload is not None else None,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = _redact(completed.stderr.strip(), self.secrets_to_redact)
            if arguments[0] == "status" and re.search(r'^Environment "[^"]+" not found\.', detail):
                raise _PreviewEnvironmentPending(detail)
            raise PreviewControllerError(
                f"Railway command {arguments[0]!r} failed while controlling the preview: "
                f"{detail or 'no diagnostic returned'}."
            )
        if not completed.stdout.strip():
            return {}
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise PreviewControllerError(
                f"Railway command {arguments[0]!r} did not return the required JSON response."
            ) from error


def configure_preview(
    config: ControllerConfig,
    *,
    preview_token: str,
    railway: RailwayCall,
    publish: Publisher,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> PreviewDeployment:
    """Configure one PR environment and report ready only for its exact head SHA."""

    _validate_config(config, preview_token)
    deadline = clock() + config.timeout_seconds
    details_url = ""
    failure_already_published = False
    publish(state="building", origin="", remediation=None, details_url="")
    try:
        environment = _wait_for_environment(railway, config, deadline, sleep, clock)
        environment_id = _required_text(environment, "id", "Railway environment")
        _require_preview_service(environment, config)
        protected_state = _protected_service_state(railway, config)
        details_url = (
            f"https://railway.com/project/{config.project_id}/service/{config.service_id}"
            f"?environmentId={environment_id}"
        )
        baseline = _list_deployments(railway, config, environment_id)
        baseline_ids = {
            str(deployment.get("id")) for deployment in baseline if deployment.get("id")
        }
        variables = _environment_config(config, preview_token)
        railway(
            (
                "environment",
                "edit",
                "--project",
                config.project_id,
                "--environment",
                environment_id,
                "--message",
                f"Configure governed preview for PR #{config.pr_number}",
                "--json",
            ),
            variables,
        )
        redeploy = railway(
            (
                "redeploy",
                "--project",
                config.project_id,
                "--environment",
                environment_id,
                "--service",
                config.service_id,
                "--from-source",
                "--yes",
                "--json",
            ),
            None,
        )
        if not isinstance(redeploy, Mapping) or redeploy.get("success") is not True:
            raise PreviewControllerError(
                "Railway did not accept the requested preview source redeploy."
            )
        deployment = _wait_for_deployment(
            railway,
            config,
            environment_id,
            baseline_ids,
            deadline,
            sleep,
            clock,
        )
        deployment_id = _required_text(deployment, "id", "Railway deployment")
        domain = _wait_for_domain(
            railway,
            config,
            environment_id,
            deadline,
            sleep,
            clock,
        )
        _assert_protected_service_unchanged(railway, config, protected_state)
        origin = f"https://{domain}"
        report = publish(
            state="ready",
            origin=origin,
            remediation=None,
            details_url=details_url,
        )
        if report.get("state") != "ready" or report.get("ok") is not True:
            failure_already_published = True
            raise PreviewControllerError(
                "The exact-SHA deployment failed the required protected preview conformance checks."
            )
        return PreviewDeployment(environment_id, deployment_id, domain)
    except PreviewControllerError as error:
        failure = error
        if "protected_state" in locals():
            try:
                _assert_protected_service_unchanged(railway, config, protected_state)
            except PreviewControllerError as isolation_error:
                failure = isolation_error
        if not failure_already_published:
            publish(
                state="failed",
                origin="",
                remediation=str(failure),
                details_url=details_url,
            )
        if failure is error:
            raise
        raise failure from error


def _wait_for_environment(
    railway: RailwayCall,
    config: ControllerConfig,
    deadline: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> Mapping[str, Any]:
    environment_name = _preview_environment_name(config)
    while True:
        try:
            payload = railway(
                (
                    "status",
                    "--project",
                    config.project_id,
                    "--environment",
                    environment_name,
                    "--json",
                ),
                None,
            )
        except _PreviewEnvironmentPending:
            payload = {}
        for environment in _connection_records(payload, "environments"):
            if environment.get("name") == environment_name:
                return environment
        _sleep_or_timeout(
            deadline,
            config.poll_seconds,
            sleep,
            clock,
            f"Railway did not create an environment for PR #{config.pr_number}",
        )


def _preview_environment_name(config: ControllerConfig) -> str:
    repository_name = config.repository.rsplit("/", 1)[-1].lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", repository_name).strip("-")
    if not slug:
        raise PreviewControllerError(
            "GitHub repository name cannot identify its Railway PR environment"
        )
    return f"{slug}-pr-{config.pr_number}"


def _require_preview_service(environment: Mapping[str, Any], config: ControllerConfig) -> None:
    services = _connection_records(environment, "serviceInstances")
    if any(service.get("serviceId") == config.service_id for service in services):
        return
    raise PreviewControllerError(
        f"Railway PR environment {_preview_environment_name(config)!r} does not contain "
        f"service {config.service_id!r}; configure PR environments to duplicate the base service."
    )


def _protected_service_state(
    railway: RailwayCall, config: ControllerConfig
) -> _ProtectedServiceState:
    payload = railway(
        (
            "status",
            "--project",
            config.project_id,
            "--environment",
            "production",
            "--json",
        ),
        None,
    )
    production = next(
        (
            environment
            for environment in _connection_records(payload, "environments")
            if environment.get("name") == "production"
        ),
        None,
    )
    if production is None:
        raise PreviewControllerError(
            "Railway production isolation cannot be verified because the production environment is missing."
        )
    instance = next(
        (
            service
            for service in _connection_records(production, "serviceInstances")
            if service.get("serviceId") == config.service_id
        ),
        None,
    )
    if instance is None:
        raise PreviewControllerError(
            "Railway production isolation cannot be verified because the protected service is missing."
        )
    source = instance.get("source")
    latest = instance.get("latestDeployment")
    meta = latest.get("meta") if isinstance(latest, Mapping) else None
    state = _ProtectedServiceState(
        source_repo=str(source.get("repo") or "") if isinstance(source, Mapping) else "",
        source_image=str(source.get("image") or "") if isinstance(source, Mapping) else "",
        deployed_repo=str(meta.get("repo") or "") if isinstance(meta, Mapping) else "",
        deployed_branch=str(meta.get("branch") or "") if isinstance(meta, Mapping) else "",
        deployed_sha=str(meta.get("commitHash") or "") if isinstance(meta, Mapping) else "",
    )
    _assert_protected_service_safe(config, state)
    return state


def _assert_protected_service_unchanged(
    railway: RailwayCall,
    config: ControllerConfig,
    baseline: _ProtectedServiceState,
) -> None:
    current = _protected_service_state(railway, config)
    if (current.source_repo, current.source_image) != (
        baseline.source_repo,
        baseline.source_image,
    ):
        raise PreviewControllerError(
            "Railway preview control changed the protected production source; refusing to publish."
        )


def _assert_protected_service_safe(config: ControllerConfig, state: _ProtectedServiceState) -> None:
    if state.deployed_repo != config.repository or state.deployed_branch != "main":
        raise PreviewControllerError(
            "Railway production must remain deployed from the repository main branch during preview control."
        )
    if state.deployed_sha == config.expected_sha:
        raise PreviewControllerError(
            "Railway production received the pull-request head; refusing to publish the preview."
        )


def _wait_for_deployment(
    railway: RailwayCall,
    config: ControllerConfig,
    environment_id: str,
    baseline_ids: set[str],
    deadline: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> Mapping[str, Any]:
    terminal_candidate = ""
    while True:
        deployments = _list_deployments(railway, config, environment_id)
        fresh = [item for item in deployments if str(item.get("id") or "") not in baseline_ids]
        fresh.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        known_heads = [
            item
            for item in fresh
            if isinstance(item.get("meta"), Mapping) and item["meta"].get("commitHash")
        ]
        if known_heads and known_heads[0]["meta"].get("commitHash") != config.expected_sha:
            raise PreviewControllerError(
                "Railway redeployed a superseded head; refusing to publish its URL"
            )
        deployment = next(
            (
                item
                for item in fresh
                if isinstance(item.get("meta"), Mapping)
                and item["meta"].get("commitHash") == config.expected_sha
            ),
            None,
        )
        if deployment is not None:
            meta = deployment.get("meta")
            observed_sha = meta.get("commitHash") if isinstance(meta, Mapping) else None
            status = str(deployment.get("status") or "").upper()
            if status == "SUCCESS":
                if observed_sha != config.expected_sha:
                    raise PreviewControllerError(
                        "Railway success omitted the expected immutable head SHA"
                    )
                return deployment
            if status in _TERMINAL_FAILURES:
                deployment_id = str(deployment.get("id") or "")
                if terminal_candidate == deployment_id:
                    raise PreviewControllerError(f"Railway deployment ended in {status.lower()}")
                terminal_candidate = deployment_id
        _sleep_or_timeout(
            deadline,
            config.poll_seconds,
            sleep,
            clock,
            "Railway did not produce a successful exact-SHA deployment before timeout",
        )


def _list_deployments(
    railway: RailwayCall,
    config: ControllerConfig,
    environment_id: str,
) -> tuple[Mapping[str, Any], ...]:
    payload = railway(
        (
            "deployment",
            "list",
            "--project",
            config.project_id,
            "--environment",
            environment_id,
            "--service",
            config.service_id,
            "--json",
        ),
        None,
    )
    return _records(payload, "deployments")


def _wait_for_domain(
    railway: RailwayCall,
    config: ControllerConfig,
    environment_id: str,
    deadline: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> str:
    while True:
        payload = railway(
            (
                "domain",
                "list",
                "--project",
                config.project_id,
                "--environment",
                environment_id,
                "--service",
                config.service_id,
                "--json",
            ),
            None,
        )
        for domain in _records(payload, "domains"):
            value = domain.get("domain")
            if isinstance(value, str) and value and domain.get("syncStatus") in {None, "ACTIVE"}:
                return value
        _sleep_or_timeout(
            deadline,
            config.poll_seconds,
            sleep,
            clock,
            "Railway did not provision an active preview domain before timeout",
        )


def _environment_config(config: ControllerConfig, preview_token: str) -> dict[str, object]:
    values = {
        "FURA_PR_PREVIEW": ("1", False),
        "FURA_PREVIEW_PR_NUMBER": (str(config.pr_number), False),
        "FURA_PREVIEW_SHA": (config.expected_sha, False),
        "FURA_PREVIEW_REVIEW_URL": (config.review_url, False),
        "FURA_PREVIEW_AUTH_TOKEN": (preview_token, True),
    }
    return {
        "services": {
            config.service_id: {
                "variables": {
                    key: {"value": value, "isSealed": sealed}
                    for key, (value, sealed) in values.items()
                }
            }
        }
    }


def _records(payload: Any, key: str) -> tuple[Mapping[str, Any], ...]:
    values = payload.get(key, []) if isinstance(payload, Mapping) else payload
    if not isinstance(values, list):
        return ()
    return tuple(item for item in values if isinstance(item, Mapping))


def _connection_records(payload: Any, key: str) -> tuple[Mapping[str, Any], ...]:
    connection = payload.get(key) if isinstance(payload, Mapping) else None
    edges = connection.get("edges") if isinstance(connection, Mapping) else None
    if not isinstance(edges, list):
        return ()
    return tuple(
        node
        for edge in edges
        if isinstance(edge, Mapping) and isinstance((node := edge.get("node")), Mapping)
    )


def _required_text(payload: Mapping[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PreviewControllerError(f"{label} omitted {key}")
    return value


def _sleep_or_timeout(
    deadline: float,
    poll_seconds: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
    message: str,
) -> None:
    remaining = deadline - clock()
    if remaining <= 0:
        raise PreviewControllerError(message)
    sleep(min(poll_seconds, remaining))


def _validate_config(config: ControllerConfig, preview_token: str) -> None:
    if not _SHA.fullmatch(config.expected_sha):
        raise PreviewControllerError("Expected SHA must be a full lowercase Git commit SHA")
    if config.pr_number < 1:
        raise PreviewControllerError("PR number must be positive")
    if config.timeout_seconds <= 0 or config.poll_seconds <= 0:
        raise PreviewControllerError("Controller timeout and poll interval must be positive")
    if len(preview_token) < 32:
        raise PreviewControllerError("Generated preview token must contain at least 32 characters")


def _redact(value: str, secret_values: Sequence[str]) -> str:
    sanitized = value.replace("\n", " ")
    for secret in secret_values:
        sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = _SENSITIVE_DIAGNOSTIC_VALUE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _GITHUB_TOKEN.sub("[REDACTED]", sanitized)
    return sanitized[:500]


def _railway_command_allowed(arguments: Sequence[str]) -> bool:
    return any(tuple(arguments[: len(prefix)]) == prefix for prefix in _ALLOWED_RAILWAY_COMMANDS)


def _github_pull_request(repository: str, pr_number: int, token: str) -> Mapping[str, Any]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/pulls/{pr_number}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "furatena-railway-preview-controller/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not isinstance(payload, Mapping):
        raise PreviewControllerError("GitHub returned an invalid pull-request response")
    return payload


def _trusted_pull_request(payload: Mapping[str, Any], repository: str, expected_sha: str) -> str:
    head = payload.get("head")
    user = payload.get("user")
    if not isinstance(head, Mapping) or not isinstance(user, Mapping):
        raise PreviewControllerError("GitHub pull-request identity is incomplete")
    repo = head.get("repo")
    head_repo = repo.get("full_name") if isinstance(repo, Mapping) else None
    if head_repo != repository or user.get("type") == "Bot":
        raise PreviewControllerError(
            "Railway previews are limited to internal non-bot pull requests"
        )
    if payload.get("state") != "open":
        raise PreviewControllerError("Railway previews are limited to open pull requests")
    if head.get("sha") != expected_sha:
        raise PreviewControllerError("Requested SHA is no longer the current pull-request head")
    review_url = payload.get("html_url")
    if not isinstance(review_url, str) or not review_url.startswith("https://github.com/"):
        raise PreviewControllerError("GitHub pull request omitted its review URL")
    return review_url


def main() -> int:
    args = _parser().parse_args()
    railway_token = os.environ.get("RAILWAY_API_TOKEN", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not railway_token:
        raise SystemExit(
            "RAILWAY_API_TOKEN must provide a Railway workspace credential for preview control."
        )
    if not github_token:
        raise SystemExit(
            "GITHUB_TOKEN must provide a GitHub credential for trusted preview reporting."
        )
    try:
        pull_request = _github_pull_request(args.repository, args.pr_number, github_token)
        review_url = _trusted_pull_request(pull_request, args.repository, args.expected_sha)
        config = ControllerConfig(
            project_id=args.project_id,
            service_id=args.service_id,
            repository=args.repository,
            pr_number=args.pr_number,
            expected_sha=args.expected_sha,
            review_url=review_url,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
        preview_token = secrets.token_urlsafe(48)

        def publish(
            *,
            state: str,
            origin: str,
            remediation: str | None,
            details_url: str,
        ) -> Mapping[str, object]:
            return report_preview_state(
                state=state,
                origin=origin,
                expected_sha=config.expected_sha,
                remediation=remediation,
                preview_token=preview_token,
                publish=True,
                repository=config.repository,
                pr_number=config.pr_number,
                github_token=github_token,
                details_url=details_url,
            )

        with tempfile.TemporaryDirectory(prefix="furatena-railway-preview-") as directory:
            cli = RailwayCLI(cwd=Path(directory), secrets_to_redact=(preview_token,))
            deployment = configure_preview(
                config,
                preview_token=preview_token,
                railway=cli,
                publish=publish,
            )
        print(
            json.dumps(
                {
                    "state": "ready",
                    "environment_id": deployment.environment_id,
                    "deployment_id": deployment.deployment_id,
                    "domain": deployment.domain,
                    "expected_sha": config.expected_sha,
                },
                indent=2,
            )
        )
        return 0
    except (OSError, ValueError, PreviewControllerError) as error:
        raise SystemExit(
            f"Railway preview controller stopped before completing the requested environment: "
            f"{error}."
        ) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=1_200)
    parser.add_argument("--poll-seconds", type=float, default=10)
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
