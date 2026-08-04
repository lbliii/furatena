"""Phase-aware serve startup contracts."""

from __future__ import annotations

import errno
import io
import json
import zlib
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from chirp.contracts import CheckResult, ContractIssue, Severity

from furatena.catalog.dev_banner import (
    ServeReloadBehavior,
    ServeStartupDiagnostic,
    ServeStartupPhase,
    ServeStartupResult,
    compose_serve_preflight,
    format_serve_preflight,
)
from furatena.catalog.dev_reload import run_docs_dev_server
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.cli.commands import serve as serve_command


class _FakeApp:
    def __init__(self) -> None:
        self.freeze_calls = 0

    def freeze(self) -> None:
        self.freeze_calls += 1


class _Terminal(io.StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _startup_result(
    *,
    diagnostics: tuple[ServeStartupDiagnostic, ...] = (),
    stale_freeze: bool = False,
    checks_skipped: bool = False,
) -> ServeStartupResult:
    return ServeStartupResult(
        phase=ServeStartupPhase.PREFLIGHT,
        mode=ServeMode.AUTHOR,
        configured_url="http://127.0.0.1:8001/",
        page_count=10,
        mount_count=2,
        frozen_dir=None,
        stale_freeze=stale_freeze,
        reload=ServeReloadBehavior(
            content="htmx",
            theme="browser",
            python="manual restart",
            frozen_baseline=False,
        ),
        checks_skipped=checks_skipped,
        check_elapsed_ms=25.25,
        diagnostics=diagnostics,
    )


def test_preflight_freezes_and_runs_chirp_contracts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _FakeApp()
    calls = 0

    def check_once(_app: object) -> CheckResult:
        nonlocal calls
        calls += 1
        return CheckResult(
            issues=[
                ContractIssue(
                    Severity.WARNING,
                    "fragment_target",
                    "Optional target is absent.",
                    template="pages/index.kida",
                    details="Register the target or remove the optional declaration.",
                )
            ]
        )

    monkeypatch.setattr("chirp.contracts.check_hypermedia_surface", check_once)
    result = compose_serve_preflight(
        cast(Any, app),
        ServeConfig(ServeMode.AUTHOR, None, False, True),
        page_count=10,
        mount_count=2,
        configured_url="http://127.0.0.1:8001/",
        run_contract_checks=True,
    )

    assert app.freeze_calls == 1
    assert calls == 1
    assert result.ok is True
    assert result.warning_count == 1
    assert result.to_dict()["phase"] == "preflight"
    assert result.to_dict()["ready"] is False


def test_preflight_skip_does_not_run_chirp_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _FakeApp()

    def unexpected_check(_app: object) -> CheckResult:
        raise AssertionError("contract checks must remain skipped")

    monkeypatch.setattr("chirp.contracts.check_hypermedia_surface", unexpected_check)
    result = compose_serve_preflight(
        cast(Any, app),
        ServeConfig(ServeMode.PREVIEW, Path("frozen"), True, False),
        page_count=10,
        mount_count=2,
        configured_url="http://127.0.0.1:8001/",
        run_contract_checks=False,
    )

    assert app.freeze_calls == 1
    assert result.checks_skipped is True


def test_docs_app_constructor_does_not_present_stale_freeze(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from furatena.catalog.docs_app import DocsApp

    repo = Path(__file__).resolve().parents[1]
    empty_sphinx_inventory = (
        b"# Sphinx inventory version 2\n"
        b"# Project: test\n"
        b"# Version: 1.0\n"
        b"# The remainder of this file is compressed with zlib.\n\n" + zlib.compress(b"")
    )
    monkeypatch.setenv("CHIRP_SKIP_CONTRACT_CHECKS", "1")
    monkeypatch.setattr(
        "furatena.catalog.inventories.store._fetch_inventory_url",
        lambda _url, _cache: empty_sphinx_inventory,
    )

    DocsApp.from_paths(
        repo / "app" / "docs.yaml",
        repo_root=repo,
        autodoc=False,
        serve=ServeConfig(
            mode=ServeMode.AUTHOR,
            frozen_dir=None,
            lazy_html=True,
            auto_reload=True,
            warn_stale_freeze=True,
        ),
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_warning_preflight_is_compact_and_never_claims_readiness() -> None:
    warning = ServeStartupDiagnostic(
        severity="warning",
        message="Optional target is absent.",
        rule_id="chirp.fragment_target",
        source_path="pages/index.kida",
        next_action="Register the target.",
    )
    lines = format_serve_preflight(_startup_result(diagnostics=(warning,), stale_freeze=True))
    output = "\n".join(lines)

    assert "1 warnings · details: uv run fura check" in output
    assert "run `fura freeze` for a fresh export" in output
    assert "Optional target is absent" not in output
    assert "Ready" not in output
    assert "http://" not in output
    assert "\x1b" not in output


def test_fatal_preflight_shows_every_origin_and_remediation() -> None:
    diagnostics = (
        ServeStartupDiagnostic(
            severity="error",
            message="Required target is absent.",
            rule_id="chirp.fragment_target",
            source_path="pages/index.kida",
            next_action="Register #results in the page shell.",
        ),
        ServeStartupDiagnostic(
            severity="error",
            message="Unknown route contract.",
            rule_id="chirp.route_contract",
            source_path="GET /missing",
            next_action="Register the route or remove the contract.",
        ),
    )
    output = "\n".join(format_serve_preflight(_startup_result(diagnostics=diagnostics)))

    for expected in (
        "pages/index.kida: Required target is absent.",
        "Register #results in the page shell.",
        "GET /missing: Unknown route contract.",
        "Register the route or remove the contract.",
    ):
        assert expected in output


@pytest.mark.parametrize("tty", [True, False])
def test_human_preflight_is_stable_for_tty_and_redirected_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tty: bool,
) -> None:
    stream = _Terminal(tty=tty)
    monkeypatch.setattr(serve_command.sys, "stderr", stream)

    serve_command._write_human_preflight(_startup_result(), structured=False)

    output = stream.getvalue()
    assert "Catalog" in output
    assert "Checks" in output
    assert "Ready" not in output
    assert "\x1b" not in output


def test_structured_preflight_stderr_is_one_json_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _Terminal(tty=False)
    monkeypatch.setattr(serve_command.sys, "stderr", stream)

    serve_command._write_human_preflight(_startup_result(), structured=True)

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "furatena.serve.preflight"
    assert payload["phase"] == "preflight"
    assert payload["ready"] is False


def test_serve_json_describes_preflight_not_completed_startup() -> None:
    result = serve_command._startup_command_result(
        Namespace(command="serve", workers=2),
        _startup_result(),
        host="127.0.0.1",
        port=8001,
    )

    payload = result.to_dict()
    assert payload["summary"] == "serve preflight passed"
    assert "startup completed" not in payload["summary"]
    assert payload["data"]["ready"] is False
    assert payload["data"]["configured_url"] == "http://127.0.0.1:8001/"


def test_dev_server_port_conflict_is_actionable_and_never_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import chirp.server.dev

    app = SimpleNamespace(
        config=SimpleNamespace(host="127.0.0.1", port=8001, debug=True),
        _ensure_frozen=lambda: None,
    )
    docs = SimpleNamespace(
        app=app,
        config=SimpleNamespace(root=tmp_path),
        repo_root=tmp_path,
    )

    def port_conflict(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(chirp.server.dev, "run_dev_server", port_conflict)
    with pytest.raises(SystemExit) as raised:
        run_docs_dev_server(cast(Any, docs))

    output = capsys.readouterr().err
    assert raised.value.code == 1
    assert "different port" in output
    assert "Ready" not in output


def test_interrupted_dev_startup_is_quiet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import chirp.server.dev

    app = SimpleNamespace(
        config=SimpleNamespace(host="127.0.0.1", port=8001, debug=True),
        _ensure_frozen=lambda: None,
    )
    docs = SimpleNamespace(
        app=app,
        config=SimpleNamespace(root=tmp_path),
        repo_root=tmp_path,
    )

    def interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(chirp.server.dev, "run_dev_server", interrupt)
    run_docs_dev_server(cast(Any, docs))

    assert capsys.readouterr().err == ""
