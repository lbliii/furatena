"""Provider-neutral artifact promotion, recovery, and transport contracts."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from typing import cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from furatena.catalog.capability_policy import (
    Capability,
    CapabilityCachePolicy,
    CapabilityDecision,
    CapabilityEffect,
    CapabilityRequest,
    CapabilityScope,
    CapabilitySubject,
)
from furatena.catalog.publication_approvals import PublicationApprovalEvaluation
from furatena.catalog.publication_contracts import PublicationPlan
from furatena.catalog.publication_promotion import (
    JsonDirectoryPublicationPromotionStore,
    PromotionArtifact,
    PromotionDeployment,
    PromotionEnvironment,
    PromotionOperation,
    PromotionPreflight,
    PromotionStatus,
    PromotionStrategy,
    PromotionVerification,
    PublicationPromotionCommand,
    PublicationPromotionError,
    PublicationPromotionService,
    PublicationPromotionTransportAdapter,
)
from furatena.cli.main import run_command
from tests.publication_support import digest, sample_actor, sample_plan

NOW = "2030-01-02T03:04:05Z"


class ArtifactResolver:
    def __init__(self, *artifacts: PromotionArtifact) -> None:
        self.artifacts = {artifact.artifact_id: artifact for artifact in artifacts}
        self.calls: list[str] = []

    def promotion_identity(self, artifact_id: str) -> dict[str, object]:
        self.calls.append(artifact_id)
        return self.artifacts[artifact_id].to_dict()


class Backend:
    def __init__(self) -> None:
        self.state: dict[PromotionEnvironment, PromotionDeployment] = {}
        self.activations: list[tuple[PromotionEnvironment, str, PromotionStrategy]] = []
        self.restorations: list[tuple[PromotionEnvironment, str | None]] = []
        self.strategy = PromotionStrategy.ATOMIC
        self.preflight_flags = (True, True, True)
        self.failed_check: str | None = None
        self.fail_activate = False
        self.fail_after_activate = False
        self.fail_restore = False
        self.interrupt_verify = False

    def inspect(self, environment: PromotionEnvironment) -> PromotionDeployment | None:
        return self.state.get(environment)

    def preflight(
        self,
        environment: PromotionEnvironment,
        artifact: PromotionArtifact,
        current: PromotionDeployment | None,
        /,
    ) -> PromotionPreflight:
        compatible, environment_policy, current_state_matches = self.preflight_flags
        return PromotionPreflight(
            strategy=self.strategy,
            compatible=compatible,
            environment_policy=environment_policy,
            current_state_matches=current_state_matches,
            checked_at=NOW,
        )

    def activate(
        self,
        environment: PromotionEnvironment,
        artifact: PromotionArtifact,
        current: PromotionDeployment | None,
        operation_id: str,
        strategy: PromotionStrategy,
        /,
    ) -> PromotionDeployment:
        if self.fail_activate:
            raise RuntimeError("provider detail must stay private")
        deployed = PromotionDeployment(
            environment=environment,
            artifact_id=artifact.artifact_id,
            artifact_digest=artifact.artifact_digest,
            manifest_digest=artifact.manifest_digest,
            generation=(current.generation + 1 if current else 1),
            serving=True,
            deployed_at=NOW,
        )
        self.activations.append((environment, artifact.artifact_id, strategy))
        self.state[environment] = deployed
        if self.fail_after_activate:
            raise RuntimeError("provider activation acknowledgement was lost")
        return deployed

    def verify(
        self,
        environment: PromotionEnvironment,
        artifact: PromotionArtifact,
        smoke_checks: tuple[str, ...],
        operation_id: str,
        /,
    ) -> PromotionVerification:
        if self.interrupt_verify:
            self.interrupt_verify = False
            raise KeyboardInterrupt
        checks = {
            name: name != self.failed_check
            for name in ("readiness", "build_identity", "public_projection", "privacy")
        }
        smoke = {name: name != self.failed_check for name in smoke_checks}
        return PromotionVerification(
            artifact_digest=artifact.artifact_digest,
            manifest_digest=artifact.manifest_digest,
            checks=checks,
            smoke=smoke,
            evidence_digest=digest(f"verify:{environment}:{artifact.artifact_id}"),
            verified_at=NOW,
        )

    def restore(
        self,
        environment: PromotionEnvironment,
        previous: PromotionDeployment | None,
        operation_id: str,
        /,
    ) -> PromotionDeployment | None:
        if self.fail_restore:
            raise RuntimeError("restore unavailable")
        self.restorations.append((environment, previous.artifact_id if previous else None))
        if previous is None:
            self.state.pop(environment, None)
        else:
            self.state[environment] = previous
        return previous


class CrashAfterCurrentStore(JsonDirectoryPublicationPromotionStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.interrupt_terminal_write = True

    def write_operation(self, operation_id: str, value) -> None:
        if value.get("status") == PromotionStatus.SUCCEEDED.value and self.interrupt_terminal_write:
            self.interrupt_terminal_write = False
            raise KeyboardInterrupt
        super().write_operation(operation_id, value)


def _artifact(plan: PublicationPlan, label: str) -> PromotionArtifact:
    content_digest = digest(f"artifact:{label}")
    return PromotionArtifact(
        artifact_id=f"publication-artifact-{content_digest.removeprefix('sha256:')}",
        artifact_digest=content_digest,
        manifest_digest=digest(f"manifest:{label}"),
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        policy_version=plan.bindings.policy_version,
        policy_digest=plan.bindings.policy_digest,
        configuration_digest=plan.bindings.config_digest,
        presentation_digest=digest("presentation"),
        runtime_identity={"python_abi": "cp314t", "gil": "disabled"},
        fingerprints={"content_ir": digest(f"content:{label}")},
        public_projection_digest=digest(f"projection:{label}"),
    )


def _approval(plan: PublicationPlan) -> PublicationApprovalEvaluation:
    return PublicationApprovalEvaluation(
        plan_digest=plan.plan_digest,
        policy_version=plan.bindings.policy_version,
        policy_digest=plan.bindings.policy_digest,
        satisfied=True,
        override_applied=False,
        required_count=plan.approval_requirements.required_count,
        approval_record_ids=("approval-1",),
        waiver_record_ids=(),
        blocking_record_ids=(),
        invalidated_record_ids=(),
        unwaived_warning_ids=(),
        reason_codes=("requirements_satisfied",),
        evaluated_at=NOW,
    )


def _authorization(
    plan: PublicationPlan,
    environment: PromotionEnvironment,
    operation: PromotionOperation,
) -> tuple[CapabilityRequest, CapabilityDecision]:
    capability = (
        Capability.PROMOTE if operation == PromotionOperation.PROMOTE else Capability.ROLL_BACK
    )
    actor = sample_actor()
    request = CapabilityRequest(
        subject=CapabilitySubject(
            actor=actor.actor,
            identity_source=actor.identity_source,
            identity_fingerprint=digest("trusted-identity"),
            roles=actor.roles,
            teams=actor.teams,
            tenant=plan.identity.tenant,
            workspace=plan.identity.workspace,
            site=plan.identity.site,
            trusted=True,
        ),
        capability=capability,
        scope=CapabilityScope(
            tenant=plan.identity.tenant,
            workspace=plan.identity.workspace,
            site=plan.identity.site,
            environment=environment.value,
        ),
        correlation_id=plan.correlation_id,
        evaluated_at=NOW,
        plan_digest=plan.plan_digest,
    )
    decision = CapabilityDecision(
        allowed=True,
        capability=capability,
        reason_code="rule_allowed",
        reason="trusted deployment automation is allowed",
        remediation="No remediation is required.",
        matched_rule_id="promotion-automation",
        matched_effect=CapabilityEffect.ALLOW,
        policy_version=plan.bindings.policy_version,
        policy_digest=plan.bindings.policy_digest,
        approval_requirements=plan.approval_requirements,
        separation_rules=plan.approval_requirements.separation_rules,
        cacheability=CapabilityCachePolicy(),
        decision_scope_digest=digest(f"scope:{environment}:{operation}"),
        evaluated_at=NOW,
        plan_digest=plan.plan_digest,
    )
    return request, decision


def _command(
    plan: PublicationPlan,
    artifact: PromotionArtifact,
    source: PromotionEnvironment,
    destination: PromotionEnvironment,
    generation: int,
    *,
    key: str,
    operation: PromotionOperation = PromotionOperation.PROMOTE,
    reason: str = "",
    smoke_checks: tuple[str, ...] = ("homepage",),
) -> PublicationPromotionCommand:
    request, decision = _authorization(plan, destination, operation)
    return PublicationPromotionCommand(
        operation=operation,
        artifact_id=artifact.artifact_id,
        source_environment=source,
        destination_environment=destination,
        actor=sample_actor(),
        plan=plan,
        approval_evaluation=_approval(plan),
        capability_request=request,
        capability_decision=decision,
        idempotency_key=key,
        expected_destination_generation=generation,
        smoke_checks=smoke_checks,
        reason=reason,
    )


def _service(
    tmp_path: Path,
    backend: Backend,
    *artifacts: PromotionArtifact,
    approval_current=lambda plan, evaluation: True,
) -> PublicationPromotionService:
    return PublicationPromotionService(
        store=JsonDirectoryPublicationPromotionStore(tmp_path / "promotion"),
        artifact_resolver=ArtifactResolver(*artifacts),
        backend=backend,
        approval_current=approval_current,
        clock=lambda: NOW,
    )


def _promote_chain(
    service: PublicationPromotionService,
    plan: PublicationPlan,
    artifact: PromotionArtifact,
    *,
    generation: int,
    prefix: str,
) -> list[dict[str, object]]:
    routes = (
        (PromotionEnvironment.ARTIFACT, PromotionEnvironment.PREVIEW),
        (PromotionEnvironment.PREVIEW, PromotionEnvironment.STAGING),
        (PromotionEnvironment.STAGING, PromotionEnvironment.PRODUCTION),
    )
    return [
        service.promote(
            _command(
                plan,
                artifact,
                source,
                destination,
                generation,
                key=f"{prefix}-{destination.value}",
            )
        )
        for source, destination in routes
    ]


def test_promotes_one_digest_through_every_environment_without_rebuild(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "one")
    backend = Backend()
    service = _service(tmp_path, backend, artifact)

    receipts = _promote_chain(service, plan, artifact, generation=0, prefix="one")

    assert [item["status"] for item in receipts] == ["succeeded"] * 3
    assert [item[0] for item in backend.activations] == [
        PromotionEnvironment.PREVIEW,
        PromotionEnvironment.STAGING,
        PromotionEnvironment.PRODUCTION,
    ]
    assert {item[1] for item in backend.activations} == {artifact.artifact_id}
    for environment in (
        PromotionEnvironment.PREVIEW,
        PromotionEnvironment.STAGING,
        PromotionEnvironment.PRODUCTION,
    ):
        current = service.current(environment, projection="trusted")
        assert current is not None
        artifact_record = cast(dict[str, object], current["artifact"])
        assert artifact_record["artifact_digest"] == artifact.artifact_digest
        assert current["generation"] == 1


def test_replay_is_exact_and_conflicting_input_fails_closed(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "one")
    backend = Backend()
    service = _service(tmp_path, backend, artifact)
    command = _command(
        plan,
        artifact,
        PromotionEnvironment.ARTIFACT,
        PromotionEnvironment.PREVIEW,
        0,
        key="same-key",
    )
    first = service.promote(command)

    replay = service.promote(command)

    assert first["operation_id"] == replay["operation_id"]
    assert replay["replayed"] is True
    assert len(backend.activations) == 1
    with pytest.raises(PublicationPromotionError, match="bound to another request") as caught:
        service.promote(replace(command, smoke_checks=("different",)))
    assert caught.value.code == "idempotency_conflict"


def test_terminal_replay_does_not_require_expired_approval(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "one")
    backend = Backend()
    current = True
    service = _service(
        tmp_path,
        backend,
        artifact,
        approval_current=lambda plan, evaluation: current,
    )
    command = _command(
        plan,
        artifact,
        PromotionEnvironment.ARTIFACT,
        PromotionEnvironment.PREVIEW,
        0,
        key="approval-replay",
    )
    service.promote(command)
    current = False

    assert service.promote(command)["replayed"] is True


@pytest.mark.parametrize(
    "failed_check", ["readiness", "build_identity", "public_projection", "privacy", "homepage"]
)
def test_failed_serving_check_restores_last_known_good(tmp_path: Path, failed_check: str) -> None:
    plan = sample_plan()
    first = _artifact(plan, "first")
    second = _artifact(plan, "second")
    backend = Backend()
    service = _service(tmp_path, backend, first, second)
    service.promote(
        _command(
            plan,
            first,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            0,
            key="first",
        )
    )
    backend.failed_check = failed_check

    failed = service.promote(
        _command(
            plan,
            second,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            1,
            key=f"failure-{failed_check}",
        )
    )

    assert failed["status"] == PromotionStatus.FAILED.value
    assert backend.state[PromotionEnvironment.PREVIEW].artifact_id == first.artifact_id
    restoration = cast(dict[str, object], failed["restoration"])
    assert restoration["status"] == "restored"
    verification = cast(dict[str, object], failed["verification"])
    if failed_check == "homepage":
        smoke = cast(dict[str, object], verification["smoke"])
        assert smoke[failed_check] is False
    else:
        checks = cast(dict[str, object], verification["checks"])
        assert checks[failed_check] is False


def test_preflight_drift_is_a_durable_failed_operation(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "one")
    backend = Backend()
    service = _service(tmp_path, backend, artifact)
    command = _command(
        plan,
        artifact,
        PromotionEnvironment.ARTIFACT,
        PromotionEnvironment.PREVIEW,
        1,
        key="stale-generation",
    )

    failed = service.promote(command)

    assert failed["status"] == "failed"
    error = cast(dict[str, object], failed["error"])
    assert error["code"] == "destination_generation_stale"
    assert service.status(str(failed["operation_id"]))["status"] == "failed"
    assert len(service.history(PromotionEnvironment.PREVIEW)) == 1


def test_restart_after_activation_verifies_without_reactivation(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "one")
    backend = Backend()
    backend.interrupt_verify = True
    command = _command(
        plan,
        artifact,
        PromotionEnvironment.ARTIFACT,
        PromotionEnvironment.PREVIEW,
        0,
        key="interrupted",
    )
    service = _service(tmp_path, backend, artifact)
    with pytest.raises(KeyboardInterrupt):
        service.promote(command)
    assert len(backend.activations) == 1

    restarted = _service(tmp_path, backend, artifact)
    result = restarted.promote(command)

    assert result["status"] == "succeeded"
    assert len(backend.activations) == 1


def test_restart_after_current_pointer_does_not_advance_generation_twice(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "current-crash")
    backend = Backend()
    root = tmp_path / "promotion"
    command = _command(
        plan,
        artifact,
        PromotionEnvironment.ARTIFACT,
        PromotionEnvironment.PREVIEW,
        0,
        key="current-crash",
    )
    interrupted = PublicationPromotionService(
        store=CrashAfterCurrentStore(root),
        artifact_resolver=ArtifactResolver(artifact),
        backend=backend,
        approval_current=lambda plan, evaluation: True,
        clock=lambda: NOW,
    )
    with pytest.raises(KeyboardInterrupt):
        interrupted.promote(command)

    restarted = PublicationPromotionService(
        store=JsonDirectoryPublicationPromotionStore(root),
        artifact_resolver=ArtifactResolver(artifact),
        backend=backend,
        approval_current=lambda plan, evaluation: True,
        clock=lambda: NOW,
    )
    result = restarted.promote(command)

    assert result["status"] == "succeeded"
    current = restarted.current(PromotionEnvironment.PREVIEW)
    assert current is not None and current["generation"] == 1
    assert len(restarted.history(PromotionEnvironment.PREVIEW)) == 1


def test_terminal_replay_repairs_missing_immutable_history(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "history-repair")
    backend = Backend()
    service = _service(tmp_path, backend, artifact)
    command = _command(
        plan,
        artifact,
        PromotionEnvironment.ARTIFACT,
        PromotionEnvironment.PREVIEW,
        0,
        key="history-repair",
    )
    service.promote(command)
    history_file = next((service.store.history_root / "preview").glob("*.json"))
    history_file.unlink()

    replay = service.promote(command)

    assert replay["replayed"] is True
    assert len(service.history(PromotionEnvironment.PREVIEW)) == 1


def test_safe_canary_strategy_is_selected_by_provider_preflight(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "canary")
    backend = Backend()
    backend.strategy = PromotionStrategy.SAFE_CANARY
    service = _service(tmp_path, backend, artifact)

    result = service.promote(
        _command(
            plan,
            artifact,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            0,
            key="canary",
        )
    )

    assert result["status"] == "succeeded"
    assert backend.activations[0][2] == PromotionStrategy.SAFE_CANARY


def test_activation_failure_after_switch_restores_previous_artifact(tmp_path: Path) -> None:
    plan = sample_plan()
    first = _artifact(plan, "activation-first")
    second = _artifact(plan, "activation-second")
    backend = Backend()
    service = _service(tmp_path, backend, first, second)
    service.promote(
        _command(
            plan,
            first,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            0,
            key="activation-first",
        )
    )
    backend.fail_after_activate = True

    result = service.promote(
        _command(
            plan,
            second,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            1,
            key="activation-second",
        )
    )

    assert result["status"] == "failed"
    assert backend.state[PromotionEnvironment.PREVIEW].artifact_id == first.artifact_id


def test_preflight_rejection_never_calls_provider_activation(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "incompatible")
    backend = Backend()
    backend.preflight_flags = (False, True, True)
    service = _service(tmp_path, backend, artifact)

    result = service.promote(
        _command(
            plan,
            artifact,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            0,
            key="incompatible",
        )
    )

    assert result["status"] == "failed"
    assert not backend.activations


def test_failed_restoration_requires_reconciliation(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "one")
    backend = Backend()
    backend.failed_check = "privacy"
    backend.fail_restore = True
    service = _service(tmp_path, backend, artifact)

    result = service.promote(
        _command(
            plan,
            artifact,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            0,
            key="reconcile",
        )
    )

    assert result["status"] == PromotionStatus.RECONCILIATION_REQUIRED.value
    restoration = cast(dict[str, object], result["restoration"])
    assert restoration["status"] == "unknown"


def test_reasoned_authorized_rollback_uses_known_destination_history(tmp_path: Path) -> None:
    plan = sample_plan()
    first = _artifact(plan, "first")
    second = _artifact(plan, "second")
    backend = Backend()
    service = _service(tmp_path, backend, first, second)
    _promote_chain(service, plan, first, generation=0, prefix="first")
    _promote_chain(service, plan, second, generation=1, prefix="second")

    result = service.rollback(
        _command(
            plan,
            first,
            PromotionEnvironment.PRODUCTION,
            PromotionEnvironment.PRODUCTION,
            2,
            key="rollback-first",
            operation=PromotionOperation.ROLLBACK,
            reason="Second artifact failed an external post-release signal.",
        )
    )

    assert result["status"] == "succeeded"
    reason = result["reason"]
    assert isinstance(reason, str) and reason.startswith("Second artifact")
    current = service.current(PromotionEnvironment.PRODUCTION, projection="trusted")
    assert current is not None
    artifact_record = cast(dict[str, object], current["artifact"])
    last_known_good = cast(dict[str, object], current["last_known_good"])
    assert artifact_record["artifact_id"] == first.artifact_id
    assert last_known_good["artifact_id"] == second.artifact_id


def test_transport_reads_have_parity_and_only_automation_can_mutate(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "one")
    backend = Backend()
    service = _service(tmp_path, backend, artifact)
    command = _command(
        plan,
        artifact,
        PromotionEnvironment.ARTIFACT,
        PromotionEnvironment.PREVIEW,
        0,
        key="transport",
    )
    browser = PublicationPromotionTransportAdapter("browser", service)
    cli = PublicationPromotionTransportAdapter("cli", service)
    mcp = PublicationPromotionTransportAdapter("mcp", service)
    automation = PublicationPromotionTransportAdapter("automation", service, deploy_authority=True)

    with pytest.raises(PublicationPromotionError) as caught:
        browser.promote(command)
    assert caught.value.code == "transport_read_only"
    receipt = automation.promote(command)
    projections = [adapter.current(PromotionEnvironment.PREVIEW) for adapter in (browser, cli, mcp)]

    assert projections[0] == projections[1] == projections[2]
    assert "actor" not in browser.status(str(receipt["operation_id"]))
    assert "idempotency_key_digest" not in browser.status(str(receipt["operation_id"]))


def test_invalid_route_and_untrusted_authority_fail_before_provider_access(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "one")
    request, decision = _authorization(
        plan, PromotionEnvironment.PRODUCTION, PromotionOperation.PROMOTE
    )
    with pytest.raises(ValueError, match="follow"):
        PublicationPromotionCommand(
            operation=PromotionOperation.PROMOTE,
            artifact_id=artifact.artifact_id,
            source_environment=PromotionEnvironment.ARTIFACT,
            destination_environment=PromotionEnvironment.PRODUCTION,
            actor=sample_actor(),
            plan=plan,
            approval_evaluation=_approval(plan),
            capability_request=request,
            capability_decision=decision,
            idempotency_key="skip",
            expected_destination_generation=0,
        )
    command = _command(
        plan,
        artifact,
        PromotionEnvironment.ARTIFACT,
        PromotionEnvironment.PREVIEW,
        0,
        key="untrusted",
    )
    untrusted_request = replace(
        command.capability_request,
        subject=replace(command.capability_request.subject, trusted=False),
    )
    with pytest.raises(ValueError, match="trusted actor"):
        replace(
            command,
            capability_request=untrusted_request,
        )


def test_durable_records_validate_against_versioned_schemas(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "schema")
    backend = Backend()
    service = _service(tmp_path, backend, artifact)
    receipt = service.promote(
        _command(
            plan,
            artifact,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            0,
            key="schema",
        )
    )
    current = service.current(PromotionEnvironment.PREVIEW, projection="trusted")
    request_path = next(service.store.requests.glob("*.json"))
    request = json.loads(request_path.read_text(encoding="utf-8"))
    root = files("furatena.catalog").joinpath("schemas/publication-promotion/v1")
    documents = {
        name: json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        for name in (
            "common.schema.json",
            "request.schema.json",
            "receipt.schema.json",
            "current.schema.json",
        )
    }
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents.values()
    )
    records = {
        "request.schema.json": request,
        "receipt.schema.json": {key: value for key, value in receipt.items() if key != "replayed"},
        "current.schema.json": current,
    }

    for name, record in records.items():
        Draft202012Validator(
            documents[name], registry=registry, format_checker=FormatChecker()
        ).validate(record)

    with pytest.raises(ValidationError):
        Draft202012Validator(
            documents["receipt.schema.json"],
            registry=registry,
            format_checker=FormatChecker(),
        ).validate({**records["receipt.schema.json"], "unexpected": True})


def test_cli_exposes_only_display_current_history_and_status(tmp_path: Path) -> None:
    plan = sample_plan()
    artifact = _artifact(plan, "cli")
    backend = Backend()
    service = _service(tmp_path, backend, artifact)
    receipt = service.promote(
        _command(
            plan,
            artifact,
            PromotionEnvironment.ARTIFACT,
            PromotionEnvironment.PREVIEW,
            0,
            key="cli",
        )
    )
    root = str(service.store.root)

    current = run_command(
        ["promotion", "current", "--environment", "preview", "--state-root", root]
    )
    history = run_command(
        ["promotion", "history", "--environment", "preview", "--state-root", root]
    )
    status = run_command(
        [
            "promotion",
            "status",
            "--operation-id",
            str(receipt["operation_id"]),
            "--state-root",
            root,
        ]
    )

    assert current is not None and current.ok
    assert history is not None and history.data["count"] == 1
    assert status is not None and status.command == "promotion status"
    assert "actor" not in status.data["operation"]
    assert "idempotency_key_digest" not in status.data["operation"]
    assert not any(action in {"promote", "rollback"} for action in _promotion_cli_actions())


def _promotion_cli_actions() -> tuple[str, ...]:
    from furatena.cli.main import _build_parser

    parser = _build_parser()
    top = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction) and action.dest == "command"
    )
    promotion = cast(argparse.ArgumentParser, top.choices["promotion"])
    nested = next(
        action
        for action in promotion._actions
        if isinstance(action, argparse._SubParsersAction) and action.dest == "promotion_command"
    )
    return tuple(nested.choices)
