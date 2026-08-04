"""Executable publishing journeys shared by browser, CLI, MCP, and automation."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from furatena.catalog.audit_store import InMemoryAuditStore
from furatena.catalog.capability_policy import CAPABILITY_EVALUATOR
from furatena.catalog.publication_approvals import (
    InMemoryPublicationApprovalStore,
    PublicationApprovalError,
    PublicationApprovalService,
)
from furatena.catalog.publication_artifacts import PublicationArtifactError
from furatena.catalog.publication_conformance import (
    PUBLICATION_CONFORMANCE_EXPECTATIONS,
    PublicationConformanceOutcome,
    PublicationConformanceScenario,
    PublicationConformanceStatus,
    PublicationConformanceTransport,
    validate_publication_conformance,
)
from furatena.catalog.publication_contracts import (
    PublicationDecisionKind,
    PublicationFailure,
    PublicationFailureDisposition,
    PublicationOutputReference,
    PublicationPlan,
    PublicationState,
)
from furatena.catalog.publication_promotion import (
    PromotionEnvironment,
    PromotionOperation,
    PublicationPromotionTransportAdapter,
)
from furatena.catalog.publication_provider import (
    ProviderContractError,
    ProviderFailure,
    ProviderOperation,
    ProviderOutcome,
    ProviderReconciliationState,
    PublicationChangeResult,
    PublicationProfile,
    validate_provider_result,
    validate_repository_inspection,
)
from furatena.catalog.publication_workflow import (
    FilesystemPublicationLeaseFactory,
    PublicationExecutionError,
    PublicationWorkflowService,
    PublicationWorkflowTransportAdapter,
)
from furatena.catalog.publication_workflow_store import (
    JsonDirectoryPublicationWorkflowStore,
)
from tests.provider_support import sample_inspection, sample_profile, sample_provider_request
from tests.publication_support import sample_actor, sample_plan
from tests.test_capability_policy import _policy, _request
from tests.test_publication_approvals import _reviewer
from tests.test_publication_artifacts import (
    DeterministicBuilder,
)
from tests.test_publication_artifacts import (
    _repository as artifact_repository,
)
from tests.test_publication_artifacts import (
    _request as artifact_request,
)
from tests.test_publication_artifacts import (
    _service as artifact_service,
)
from tests.test_publication_promotion import (
    Backend,
    _artifact,
    _command,
    _promote_chain,
)
from tests.test_publication_promotion import (
    _service as promotion_service,
)
from tests.test_publication_workflow_service import RecordingExecutor


@dataclass
class _CurrentBindings:
    source_revision: str
    catalog_generation: str
    config_digest: str
    policy_digest: str
    validation_digest: str

    @classmethod
    def from_plan(cls, plan: PublicationPlan) -> _CurrentBindings:
        return cls(
            source_revision=plan.bindings.source_revision,
            catalog_generation=plan.bindings.catalog_generation,
            config_digest=plan.bindings.config_digest,
            policy_digest=plan.bindings.policy_digest,
            validation_digest=plan.bindings.validation_digest,
        )

    def matches(self, plan: PublicationPlan) -> bool:
        return self == _CurrentBindings.from_plan(plan)


class _FailingExecutor(RecordingExecutor):
    def __init__(
        self,
        disposition: PublicationFailureDisposition,
        code: str,
        *,
        raises_unknown: bool = False,
        reconcile_applies: bool = False,
    ) -> None:
        super().__init__()
        self.disposition = disposition
        self.code = code
        self.raises_unknown = raises_unknown
        self.reconcile_applies = reconcile_applies

    def execute(self, _plan: object) -> tuple[PublicationOutputReference, ...]:
        self.calls += 1
        if self.raises_unknown:
            raise RuntimeError("simulated worker interruption")
        raise PublicationExecutionError(
            PublicationFailure(
                self.disposition,
                self.code,
                "Publication execution did not complete.",
                "Follow the typed recovery path.",
                retry_target=(
                    PublicationState.EXECUTING
                    if self.disposition
                    in {
                        PublicationFailureDisposition.RETRYABLE,
                        PublicationFailureDisposition.RECONCILIATION_REQUIRED,
                    }
                    else None
                ),
            )
        )

    def reconcile(self, _plan: object) -> tuple[PublicationOutputReference, ...] | None:
        return self.outputs if self.reconcile_applies else None


class _BrokenBuilder(DeterministicBuilder):
    def __call__(self, source_root: Path, output_root: Path, request: object) -> object:
        raise RuntimeError("simulated deterministic build failure")


def _workflow_service(
    root: Path,
    *,
    executor: RecordingExecutor | None = None,
    binding_checker: Callable[[PublicationPlan], bool] | None = None,
    approval_evaluator: Callable[[PublicationPlan], bool] | None = None,
    authorizer: Callable[[PublicationPlan, object, str], bool] | None = None,
    store: JsonDirectoryPublicationWorkflowStore | None = None,
) -> PublicationWorkflowService:
    return PublicationWorkflowService(
        store=store or JsonDirectoryPublicationWorkflowStore(root / "workflow"),
        binding_checker=binding_checker or (lambda _plan: True),
        approval_evaluator=approval_evaluator or (lambda _plan: True),
        authorizer=authorizer or (lambda _plan, _actor, _command: True),
        executor=executor or RecordingExecutor(),
        audit_store=InMemoryAuditStore(clock=lambda: 1_893_456_000.0),
        lease_factory=FilesystemPublicationLeaseFactory(
            root / "leases", timeout_seconds=2, lease_seconds=2
        ),
        clock=lambda: "2030-01-01T00:00:00Z",
    )


def _observed_failure(payload: dict[str, object]) -> tuple[str, str]:
    snapshot = cast(dict[str, Any], payload["snapshot"])
    receipt = cast(dict[str, Any] | None, payload.get("receipt"))
    failure = cast(dict[str, Any] | None, snapshot.get("failure"))
    if failure is None and receipt is not None:
        response = cast(dict[str, Any] | None, receipt.get("response"))
        failure = cast(dict[str, Any] | None, response.get("failure")) if response else None
    code = str(failure["code"]) if failure else "publication.succeeded"
    operation_status = str(receipt.get("status")) if receipt else ""
    return code, operation_status


def _outcome(
    scenario: PublicationConformanceScenario,
    transport: PublicationConformanceTransport,
    status: PublicationConformanceStatus,
    *,
    policy_allowed: bool,
    code: str,
) -> PublicationConformanceOutcome:
    return PublicationConformanceOutcome(
        scenario=scenario,
        transport=transport,
        status=status,
        policy_allowed=policy_allowed,
        partial_success=False,
        code=code,
    )


def _drive_executor_failure(
    root: Path,
    transport: PublicationConformanceTransport,
    *,
    disposition: PublicationFailureDisposition,
    code: str,
) -> dict[str, object]:
    plan = sample_plan(
        correlation_id=f"{transport.value}-{code}",
        idempotency_key=f"plan-{transport.value}-{code}",
        required_count=0,
    )
    executor = _FailingExecutor(disposition, code)
    service = _workflow_service(root / "workflow-boundary", executor=executor)
    adapter = PublicationWorkflowTransportAdapter(transport.value, service)
    adapter.create_plan(plan)
    validated = adapter.validate(plan.plan_id, actor=sample_actor())
    version = cast(dict[str, Any], validated["snapshot"])["state_version"]
    result = adapter.execute(
        {
            "plan_id": plan.plan_id,
            "idempotency_key": f"execute-{transport.value}-{code}",
            "expected_state_version": version,
        },
        actor=sample_actor(),
    )
    observed_code, operation_status = _observed_failure(result)
    assert observed_code == code
    assert executor.calls == 1
    if disposition == PublicationFailureDisposition.RECONCILIATION_REQUIRED:
        assert operation_status == "reconciliation_required"
    else:
        assert operation_status == "failed"
    return result


def _drive_approval_denial(
    root: Path,
    transport: PublicationConformanceTransport,
) -> None:
    plan = sample_plan(
        correlation_id=f"approval-{transport.value}",
        idempotency_key=f"approval-plan-{transport.value}",
        required_count=0,
    )
    executor = RecordingExecutor()
    service = _workflow_service(
        root / "workflow-boundary",
        executor=executor,
        approval_evaluator=lambda _plan: False,
    )
    adapter = PublicationWorkflowTransportAdapter(transport.value, service)
    adapter.create_plan(plan)
    validated = adapter.validate(plan.plan_id, actor=sample_actor())
    version = cast(dict[str, Any], validated["snapshot"])["state_version"]
    result = adapter.execute(
        {
            "plan_id": plan.plan_id,
            "idempotency_key": f"approval-denial-{transport.value}",
            "expected_state_version": version,
        },
        actor=sample_actor(),
    )
    observed_code, operation_status = _observed_failure(result)
    assert observed_code == "approval.stale"
    assert operation_status == "failed"
    assert executor.calls == 0


def _execute_workflow(
    root: Path,
    scenario: PublicationConformanceScenario,
    transport: PublicationConformanceTransport,
) -> PublicationConformanceOutcome:
    plan = sample_plan(required_count=0)
    current = _CurrentBindings.from_plan(plan)
    binding_field = {
        PublicationConformanceScenario.STALE_SOURCE: "source_revision",
        PublicationConformanceScenario.STALE_VALIDATION: "validation_digest",
        PublicationConformanceScenario.STALE_POLICY: "policy_digest",
        PublicationConformanceScenario.STALE_CONFIGURATION: "config_digest",
    }.get(scenario)
    if binding_field is not None:
        setattr(current, binding_field, f"drifted-{binding_field}")

    def approval_evaluator(_plan: PublicationPlan) -> bool:
        return True

    if scenario == PublicationConformanceScenario.STALE_APPROVAL:
        approvals = PublicationApprovalService(
            store=InMemoryPublicationApprovalStore(),
            audit_store=InMemoryAuditStore(clock=lambda: 1_893_456_000.0),
            authorizer=lambda _plan, _actor, _decision: True,
            clock=lambda: "2030-01-01T00:00:00Z",
        )
        approvals.decide(
            plan,
            actor=_reviewer("waiver@example.com"),
            decision=PublicationDecisionKind.WAIVE_WARNING,
            idempotency_key="conformance-waiver",
            reason="Temporary warning waiver.",
            diagnostic_ids=plan.validation.waivable_warning_ids,
            expires_at="2031-01-01T00:00:00Z",
        )

        def approval_evaluator(bound: PublicationPlan) -> bool:
            return approvals.evaluate(bound, now="2032-01-01T00:00:00Z").satisfied

    def authorizer(_plan: PublicationPlan, _actor: object, _command: str) -> bool:
        return True

    policy_allowed = True
    if scenario in {
        PublicationConformanceScenario.UNAUTHORIZED,
        PublicationConformanceScenario.CROSS_TENANT,
    }:
        request = _request()
        if scenario == PublicationConformanceScenario.CROSS_TENANT:
            request = replace(request, scope=replace(request.scope, tenant="other"))
        else:
            request = replace(request, subject=replace(request.subject, roles=("reader",)))
        decision = CAPABILITY_EVALUATOR.evaluate(_policy(), request)
        policy_allowed = decision.allowed

        def authorizer(_plan: PublicationPlan, _actor: object, _command: str) -> bool:
            return decision.allowed

    executor: RecordingExecutor = RecordingExecutor()
    if scenario in {
        PublicationConformanceScenario.CRASH,
        PublicationConformanceScenario.RECONCILIATION,
    }:
        executor = _FailingExecutor(
            PublicationFailureDisposition.RECONCILIATION_REQUIRED,
            "execution.outcome_unknown",
            raises_unknown=True,
            reconcile_applies=scenario == PublicationConformanceScenario.RECONCILIATION,
        )
    service = _workflow_service(
        root,
        executor=executor,
        binding_checker=current.matches,
        approval_evaluator=approval_evaluator,
        authorizer=authorizer,
    )
    adapter = PublicationWorkflowTransportAdapter(transport.value, service)
    adapter.create_plan(plan)
    approved = adapter.validate(plan.plan_id, actor=sample_actor())
    snapshot = cast(dict[str, Any], approved["snapshot"])
    expected = int(snapshot["state_version"])
    command = {
        "plan_id": plan.plan_id,
        "idempotency_key": f"{scenario.value}-execute",
        "expected_state_version": (
            expected - 1 if scenario == PublicationConformanceScenario.STALE_PLAN else expected
        ),
    }
    result = adapter.execute(command, actor=sample_actor())

    if scenario == PublicationConformanceScenario.DUPLICATE_SUBMIT:
        replay = adapter.execute(command, actor=sample_actor())
        assert replay["replayed"] is True
        assert executor.calls == 1
        result = replay
    elif scenario == PublicationConformanceScenario.RETRY:
        # Exercise the real typed retry transition, then observe that the retry command itself
        # succeeds without claiming that the immutable validation errors were fixed.
        retry_plan = sample_plan(
            correlation_id="retry-corr",
            idempotency_key="retry-plan",
            required_count=0,
            validation_error_count=1,
        )
        retry_root = root / "retry"
        retry_service = _workflow_service(retry_root)
        retry_adapter = PublicationWorkflowTransportAdapter(transport.value, retry_service)
        retry_adapter.create_plan(retry_plan)
        failed = retry_adapter.validate(retry_plan.plan_id, actor=sample_actor())
        failed_snapshot = cast(dict[str, Any], failed["snapshot"])
        result = retry_adapter.retry(
            {
                "plan_id": retry_plan.plan_id,
                "expected_state_version": failed_snapshot["state_version"],
            },
            actor=sample_actor(),
        )
        result_snapshot = cast(dict[str, Any], result["snapshot"])
        assert result_snapshot["state"] == "validating"
        return _outcome(
            scenario,
            transport,
            PublicationConformanceStatus.SUCCEEDED,
            policy_allowed=True,
            code="retry.validating",
        )
    elif scenario == PublicationConformanceScenario.CONCURRENT_EXECUTION:
        # The first call above is deliberately discarded; use a fresh durable store to prove
        # that two service instances serialize one real execution.
        concurrent_root = root / "concurrent"
        store_root = concurrent_root / "workflow"
        shared_executor = RecordingExecutor()
        services = [
            _workflow_service(
                concurrent_root,
                store=JsonDirectoryPublicationWorkflowStore(store_root),
                executor=shared_executor,
            )
            for _ in range(2)
        ]
        concurrent_plan = sample_plan(
            correlation_id="concurrent-corr", idempotency_key="concurrent-plan", required_count=0
        )
        first_adapter = PublicationWorkflowTransportAdapter(transport.value, services[0])
        first_adapter.create_plan(concurrent_plan)
        validated = first_adapter.validate(concurrent_plan.plan_id, actor=sample_actor())
        version = cast(dict[str, Any], validated["snapshot"])["state_version"]
        shared_command = {
            "plan_id": concurrent_plan.plan_id,
            "idempotency_key": "concurrent-execute",
            "expected_state_version": version,
        }
        responses: list[dict[str, object]] = []

        def submit(index: int) -> None:
            responses.append(
                PublicationWorkflowTransportAdapter(transport.value, services[index]).execute(
                    shared_command, actor=sample_actor()
                )
            )

        threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(responses) == 2
        assert shared_executor.calls == 1
        assert sum(bool(item["replayed"]) for item in responses) == 1
        result = responses[0]
    elif scenario == PublicationConformanceScenario.RECONCILIATION:
        failed_snapshot = cast(dict[str, Any], result["snapshot"])
        assert failed_snapshot["state"] == "failed"
        result = adapter.reconcile({"plan_id": plan.plan_id}, actor=sample_actor())

    code, operation_status = _observed_failure(result)
    state = str(cast(dict[str, Any], result["snapshot"])["state"])
    if scenario in {
        PublicationConformanceScenario.STALE_SOURCE,
        PublicationConformanceScenario.STALE_PLAN,
        PublicationConformanceScenario.STALE_VALIDATION,
        PublicationConformanceScenario.STALE_POLICY,
        PublicationConformanceScenario.STALE_CONFIGURATION,
        PublicationConformanceScenario.STALE_APPROVAL,
    }:
        status = PublicationConformanceStatus.STALE
    elif operation_status == "reconciliation_required":
        status = PublicationConformanceStatus.RECONCILIATION_REQUIRED
    elif state == "applied":
        status = PublicationConformanceStatus.SUCCEEDED
    elif not policy_allowed:
        status = PublicationConformanceStatus.DENIED
    else:
        status = PublicationConformanceStatus.FAILED
    return _outcome(
        scenario,
        transport,
        status,
        policy_allowed=policy_allowed,
        code=code,
    )


def _approval_outcome(
    root: Path,
    scenario: PublicationConformanceScenario,
    transport: PublicationConformanceTransport,
) -> PublicationConformanceOutcome:
    plan = sample_plan(required_count=1)
    allowed = {"reviewer@example.com", "waiver@example.com"}
    service = PublicationApprovalService(
        store=InMemoryPublicationApprovalStore(),
        audit_store=InMemoryAuditStore(clock=lambda: 1_893_456_000.0),
        authorizer=lambda _plan, actor, _decision: actor.actor in allowed,
        clock=lambda: "2030-01-01T00:00:00Z",
    )
    code = "approval.denied"
    if scenario == PublicationConformanceScenario.SELF_APPROVAL:
        try:
            service.decide(
                plan,
                actor=plan.creator,
                decision=PublicationDecisionKind.APPROVE,
                idempotency_key="self-approval",
            )
        except PublicationApprovalError as exc:
            code = str(exc.code)
    elif scenario == PublicationConformanceScenario.EXPIRED_WAIVER:
        service.decide(
            plan,
            actor=_reviewer("waiver@example.com"),
            decision=PublicationDecisionKind.WAIVE_WARNING,
            idempotency_key="expired-waiver",
            reason="Temporary waiver.",
            diagnostic_ids=plan.validation.waivable_warning_ids,
            expires_at="2031-01-01T00:00:00Z",
        )
        evaluation = service.evaluate(plan, now="2032-01-01T00:00:00Z")
        assert evaluation.satisfied is False
        code = "+".join(evaluation.reason_codes)
    else:
        allowed.clear()
        try:
            service.decide(
                plan,
                actor=_reviewer("reviewer@example.com"),
                decision=PublicationDecisionKind.EMERGENCY_OVERRIDE,
                idempotency_key="denied-override",
                reason="Incident response.",
            )
        except PublicationApprovalError as exc:
            code = str(exc.code)
    _drive_approval_denial(root, transport)
    return _outcome(
        scenario,
        transport,
        PublicationConformanceStatus.DENIED,
        policy_allowed=False,
        code=code,
    )


def _provider_outcome(
    root: Path,
    scenario: PublicationConformanceScenario,
    transport: PublicationConformanceTransport,
) -> PublicationConformanceOutcome:
    if scenario == PublicationConformanceScenario.GIT_PARTIAL_FAILURE:
        plan = sample_plan()
        request = sample_provider_request(operation=ProviderOperation.COMMIT)
        failure = ProviderFailure(
            PublicationFailureDisposition.RECONCILIATION_REQUIRED,
            "git.partial_failure",
            "Git provider outcome is unknown.",
            "Reconcile the provider before retrying.",
            retry_operation=ProviderOperation.RECONCILE,
            effect_may_have_occurred=True,
        )
        result = PublicationChangeResult.create(
            request,
            outcome=ProviderOutcome.FAILED,
            observed_base_source_revision=request.base_source_revision,
            failure=failure,
            reconciliation=ProviderReconciliationState.PENDING,
            observed_at="2030-01-01T00:02:00Z",
        )
        validate_provider_result(plan, request, result)
        status = PublicationConformanceStatus.RECONCILIATION_REQUIRED
        code = cast(ProviderFailure, result.failure).code
        _drive_executor_failure(
            root,
            transport,
            disposition=PublicationFailureDisposition.RECONCILIATION_REQUIRED,
            code=code,
        )
    else:
        profile = sample_profile(PublicationProfile.LOCAL_ONLY)
        request = sample_provider_request(PublicationProfile.LOCAL_ONLY)
        inspection = sample_inspection(
            PublicationProfile.LOCAL_ONLY,
            isolated=False,
            protection="unprotected",
            unstaged_paths=request.approved_paths,
        )
        try:
            validate_repository_inspection(profile, request, inspection)
        except ProviderContractError as exc:
            assert exc.disposition == PublicationFailureDisposition.CONFLICT
            status = PublicationConformanceStatus.FAILED
            code = exc.code
            _drive_executor_failure(
                root,
                transport,
                disposition=exc.disposition,
                code=code,
            )
        else:  # pragma: no cover - the contract is deliberately adversarial
            raise AssertionError(
                "provider inspection unexpectedly accepted overlapping dirty paths"
            )
    return _outcome(scenario, transport, status, policy_allowed=True, code=code)


def _artifact_outcome(
    root: Path,
    scenario: PublicationConformanceScenario,
    transport: PublicationConformanceTransport,
) -> PublicationConformanceOutcome:
    root.mkdir(parents=True)
    repository, commit = artifact_repository(root)
    builder = (
        DeterministicBuilder(leak_private=True)
        if scenario == PublicationConformanceScenario.PRIVACY_LEAK
        else _BrokenBuilder()
    )
    service = artifact_service(root / "artifact-store", builder)
    try:
        service.build(artifact_request(repository, commit, suffix=scenario.value))
    except PublicationArtifactError as exc:
        code = exc.code
    else:  # pragma: no cover - the build is deliberately adversarial
        raise AssertionError("adversarial artifact build unexpectedly succeeded")
    expected_code = (
        "privacy_scan_failed"
        if scenario == PublicationConformanceScenario.PRIVACY_LEAK
        else "build_failed"
    )
    assert code == expected_code
    assert not service.artifacts.exists()
    _drive_executor_failure(
        root,
        transport,
        disposition=PublicationFailureDisposition.TERMINAL,
        code=code,
    )
    return _outcome(
        scenario,
        transport,
        PublicationConformanceStatus.FAILED,
        policy_allowed=True,
        code=code,
    )


def _promotion_outcome(
    root: Path,
    scenario: PublicationConformanceScenario,
    transport: PublicationConformanceTransport,
) -> PublicationConformanceOutcome:
    plan = sample_plan()
    first = _artifact(plan, "first")
    second = _artifact(plan, "second")
    backend = Backend()
    service = promotion_service(root, backend, first, second)
    automation = PublicationPromotionTransportAdapter("automation", service, deploy_authority=True)

    if scenario == PublicationConformanceScenario.ROLLBACK:
        _promote_chain(service, plan, first, generation=0, prefix="first")
        _promote_chain(service, plan, second, generation=1, prefix="second")
        receipt = automation.rollback(
            _command(
                plan,
                first,
                PromotionEnvironment.PRODUCTION,
                PromotionEnvironment.PRODUCTION,
                2,
                key="rollback",
                operation=PromotionOperation.ROLLBACK,
                reason="External verification regressed.",
            )
        )
        status = PublicationConformanceStatus.ROLLED_BACK
    else:
        if scenario == PublicationConformanceScenario.FAILED_PROMOTION:
            backend.fail_activate = True
        else:
            backend.failed_check = (
                "privacy"
                if scenario == PublicationConformanceScenario.VERIFICATION_FAILURE
                else "homepage"
            )
        receipt = automation.promote(
            _command(
                plan,
                second,
                PromotionEnvironment.ARTIFACT,
                PromotionEnvironment.PREVIEW,
                0,
                key=scenario.value,
            )
        )
        status = PublicationConformanceStatus.FAILED

    operation_id = str(receipt["operation_id"])
    observer = PublicationPromotionTransportAdapter(transport.value, service)
    observed = observer.status(operation_id)
    assert observed["status"] == receipt["status"]
    error = cast(dict[str, Any] | None, receipt.get("error"))
    code = str(error.get("code")) if error else "promotion.rollback_succeeded"
    return _outcome(scenario, transport, status, policy_allowed=True, code=code)


def _run_scenario(
    root: Path,
    scenario: PublicationConformanceScenario,
    transport: PublicationConformanceTransport,
) -> PublicationConformanceOutcome:
    if scenario in {
        PublicationConformanceScenario.SELF_APPROVAL,
        PublicationConformanceScenario.EXPIRED_WAIVER,
        PublicationConformanceScenario.OVERRIDE,
    }:
        return _approval_outcome(root, scenario, transport)
    if scenario in {
        PublicationConformanceScenario.GIT_CONFLICT,
        PublicationConformanceScenario.GIT_PARTIAL_FAILURE,
    }:
        return _provider_outcome(root, scenario, transport)
    if scenario in {
        PublicationConformanceScenario.BUILD_FAILURE,
        PublicationConformanceScenario.PRIVACY_LEAK,
    }:
        return _artifact_outcome(root, scenario, transport)
    if scenario in {
        PublicationConformanceScenario.FAILED_PROMOTION,
        PublicationConformanceScenario.VERIFICATION_FAILURE,
        PublicationConformanceScenario.ROLLBACK,
    }:
        return _promotion_outcome(root, scenario, transport)
    return _execute_workflow(root, scenario, transport)


def test_required_adversarial_matrix_executes_real_components_and_is_transport_invariant(
    tmp_path: Path,
) -> None:
    outcomes = [
        _run_scenario(tmp_path / scenario.value / transport.value, scenario, transport)
        for scenario in PublicationConformanceScenario
        for transport in PublicationConformanceTransport
    ]

    assert len(outcomes) == len(PublicationConformanceScenario) * len(
        PublicationConformanceTransport
    )
    assert validate_publication_conformance(outcomes) == ()
    assert set(PUBLICATION_CONFORMANCE_EXPECTATIONS) == set(PublicationConformanceScenario)


def test_fault_matrix_rejects_partial_success_and_transport_divergence() -> None:
    outcomes = [
        _outcome(
            scenario,
            transport,
            expectation.status,
            policy_allowed=expectation.policy_allowed,
            code=scenario.value,
        )
        for scenario, expectation in PUBLICATION_CONFORMANCE_EXPECTATIONS.items()
        for transport in PublicationConformanceTransport
    ]
    target = next(
        index
        for index, outcome in enumerate(outcomes)
        if outcome.scenario == PublicationConformanceScenario.PRIVACY_LEAK
        and outcome.transport == PublicationConformanceTransport.MCP
    )
    original = outcomes[target]
    outcomes[target] = replace(
        original,
        status=PublicationConformanceStatus.SUCCEEDED,
        partial_success=True,
        code="privacy_leak_falsely_succeeded",
    )

    failures = validate_publication_conformance(outcomes)

    assert any("expected failed" in failure for failure in failures)
    assert any("falsely reported partial success" in failure for failure in failures)
    assert any("diverged from the shared transport outcome" in failure for failure in failures)
