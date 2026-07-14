"""Provider-neutral preview lifecycle and serialization contracts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from furatena.catalog.preview_contracts import (
    PreviewAction,
    PreviewCheck,
    PreviewCheckStatus,
    PreviewFailure,
    PreviewFailureDisposition,
    PreviewManifest,
    PreviewProvider,
    PreviewRequest,
    PreviewState,
    canonical_json_bytes,
    preview_id_for,
    preview_request_is_replay,
    preview_transition_allowed,
    require_preview_transition,
)
from tests.preview_support import (
    sample_access,
    sample_provider,
    sample_ready_manifest,
    sample_request,
    sample_source,
)


def test_lifecycle_allows_updates_teardown_and_reopen_but_not_promotion_states() -> None:
    assert preview_transition_allowed(PreviewState.REQUESTED, PreviewState.BUILDING)
    assert preview_transition_allowed(PreviewState.BUILDING, PreviewState.READY)
    assert preview_transition_allowed(PreviewState.READY, PreviewState.BUILDING)
    assert preview_transition_allowed(PreviewState.READY, PreviewState.STOPPING)
    assert preview_transition_allowed(PreviewState.STOPPING, PreviewState.STOPPED)
    assert preview_transition_allowed(PreviewState.STOPPED, PreviewState.REQUESTED)
    assert not preview_transition_allowed(PreviewState.REQUESTED, PreviewState.READY)

    with pytest.raises(ValueError, match="invalid preview transition"):
        require_preview_transition(PreviewState.READY, PreviewState.STOPPED)
    with pytest.raises(ValueError):
        PreviewState("promoted")


def test_request_identity_is_replay_safe_and_transport_independent() -> None:
    first = sample_request()
    replay = sample_request(
        idempotency_key="different-delivery-key",
        requested_at="2030-01-01T00:00:30Z",
        correlation_id="different-correlation",
    )

    assert first.request_id == replay.request_id
    assert first.request_digest == replay.request_digest
    assert first.idempotency_key != replay.idempotency_key
    assert not preview_request_is_replay(first, replay)
    assert PreviewRequest.from_dict(first.to_dict()) == first

    changed = sample_request(source=sample_source(head_sha="b" * 40))
    assert changed.request_digest != first.request_digest
    with pytest.raises(ValueError, match="idempotency key"):
        preview_request_is_replay(first, changed)

    exact_replay = sample_request()
    assert preview_request_is_replay(first, exact_replay)


def test_request_rejects_tampering_unknown_versions_and_ambiguous_actions() -> None:
    request = sample_request()
    payload = request.to_dict()

    with pytest.raises(ValueError, match="request_digest"):
        PreviewRequest.from_dict({**payload, "expires_at": "2030-01-09T00:00:00Z"})
    with pytest.raises(ValueError, match="unsupported preview schema_version"):
        PreviewRequest.from_dict({**payload, "schema_version": 2})
    with pytest.raises(ValueError, match="requires previous_preview_id"):
        replace(
            request,
            action=PreviewAction.UPDATE,
            request_id="",
            request_digest="",
        )


def test_ready_manifest_is_commit_bound_deterministic_and_round_trips() -> None:
    manifest = sample_ready_manifest()

    assert manifest.preview_id == preview_id_for(manifest.source)
    assert manifest.preview_id == preview_id_for(sample_source(head_sha="b" * 40))
    assert manifest.artifact is not None
    assert manifest.artifact.source_sha == manifest.source.head_sha
    assert PreviewManifest.from_dict(manifest.to_dict()) == manifest
    assert canonical_json_bytes(PreviewManifest.from_dict(manifest.to_dict()).to_dict()) == (
        canonical_json_bytes(manifest.to_dict())
    )

    reordered = replace(
        manifest,
        checks=tuple(reversed(manifest.checks)),
        manifest_digest="",
    )
    assert reordered.manifest_digest == manifest.manifest_digest


def test_manifest_enforces_ready_failed_and_stopped_state_evidence() -> None:
    ready = sample_ready_manifest()

    with pytest.raises(ValueError, match="passing readiness"):
        replace(
            ready,
            checks=(
                PreviewCheck(
                    check_id="readiness",
                    status=PreviewCheckStatus.FAIL,
                    summary="Readiness failed.",
                    observed_at="2030-01-01T00:04:00Z",
                    remediation="Inspect deployment logs.",
                ),
            ),
            manifest_digest="",
        )
    with pytest.raises(ValueError, match="source_sha"):
        replace(
            ready,
            artifact=replace(ready.artifact, source_sha="b" * 40),  # type: ignore[arg-type]
            manifest_digest="",
        )

    failed = PreviewManifest(
        state=PreviewState.FAILED,
        state_version=2,
        source=sample_source(),
        provider=sample_provider(ready=False),
        access=sample_access(),
        failure=PreviewFailure(
            disposition=PreviewFailureDisposition.RETRYABLE,
            code="provider.build_failed",
            safe_message="Preview build failed.",
            remediation="Inspect the provider build log and retry.",
        ),
        created_at="2030-01-01T00:00:00Z",
        updated_at="2030-01-01T00:02:00Z",
        expires_at="2030-01-08T00:00:00Z",
    )
    stopped = PreviewManifest(
        state=PreviewState.STOPPED,
        state_version=5,
        source=sample_source(),
        provider=sample_provider(),
        access=sample_access(),
        created_at="2030-01-01T00:00:00Z",
        updated_at="2030-01-02T00:01:00Z",
        expires_at="2030-01-08T00:00:00Z",
        stopped_at="2030-01-02T00:01:00Z",
    )

    assert PreviewManifest.from_dict(failed.to_dict()) == failed
    assert PreviewManifest.from_dict(stopped.to_dict()) == stopped


def test_provider_boundary_is_structural_and_provider_neutral() -> None:
    class ReferenceProvider:
        id = "reference"

        def request(
            self, request: PreviewRequest, previous: PreviewManifest | None
        ) -> PreviewManifest:
            del request, previous
            return sample_ready_manifest()

        def observe(self, manifest: PreviewManifest) -> PreviewManifest:
            return manifest

        def stop(self, request: PreviewRequest, manifest: PreviewManifest) -> PreviewManifest:
            del request
            return manifest

    assert isinstance(ReferenceProvider(), PreviewProvider)
