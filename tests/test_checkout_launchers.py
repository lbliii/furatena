"""Checkout launchers delegate to the installed CLI without parallel startup logic."""

from __future__ import annotations

import ast
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_RUN = REPO / "app" / "run"


def _fake_uv(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    arguments = tmp_path / "arguments"
    environment = tmp_path / "environment"
    executable = bin_dir / "uv"
    executable.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$FURA_TEST_ARGUMENTS"\n'
        'printf "%s\\n" "$CHIRP_SKIP_CONTRACT_CHECKS" > "$FURA_TEST_ENVIRONMENT"\n'
        'exit "$FURA_TEST_EXIT"\n',
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return bin_dir, arguments, environment


def _run_wrapper(
    tmp_path: Path,
    *arguments: str,
    contract_checks: str | None = None,
    exit_code: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
    bin_dir, captured_arguments, captured_environment = _fake_uv(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FURA_TEST_ARGUMENTS": str(captured_arguments),
        "FURA_TEST_ENVIRONMENT": str(captured_environment),
        "FURA_TEST_EXIT": str(exit_code),
    }
    env.pop("CHIRP_SKIP_CONTRACT_CHECKS", None)
    if contract_checks is not None:
        env["CHIRP_SKIP_CONTRACT_CHECKS"] = contract_checks

    completed = subprocess.run(
        (str(APP_RUN), *arguments),
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return (
        completed,
        captured_arguments.read_text(encoding="utf-8").splitlines(),
        captured_environment.read_text(encoding="utf-8").strip(),
    )


def test_app_run_forwards_flags_defaults_environment_and_exit_status(tmp_path: Path) -> None:
    flags = ("--author", "--preview", "--port", "8123", "--json")

    completed, arguments, contract_checks = _run_wrapper(tmp_path, *flags, exit_code=23)

    assert completed.returncode == 23
    assert arguments == ["run", "fura", "serve", *flags]
    assert contract_checks == "1"


def test_app_run_preserves_explicit_contract_check_setting(tmp_path: Path) -> None:
    completed, arguments, contract_checks = _run_wrapper(
        tmp_path,
        "--port",
        "9000",
        contract_checks="0",
    )

    assert completed.returncode == 0
    assert arguments == ["run", "fura", "serve", "--port", "9000"]
    assert contract_checks == "0"


def test_checkout_launchers_share_the_cli_and_app_module_is_import_only() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    wrapper = APP_RUN.read_text(encoding="utf-8")
    app_source = (REPO / "app" / "app.py").read_text(encoding="utf-8")

    assert "serve:\n\t$(UV_RUN) fura serve" in makefile
    assert 'exec uv run fura serve "$@"' in wrapper
    assert "app/app.py" not in wrapper
    assert "run_serve(" not in app_source
    assert not any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in ast.parse(app_source).body
    )


def test_checkout_docs_name_canonical_and_optional_launcher_paths() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    installation = (
        REPO / "content" / "furatena" / "docs" / "get-started" / "installation.md"
    ).read_text(encoding="utf-8")
    guidance = f"{readme}\n{installation}"

    assert "uv run fura serve" in guidance
    assert "uv tool install --editable ." in guidance
    assert "uv tool update-shell" in guidance
    assert "uv tool dir --bin" in guidance
