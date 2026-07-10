"""Documentation completeness and exemption gate contracts."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from furatena.catalog import docs_quality
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.docs_quality import (
    _broken_link_findings,
    _freshness_findings,
    _orphan_findings,
    _snippet_findings,
    _snippet_semantic_errors,
    build_docs_quality_report,
    load_docs_quality_exemptions,
)
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.cli.main import run_command

REPO = Path(__file__).resolve().parents[1]
EXEMPTIONS = REPO / "docs" / "docs-quality-exemptions.json"
DOC_ROOTS = (REPO / "docs", REPO / "content" / "furatena", REPO / "app" / "content")


@pytest.fixture(scope="module")
def docs_app() -> DocsApp:
    return DocsApp.from_paths(
        REPO / "app" / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )


def test_repository_docs_quality_gate_is_clean(docs_app: DocsApp) -> None:
    previous = json.loads((REPO / "docs" / "public-surface-inventory.json").read_text())
    report = build_docs_quality_report(
        docs_app,
        documentation_roots=DOC_ROOTS,
        exemptions=load_docs_quality_exemptions(EXEMPTIONS),
        previous_inventory=previous,
    )

    assert report["ok"] is True
    assert report["findings"] == []
    assert report["unused_exemptions"] == []
    assert report["summary"] == {
        "finding_count": 8,
        "active_count": 0,
        "exempted_count": 8,
        "unused_exemption_count": 0,
    }
    assert {item["disposition"] for item in report["exemptions"]} == {
        "deferred",
        "internal",
    }
    assert all(item["reason"] for item in report["exemptions"])
    assert report["snippet_summary"]["block_count"] >= 80


def test_unexempted_report_names_owner_and_page_type(docs_app: DocsApp) -> None:
    report = build_docs_quality_report(docs_app, documentation_roots=DOC_ROOTS)

    assert report["ok"] is False
    assert {item["rule_id"] for item in report["findings"]} == {
        "fura.docs_quality.navigation",
        "fura.docs_quality.public_feature",
    }
    assert all(item["owner"] for item in report["findings"])
    assert all(item["recommended_page_type"] for item in report["findings"])


def test_docs_quality_command_returns_actionable_validation_diagnostics(tmp_path: Path) -> None:
    missing = tmp_path / "missing-exemptions.json"
    result = run_command(["docs-quality", "--exemptions", str(missing)])

    assert result is not None
    assert result.ok is False
    assert int(result.exit_code) == 2
    assert result.diagnostics
    assert result.diagnostics[0].rule_id.startswith("fura.docs_quality.")
    assert "Owner:" in str(result.diagnostics[0].next_action)
    assert "Recommended" in str(result.diagnostics[0].next_action)


def test_broken_link_and_orphan_detectors_are_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        docs_quality,
        "check_broken_internal_links",
        lambda catalog: ["guide.md:3: broken internal link [Missing](/docs/missing/)"],
    )
    catalog = SimpleNamespace(
        nodes=[
            SimpleNamespace(
                source_path="guide.md",
                slug="guide",
                url="/docs/guide/",
                meta={"owner": "devrel"},
            )
        ]
    )
    broken = _broken_link_findings(catalog)
    assert broken[0].rule_id == "fura.docs_quality.link"
    assert broken[0].owner == "devrel"
    assert broken[0].recommended_page_type == "how-to"

    lonely = SimpleNamespace(
        url="/docs/concepts/lonely/",
        source_path="docs/concepts/lonely.md",
        meta={},
    )
    fake_docs = SimpleNamespace(catalog=SimpleNamespace(nodes=[lonely], nav_tree=lambda: []))
    monkeypatch.setattr(docs_quality, "accessible_nodes", lambda *args, **kwargs: [lonely])
    monkeypatch.setattr(docs_quality, "build_federated_backlinks", lambda *args, **kwargs: {})
    orphan = _orphan_findings(fake_docs)
    assert orphan[0].rule_id == "fura.docs_quality.orphan"
    assert orphan[0].recommended_page_type == "explanation"


def test_exemptions_require_exact_reasoned_dispositions(tmp_path: Path) -> None:
    path = tmp_path / "exemptions.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "exemptions": [
                    {"finding": "navigation:/docs/", "disposition": "ignored", "reason": ""}
                ],
            }
        )
    )

    with pytest.raises(ValueError, match="internal/deferred disposition"):
        load_docs_quality_exemptions(path)


def test_snippet_gate_checks_shell_syntax_cli_options_and_make_targets(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "bad.md").write_text("```bash\nif true; then\n```\n", encoding="utf-8")

    findings, summary = _snippet_findings((root,))
    assert summary == {"file_count": 1, "block_count": 1}
    assert findings[0].rule_id == "fura.docs_quality.snippet"
    assert "unexpected end of file" in findings[0].message

    errors = _snippet_semantic_errors(
        "fura export --removed-option\nmake removed-target",
        make_targets={"export"},
    )
    assert errors == [
        "unknown option --removed-option for fura export",
        "unknown make target removed-target",
    ]


def test_freshness_gate_requires_owner_valid_date_and_threshold() -> None:
    nodes = [
        SimpleNamespace(
            url="/docs/operations/missing/",
            source_path="docs/operations/missing.md",
            meta={},
        ),
        SimpleNamespace(
            url="/docs/reference/stale/",
            source_path="docs/reference/stale.md",
            meta={"owner": "platform-docs", "reviewed_at": "2025-01-01"},
        ),
        SimpleNamespace(
            url="/docs/reference/current/",
            source_path="docs/reference/current.md",
            meta={"owner": "platform-docs", "reviewed_at": "2026-07-01"},
        ),
    ]
    fake_docs = SimpleNamespace(catalog=SimpleNamespace(nodes=nodes))

    findings = _freshness_findings(
        fake_docs,
        freshness_days=180,
        today=date(2026, 7, 7),
    )
    assert [item.target for item in findings] == [
        "/docs/operations/missing/",
        "/docs/reference/stale/",
    ]
    assert all(item.rule_id == "fura.docs_quality.freshness" for item in findings)
