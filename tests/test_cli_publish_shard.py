"""Stable automation diagnostics for ``fura publish-shard``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from furatena.catalog.federation_publish import (
    ShardPublishAuthError,
    ShardPublishConflictError,
    ShardPublishPartialError,
)
from furatena.cli.main import run_command


@pytest.mark.parametrize(
    ("error_type", "code"),
    (
        (ShardPublishAuthError, "fura.publish_shard.auth"),
        (ShardPublishPartialError, "fura.publish_shard.partial"),
        (ShardPublishConflictError, "fura.publish_shard.conflict"),
    ),
)
def test_publish_shard_failures_keep_stable_nonzero_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    code: str,
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "docs.yaml").write_text("site:\n  name: Test\n", encoding="utf-8")
    verification = tmp_path / "verification.json"
    verification.write_text(json.dumps({"signatures": [], "attestations": []}), encoding="utf-8")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr("furatena.catalog.freeze.freeze_catalog", lambda _options: None)

    class Backend:
        def __init__(self, **_kwargs: object) -> None:
            pass

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error_type("injected provider failure")

    monkeypatch.setattr("furatena.catalog.federation_s3.S3ObjectBackend", Backend)
    monkeypatch.setattr("furatena.catalog.federation_publish.publish_shard", fail)

    result = run_command(
        [
            "--app-root",
            str(app),
            "publish-shard",
            "--mount",
            "docs",
            "--edition",
            "1.0.0",
            "--public-base-url",
            "https://objects.example.com/shards",
            "--verification",
            str(verification),
            "--s3-endpoint",
            "https://objects.example.com",
            "--s3-bucket",
            "docs",
            "--json",
        ]
    )

    assert result is not None
    assert result.ok is False
    assert result.exit_code != 0
    assert result.diagnostics[0].rule_id == code
