"""Versioned retrieval known-answer dataset contracts."""

from __future__ import annotations

from dataclasses import replace
from importlib import resources
from pathlib import Path

import pytest

from furatena.catalog.access import AccessPolicy, AccessRole
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.lifecycle import visibility_state
from furatena.catalog.retrieval_dataset import (
    load_known_answer_dataset,
    validate_dataset_against_catalog,
    validate_known_answer_dataset,
    verify_dataset_provenance,
)
from furatena.catalog.sources.parse import parse_source_text

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def dataset():
    return load_known_answer_dataset()


def test_known_answer_dataset_covers_required_query_and_content_classes(dataset) -> None:
    assert dataset.schema_version == 1
    assert dataset.dataset_id == "furatena-known-answers"
    assert dataset.version == "1.1.8"
    assert validate_known_answer_dataset(dataset) == ()
    assert {case.query_class for case in dataset.cases} >= {
        "navigational",
        "factual",
        "troubleshooting",
        "cross_surface",
        "negative",
        "access_restricted",
    }
    assert {case.corpus for case in dataset.cases} == {
        "furatena-dogfood",
        "access-boundary-fixture",
    }
    assert set().union(*(set(case.surfaces) for case in dataset.cases)) == {
        "browser",
        "dcp",
        "sidecar",
        "mcp",
    }
    assert any("/api/" in target.url for case in dataset.cases for target in case.targets)
    assert any(target.url == "/releases/" for case in dataset.cases for target in case.targets)
    assert any("/docs/operations/" in target.url for case in dataset.cases for target in case.targets)


def test_known_answer_dataset_provenance_matches_repository_and_fixture(dataset) -> None:
    assert verify_dataset_provenance(dataset, repo_root=REPO) == ()

    corpus = dataset.corpus("furatena-dogfood")
    drifted_source = replace(corpus.sources[0], sha256="0" * 64)
    drifted_corpus = replace(corpus, sources=(drifted_source, *corpus.sources[1:]))
    drifted_dataset = replace(
        dataset,
        corpora=tuple(
            drifted_corpus if item.id == drifted_corpus.id else item
            for item in dataset.corpora
        ),
    )
    findings = verify_dataset_provenance(drifted_dataset, repo_root=REPO)
    assert any("source drifted" in finding for finding in findings)


def test_dogfood_targets_and_sections_resolve_against_active_catalog(dataset) -> None:
    docs = DocsApp.from_paths(
        REPO / "app" / "docs.yaml",
        repo_root=REPO,
        autodoc_config=REPO / "config" / "autodoc.yaml",
        autodoc=True,
    )

    assert validate_dataset_against_catalog(
        dataset,
        docs.catalog,
        corpus_id="furatena-dogfood",
    ) == ()


def test_access_boundary_fixture_is_private_and_has_unique_sentinel(dataset) -> None:
    corpus = dataset.corpus("access-boundary-fixture")
    source = corpus.sources[0]
    path = resources.files("furatena.catalog").joinpath(corpus.root, source.path)
    raw = path.read_text(encoding="utf-8")
    meta, body = parse_source_text(raw, content_format="patitas-markdown")

    assert visibility_state(meta) == "private"
    assert "orion-cinder recovery sequence" in body
    assert AccessPolicy.from_page_meta(meta).roles == frozenset({AccessRole.ADMIN})
    case = next(item for item in dataset.cases if item.id == "exclude-private-incident-runbook")
    assert case.result_policy == "excluded_public_included_trusted"
    assert case.access == "admin"


def test_dataset_validation_rejects_duplicate_case_identity(dataset) -> None:
    invalid = replace(dataset, cases=(dataset.cases[0], dataset.cases[0]))

    assert validate_known_answer_dataset(invalid) == (
        "duplicate case id: navigate-installation",
    )


def test_dataset_validation_rejects_unknown_access_level(dataset) -> None:
    invalid_case = replace(dataset.cases[0], access="superuser")
    invalid = replace(dataset, cases=(invalid_case, *dataset.cases[1:]))

    assert validate_known_answer_dataset(invalid) == (
        "case navigate-installation has unknown access level superuser",
    )


def test_unknown_dataset_version_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown retrieval dataset version"):
        load_known_answer_dataset("v999")
