"""Managed public-Git content generation and authorization contracts."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from furatena.catalog.content_deployment import (
    ContentDeploymentConfig,
    ContentDeploymentError,
    ContentDeploymentStore,
    refresh_authorized,
)


def _config(root: Path, **changes) -> ContentDeploymentConfig:
    values = {
        "repository": "https://github.com/example/docs.git",
        "ref": "main",
        "subdirectory": "app",
        "allowed_hosts": frozenset({"github.com"}),
        "state_root": root,
        "max_bytes": 10_000,
        "max_files": 100,
        "refresh_on_start": True,
        **changes,
    }
    return ContentDeploymentConfig(**values)


def _checkout(commit: str, *, title: str = "Docs"):
    def checkout(target: Path) -> str:
        app = target / "app"
        app.mkdir(parents=True)
        (app / "docs.yaml").write_text(f"site:\n  title: {title}\n", encoding="utf-8")
        (app / "index.md").write_text(f"# {title}\n", encoding="utf-8")
        return commit

    return checkout


def _freezer(app_root: Path, output: Path):
    assert (app_root / "docs.yaml").is_file()
    output.mkdir(parents=True)
    for name, content in {
        "catalog.json": '{"pages": [{"id": "index"}]}',
        "search.json": "[]",
        "semantic.json": "[]",
        "llms-full.txt": "# Docs\n",
    }.items():
        (output / name).write_text(content, encoding="utf-8")
    return {"page_count": 1, "frozen_root": output}


def test_environment_accepts_only_credential_free_allowlisted_https() -> None:
    config = ContentDeploymentConfig.from_environment(
        {
            "FURA_CONTENT_REPOSITORY": "https://github.com/example/docs.git",
            "FURA_CONTENT_REF": "release/1.0",
            "FURA_CONTENT_SUBDIRECTORY": "site/app",
            "FURA_CONTENT_ALLOWED_HOSTS": "github.com,gitlab.com",
            "FURA_CONTENT_STATE_ROOT": "/tmp/furatena-content-test",
            "FURA_CONTENT_MAX_BYTES": "1234",
            "FURA_CONTENT_MAX_FILES": "56",
            "FURA_CONTENT_REFRESH_ON_START": "false",
        }
    )

    assert config is not None
    assert config.ref == "release/1.0"
    assert config.allowed_hosts == frozenset({"github.com", "gitlab.com"})
    assert config.max_bytes == 1234
    assert config.max_files == 56
    assert not config.refresh_on_start

    with pytest.raises(ContentDeploymentError, match="HTTPS public Git URL"):
        ContentDeploymentConfig.from_environment(
            {
                "FURA_CONTENT_REPOSITORY": "https://token@github.com/example/private.git",
                "FURA_CONTENT_ALLOWED_HOSTS": "github.com",
            }
        )


def test_refresh_atomically_promotes_and_retains_last_known_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout("a" * 40, title="First"))
    first = store.refresh(trigger="test")

    assert first["resolved_ref"] == "a" * 40
    assert first["source_files"] == 2
    first_target = store.active.resolve()
    assert first_target.name.endswith("-aaaaaaaaaaaa")
    assert json.loads((first_target / "receipt.json").read_text())["status"] == "active"

    monkeypatch.setattr(store, "_checkout", _checkout("b" * 40, title="Second"))
    second = store.refresh(trigger="webhook")

    assert second["resolved_ref"] == "b" * 40
    assert store.active.resolve().name.endswith("-bbbbbbbbbbbb")
    assert store.last_known_good.resolve() == first_target
    assert store.status()["status"] == "ready"


def test_failed_generation_never_replaces_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout("a" * 40))
    store.refresh()
    active = store.active.resolve()

    def broken_freezer(app_root: Path, output: Path):
        output.mkdir(parents=True)
        (output / "catalog.json").write_text("{}", encoding="utf-8")
        return {"page_count": 1, "frozen_root": output}

    store._freezer = broken_freezer
    monkeypatch.setattr(store, "_checkout", _checkout("b" * 40))
    with pytest.raises(ContentDeploymentError, match="incomplete"):
        store.refresh()

    assert store.active.resolve() == active
    failures = list(store.receipts.glob("failed-*.json"))
    assert len(failures) == 1
    assert json.loads(failures[0].read_text())["status"] == "failed"


def test_same_commit_and_image_is_a_noop_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FURA_IMAGE_DIGEST", f"sha256:{'c' * 64}")
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout("a" * 40))
    first = store.refresh(trigger="startup")
    active = store.active.resolve()

    second = store.refresh(trigger="startup")

    assert first["image_digest"] == f"sha256:{'c' * 64}"
    assert second["operation"] == "no_change"
    assert store.active.resolve() == active
    assert len(list(store.generations.iterdir())) == 1


def test_rollback_swaps_active_and_last_known_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout("a" * 40))
    store.refresh()
    first = store.active.resolve()
    monkeypatch.setattr(store, "_checkout", _checkout("b" * 40))
    store.refresh()
    second = store.active.resolve()

    receipt = store.rollback()

    assert receipt["operation"] == "rollback"
    assert store.active.resolve() == first
    assert store.last_known_good.resolve() == second
    assert store.status()["rollback_hold"]
    assert not store.startup_refresh_needed()

    monkeypatch.setattr(store, "_checkout", _checkout("a" * 40))
    refreshed = store.refresh(trigger="manual")
    assert refreshed["operation"] == "no_change"
    assert not store.status()["rollback_hold"]


def test_reconcile_restores_dangling_active_from_last_known_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ContentDeploymentStore(_config(tmp_path), freezer=_freezer, clock=lambda: 1_700_000_000)
    monkeypatch.setattr(store, "_checkout", _checkout("a" * 40))
    store.refresh()
    target = store.active.resolve()
    store._replace_link(store.last_known_good, target)
    store.active.unlink()

    status = store.reconcile()

    assert status["repaired"]
    assert store.active.resolve() == target


def test_source_limits_and_symlinks_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "large.md").write_bytes(b"x" * 11)
    store = ContentDeploymentStore(_config(tmp_path / "state", max_bytes=10), freezer=_freezer)
    with pytest.raises(ContentDeploymentError, match="MAX_BYTES"):
        store._validate_source(source)

    (source / "large.md").unlink()
    (source / "link.md").symlink_to("missing.md")
    with pytest.raises(ContentDeploymentError, match="symlinks"):
        store._validate_source(source)


def test_refresh_authority_is_independent_and_constant_time_compatible() -> None:
    token = "t" * 32
    secret = "s" * 32
    body = b'{"ref":"refs/heads/main"}'
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    environ = {
        "FURA_CONTENT_REFRESH_TOKEN": token,
        "FURA_CONTENT_WEBHOOK_SECRET": secret,
    }

    assert refresh_authorized(f"Bearer {token}", b"", None, environ=environ)
    assert refresh_authorized(None, body, f"sha256={signature}", environ=environ)
    assert not refresh_authorized("Bearer wrong", body, "sha256=wrong", environ=environ)
