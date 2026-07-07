"""Generated CLI/config reference and drift gate contracts."""

from __future__ import annotations

from pathlib import Path

from furatena.catalog.docs_reference import (
    cli_reference_records,
    config_reference_records,
    environment_reference_records,
    render_docs_reference,
)
from furatena.cli.main import run_command

REPO = Path(__file__).resolve().parents[1]
REFERENCE = REPO / "content" / "furatena" / "docs" / "reference" / "generated-cli-config.md"


def test_committed_reference_matches_active_implementation() -> None:
    rendered = render_docs_reference()

    assert REFERENCE.read_text(encoding="utf-8") == rendered
    assert len(cli_reference_records()) >= 30
    assert len(config_reference_records()) >= 90
    environment = {record["name"]: record for record in environment_reference_records()}
    assert {
        "FURA_APP_ROOT",
        "FURA_BASE_PATH",
        "FURA_BASE_URL",
        "FURA_CHANNEL",
        "FURA_ENV",
        "FURA_LANG",
        "FURA_PORT",
        "FURA_SESSION_SECRET",
        "FURA_WORKERS",
    } <= set(environment)
    assert "Error and remediation examples" in rendered
    assert "fura.docs_reference.drift" not in rendered
    assert str(REPO) not in rendered
    assert "`app/theme-skin`" in rendered


def test_docs_reference_command_writes_and_detects_drift(tmp_path: Path) -> None:
    output = tmp_path / "reference.md"
    output.write_text("stale\n", encoding="utf-8")

    drifted = run_command(["docs-reference", "--output", str(output), "--check"])
    assert drifted is not None
    assert drifted.ok is False
    assert int(drifted.exit_code) == 2
    assert drifted.diagnostics[0].rule_id == "fura.docs_reference.drift"

    written = run_command(["docs-reference", "--output", str(output)])
    assert written is not None and written.ok is True
    current = run_command(["docs-reference", "--output", str(output), "--check"])
    assert current is not None and current.ok is True
