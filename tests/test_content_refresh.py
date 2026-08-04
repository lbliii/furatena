"""Durable managed-content refresh operation contracts."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from chirp.testing import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.build_identity import _content_identity
from furatena.catalog.content_deployment import (
    ContentDeploymentConfig,
    ContentDeploymentConflict,
    ContentDeploymentStore,
)
from furatena.catalog.content_refresh import (
    ContentRefreshActor,
    ContentRefreshConflict,
    ContentRefreshRequest,
    ContentRefreshService,
    ContentRefreshState,
    authenticate_content_actor,
)
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

SCHEMAS = Path("src/furatena/catalog/schemas/content-refresh/v1")
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


@dataclass
class _Config:
    repository: str = "https://github.com/example/docs.git"
    ref: str = "main"
    subdirectory: str = "app"


class _Store:
    def __init__(self, root: Path, *, active_commit: str | None = None) -> None:
        self.root = root
        self.leases = root / "leases"
        self.config = _Config()
        self.active_commit = active_commit
        self.active_generation = "generation-a" if active_commit else None
        self.image_digest = f"sha256:{'c' * 64}"
        self.build_commit = "d" * 40
        self.refresh_calls = 0
        self.unreachable = False

    def status(self) -> dict[str, Any]:
        receipt = None
        if self.active_commit is not None:
            receipt = {
                "resolved_ref": self.active_commit,
                "image_digest": self.image_digest,
                "build_commit": self.build_commit,
            }
        return {
            "active_generation": self.active_generation,
            "receipt": receipt,
        }

    def resolve_requested_commit(self, requested_commit: str) -> str:
        if self.unreachable:
            raise ContentDeploymentConflict(
                "unreachable_commit",
                "The exact requested commit is not reachable under the configured ref policy.",
            )
        return requested_commit

    def refresh(self, **values: Any) -> dict[str, Any]:
        self.refresh_calls += 1
        expected = values.get("expected_active_commit")
        if expected != self.active_commit:
            raise ContentDeploymentConflict("stale_active_commit", "stale active commit")
        requested = cast(str, values["requested_commit"])
        self.active_commit = requested
        self.active_generation = "generation-b"
        return {
            "operation": "refresh",
            "resolved_ref": requested,
            "generation": self.active_generation,
        }


def _actor() -> ContentRefreshActor:
    return ContentRefreshActor("operator", "test-credential", "test")


def _request(
    *, expected: str | None = None, requested: str = COMMIT_B, key: str = "refresh-key-0001"
) -> ContentRefreshRequest:
    return ContentRefreshRequest(expected, requested, key)


def _service(store: _Store, **values: Any) -> ContentRefreshService:
    return ContentRefreshService(cast(Any, store), **values)


def test_request_is_versioned_exact_and_cannot_override_configured_scope() -> None:
    request = _request()
    assert ContentRefreshRequest.from_dict(request.to_dict()) == request

    for field in ("actor", "repository", "ref", "subdirectory"):
        with pytest.raises(ValueError, match="unsupported fields"):
            ContentRefreshRequest.from_dict({**request.to_dict(), field: "override"})
    with pytest.raises(ValueError, match="40-character"):
        ContentRefreshRequest(None, "main", "refresh-key-0001")
    with pytest.raises(ValueError, match="unsupported schema version"):
        ContentRefreshRequest.from_dict({**request.to_dict(), "schema_version": True})


def test_submit_persists_transitions_replays_and_detects_key_collision(tmp_path: Path) -> None:
    store = _Store(tmp_path)
    service = _service(store, restart_scheduler=lambda: True)

    receipt = service.submit(_request(), actor=_actor(), asynchronous=False)

    assert receipt.state == ContentRefreshState.RESTART_SCHEDULED
    assert receipt.restart_required and receipt.restart_scheduled
    assert store.refresh_calls == 1
    assert [event.state for event in receipt.history] == [
        ContentRefreshState.QUEUED,
        ContentRefreshState.STAGING,
        ContentRefreshState.PROMOTED,
        ContentRefreshState.ACTIVATION_PENDING_RESTART,
        ContentRefreshState.RESTART_SCHEDULED,
    ]
    assert service.get(receipt.operation_id) == receipt
    assert "refresh-key-0001" not in json.dumps(receipt.to_dict())

    replay = service.submit(_request(), actor=_actor(), asynchronous=False)
    assert replay.operation_id == receipt.operation_id
    assert replay.replayed
    assert store.refresh_calls == 1

    with pytest.raises(ContentRefreshConflict) as collision:
        service.submit(
            _request(requested=COMMIT_A),
            actor=_actor(),
            asynchronous=False,
        )
    assert collision.value.code == "idempotency_key_collision"

    with pytest.raises(ContentRefreshConflict) as pending:
        service.submit(
            _request(expected=COMMIT_B, requested=COMMIT_A, key="refresh-key-0007"),
            actor=_actor(),
            asynchronous=False,
        )
    assert pending.value.code == "refresh_in_progress"


def test_conflicts_are_deterministic_before_promotion(tmp_path: Path) -> None:
    store = _Store(tmp_path, active_commit=COMMIT_A)
    service = _service(store)

    with pytest.raises(ContentRefreshConflict) as stale:
        service.submit(_request(expected=None), actor=_actor(), asynchronous=False)
    assert stale.value.code == "stale_active_commit"
    assert store.refresh_calls == 0

    store.unreachable = True
    with pytest.raises(ContentRefreshConflict) as unreachable:
        service.submit(
            _request(expected=COMMIT_A, key="refresh-key-0002"),
            actor=_actor(),
            asynchronous=False,
        )
    assert unreachable.value.code == "unreachable_commit"
    assert store.refresh_calls == 0


def test_concurrent_workers_replay_one_delivery_and_reject_distinct_pending_work(
    tmp_path: Path,
) -> None:
    assert_free_threading()
    entered = threading.Event()
    release = threading.Event()

    class BlockingStore(_Store):
        def refresh(self, **values: Any) -> dict[str, Any]:
            entered.set()
            assert release.wait(timeout=5)
            return super().refresh(**values)

    store = BlockingStore(tmp_path)
    first_service = _service(store)
    second_service = _service(store)
    request = _request()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(first_service.submit, request, actor=_actor(), asynchronous=False)
        assert entered.wait(timeout=5)

        replay = second_service.submit(request, actor=_actor(), asynchronous=False)
        assert replay.replayed
        assert replay.operation_id.startswith("refresh-")
        assert store.refresh_calls == 0

        with pytest.raises(ContentRefreshConflict) as pending:
            second_service.submit(
                _request(key="refresh-key-0008"),
                actor=_actor(),
                asynchronous=False,
            )
        assert pending.value.code == "refresh_in_progress"

        release.set()
        completed = first.result(timeout=5)

    assert completed.state == ContentRefreshState.ACTIVATION_PENDING_RESTART
    assert store.refresh_calls == 1


def test_startup_reconciliation_is_local_and_binds_generation_image_and_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(tmp_path)
    receipt = _service(store).submit(_request(), actor=_actor(), asynchronous=False)
    assert receipt.state == ContentRefreshState.ACTIVATION_PENDING_RESTART

    monkeypatch.setenv("FURA_IMAGE_DIGEST", store.image_digest)
    monkeypatch.setenv("FURA_BUILD_GIT_SHA", store.build_commit)
    reconciled = _service(store).reconcile_startup()
    assert reconciled[-1].state == ContentRefreshState.READY
    assert reconciled[-1].readiness == {
        "ready": True,
        "active_generation": "generation-b",
        "resolved_commit": COMMIT_B,
        "image_digest": store.image_digest,
        "build_commit": store.build_commit,
    }

    other = _Store(tmp_path / "interrupted")
    queued = _service(other)._initial_receipt(
        "refresh-" + "0" * 24,
        "sha256:" + "1" * 64,
        _request(key="refresh-key-0003"),
        _actor(),
        active_commit=None,
        compatibility_mode=False,
    )
    _service(other)._write(queued)
    interrupted = _service(other).reconcile_startup()
    assert interrupted[-1].state == ContentRefreshState.FAILED
    assert interrupted[-1].failure["code"] == "interrupted_before_promotion"
    public_failure = interrupted[-1].public_dict(
        status_url=f"/_fura/content/operations/{interrupted[-1].operation_id}",
        include_failure_message=False,
    )["failure"]
    assert public_failure == {"code": "interrupted_before_promotion"}


def test_actor_comes_from_bearer_transport_not_request_body() -> None:
    token = "t" * 32
    actor = authenticate_content_actor(
        f"Bearer {token}", transport="http", environ={"FURA_CONTENT_REFRESH_TOKEN": token}
    )
    assert actor == ContentRefreshActor(
        "configured-content-operator", "content-refresh-bearer", "http"
    )
    assert (
        authenticate_content_actor(
            "Bearer wrong", transport="http", environ={"FURA_CONTENT_REFRESH_TOKEN": token}
        )
        is None
    )
    assert (
        authenticate_content_actor(
            token, transport="http", environ={"FURA_CONTENT_REFRESH_TOKEN": token}
        )
        is None
    )


def test_json_schemas_validate_request_and_durable_receipt(tmp_path: Path) -> None:
    request = _request()
    receipt = _service(_Store(tmp_path)).submit(request, actor=_actor(), asynchronous=False)
    checker = FormatChecker()
    request_validator = Draft202012Validator(
        json.loads((SCHEMAS / "request.schema.json").read_text(encoding="utf-8")),
        format_checker=checker,
    )
    receipt_validator = Draft202012Validator(
        json.loads((SCHEMAS / "receipt.schema.json").read_text(encoding="utf-8")),
        format_checker=checker,
    )
    request_validator.validate(request.to_dict())
    receipt_validator.validate(receipt.to_dict())

    with pytest.raises(ValueError, match="unsupported schema version"):
        type(receipt).from_dict({**receipt.to_dict(), "schema_version": True})
    with pytest.raises(ValidationError):
        request_validator.validate({**request.to_dict(), "schema_version": True})
    with pytest.raises(ValidationError):
        receipt_validator.validate({**receipt.to_dict(), "schema_version": True})

    with pytest.raises(ValidationError):
        request_validator.validate({**request.to_dict(), "actor": "body-actor"})

    fixture_root = Path("tests/fixtures/content-refresh/v1")
    request_validator.validate(
        json.loads((fixture_root / "request.json").read_text(encoding="utf-8"))
    )
    receipt_validator.validate(
        json.loads((fixture_root / "receipt.json").read_text(encoding="utf-8"))
    )


def test_http_refresh_returns_202_and_deterministic_409_without_scope_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from furatena.catalog import route_registrars

    app_root = tmp_path / "app"
    content_root = tmp_path / "content"
    app_root.mkdir()
    content_root.mkdir()
    copy_app_theme(app_root, Path(__file__).resolve().parents[1] / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(app_root / "mounts.yaml", content_root)
    (content_root / "_index.md").write_text("# Home\n", encoding="utf-8")
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, False, False),
    )
    token = "t" * 32
    monkeypatch.setenv("FURA_CONTENT_REFRESH_TOKEN", token)
    monkeypatch.setattr(route_registrars, "_schedule_content_restart", lambda: False)
    store = _Store(tmp_path / "managed")
    monkeypatch.setattr(route_registrars, "_content_store", lambda: cast(Any, store))
    client = TestClient(docs.create_app())

    async def exercise() -> None:
        headers = {"authorization": f"Bearer {token}"}
        accepted = await client.post(
            "/_fura/content/refresh",
            headers=headers,
            json=_request().to_dict(),
        )
        assert accepted.status == 202
        payload = json.loads(accepted.text)
        assert payload["status_url"] == f"/_fura/content/operations/{payload['operation_id']}"
        assert "repository" not in payload

        public_status = await client.get("/_fura/content/status")
        assert public_status.status == 200
        status_payload = json.loads(public_status.text)
        assert "repository" not in status_payload
        assert "generation_selection" not in status_payload
        assert status_payload["lifecycle_state"] in {"staging", "active", "stale"}

        unreachable_store = _Store(tmp_path / "unreachable")
        unreachable_store.unreachable = True
        monkeypatch.setattr(
            route_registrars, "_content_store", lambda: cast(Any, unreachable_store)
        )
        conflict = await client.post(
            "/_fura/content/refresh",
            headers=headers,
            json=_request(key="refresh-key-0004").to_dict(),
        )
        assert conflict.status == 409
        assert json.loads(conflict.text)["error"]["code"] == "unreachable_commit"

        invalid = await client.post(
            "/_fura/content/refresh",
            headers=headers,
            json={**_request(key="refresh-key-0005").to_dict(), "actor": "body"},
        )
        assert invalid.status == 400
        assert json.loads(invalid.text)["error"]["code"] == "invalid_refresh_request"

        unauthorized = await client.post(
            "/_fura/content/refresh",
            json=_request(key="refresh-key-0006").to_dict(),
        )
        assert unauthorized.status == 401

    asyncio.run(exercise())


def test_build_identity_reports_running_generation_until_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generations = tmp_path / "generations"
    running = generations / "generation-running"
    selected = generations / "generation-selected"
    for generation, commit in ((running, COMMIT_A), (selected, COMMIT_B)):
        generation.mkdir(parents=True)
        (generation / "receipt.json").write_text(
            json.dumps(
                {
                    "status": "active",
                    "generation": generation.name,
                    "resolved_ref": commit,
                    "image_digest": f"sha256:{'c' * 64}",
                    "build_commit": "d" * 40,
                }
            ),
            encoding="utf-8",
        )
    (tmp_path / "active").symlink_to(selected)
    monkeypatch.setenv("FURA_CONTENT_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FURA_ACTIVE_CONTENT_GENERATION", running.name)

    identity = _content_identity()

    assert identity["generation"] == running.name
    assert identity["resolved_ref"] == COMMIT_A
    assert identity["selected_generation"] == selected.name
    assert identity["activation_pending_restart"] is True


def test_promoted_generation_restarts_into_browser_search_catalog_and_agent_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    state_root = tmp_path / "state"
    config = ContentDeploymentConfig(
        repository="https://github.com/example/docs.git",
        ref="main",
        subdirectory="app",
        allowed_hosts=frozenset({"github.com"}),
        state_root=state_root,
    )
    store = ContentDeploymentStore(config)
    canary = "RUNTIME_REFRESH_CANARY_436"

    def checkout(target: Path, *, requested_commit: str | None = None) -> str:
        assert requested_commit == COMMIT_B
        app_root = target / "app"
        content_root = app_root / "content"
        content_root.mkdir(parents=True)
        copy_app_theme(app_root, repository_root / "app")
        write_minimal_docs_yaml(app_root / "docs.yaml")
        write_mounts_yaml(app_root / "mounts.yaml", content_root)
        (content_root / "_index.md").write_text(
            f"---\ntitle: Refreshed home\n---\n\n# Refreshed home\n\n{canary}\n",
            encoding="utf-8",
        )
        return COMMIT_B

    monkeypatch.setattr(store, "_checkout", checkout)
    monkeypatch.setenv("FURA_CONTENT_REPOSITORY", config.repository)
    monkeypatch.setenv("FURA_CONTENT_REF", config.ref)
    monkeypatch.setenv("FURA_CONTENT_SUBDIRECTORY", config.subdirectory)
    monkeypatch.setenv("FURA_CONTENT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("FURA_PLATFORM_ROOT", str(repository_root / "app"))
    monkeypatch.setenv("FURA_RUNTIME_STATE_ROOT", str(tmp_path / "runtime-state"))
    monkeypatch.setenv("FURA_OUTPUT_ROOT", str(tmp_path / "runtime-output"))
    monkeypatch.setenv("FURA_IMAGE_DIGEST", "sha256:" + "c" * 64)
    monkeypatch.setenv("FURA_BUILD_GIT_SHA", "d" * 40)
    service = ContentRefreshService(store)

    promoted = service.submit(_request(), actor=_actor(), asynchronous=False)
    assert promoted.state == ContentRefreshState.ACTIVATION_PENDING_RESTART

    selection = store.active_selection()
    monkeypatch.setenv("FURA_ACTIVE_CONTENT_GENERATION", selection.generation)
    reconciled = service.reconcile_startup()
    assert reconciled[-1].state == ContentRefreshState.READY

    docs = DocsApp.from_paths(
        selection.site_root / "docs.yaml",
        repo_root=selection.checkout_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, selection.frozen_root, True, False),
    )
    client = TestClient(docs.create_app())

    async def assert_surfaces() -> None:
        async with client:
            browser, search, catalog, llms = await asyncio.gather(
                client.get("/"),
                client.get("/search.json"),
                client.get("/catalog.json"),
                client.get("/llms-full.txt"),
            )
        for response in (browser, search, catalog, llms):
            assert response.status == 200
        assert "Refreshed home" in browser.text
        for response in (search, catalog, llms):
            assert canary in response.text

    asyncio.run(assert_surfaces())
