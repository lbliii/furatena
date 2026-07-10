"""Deterministic quality metrics and regression gates for retrieval datasets."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.registry import CatalogRegistry, MountConfig
from furatena.catalog.retrieval_dataset import (
    KnownAnswerCase,
    KnownAnswerDataset,
    RetrievalCorpus,
    load_known_answer_dataset,
)
from furatena.catalog.semantic import hybrid_search

_METRIC_MINIMUMS = frozenset({"recall_at_3", "mrr", "no_result_rate"})
_METRIC_MAXIMUMS = frozenset({"stale_answer_failures", "private_leaks"})


@dataclass(frozen=True, slots=True)
class RetrievalObservation:
    """Ranked public/trusted results observed for one known-answer case."""

    case_id: str
    public_node_ids: tuple[str, ...]
    trusted_node_ids: tuple[str, ...]
    stale_node_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RetrievalCaseResult:
    """One scored known-answer result."""

    case_id: str
    corpus: str
    query_class: str
    result_policy: str
    relevant_rank: int | None
    recalled_at_3: bool | None
    no_result: bool | None
    stale_answer_node_ids: tuple[str, ...]
    private_leak_node_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "corpus": self.corpus,
            "query_class": self.query_class,
            "result_policy": self.result_policy,
            "relevant_rank": self.relevant_rank,
            "recalled_at_3": self.recalled_at_3,
            "no_result": self.no_result,
            "stale_answer_node_ids": list(self.stale_answer_node_ids),
            "private_leak_node_ids": list(self.private_leak_node_ids),
        }


@dataclass(frozen=True, slots=True)
class RetrievalMetricSlice:
    """Aggregate retrieval metrics for a deterministic case slice."""

    case_count: int
    retrieval_case_count: int
    negative_case_count: int
    recall_at_3: float | None
    mrr: float | None
    no_result_rate: float | None
    stale_answer_failures: int
    private_leaks: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "retrieval_case_count": self.retrieval_case_count,
            "negative_case_count": self.negative_case_count,
            "recall_at_3": self.recall_at_3,
            "mrr": self.mrr,
            "no_result_rate": self.no_result_rate,
            "stale_answer_failures": self.stale_answer_failures,
            "private_leaks": self.private_leaks,
        }


def load_retrieval_thresholds(
    version: str = "v1",
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Load the packaged threshold policy or an explicit local policy."""
    if path is not None:
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        resource = resources.files("furatena.catalog").joinpath(
            "eval_datasets", version, "thresholds.json"
        )
        try:
            raw = json.loads(resource.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"unknown retrieval threshold version: {version}") from exc
    if not isinstance(raw, dict):
        raise ValueError("retrieval threshold policy must be an object")
    return raw


def run_retrieval_evaluation(
    docs_app: Any,
    *,
    dataset: KnownAnswerDataset | None = None,
    threshold_policy: Mapping[str, Any] | None = None,
    approval_reason: str | None = None,
) -> dict[str, Any]:
    """Execute known-answer searches and return sliced, thresholded metrics."""
    active_dataset = dataset or load_known_answer_dataset()
    policy = dict(threshold_policy or load_retrieval_thresholds())
    required_mounts = {
        corpus.mount for corpus in active_dataset.corpora if corpus.kind == "repository"
    }
    available_mounts = {str(mount.id) for mount in getattr(docs_app.catalog, "mounts", ())}
    missing_mounts = sorted(required_mounts - available_mounts)
    if missing_mounts:
        return _not_applicable_report(active_dataset, policy, missing_mounts)
    stale_node_ids = _stale_node_ids(docs_app.catalog)
    observations: list[RetrievalObservation] = []
    for corpus in active_dataset.corpora:
        if corpus.kind == "repository":
            observations.extend(
                _observe_cases(
                    active_dataset,
                    corpus_id=corpus.id,
                    catalog=docs_app.catalog,
                    index=docs_app.embedding_index,
                    stale_node_ids=stale_node_ids,
                )
            )
    for corpus in active_dataset.corpora:
        if corpus.kind == "synthetic":
            observations.extend(_observe_synthetic_corpus(active_dataset, corpus))
    return evaluate_retrieval_observations(
        active_dataset,
        observations,
        threshold_policy=policy,
        approval_reason=approval_reason,
    )


def evaluate_retrieval_observations(
    dataset: KnownAnswerDataset,
    observations: Sequence[RetrievalObservation],
    *,
    threshold_policy: Mapping[str, Any],
    approval_reason: str | None = None,
) -> dict[str, Any]:
    """Score fixed observations and enforce the supplied threshold policy."""
    by_case = {item.case_id: item for item in observations}
    expected_ids = {case.id for case in dataset.cases}
    if set(by_case) != expected_ids or len(by_case) != len(observations):
        missing = sorted(expected_ids - set(by_case))
        extra = sorted(set(by_case) - expected_ids)
        raise ValueError(
            f"retrieval observations do not match dataset; missing={missing}, extra={extra}"
        )

    results = tuple(_score_case(case, by_case[case.id]) for case in dataset.cases)
    metrics = {
        "overall": _metric_slice(results).to_dict(),
        "by_corpus": {
            corpus.id: _metric_slice(
                tuple(item for item in results if item.corpus == corpus.id)
            ).to_dict()
            for corpus in dataset.corpora
        },
        "by_query_class": {
            query_class: _metric_slice(
                tuple(item for item in results if item.query_class == query_class)
            ).to_dict()
            for query_class in sorted({case.query_class for case in dataset.cases})
        },
    }
    regressions = _threshold_regressions(dataset, metrics, threshold_policy)
    reason = (approval_reason or "").strip()
    approved = bool(regressions and reason)
    return {
        "schema_version": 1,
        "applicable": True,
        "dataset": {"id": dataset.dataset_id, "version": dataset.version},
        "threshold_policy": {
            "id": str(threshold_policy.get("policy_id") or ""),
            "version": str(threshold_policy.get("version") or ""),
        },
        "metrics": metrics,
        "cases": [item.to_dict() for item in results],
        "regressions": list(regressions),
        "regression_count": len(regressions),
        "approval": {
            "required": bool(regressions),
            "approved": approved,
            "reason": reason or None,
        },
        "ok": not regressions or approved,
    }


def _not_applicable_report(
    dataset: KnownAnswerDataset,
    policy: Mapping[str, Any],
    missing_mounts: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "applicable": False,
        "dataset": {"id": dataset.dataset_id, "version": dataset.version},
        "threshold_policy": {
            "id": str(policy.get("policy_id") or ""),
            "version": str(policy.get("version") or ""),
        },
        "metrics": None,
        "cases": [],
        "regressions": [],
        "regression_count": 0,
        "approval": {"required": False, "approved": False, "reason": None},
        "skip_reason": f"dataset corpus mounts are unavailable: {', '.join(missing_mounts)}",
        "ok": True,
    }


def _observe_cases(
    dataset: KnownAnswerDataset,
    *,
    corpus_id: str,
    catalog: Any,
    index: EmbeddingIndex,
    stale_node_ids: frozenset[str] = frozenset(),
) -> tuple[RetrievalObservation, ...]:
    observations: list[RetrievalObservation] = []
    for case in dataset.cases:
        if case.corpus != corpus_id:
            continue
        public_ids = _search_node_ids(catalog, index, case.query, include_private=False)
        trusted_ids = (
            _search_node_ids(catalog, index, case.query, include_private=True)
            if case.result_policy == "excluded_public_included_trusted"
            else public_ids
        )
        observations.append(RetrievalObservation(case.id, public_ids, trusted_ids, stale_node_ids))
    return tuple(observations)


def _observe_synthetic_corpus(
    dataset: KnownAnswerDataset,
    corpus: RetrievalCorpus,
) -> tuple[RetrievalObservation, ...]:
    resource = resources.files("furatena.catalog").joinpath(corpus.root)
    with resources.as_file(resource) as content_root:
        root = Path(content_root)
        catalog = CatalogRegistry(
            (
                MountConfig(
                    id=corpus.mount,
                    label=corpus.id,
                    content_root=root,
                    url_prefix=corpus.url_prefix,
                    default=True,
                ),
            ),
            repo_root=Path(content_root),
            app_root=root,
            include_private=True,
            autodoc=False,
        )
        index = EmbeddingIndex.from_nodes(list(catalog.nodes))
        return _observe_cases(
            dataset,
            corpus_id=corpus.id,
            catalog=catalog,
            index=index,
        )


def _search_node_ids(
    catalog: Any,
    index: EmbeddingIndex,
    query: str,
    *,
    include_private: bool,
) -> tuple[str, ...]:
    result = hybrid_search(
        catalog,
        index,
        query,
        limit=12,
        include_private=include_private,
    )
    return tuple(hit.node.node_id for hit in result.hits)


def _stale_node_ids(catalog: Any) -> frozenset[str]:
    if not hasattr(catalog, "author_stale_entries"):
        return frozenset()
    node_ids: set[str] = set()
    for entry in catalog.author_stale_entries():
        slug = str(entry.get("slug") or "")
        mount = str(entry.get("mount") or "")
        node = catalog.get_by_slug(slug, mount=mount) if slug else None
        if node is not None:
            node_ids.add(node.node_id)
    return frozenset(node_ids)


def _score_case(case: KnownAnswerCase, observed: RetrievalObservation) -> RetrievalCaseResult:
    relevant = {target.node_id for target in (*case.targets, *case.acceptable_alternatives)}
    ranked = (
        observed.trusted_node_ids
        if case.result_policy == "excluded_public_included_trusted"
        else observed.public_node_ids
    )
    relevant_rank = next(
        (rank for rank, node_id in enumerate(ranked, start=1) if node_id in relevant),
        None,
    )
    is_negative = case.result_policy == "no_results"
    stale = tuple(node_id for node_id in ranked[:3] if node_id in observed.stale_node_ids)
    leaks = (
        tuple(node_id for node_id in observed.public_node_ids if node_id in relevant)
        if case.result_policy == "excluded_public_included_trusted"
        else ()
    )
    return RetrievalCaseResult(
        case_id=case.id,
        corpus=case.corpus,
        query_class=case.query_class,
        result_policy=case.result_policy,
        relevant_rank=None if is_negative else relevant_rank,
        recalled_at_3=None if is_negative else relevant_rank is not None and relevant_rank <= 3,
        no_result=not observed.public_node_ids if is_negative else None,
        stale_answer_node_ids=stale,
        private_leak_node_ids=leaks,
    )


def _metric_slice(results: Sequence[RetrievalCaseResult]) -> RetrievalMetricSlice:
    retrieval = tuple(item for item in results if item.recalled_at_3 is not None)
    negative = tuple(item for item in results if item.no_result is not None)
    recall = (
        sum(bool(item.recalled_at_3) for item in retrieval) / len(retrieval) if retrieval else None
    )
    mrr = (
        sum(1.0 / item.relevant_rank if item.relevant_rank else 0.0 for item in retrieval)
        / len(retrieval)
        if retrieval
        else None
    )
    no_result_rate = (
        sum(bool(item.no_result) for item in negative) / len(negative) if negative else None
    )
    return RetrievalMetricSlice(
        case_count=len(results),
        retrieval_case_count=len(retrieval),
        negative_case_count=len(negative),
        recall_at_3=_rounded(recall),
        mrr=_rounded(mrr),
        no_result_rate=_rounded(no_result_rate),
        stale_answer_failures=sum(len(item.stale_answer_node_ids) for item in results),
        private_leaks=sum(len(item.private_leak_node_ids) for item in results),
    )


def _threshold_regressions(
    dataset: KnownAnswerDataset,
    metrics: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> tuple[str, ...]:
    if (
        policy.get("dataset_id") != dataset.dataset_id
        or policy.get("dataset_version") != dataset.version
    ):
        raise ValueError("retrieval threshold policy does not match the dataset identity")
    findings: list[str] = []
    _compare_slice(findings, "overall", metrics["overall"], policy.get("overall"))
    for group, key in (("by_corpus", "corpora"), ("by_query_class", "query_classes")):
        rules = policy.get(key)
        if not isinstance(rules, Mapping):
            continue
        for name, rule in rules.items():
            observed = metrics[group].get(name)
            if observed is None:
                findings.append(f"{group}.{name}: metric slice is missing")
            else:
                _compare_slice(findings, f"{group}.{name}", observed, rule)
    return tuple(findings)


def _compare_slice(
    findings: list[str],
    label: str,
    observed: Mapping[str, Any],
    rules: Any,
) -> None:
    if not isinstance(rules, Mapping):
        return
    for metric in sorted(_METRIC_MINIMUMS):
        key = f"{metric}_min"
        if key not in rules:
            continue
        value = observed.get(metric)
        minimum = float(rules[key])
        if value is None or float(value) + 1e-9 < minimum:
            findings.append(f"{label}.{metric}: {value} is below {minimum}")
    for metric in sorted(_METRIC_MAXIMUMS):
        key = f"{metric}_max"
        if key not in rules:
            continue
        value = observed.get(metric)
        maximum = int(rules[key])
        if value is None or int(value) > maximum:
            findings.append(f"{label}.{metric}: {value} exceeds {maximum}")


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None
