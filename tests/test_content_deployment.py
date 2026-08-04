"""Managed public-Git content generation and authorization contracts."""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from furatena.catalog.content_deployment import (
    ContentDeploymentConfig,
    ContentDeploymentError,
    ContentDeploymentStore,
    refresh_authorized,
)
from furatena.cli.main import main, run_command


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


def test_content_cli_reports_configuration_errors_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("FURA_CONTENT_REPOSITORY", raising=False)

    with pytest.raises(SystemExit, match="4"):
        main(["content", "status"])

    output = capsys.readouterr()
    assert "FURA_CONTENT_REPOSITORY is not configured" in output.out
    assert "Traceback" not in output.out
    assert output.err == ""


def test_content_cli_translates_read_only_state_into_railway_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = Path("/data/furatena")
    monkeypatch.setenv("FURA_CONTENT_REPOSITORY", "https://github.com/example/docs.git")
    monkeypatch.setenv("FURA_CONTENT_STATE_ROOT", str(state_root))

    def read_only(_self: ContentDeploymentStore) -> dict[str, object]:
        raise OSError(errno.EROFS, "Read-only file system", str(state_root / "leases"))

    monkeypatch.setattr(ContentDeploymentStore, "status", read_only)

    result = run_command(["content", "status"])

    assert result is not None
    assert not result.ok
    assert result.exit_code == 4
    assert result.data["error"]["code"] == "fura.content_deployment"
    assert result.data["error"]["context"] == {
        "operation": "content status",
        "path": "/data/furatena/leases",
    }
    assert "FURA_CONTENT_STATE_ROOT=/data/furatena" in result.summary
    assert "writable Railway volume at /data/furatena" in result.summary


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
    recorded = json.loads((first_target / "receipt.json").read_text())
    assert recorded["status"] == "active"
    assert recorded["schema_version"] == 2
    assert recorded["selection"]["site"] == "source/app"
    assert first["generation_selection"]["site_root"] == str(first_target / "source" / "app")
    assert not bool((first_target / "source" / "app" / "docs.yaml").stat().st_mode & 0o222)

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


def test_managed_subdirectory_and_state_roots_fail_closed() -> None:
    base = {
        "FURA_CONTENT_REPOSITORY": "https://github.com/example/docs.git",
        "FURA_CONTENT_ALLOWED_HOSTS": "github.com",
    }
    with pytest.raises(ContentDeploymentError, match="protected managed namespaces"):
        ContentDeploymentConfig.from_environment(
            {**base, "FURA_CONTENT_SUBDIRECTORY": "frozen/site"}
        )
    with pytest.raises(ContentDeploymentError, match="absolute non-symlink"):
        ContentDeploymentConfig.from_environment(
            {**base, "FURA_CONTENT_STATE_ROOT": "relative-state"}
        )


def test_refresh_authority_requires_the_independent_bearer() -> None:
    token = "t" * 32
    body = b'{"ref":"refs/heads/main"}'
    environ = {"FURA_CONTENT_REFRESH_TOKEN": token}

    assert refresh_authorized(f"Bearer {token}", b"", None, environ=environ)
    assert not refresh_authorized(None, body, "sha256=deferred", environ=environ)
    assert not refresh_authorized("Bearer wrong", body, "sha256=wrong", environ=environ)
