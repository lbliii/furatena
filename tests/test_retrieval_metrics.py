"""Known-answer retrieval metrics and regression approval contracts."""

from __future__ import annotations

from pathlib import Path

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.retrieval_dataset import load_known_answer_dataset
from furatena.catalog.retrieval_metrics import (
    RetrievalObservation,
    evaluate_retrieval_observations,
    load_retrieval_thresholds,
    run_retrieval_evaluation,
)

REPO = Path(__file__).resolve().parents[1]


def _perfect_observations():
    dataset = load_known_answer_dataset()
    observations = []
    for case in dataset.cases:
        target_ids = tuple(target.node_id for target in case.targets)
        if case.result_policy == "no_results":
            public = ()
            trusted = ()
        elif case.result_policy == "excluded_public_included_trusted":
            public = ()
            trusted = target_ids
        else:
            public = target_ids
            trusted = target_ids
        observations.append(RetrievalObservation(case.id, public, trusted))
    return dataset, tuple(observations)


def _strict_policy() -> dict[str, object]:
    return {
        "policy_id": "test-ratchet",
        "version": "1",
        "dataset_id": "furatena-known-answers",
        "dataset_version": "1.1.0",
        "overall": {
            "recall_at_3_min": 1.0,
            "mrr_min": 1.0,
            "no_result_rate_min": 1.0,
            "stale_answer_failures_max": 0,
            "private_leaks_max": 0,
        },
    }


def test_metrics_report_recall_mrr_negative_stale_and_access_boundaries() -> None:
    dataset, observations = _perfect_observations()

    report = evaluate_retrieval_observations(
        dataset,
        observations,
        threshold_policy=_strict_policy(),
    )

    assert report["ok"] is True
    assert report["metrics"]["overall"] == {
        "case_count": 8,
        "retrieval_case_count": 7,
        "negative_case_count": 1,
        "recall_at_3": 1.0,
        "mrr": 1.0,
        "no_result_rate": 1.0,
        "stale_answer_failures": 0,
        "private_leaks": 0,
    }
    assert set(report["metrics"]["by_corpus"]) == {
        "furatena-dogfood",
        "access-boundary-fixture",
    }
    assert "cross_surface" in report["metrics"]["by_query_class"]


def test_unapproved_regression_fails_and_reasoned_approval_is_auditable() -> None:
    dataset, observations = _perfect_observations()
    first = observations[0]
    regressed = (
        RetrievalObservation(first.case_id, ("irrelevant",), ("irrelevant",)),
        *observations[1:],
    )

    blocked = evaluate_retrieval_observations(
        dataset,
        regressed,
        threshold_policy=_strict_policy(),
    )
    approved = evaluate_retrieval_observations(
        dataset,
        regressed,
        threshold_policy=_strict_policy(),
        approval_reason="accepted for the ranking migration",
    )

    assert blocked["ok"] is False
    assert blocked["approval"] == {"required": True, "approved": False, "reason": None}
    assert any("recall_at_3" in item for item in blocked["regressions"])
    assert approved["ok"] is True
    assert approved["approval"] == {
        "required": True,
        "approved": True,
        "reason": "accepted for the ranking migration",
    }


def test_private_leak_and_stale_answer_are_threshold_failures() -> None:
    dataset, observations = _perfect_observations()
    access_index = next(
        index for index, case in enumerate(dataset.cases) if case.query_class == "access_restricted"
    )
    access = observations[access_index]
    leaked = RetrievalObservation(
        access.case_id,
        access.trusted_node_ids,
        access.trusted_node_ids,
        frozenset(access.trusted_node_ids),
    )
    changed = (*observations[:access_index], leaked, *observations[access_index + 1 :])

    report = evaluate_retrieval_observations(
        dataset,
        changed,
        threshold_policy=_strict_policy(),
    )

    assert report["metrics"]["overall"]["private_leaks"] == 1
    assert report["metrics"]["overall"]["stale_answer_failures"] == 1
    assert report["regression_count"] == 2


def test_active_catalog_matches_packaged_free_threaded_ratchet() -> None:
    docs = DocsApp.from_paths(
        REPO / "app" / "docs.yaml",
        repo_root=REPO,
        autodoc_config=REPO / "config" / "autodoc.yaml",
        autodoc=True,
    )

    report = run_retrieval_evaluation(docs)

    assert report["ok"] is True
    assert report["applicable"] is True
    assert report["regressions"] == []
    assert report["metrics"]["overall"] == {
        "case_count": 8,
        "retrieval_case_count": 7,
        "negative_case_count": 1,
        "recall_at_3": 0.714286,
        "mrr": 0.678571,
        "no_result_rate": 0.0,
        "stale_answer_failures": 0,
        "private_leaks": 0,
    }
    access = next(item for item in report["cases"] if item["query_class"] == "access_restricted")
    assert access["relevant_rank"] == 1
    assert access["private_leak_node_ids"] == []
    assert load_retrieval_thresholds()["runtime"] == "CPython 3.14t, PYTHON_GIL=0"
