"""Documentation completeness and exemption gate contracts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from furatena.catalog import docs_quality
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.docs_quality import (
    _broken_link_findings,
    _orphan_findings,
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
        "finding_count": 9,
        "active_count": 0,
        "exempted_count": 9,
        "unused_exemption_count": 0,
    }
    assert {item["disposition"] for item in report["exemptions"]} == {
        "deferred",
        "internal",
    }
    assert all(item["reason"] for item in report["exemptions"])


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
    fake_docs = SimpleNamespace(
        catalog=SimpleNamespace(nodes=[lonely], nav_tree=lambda: [])
    )
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
