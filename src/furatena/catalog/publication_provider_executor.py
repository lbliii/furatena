"""Production publication executor for explicit provider profiles."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from furatena.catalog.publication_contracts import (
    PublicationActor,
    PublicationFailure,
    PublicationFailureDisposition,
    PublicationOutputReference,
    PublicationPlan,
    PublicationState,
)
from furatena.catalog.publication_provider import (
    ProviderCommitAttribution,
    ProviderContractError,
    ProviderOperation,
    ProviderOutcome,
    ProviderReconciliationState,
    ProviderReviewState,
    PublicationChangeProvider,
    PublicationChangeRequest,
    PublicationChangeResult,
    PublicationProfile,
    PublicationProfileConfig,
    validate_change_request,
    validate_provider_result,
    validate_repository_inspection,
)
from furatena.catalog.publication_workflow import (
    PublicationExecutionDisposition,
    PublicationExecutionError,
    PublicationExecutionResult,
)


@dataclass(frozen=True, slots=True)
class PublicationProviderSelection:
    """Explicit profile and Git/source facts selected by trusted composition."""

    profile: PublicationProfileConfig
    repository_base_revision: str
    attribution: ProviderCommitAttribution

    def __post_init__(self) -> None:
        if self.profile.profile == PublicationProfile.EXTERNAL:
            raise ValueError(
                "Publication provider executor supports only local_only, commit, and "
                "pull_request profiles."
            )
        if not self.repository_base_revision.strip():
            raise ValueError(
                "Publication repository_base_revision is required; select the exact approved "
                "commit."
            )


class PublicationProviderExecutionStore(Protocol):
    """Durable typed provider history required for replay and reconciliation."""

    def bind_profile(self, plan_id: str, profile: PublicationProfileConfig) -> None: ...

    def append_result(self, plan_id: str, result: PublicationChangeResult) -> None: ...

    def results(self, plan_id: str) -> tuple[PublicationChangeResult, ...]: ...

    def latest_result(self, plan_id: str) -> PublicationChangeResult | None: ...


class InMemoryPublicationProviderExecutionStore:
    """Free-threaded-safe provider history for tests and embedded deployments."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profiles: dict[str, PublicationProfileConfig] = {}
        self._results: dict[str, dict[str, PublicationChangeResult]] = {}
        self._orders: dict[str, list[str]] = {}

    def bind_profile(self, plan_id: str, profile: PublicationProfileConfig) -> None:
        with self._lock:
            existing = self._profiles.get(plan_id)
            if existing is not None and existing.profile_digest != profile.profile_digest:
                raise ValueError("Publication plan is already bound to another provider profile.")
            self._profiles[plan_id] = profile

    def append_result(self, plan_id: str, result: PublicationChangeResult) -> None:
        with self._lock:
            records = self._results.setdefault(plan_id, {})
            if result.result_id not in records:
                self._orders.setdefault(plan_id, []).append(result.result_id)
            records[result.result_id] = result

    def results(self, plan_id: str) -> tuple[PublicationChangeResult, ...]:
        with self._lock:
            records = self._results.get(plan_id, {})
            return tuple(records[result_id] for result_id in self._orders.get(plan_id, ()))

    def latest_result(self, plan_id: str) -> PublicationChangeResult | None:
        values = self.results(plan_id)
        return values[-1] if values else None


class JsonDirectoryPublicationProviderExecutionStore:
    """Restart-safe immutable provider records rooted outside source checkouts."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._lock = threading.RLock()

    def bind_profile(self, plan_id: str, profile: PublicationProfileConfig) -> None:
        with self._lock:
            path = self._plan_root(plan_id) / "profile.json"
            if path.exists():
                existing = PublicationProfileConfig.from_dict(_read_json(path))
                if existing.profile_digest != profile.profile_digest:
                    raise ValueError(
                        "Publication plan is already bound to another provider profile."
                    )
                return
            _write_json_once(path, profile.to_dict())

    def append_result(self, plan_id: str, result: PublicationChangeResult) -> None:
        with self._lock:
            path = self._plan_root(plan_id) / "results" / f"{result.result_id}.json"
            if path.exists():
                existing = PublicationChangeResult.from_dict(_read_json(path))
                if existing != result:
                    raise ValueError(
                        "Provider result ID was reused with different content; reconcile the "
                        "stored result."
                    )
            else:
                _write_json_once(path, result.to_dict("trusted"))
            index_path = self._plan_root(plan_id) / "results" / "index.json"
            order = []
            if index_path.exists():
                raw = json.loads(index_path.read_text(encoding="utf-8"))
                if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
                    raise ValueError(
                        "Provider result index is corrupt; repair or restore the durable "
                        "execution store."
                    )
                order = list(raw)
            if result.result_id not in order:
                order.append(result.result_id)
                _write_json_replace(index_path, order)

    def results(self, plan_id: str) -> tuple[PublicationChangeResult, ...]:
        with self._lock:
            root = self._plan_root(plan_id) / "results"
            if not root.exists():
                return ()
            index_path = root / "index.json"
            if index_path.exists():
                raw = json.loads(index_path.read_text(encoding="utf-8"))
                if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
                    raise ValueError(
                        "Provider result index is corrupt; repair or restore the durable "
                        "execution store."
                    )
                paths = tuple(root / f"{result_id}.json" for result_id in raw)
            else:
                paths = tuple(sorted(root.glob("provider-result-*.json")))
            return tuple(PublicationChangeResult.from_dict(_read_json(path)) for path in paths)

    def latest_result(self, plan_id: str) -> PublicationChangeResult | None:
        values = self.results(plan_id)
        return values[-1] if values else None

    def _plan_root(self, plan_id: str) -> Path:
        if not plan_id.startswith("plan-") or "/" in plan_id or ".." in plan_id:
            raise ValueError("invalid publication plan ID for provider store")
        return self.root / plan_id


type PublicationProviderSelector = Callable[[PublicationPlan], PublicationProviderSelection]


class PublicationProviderExecutor:
    """Sequence one explicit profile through a provider without inferring effects."""

    def __init__(
        self,
        *,
        selector: PublicationProviderSelector,
        providers: Mapping[str, PublicationChangeProvider],
        store: PublicationProviderExecutionStore,
    ) -> None:
        if not providers:
            raise ValueError(
                "Publication provider executor requires at least one configured provider."
            )
        self.selector = selector
        self.providers = MappingProxyType(dict(providers))
        self.store = store

    def execute(self, plan: PublicationPlan, /) -> PublicationExecutionResult:
        return self.execute_with_context(
            plan,
            actor=plan.creator,
            idempotency_key=plan.idempotency_key,
        )

    def execute_with_context(
        self,
        plan: PublicationPlan,
        /,
        *,
        actor: PublicationActor,
        idempotency_key: str,
    ) -> PublicationExecutionResult:
        selection, provider = self._selection(plan, actor)
        results: list[PublicationChangeResult] = []
        try:
            inspect_request = self._request(
                plan, selection, ProviderOperation.INSPECT, actor, idempotency_key
            )
            inspection = provider.inspect(inspect_request)
            validate_repository_inspection(selection.profile, inspect_request, inspection)

            prepare_request = self._request(
                plan, selection, ProviderOperation.PREPARE, actor, idempotency_key
            )
            prepared = self._record(
                plan,
                prepare_request,
                provider.prepare(prepare_request, inspection),
                results,
            )
            self._raise_failed(plan, selection.profile, prepared)
            if selection.profile.profile == PublicationProfile.LOCAL_ONLY:
                return self._complete(plan, selection.profile, results)

            commit_request = self._request(
                plan, selection, ProviderOperation.COMMIT, actor, idempotency_key
            )
            committed = self._record(
                plan,
                commit_request,
                provider.commit(commit_request, prepared),
                results,
            )
            self._raise_failed(plan, selection.profile, committed)
            if selection.profile.profile == PublicationProfile.COMMIT:
                return self._complete(plan, selection.profile, results)

            return self._propose_and_observe(
                plan,
                selection,
                provider,
                actor,
                idempotency_key,
                committed,
                results,
            )
        except ProviderContractError as exc:
            raise self._contract_failure(plan, selection.profile, exc) from exc

    def reconcile(self, plan: PublicationPlan, /) -> PublicationExecutionResult | None:
        return self.reconcile_with_context(plan, actor=plan.creator)

    def reconcile_with_context(
        self,
        plan: PublicationPlan,
        /,
        *,
        actor: PublicationActor,
    ) -> PublicationExecutionResult | None:
        selection, provider = self._selection(plan, actor)
        previous = self.store.latest_result(plan.plan_id)
        if previous is None:
            return None
        results: list[PublicationChangeResult] = []
        try:
            request = self._request(
                plan,
                selection,
                ProviderOperation.RECONCILE,
                actor,
                f"{plan.idempotency_key}:reconcile",
            )
            reconciled = self._record(
                plan,
                request,
                provider.reconcile(request, previous),
                results,
            )
            self._raise_failed(plan, selection.profile, reconciled)
            if (
                selection.profile.profile == PublicationProfile.PULL_REQUEST
                and reconciled.outcome == ProviderOutcome.COMMITTED
                and reconciled.reconciliation == ProviderReconciliationState.MATCHED
            ):
                return self._propose_and_observe(
                    plan,
                    selection,
                    provider,
                    actor,
                    f"{plan.idempotency_key}:reconcile",
                    reconciled,
                    results,
                )
            return self._complete(plan, selection.profile, results)
        except ProviderContractError as exc:
            raise self._contract_failure(plan, selection.profile, exc) from exc

    def _propose_and_observe(
        self,
        plan: PublicationPlan,
        selection: PublicationProviderSelection,
        provider: PublicationChangeProvider,
        actor: PublicationActor,
        idempotency_key: str,
        committed: PublicationChangeResult,
        results: list[PublicationChangeResult],
    ) -> PublicationExecutionResult:
        propose_request = self._request(
            plan, selection, ProviderOperation.PROPOSE_REVIEW, actor, idempotency_key
        )
        proposed = self._record(
            plan,
            propose_request,
            provider.propose_review(propose_request, committed),
            results,
        )
        self._raise_failed(plan, selection.profile, proposed)
        observe_request = self._request(
            plan, selection, ProviderOperation.OBSERVE_REVIEW, actor, idempotency_key
        )
        observed = self._record(
            plan,
            observe_request,
            provider.observe_review(observe_request, proposed),
            results,
        )
        self._raise_failed(plan, selection.profile, observed)
        return self._complete(plan, selection.profile, results)

    def _selection(
        self, plan: PublicationPlan, actor: PublicationActor
    ) -> tuple[PublicationProviderSelection, PublicationChangeProvider]:
        try:
            selection = self.selector(plan)
        except (TypeError, ValueError) as exc:
            raise PublicationExecutionError(
                PublicationFailure(
                    PublicationFailureDisposition.TERMINAL,
                    "provider.profile_invalid",
                    "The selected publication profile is invalid.",
                    "Correct the explicit publication profile before executing again.",
                )
            ) from exc
        if selection.attribution.workflow_actor != actor.actor:
            raise PublicationExecutionError(
                PublicationFailure(
                    PublicationFailureDisposition.AUTHORIZATION,
                    "provider.attribution_actor_mismatch",
                    "Publication commit attribution does not match the executing actor.",
                    "Select attribution bound to the authenticated workflow actor.",
                )
            )
        provider = self.providers.get(selection.profile.provider_id)
        if provider is None or provider.id != selection.profile.provider_id:
            raise PublicationExecutionError(
                PublicationFailure(
                    PublicationFailureDisposition.TERMINAL,
                    "provider.not_configured",
                    "The selected publication provider is not configured.",
                    "Configure the provider named by the explicit publication profile.",
                )
            )
        try:
            self.store.bind_profile(plan.plan_id, selection.profile)
        except ValueError as exc:
            raise PublicationExecutionError(
                PublicationFailure(
                    PublicationFailureDisposition.CONFLICT,
                    "provider.profile_changed",
                    "The publication plan is already bound to another provider profile.",
                    "Create a new publication plan for the changed profile.",
                )
            ) from exc
        return selection, provider

    @staticmethod
    def _request(
        plan: PublicationPlan,
        selection: PublicationProviderSelection,
        operation: ProviderOperation,
        actor: PublicationActor,
        idempotency_key: str,
    ) -> PublicationChangeRequest:
        request = PublicationChangeRequest.create(
            plan,
            selection.profile,
            operation=operation,
            repository_base_revision=selection.repository_base_revision,
            attribution=selection.attribution,
            actor=actor,
            idempotency_key=f"{idempotency_key}:{operation.value}",
        )
        validate_change_request(plan, selection.profile, request)
        return request

    def _record(
        self,
        plan: PublicationPlan,
        request: PublicationChangeRequest,
        result: PublicationChangeResult,
        current: list[PublicationChangeResult],
    ) -> PublicationChangeResult:
        validate_provider_result(plan, request, result)
        self.store.append_result(plan.plan_id, result)
        current.append(result)
        return result

    def _raise_failed(
        self,
        plan: PublicationPlan,
        profile: PublicationProfileConfig,
        result: PublicationChangeResult,
    ) -> None:
        if result.failure is None:
            return
        retry_target = None
        if result.failure.disposition == PublicationFailureDisposition.RETRYABLE:
            retry_target = PublicationState.APPROVED
        elif result.failure.disposition == PublicationFailureDisposition.RECONCILIATION_REQUIRED:
            retry_target = PublicationState.EXECUTING
        raise PublicationExecutionError(
            PublicationFailure(
                result.failure.disposition,
                result.failure.code,
                result.failure.safe_message,
                result.failure.remediation,
                retry_target=retry_target,
            ),
            outputs=self._outputs(plan, profile),
        )

    def _contract_failure(
        self,
        plan: PublicationPlan,
        profile: PublicationProfileConfig,
        error: ProviderContractError,
    ) -> PublicationExecutionError:
        return PublicationExecutionError(
            PublicationFailure(
                error.disposition,
                error.code,
                str(error),
                error.remediation,
            ),
            outputs=self._outputs(plan, profile),
        )

    def _complete(
        self,
        plan: PublicationPlan,
        profile: PublicationProfileConfig,
        current: list[PublicationChangeResult],
    ) -> PublicationExecutionResult:
        latest = current[-1]
        if profile.profile == PublicationProfile.PULL_REQUEST:
            if latest.outcome == ProviderOutcome.MERGED:
                disposition = PublicationExecutionDisposition.APPLIED
            elif latest.outcome == ProviderOutcome.REVIEW_OPEN and latest.review_state in {
                ProviderReviewState.DRAFT,
                ProviderReviewState.OPEN,
            }:
                disposition = PublicationExecutionDisposition.REVIEWABLE
            else:
                raise PublicationExecutionError(
                    PublicationFailure(
                        PublicationFailureDisposition.CONFLICT,
                        "provider.review_not_mergeable",
                        "The publication review is closed or no longer mergeable.",
                        "Inspect the review outcome and create a fresh publication plan if needed.",
                    ),
                    outputs=self._outputs(plan, profile),
                )
        elif profile.profile == PublicationProfile.COMMIT:
            if latest.outcome not in {ProviderOutcome.COMMITTED, ProviderOutcome.NO_CHANGE}:
                raise self._unexpected(plan, profile, latest)
            disposition = PublicationExecutionDisposition.APPLIED
        else:
            if latest.outcome not in {
                ProviderOutcome.PREPARED,
                ProviderOutcome.DIRTY_WORKING_TREE,
                ProviderOutcome.NO_CHANGE,
            }:
                raise self._unexpected(plan, profile, latest)
            disposition = PublicationExecutionDisposition.APPLIED
        return PublicationExecutionResult(disposition, self._outputs(plan, profile))

    def _unexpected(
        self,
        plan: PublicationPlan,
        profile: PublicationProfileConfig,
        result: PublicationChangeResult,
    ) -> PublicationExecutionError:
        return PublicationExecutionError(
            PublicationFailure(
                PublicationFailureDisposition.TERMINAL,
                "provider.unexpected_outcome",
                f"Provider returned unexpected {result.outcome.value} outcome for the profile.",
                "Inspect the provider adapter and reconcile the recorded result.",
            ),
            outputs=self._outputs(plan, profile),
        )

    def _outputs(
        self, plan: PublicationPlan, profile: PublicationProfileConfig
    ) -> tuple[PublicationOutputReference, ...]:
        outputs = [
            PublicationOutputReference(
                kind="publication_provider_profile",
                identifier=profile.profile_digest,
                status=profile.profile.value,
                revision=profile.profile_digest,
            )
        ]
        for result in self.store.results(plan.plan_id):
            outputs.append(
                PublicationOutputReference(
                    kind="publication_provider_result",
                    identifier=result.result_id,
                    status=result.outcome.value,
                    revision=result.result_digest,
                    url=result.review_url,
                )
            )
        latest = self.store.latest_result(plan.plan_id)
        if latest is not None:
            outputs.extend(
                (
                    PublicationOutputReference(
                        kind="publication_provider_protection",
                        identifier=latest.repository_id,
                        status=latest.protection.value,
                    ),
                    PublicationOutputReference(
                        kind="publication_provider_reconciliation",
                        identifier=latest.result_id,
                        status=latest.reconciliation.value,
                    ),
                )
            )
            if latest.review_id is not None:
                outputs.append(
                    PublicationOutputReference(
                        kind="publication_provider_review",
                        identifier=latest.review_id,
                        status=latest.review_state.value,
                        revision=latest.merge_revision or latest.commit_id,
                        url=latest.review_url,
                    )
                )
        return tuple(outputs)


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"provider execution record is not an object: {path.name}")
    return value


def _write_json_once(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_json_replace(path: Path, value: object) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
