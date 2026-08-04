"""Managed generation manifest, verification, and fault-containment contracts."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from furatena.catalog.content_deployment import (
    ContentDeploymentConfig,
    ContentDeploymentConflict,
    ContentDeploymentError,
    ContentDeploymentStore,
)
from furatena.catalog.content_generation import (
    GenerationContractError,
    verify_generation_contract,
)
from furatena.catalog.operational_status import _managed_content_readiness

SCHEMAS = Path("src/furatena/catalog/schemas/content-generation/v1")
COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def _config(root: Path) -> ContentDeploymentConfig:
    return ContentDeploymentConfig(
        repository="https://GitHub.com/example/docs.git/",
        ref="main",
        subdirectory="app",
        allowed_hosts=frozenset({"github.com"}),
        state_root=root,
        max_bytes=100_000,
        max_files=100,
        refresh_on_start=True,
    )


def _checkout(commit: str, *, invalid_config: bool = False, private: bool = False):
    def checkout(target: Path, *, requested_commit: str | None = None) -> str:
        assert requested_commit in {None, commit}
        app = target / "app"
        app.mkdir(parents=True)
        config = "[invalid" if invalid_config else "site:\n  title: Docs\n"
        (app / "docs.yaml").write_text(config, encoding="utf-8")
        (app / "index.md").write_text("# Public home\n", encoding="utf-8")
        if private:
            (app / "private.md").write_text(
                "---\ntitle: Private Canary\nvisibility: private\n---\n\nPRIVATE_GENERATION_CANARY_7429\n",
                encoding="utf-8",
            )
        return commit

    return checkout


def _freezer(app_root: Path, output: Path, *, leak: bool = False):
    output.mkdir(parents=True)
    token = " PRIVATE_GENERATION_CANARY_7429" if leak else ""
    (output / "catalog.json").write_text(
        json.dumps({"pages": [{"id": "index", "title": f"Home{token}"}]}),
        encoding="utf-8",
    )
    (output / "search.json").write_text("[]", encoding="utf-8")
    (output / "semantic.json").write_text("[]", encoding="utf-8")
    (output / "llms-full.txt").write_text(f"# Public docs{token}\n", encoding="utf-8")
    (output / "renderer.fingerprint").write_text("renderer-v1\n", encoding="utf-8")
    return {"page_count": 1, "frozen_root": output}


def _make_writable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IWUSR)


def test_generation_manifest_binds_source_runtime_artifacts_and_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FURA_IMAGE_DIGEST", f"sha256:{'c' * 64}")
    monkeypatch.setenv("FURA_BUILD_GIT_SHA", "d" * 40)
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout(COMMIT_A, private=True))

    receipt = store.refresh(
        requested_commit=COMMIT_A,
        expected_active_commit=None,
        actor="operator",
        operation_id="refresh-" + "1" * 24,
        semantic_digest="sha256:" + "2" * 64,
        idempotency_key_digest="sha256:" + "3" * 64,
    )

    root = store.active.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    verification = json.loads((root / "verification.json").read_text(encoding="utf-8"))
    assert manifest["source"] == {
        "repository": "https://github.com/example/docs.git",
        "ref": "main",
        "requested_commit": COMMIT_A,
        "resolved_commit": COMMIT_A,
    }
    assert manifest["runtime"] == {
        "image_digest": f"sha256:{'c' * 64}",
        "build_commit": "d" * 40,
    }
    assert manifest["fingerprints"]["renderer"] == "renderer-v1"
    assert manifest["fingerprints"]["presentation"].startswith("sha256:")
    assert manifest["operation_receipt"]["operation_id"] == "refresh-" + "1" * 24
    assert all(item["sha256"].startswith("sha256:") for item in manifest["artifacts"])
    assert {item["kind"] for item in manifest["artifacts"]} == {"source", "frozen"}
    assert verification["checks"]["privacy_canaries"]["canaries"] == 1
    assert receipt["manifest_digest"] == verification["manifest_digest"]
    assert verify_generation_contract(root, full=True, allow_legacy=False)["verified"]

    monkeypatch.setenv("FURA_CONTENT_REPOSITORY", "https://github.com/example/docs.git")
    monkeypatch.setenv("FURA_CONTENT_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FURA_CONTENT_REF", "main")
    monkeypatch.setenv("FURA_CONTENT_SUBDIRECTORY", "app")
    monkeypatch.setenv("FURA_ACTIVE_CONTENT_GENERATION", root.name)
    readiness = _managed_content_readiness()
    assert readiness is not None and readiness["ok"] is True
    assert readiness["lifecycle_state"] == "active"
    assert str(tmp_path) not in json.dumps(readiness)

    checker = FormatChecker()
    Draft202012Validator(
        json.loads((SCHEMAS / "manifest.schema.json").read_text(encoding="utf-8")),
        format_checker=checker,
    ).validate(manifest)
    Draft202012Validator(
        json.loads((SCHEMAS / "verification.schema.json").read_text(encoding="utf-8")),
        format_checker=checker,
    ).validate(verification)


@pytest.mark.parametrize(
    "fault", ["config", "privacy", "freeze", "markdown", "clone", "disk", "worker"]
)
def test_pre_promotion_faults_preserve_active_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout(COMMIT_A))
    store.refresh()
    active = store.active.resolve()

    monkeypatch.setattr(
        store,
        "_checkout",
        _checkout(COMMIT_B, invalid_config=fault == "config", private=fault == "privacy"),
    )
    if fault == "clone":

        def interrupted_clone(_target: Path):
            _target.mkdir(parents=True)
            raise ContentDeploymentError("clone interrupted")

        monkeypatch.setattr(store, "_checkout", interrupted_clone)
    elif fault == "privacy":
        store._freezer = lambda app, output: _freezer(app, output, leak=True)
    elif fault == "freeze":
        store._freezer = lambda _app, output: {
            "page_count": 1,
            "frozen_root": output,
        }
    elif fault == "markdown":

        def invalid_markdown(_app: Path, _output: Path):
            raise ContentDeploymentError("invalid Markdown directive")

        store._freezer = invalid_markdown
    elif fault == "disk":

        def disk_exhausted(_app: Path, _output: Path):
            raise OSError(28, "No space left on device")

        store._freezer = disk_exhausted
    elif fault == "worker":

        def worker_failed(_app: Path, _output: Path):
            raise RuntimeError("freeze worker terminated")

        store._freezer = worker_failed

    with pytest.raises((ContentDeploymentError, OSError, RuntimeError)):
        store.refresh()

    assert store.active.resolve() == active
    assert list(store.staging.glob("failed-*/failure.json"))


def test_interrupted_promotion_preserves_active_and_recovers_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout(COMMIT_A))
    store.refresh()
    active = store.active.resolve()
    store._clock = lambda: 1_700_000_001
    monkeypatch.setattr(store, "_checkout", _checkout(COMMIT_B))
    replace_link = store._replace_link

    def interrupt_active(link: Path, target: Path) -> None:
        if link == store.active:
            raise ContentDeploymentError("promotion interrupted before selector swap")
        replace_link(link, target)

    monkeypatch.setattr(store, "_replace_link", interrupt_active)
    with pytest.raises(ContentDeploymentError, match="promotion interrupted"):
        store.refresh()

    assert store.active.resolve() == active
    monkeypatch.setattr(store, "_replace_link", replace_link)
    assert store.reconcile()["active_generation"] == active.name
    assert list(store.receipts.glob("failed-*.json"))


def test_corrupt_recorded_generation_cannot_replace_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout(COMMIT_A))
    store.refresh()
    first = store.active.resolve()
    store._clock = lambda: 1_700_000_001
    monkeypatch.setattr(store, "_checkout", _checkout(COMMIT_B))
    store.refresh()
    second = store.active.resolve()

    corrupt = first / "frozen" / "catalog.json"
    _make_writable(corrupt)
    corrupt.write_text('{"pages": []}\n', encoding="utf-8")

    with pytest.raises(ContentDeploymentConflict) as error:
        store.rollback(actor="operator", reason="fault injection")
    assert error.value.code == "artifact_integrity_failed"
    assert store.active.resolve() == second
    assert not first.exists()
    quarantined = store.status()["generation_quarantine"]
    assert quarantined["generation"] == first.name
    assert quarantined["code"] == "artifact_integrity_failed"
    assert any(store.quarantine.glob(f"{first.name}-*"))


def test_full_verification_rejects_artifacts_missing_from_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FURA_IMAGE_DIGEST", "sha256:" + "c" * 64)
    monkeypatch.setenv("FURA_BUILD_GIT_SHA", "d" * 40)
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout(COMMIT_A))
    store.refresh()
    generation = store.active.resolve()
    frozen = generation / "frozen"
    _make_writable(frozen)
    (frozen / "unrecorded.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(GenerationContractError) as error:
        verify_generation_contract(generation, full=True, allow_legacy=False)

    assert error.value.code == "artifact_integrity_failed"
    monkeypatch.setenv("FURA_CONTENT_REPOSITORY", "https://github.com/example/docs.git")
    monkeypatch.setenv("FURA_CONTENT_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("FURA_CONTENT_REF", "main")
    monkeypatch.setenv("FURA_CONTENT_SUBDIRECTORY", "app")
    monkeypatch.setenv("FURA_ACTIVE_CONTENT_GENERATION", generation.name)
    readiness = _managed_content_readiness()
    assert readiness is not None and readiness["ok"] is False
    assert readiness["lifecycle_state"] == "degraded"


def test_corrupt_manifest_fails_closed_and_legacy_v1_is_explicitly_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout(COMMIT_A))
    store.refresh()
    generation = store.active.resolve()
    manifest = generation / "manifest.json"
    _make_writable(manifest)
    manifest.write_text("{}\n", encoding="utf-8")

    assert store.status()["active_generation"] is None
    quarantined = store.status()["generation_quarantine"]
    assert quarantined["generation"] == generation.name
    assert quarantined["code"] == "generation_manifest_invalid"
    assert "message" not in quarantined
    assert str(tmp_path) not in json.dumps(quarantined)
    assert any(store.quarantine.glob(f"{generation.name}-*"))

    # A pre-v1-contract generation retains time-bounded read compatibility,
    # while all newly promoted generations require manifest verification.
    legacy = tmp_path / "legacy-generation"
    (legacy / "source" / "app").mkdir(parents=True)
    (legacy / "source" / "app" / "docs.yaml").write_text("site: {}\n", encoding="utf-8")
    (legacy / "frozen").mkdir()
    for name in ("catalog.json", "search.json", "semantic.json", "llms-full.txt"):
        (legacy / "frozen" / name).write_text("{}\n", encoding="utf-8")
    (legacy / "receipt.json").write_text(
        json.dumps({"schema_version": 2, "generation": legacy.name}), encoding="utf-8"
    )
    status = verify_generation_contract(legacy, full=True, allow_legacy=True)
    assert status["status"] == "legacy_v1"
    assert status["verified"] is False
