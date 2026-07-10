"""Migration playbook, ownership, and safe-remediation contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from furatena.catalog.benchmarks import assert_free_threading
from furatena.catalog.migrate import (
    build_migration_report,
    remediate_mdx_file_safely,
)
from furatena.cli.main import main, run_command


def test_playbook_groups_findings_by_ecosystem_risk_source_and_owner() -> None:
    catalog = SimpleNamespace(
        nodes=(
            SimpleNamespace(
                source_path="docs/safe.mdx",
                content_format="mdx",
                body_md="<Note>Mapped</Note>\n",
                meta={"owner": "devrel"},
            ),
            SimpleNamespace(
                source_path="docs/legacy.mdx",
                content_format="mdx",
                body_md='<ApiTable endpoint="/v1" />\n',
                meta={"owner": "platform-docs"},
            ),
        )
    )

    report = build_migration_report(catalog)
    plan = report["remediation_plan"]

    assert report["groups"]["by_owner"] == {"devrel": 1, "platform-docs": 1}
    assert plan["groups"]["by_ecosystem"] == {"mdx": 2}
    assert plan["groups"]["by_owner"] == {"devrel": 1, "platform-docs": 1}
    assert plan["groups"]["by_source_path"] == {
        "docs/legacy.mdx": 1,
        "docs/safe.mdx": 1,
    }
    assert plan["groups"]["by_risk"] == {"manual": 1, "safe": 1}
    assert plan["safe_candidate_count"] == 1
    assert plan["manual_blocker_count"] == 1
    safe = next(item for item in plan["items"] if item["risk"] == "safe")
    manual = next(item for item in plan["items"] if item["risk"] == "manual")
    assert safe["automation"] == {
        "command": "fura migrate --apply-safe docs/safe.mdx --json",
        "writes": "canonical .md sibling",
        "preserves_source": True,
        "overwrites_existing": False,
    }
    assert safe["reversible"] is True
    assert manual["automation"] is None
    assert manual["owner"] == "platform-docs"


def test_safe_remediation_preserves_source_and_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "guide.mdx"
    original = "---\ntitle: Guide\nowner: devrel\n---\n\n<Note>Keep this safe</Note>\n"
    source.write_text(original, encoding="utf-8")

    preview = remediate_mdx_file_safely(source, write=False)
    applied = remediate_mdx_file_safely(source, write=True)
    repeated = remediate_mdx_file_safely(source, write=True)

    assert preview.status == "planned"
    assert applied.status == "applied"
    assert repeated.status == "unchanged"
    assert source.read_text(encoding="utf-8") == original
    assert source.with_suffix(".md").is_file()
    assert ":::note" in source.with_suffix(".md").read_text(encoding="utf-8")
    assert applied.source_sha256 == repeated.source_sha256
    assert applied.target_sha256 == repeated.target_sha256
    assert applied.to_dict()["reversible"] is True


def test_playbook_never_automates_part_of_a_source_with_manual_blockers() -> None:
    catalog = SimpleNamespace(
        nodes=(
            SimpleNamespace(
                source_path="docs/mixed.mdx",
                content_format="mdx",
                body_md='<Note>Mapped</Note>\n<ApiTable endpoint="/v1" />\n',
                meta={"owner": "devrel"},
            ),
        )
    )

    plan = build_migration_report(catalog)["remediation_plan"]

    assert plan["safe_candidate_count"] == 0
    assert plan["manual_blocker_count"] == 2
    assert {item["risk"] for item in plan["items"]} == {"manual"}
    assert all(item["automation"] is None for item in plan["items"])


def test_safe_remediation_refuses_unmapped_components_and_target_conflicts(
    tmp_path: Path,
) -> None:
    unmapped = tmp_path / "unmapped.mdx"
    unmapped.write_text('<ApiTable endpoint="/v1" />\n', encoding="utf-8")
    conflict = tmp_path / "conflict.mdx"
    conflict.write_text("<Note>Converted</Note>\n", encoding="utf-8")
    conflict_target = conflict.with_suffix(".md")
    conflict_target.write_text("hand-authored target\n", encoding="utf-8")

    unmapped_result = remediate_mdx_file_safely(unmapped, write=True)
    conflict_result = remediate_mdx_file_safely(conflict, write=True)

    assert unmapped_result.status == "manual"
    assert "unmapped JSX" in unmapped_result.reason
    assert not unmapped.with_suffix(".md").exists()
    assert conflict_result.status == "manual"
    assert "refusing to overwrite" in conflict_result.reason
    assert conflict_target.read_text(encoding="utf-8") == "hand-authored target\n"
    assert unmapped.is_file() and conflict.is_file()


def test_concurrent_safe_remediation_is_free_thread_safe(tmp_path: Path) -> None:
    assert_free_threading()
    source = tmp_path / "concurrent.mdx"
    source.write_text("<Tip>One deterministic output</Tip>\n", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = list(
            executor.map(lambda _: remediate_mdx_file_safely(source, write=True), range(64))
        )

    assert sum(result.status == "applied" for result in results) == 1
    assert sum(result.status == "unchanged" for result in results) == 63
    assert len({result.target_sha256 for result in results}) == 1
    assert source.is_file()
    assert source.with_suffix(".md").is_file()


def test_cli_applies_safe_sources_and_reports_manual_blockers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_root = tmp_path / "docs-site"
    result = run_command(["init", str(app_root), "--name", "Migration Playbook"])
    assert result is not None and result.ok
    safe = app_root / "content/docs/safe.mdx"
    manual = app_root / "content/docs/manual.mdx"
    safe.write_text("---\ntitle: Safe\nowner: devrel\n---\n\n<Note>Safe</Note>\n", encoding="utf-8")
    manual.write_text(
        '---\ntitle: Manual\nowner: platform-docs\n---\n\n<ApiTable endpoint="/v1" />\n',
        encoding="utf-8",
    )
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        main(["--app-root", str(app_root), "migrate", "--apply-safe", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert int(exc_info.value.code) == 1
    assert payload["command"] == "migrate"
    assert payload["data"]["manual_count"] == 1
    assert payload["data"]["source_preservation"] == "required"
    assert payload["data"]["overwrite_existing"] is False
    assert safe.is_file() and safe.with_suffix(".md").is_file()
    assert manual.is_file() and not manual.with_suffix(".md").exists()
    statuses = {
        Path(item["source_path"]).name: item["status"] for item in payload["data"]["remediations"]
    }
    assert statuses == {"manual.mdx": "manual", "safe.mdx": "applied"}
