"""Contract tests for the open-source PyPI Trusted Publishing workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "python-publish.yml"


def test_python_publish_workflow_matches_trusted_publishing_pattern() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    triggers = workflow.get("on") or workflow.get(True)
    jobs = workflow["jobs"]

    assert triggers == {"release": {"types": ["published"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(jobs) == {"release-gate", "release-build", "pypi-publish"}

    assert jobs["release-build"]["needs"] == ["release-gate"]
    assert jobs["pypi-publish"]["needs"] == ["release-build"]
    assert jobs["pypi-publish"]["permissions"] == {"id-token": "write"}
    assert jobs["pypi-publish"]["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/p/furatena",
    }

    assert "PYPI_TOKEN" not in source
    assert "pypa/gh-action-pypi-publish@release/v1" in source
    assert "make ci-release" in source
    assert "uvx twine check dist/*" in source
    assert "furatena.__version__" in source
    assert 'tag="${GITHUB_REF_NAME#v}"' in source


def test_pypi_runbook_documents_trusted_publisher_registration() -> None:
    docs = (REPO / "docs" / "PYPI.md").read_text(encoding="utf-8")
    decision = (REPO / "docs" / "OSS_DISTRIBUTION_DECISION.md").read_text(encoding="utf-8")

    assert "Trusted Publishing" in docs
    assert "python-publish.yml" in docs
    assert "Environment name" in docs
    assert "`pypi`" in docs
    assert "lbliii" in docs
    assert "furatena" in docs
    assert "MIT" in decision
    assert "Railway" in decision
    assert "kickbacks" in decision.lower() or "marketplace" in decision.lower()
