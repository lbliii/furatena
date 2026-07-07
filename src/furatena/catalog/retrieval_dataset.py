"""Versioned known-answer datasets for retrieval evaluation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

_QUERY_CLASSES = frozenset(
    {"navigational", "factual", "troubleshooting", "cross_surface", "negative", "access_restricted"}
)
_SURFACES = frozenset({"browser", "dcp", "sidecar", "mcp"})
_ACCESS_LEVELS = frozenset({"public", "reader", "contributor", "publisher", "admin"})
_RESULT_POLICIES = frozenset(
    {"any_target", "no_results", "excluded_public_included_trusted"}
)


class RetrievalDatasetError(ValueError):
    """Raised when a retrieval dataset violates its versioned contract."""


@dataclass(frozen=True, slots=True)
class CorpusSource:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RetrievalCorpus:
    id: str
    kind: str
    root: str
    revision: str
    sources: tuple[CorpusSource, ...]


@dataclass(frozen=True, slots=True)
class RetrievalTarget:
    node_id: str
    url: str
    sections: tuple[str, ...]
    source_path: str


@dataclass(frozen=True, slots=True)
class KnownAnswerCase:
    id: str
    corpus: str
    query_class: str
    query: str
    surfaces: tuple[str, ...]
    access: str
    result_policy: str
    targets: tuple[RetrievalTarget, ...]
    acceptable_alternatives: tuple[RetrievalTarget, ...]


@dataclass(frozen=True, slots=True)
class KnownAnswerDataset:
    schema_version: int
    dataset_id: str
    version: str
    description: str
    corpora: tuple[RetrievalCorpus, ...]
    cases: tuple[KnownAnswerCase, ...]

    def corpus(self, corpus_id: str) -> RetrievalCorpus:
        match = next((item for item in self.corpora if item.id == corpus_id), None)
        if match is None:
            raise KeyError(corpus_id)
        return match


def load_known_answer_dataset(version: str = "v1") -> KnownAnswerDataset:
    """Load and validate the packaged known-answer dataset version."""
    path = resources.files("furatena.catalog").joinpath(
        "eval_datasets", version, "known_answers.json"
    )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RetrievalDatasetError(f"unknown retrieval dataset version: {version}") from exc
    if not isinstance(raw, Mapping):
        raise RetrievalDatasetError("retrieval dataset root must be an object")
    dataset = _dataset_from_dict(raw)
    findings = validate_known_answer_dataset(dataset)
    if findings:
        raise RetrievalDatasetError("; ".join(findings))
    return dataset


def validate_known_answer_dataset(dataset: KnownAnswerDataset) -> tuple[str, ...]:
    """Return deterministic schema and identity findings for one dataset."""
    findings: list[str] = []
    if dataset.schema_version != 1:
        findings.append(f"unsupported schema_version: {dataset.schema_version}")
    if not dataset.dataset_id:
        findings.append("dataset_id is required")
    if not dataset.version:
        findings.append("dataset version is required")

    corpus_ids = [corpus.id for corpus in dataset.corpora]
    _append_duplicate_findings(findings, "corpus", corpus_ids)
    case_ids = [case.id for case in dataset.cases]
    _append_duplicate_findings(findings, "case", case_ids)
    known_corpora = set(corpus_ids)

    for corpus in dataset.corpora:
        if not corpus.revision.startswith("sha256:"):
            findings.append(f"corpus {corpus.id} revision must use sha256")
        source_paths = [source.path for source in corpus.sources]
        _append_duplicate_findings(findings, f"corpus {corpus.id} source", source_paths)
        for source in corpus.sources:
            if len(source.sha256) != 64:
                findings.append(f"corpus {corpus.id} source {source.path} has invalid sha256")

    for case in dataset.cases:
        prefix = f"case {case.id}"
        if case.corpus not in known_corpora:
            findings.append(f"{prefix} references unknown corpus {case.corpus}")
        if case.query_class not in _QUERY_CLASSES:
            findings.append(f"{prefix} has unknown query_class {case.query_class}")
        unknown_surfaces = sorted(set(case.surfaces) - _SURFACES)
        if not case.surfaces:
            findings.append(f"{prefix} requires at least one surface")
        if unknown_surfaces:
            findings.append(f"{prefix} has unknown surfaces: {', '.join(unknown_surfaces)}")
        if case.access not in _ACCESS_LEVELS:
            findings.append(f"{prefix} has unknown access level {case.access}")
        if case.result_policy not in _RESULT_POLICIES:
            findings.append(f"{prefix} has unknown result_policy {case.result_policy}")
        if not case.query.strip():
            findings.append(f"{prefix} query is required")
        if case.result_policy != "no_results" and not case.targets:
            findings.append(f"{prefix} requires at least one target")
        if case.result_policy == "no_results" and (case.targets or case.acceptable_alternatives):
            findings.append(f"{prefix} no_results policy cannot declare targets")
        target_ids = [
            target.node_id for target in (*case.targets, *case.acceptable_alternatives)
        ]
        _append_duplicate_findings(findings, f"{prefix} target", target_ids)
        for target in (*case.targets, *case.acceptable_alternatives):
            if not target.node_id or not target.url or not target.source_path:
                findings.append(f"{prefix} target identity, URL, and source_path are required")
    return tuple(findings)


def verify_dataset_provenance(
    dataset: KnownAnswerDataset,
    *,
    repo_root: Path,
) -> tuple[str, ...]:
    """Verify source hashes and aggregate corpus revisions without network access."""
    findings: list[str] = []
    package_root = resources.files("furatena.catalog")
    for corpus in dataset.corpora:
        observed_hashes: list[str] = []
        for source in corpus.sources:
            try:
                if corpus.kind == "repository":
                    data = (repo_root / corpus.root / source.path).read_bytes()
                else:
                    data = package_root.joinpath(corpus.root, source.path).read_bytes()
            except FileNotFoundError:
                findings.append(f"corpus {corpus.id} source is missing: {source.path}")
                continue
            observed = hashlib.sha256(data).hexdigest()
            observed_hashes.append(observed)
            if observed != source.sha256:
                findings.append(f"corpus {corpus.id} source drifted: {source.path}")
        aggregate = hashlib.sha256(
            "".join(f"{value}\n" for value in observed_hashes).encode("utf-8")
        ).hexdigest()
        if observed_hashes and corpus.revision != f"sha256:{aggregate}":
            findings.append(f"corpus {corpus.id} aggregate revision drifted")
    return tuple(findings)


def validate_dataset_against_catalog(
    dataset: KnownAnswerDataset,
    catalog: Any,
    *,
    corpus_id: str,
) -> tuple[str, ...]:
    """Verify expected public node, URL, source, and section identities."""
    findings: list[str] = []
    for case in dataset.cases:
        if case.corpus != corpus_id or case.result_policy == "no_results":
            continue
        for target in (*case.targets, *case.acceptable_alternatives):
            node = catalog.get_by_node_id(target.node_id)
            if node is None:
                findings.append(f"case {case.id} target is missing: {target.node_id}")
                continue
            if str(node.url) != target.url:
                findings.append(f"case {case.id} target URL drifted: {target.node_id}")
            source_path = str(getattr(node, "source_path", "") or "")
            if source_path and not (
                source_path.endswith(target.source_path)
                or target.source_path.endswith(source_path)
            ):
                findings.append(f"case {case.id} target source drifted: {target.node_id}")
            content_ir = getattr(node, "content_ir", None)
            anchors = {
                str(getattr(item, "anchor", "") or "")
                for item in getattr(content_ir, "headings", ())
            }
            for section in target.sections:
                if section not in anchors:
                    findings.append(
                        f"case {case.id} target section is missing: {target.node_id}#{section}"
                    )
    return tuple(findings)


def _dataset_from_dict(raw: Mapping[str, Any]) -> KnownAnswerDataset:
    return KnownAnswerDataset(
        schema_version=int(raw.get("schema_version") or 0),
        dataset_id=str(raw.get("dataset_id") or ""),
        version=str(raw.get("version") or ""),
        description=str(raw.get("description") or ""),
        corpora=tuple(_corpus_from_dict(item) for item in _objects(raw.get("corpora"))),
        cases=tuple(_case_from_dict(item) for item in _objects(raw.get("cases"))),
    )


def _corpus_from_dict(raw: Mapping[str, Any]) -> RetrievalCorpus:
    return RetrievalCorpus(
        id=str(raw.get("id") or ""),
        kind=str(raw.get("kind") or ""),
        root=str(raw.get("root") or ""),
        revision=str(raw.get("revision") or ""),
        sources=tuple(
            CorpusSource(path=str(item.get("path") or ""), sha256=str(item.get("sha256") or ""))
            for item in _objects(raw.get("sources"))
        ),
    )


def _case_from_dict(raw: Mapping[str, Any]) -> KnownAnswerCase:
    return KnownAnswerCase(
        id=str(raw.get("id") or ""),
        corpus=str(raw.get("corpus") or ""),
        query_class=str(raw.get("query_class") or ""),
        query=str(raw.get("query") or ""),
        surfaces=tuple(str(item) for item in _items(raw.get("surfaces"))),
        access=str(raw.get("access") or ""),
        result_policy=str(raw.get("result_policy") or ""),
        targets=tuple(_target_from_dict(item) for item in _objects(raw.get("targets"))),
        acceptable_alternatives=tuple(
            _target_from_dict(item) for item in _objects(raw.get("acceptable_alternatives"))
        ),
    )


def _target_from_dict(raw: Mapping[str, Any]) -> RetrievalTarget:
    return RetrievalTarget(
        node_id=str(raw.get("node_id") or ""),
        url=str(raw.get("url") or ""),
        sections=tuple(str(item) for item in _items(raw.get("sections"))),
        source_path=str(raw.get("source_path") or ""),
    )


def _objects(value: Any) -> tuple[Mapping[str, Any], ...]:
    return tuple(item for item in _items(value) if isinstance(item, Mapping))


def _items(value: Any) -> Sequence[Any]:
    return value if isinstance(value, list | tuple) else ()


def _append_duplicate_findings(findings: list[str], kind: str, values: list[str]) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    findings.extend(f"duplicate {kind} id: {value}" for value in duplicates)
