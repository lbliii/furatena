"""Deterministic publish-time indexes and rank merge for federated shards."""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_WORD_RE = re.compile(r"\w+")
_MOUNT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_EDITION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "with",
        "you",
        "your",
    }
)
_INDEX_ALGORITHM = "keyword-tfidf-v1"
_ROUND_DIGITS = 12


@dataclass(frozen=True, slots=True)
class FederatedShardSearchHit:
    """One shard-local result whose normalized scores are safe to rank-merge."""

    node_id: str
    title: str
    snippet: str
    score: float
    keyword_score: float
    tfidf_score: float


@dataclass(frozen=True, slots=True)
class FederatedSearchHit:
    """One result after deterministic merge across immutable shard indexes."""

    node_id: str
    title: str
    snippet: str
    mount: str
    edition: str
    shard_fingerprint: str
    score: float
    keyword_score: float
    tfidf_score: float


@dataclass(frozen=True, slots=True)
class FederatedSearchResult:
    """Merged results plus bounded fan-out accounting."""

    hits: tuple[FederatedSearchHit, ...]
    searched_shards: tuple[str, ...]
    skipped_shards: tuple[str, ...]


def rank_merge_federated_hits(
    hits: Iterable[FederatedSearchHit], *, limit: int
) -> tuple[FederatedSearchHit, ...]:
    """Merge normalized per-shard results with a total deterministic order."""
    ranked = sorted(
        hits,
        key=lambda hit: (
            -hit.score,
            -hit.keyword_score,
            hit.mount,
            hit.edition,
            hit.title.casefold(),
            hit.node_id,
        ),
    )
    return tuple(ranked[: max(limit, 0)])


@dataclass(frozen=True, slots=True)
class _IndexedDocument:
    node_id: str
    title: str
    snippet: str


class FederatedShardSearchIndex:
    """Immutable parsed view of one publish-time shard search index."""

    __slots__ = (
        "_idf",
        "_keyword",
        "_legacy_text",
        "_tfidf",
        "documents",
        "edition",
        "mount",
        "resident_bytes",
        "schema_version",
    )

    def __init__(self, payload: Mapping[str, Any]) -> None:
        errors = validate_federated_search_index(payload)
        if errors:
            raise ValueError("; ".join(errors[:6]))
        self.schema_version = int(payload["schema_version"])
        self.mount = str(payload["mount"])
        self.edition = str(payload["edition"])
        self.documents = tuple(
            _IndexedDocument(
                node_id=str(item["node_id"]),
                title=str(item["title"]),
                snippet=str(item.get("snippet") or item.get("text") or ""),
            )
            for item in payload["documents"]
        )
        self._legacy_text = tuple(
            str(item.get("text") or "").casefold() for item in payload["documents"]
        )
        if self.schema_version == 1:
            self._idf: dict[str, float] = {}
            self._keyword: dict[str, tuple[tuple[int, int, int], ...]] = {}
            self._tfidf: dict[str, tuple[tuple[int, float], ...]] = {}
        else:
            self._idf = {str(term): float(value) for term, value in payload["idf"].items()}
            self._keyword = {
                str(term): tuple((int(row[0]), int(row[1]), int(row[2])) for row in rows)
                for term, rows in payload["keyword_postings"].items()
            }
            self._tfidf = {
                str(term): tuple((int(row[0]), float(row[1])) for row in rows)
                for term, rows in payload["tfidf_postings"].items()
            }
        # Compute this once during cold load. Query hot paths only read the
        # resulting integer, and the cache therefore budgets the resident
        # immutable graph rather than its potentially much smaller wire JSON.
        self.resident_bytes = 0
        self.resident_bytes = estimate_federated_search_index_resident_bytes(self)

    def search(self, query: str, *, limit: int) -> tuple[FederatedShardSearchHit, ...]:
        if limit <= 0:
            return ()
        terms = _tokens(query)
        if not terms:
            return ()
        if self.schema_version == 1:
            return self._search_legacy(terms, limit=limit)

        unique_terms = tuple(sorted(set(terms)))
        keyword_counts: dict[int, list[int]] = {}
        for term in unique_terms:
            for doc_index, title_count, body_count in self._keyword.get(term, ()):
                totals = keyword_counts.setdefault(doc_index, [0, 0, 0])
                totals[0] += 1
                totals[1] += title_count
                totals[2] += body_count

        query_counts = Counter(term for term in terms if term in self._tfidf)
        query_weights = {
            term: (count / max(sum(query_counts.values()), 1)) * self._idf[term]
            for term, count in query_counts.items()
        }
        query_norm = math.sqrt(sum(value * value for value in query_weights.values())) or 1.0
        semantic_scores: dict[int, float] = {}
        for term, weight in query_weights.items():
            normalized_query_weight = weight / query_norm
            for doc_index, normalized_doc_weight in self._tfidf.get(term, ()):
                semantic_scores[doc_index] = (
                    semantic_scores.get(doc_index, 0.0)
                    + normalized_query_weight * normalized_doc_weight
                )

        hits: list[FederatedShardSearchHit] = []
        candidate_indexes = set(keyword_counts) | set(semantic_scores)
        for doc_index in candidate_indexes:
            matched, title_count, body_count = keyword_counts.get(doc_index, [0, 0, 0])
            coverage = matched / len(unique_terms)
            density = min(
                1.0,
                math.log1p(title_count * 4 + body_count) / math.log(6),
            )
            keyword_score = round(coverage * 0.7 + density * 0.3, 8)
            tfidf_score = round(max(0.0, min(1.0, semantic_scores.get(doc_index, 0.0))), 8)
            score = round(keyword_score * 0.6 + tfidf_score * 0.4, 8)
            document = self.documents[doc_index]
            hits.append(
                FederatedShardSearchHit(
                    node_id=document.node_id,
                    title=document.title,
                    snippet=document.snippet,
                    score=score,
                    keyword_score=keyword_score,
                    tfidf_score=tfidf_score,
                )
            )
        hits.sort(
            key=lambda hit: (-hit.score, -hit.keyword_score, hit.title.casefold(), hit.node_id)
        )
        return tuple(hits[:limit])

    def _search_legacy(
        self, terms: tuple[str, ...], *, limit: int
    ) -> tuple[FederatedShardSearchHit, ...]:
        hits: list[FederatedShardSearchHit] = []
        unique_terms = set(terms)
        for index, document in enumerate(self.documents):
            title = document.title.casefold()
            text = self._legacy_text[index]
            matched = sum(term in title or term in text for term in unique_terms)
            if not matched:
                continue
            title_matches = sum(term in title for term in unique_terms)
            keyword_score = round(
                min(1.0, matched / len(unique_terms) * 0.8 + title_matches * 0.2), 8
            )
            hits.append(
                FederatedShardSearchHit(
                    node_id=document.node_id,
                    title=document.title,
                    snippet=document.snippet,
                    score=keyword_score,
                    keyword_score=keyword_score,
                    tfidf_score=0.0,
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.title.casefold(), hit.node_id))
        return tuple(hits[:limit])


def build_federated_search_index(
    pages: Iterable[Mapping[str, Any]], *, mount: str, edition: str
) -> dict[str, Any]:
    """Build a deterministic inverted keyword/TF-IDF index for one public shard."""
    records = sorted(
        (page for page in pages if page.get("node_id")), key=lambda page: str(page["node_id"])
    )
    documents: list[dict[str, str]] = []
    tokenized: list[tuple[Counter[str], Counter[str]]] = []
    document_frequency: Counter[str] = Counter()
    for page in records:
        title = str(page.get("title") or "")
        body = _page_text(page)
        title_counts = Counter(_tokens(title))
        body_counts = Counter(_tokens(body))
        combined = title_counts + body_counts
        document_frequency.update(combined.keys())
        tokenized.append((title_counts, body_counts))
        documents.append(
            {
                "node_id": str(page["node_id"]),
                "title": title,
                "snippet": _snippet(page, body),
            }
        )

    document_count = max(len(documents), 1)
    idf = {
        term: round(math.log((document_count + 1) / (frequency + 1)) + 1.0, _ROUND_DIGITS)
        for term, frequency in sorted(document_frequency.items())
    }
    keyword_postings: dict[str, list[list[int]]] = {}
    tfidf_postings: dict[str, list[list[int | float]]] = {}
    for doc_index, (title_counts, body_counts) in enumerate(tokenized):
        combined = title_counts + body_counts
        total = sum(combined.values()) or 1
        vector = {term: count / total * idf[term] for term, count in combined.items()}
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        for term in sorted(combined):
            keyword_postings.setdefault(term, []).append(
                [doc_index, title_counts[term], body_counts[term]]
            )
            tfidf_postings.setdefault(term, []).append(
                [doc_index, round(vector[term] / norm, _ROUND_DIGITS)]
            )
    return {
        "schema_version": 2,
        "algorithm": _INDEX_ALGORITHM,
        "mount": mount,
        "edition": edition,
        "documents": documents,
        "idf": idf,
        "keyword_postings": dict(sorted(keyword_postings.items())),
        "tfidf_postings": dict(sorted(tfidf_postings.items())),
    }


def validate_federated_search_index(payload: Mapping[str, Any]) -> list[str]:
    """Validate the bounded reader contract for a v1 or v2 shard search object."""
    errors: list[str] = []
    version = payload.get("schema_version")
    if type(version) is not int or version not in {1, 2}:
        return ["schema_version: expected federated search index v1 or v2"]
    expected_keys = (
        {"schema_version", "mount", "edition", "documents"}
        if version == 1
        else {
            "schema_version",
            "algorithm",
            "mount",
            "edition",
            "documents",
            "idf",
            "keyword_postings",
            "tfidf_postings",
        }
    )
    unexpected_keys = set(payload) - expected_keys
    if unexpected_keys:
        errors.append(f"unexpected fields: {sorted(unexpected_keys)}")
    mount = payload.get("mount")
    edition = payload.get("edition")
    if not isinstance(mount, str) or _MOUNT_RE.fullmatch(mount) is None:
        errors.append("mount: expected a canonical mount id")
    if not isinstance(edition, str) or _EDITION_RE.fullmatch(edition) is None:
        errors.append("edition: expected a canonical edition id")
    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        return [*errors, "documents: expected a non-empty array"]
    node_ids: list[str] = []
    for index, item in enumerate(documents):
        if not isinstance(item, dict):
            errors.append(f"documents.{index}: expected an object")
            continue
        node_id = item.get("node_id")
        title = item.get("title")
        text_key = "text" if version == 1 else "snippet"
        unexpected_document_keys = set(item) - {"node_id", "title", text_key}
        if unexpected_document_keys:
            errors.append(
                f"documents.{index}: unexpected fields {sorted(unexpected_document_keys)}"
            )
        if not isinstance(node_id, str) or not 3 <= len(node_id) <= 1024:
            errors.append(f"documents.{index}.node_id: expected a 3..1024 character string")
        else:
            node_ids.append(node_id)
            if (
                isinstance(mount, str)
                and isinstance(edition, str)
                and not node_id.startswith(f"{mount}:{edition}:")
            ):
                errors.append(f"documents.{index}.node_id: identity prefix mismatch")
        if not isinstance(title, str):
            errors.append(f"documents.{index}.title: expected a string")
        text = item.get(text_key)
        if not isinstance(text, str):
            errors.append(f"documents.{index}.{text_key}: expected a string")
        elif version == 2 and len(text) > 320:
            errors.append(f"documents.{index}.snippet: expected at most 320 characters")
    if node_ids != sorted(node_ids) or len(node_ids) != len(set(node_ids)):
        errors.append("documents: node ids must be unique and sorted")
    if version == 1:
        return errors
    if payload.get("algorithm") != _INDEX_ALGORITHM:
        errors.append(f"algorithm: expected {_INDEX_ALGORITHM!r}")
    idf = payload.get("idf")
    keyword = payload.get("keyword_postings")
    tfidf = payload.get("tfidf_postings")
    for label, value in (("idf", idf), ("keyword_postings", keyword), ("tfidf_postings", tfidf)):
        if not isinstance(value, dict):
            errors.append(f"{label}: expected an object")
    if not isinstance(idf, dict) or not isinstance(keyword, dict) or not isinstance(tfidf, dict):
        return errors
    term_sets = (list(idf), list(keyword), list(tfidf))
    if any(not isinstance(term, str) or not term for terms in term_sets for term in terms):
        errors.append("index terms must be non-empty strings")
    elif any(terms != sorted(terms) for terms in term_sets):
        errors.append("index terms must be sorted for deterministic output")
    if not (set(idf) == set(keyword) == set(tfidf)):
        errors.append("idf and posting term sets must match")
    for term, value in idf.items():
        if (
            not isinstance(term, str)
            or not term
            or type(value) not in {int, float}
            or not math.isfinite(float(value))
            or value <= 0
        ):
            errors.append(f"idf.{term}: expected a positive numeric weight")
    errors.extend(_posting_errors(keyword, len(documents), keyword=True))
    errors.extend(_posting_errors(tfidf, len(documents), keyword=False))
    if not errors:
        errors.extend(_index_drift_errors(idf, keyword, tfidf, len(documents)))
    return errors


def _posting_errors(
    postings: Mapping[str, Any], document_count: int, *, keyword: bool
) -> list[str]:
    errors: list[str] = []
    width = 3 if keyword else 2
    for term, rows in postings.items():
        if not isinstance(rows, list) or not rows:
            errors.append(f"postings.{term}: expected an array")
            continue
        previous = -1
        for row in rows:
            if not isinstance(row, list) or len(row) != width:
                errors.append(f"postings.{term}: expected {width}-item rows")
                continue
            index = row[0]
            if type(index) is not int or not 0 <= index < document_count or index <= previous:
                errors.append(
                    f"postings.{term}: document indexes must be unique, sorted, and in range"
                )
                continue
            previous = index
            values = row[1:]
            if keyword:
                if any(type(value) is not int or value < 0 for value in values) or not any(values):
                    errors.append(
                        f"postings.{term}: keyword counts must be non-negative and nonzero"
                    )
            elif (
                type(values[0]) not in {int, float}
                or not math.isfinite(float(values[0]))
                or not 0 < values[0] <= 1
            ):
                errors.append(f"postings.{term}: TF-IDF weights must be in (0, 1]")
    return errors


def estimate_federated_search_index_resident_bytes(
    index: FederatedShardSearchIndex,
) -> int:
    """Return the deep resident size of the immutable index graph once per load."""
    seen: set[int] = set()

    def size(value: object) -> int:
        identity = id(value)
        if identity in seen:
            return 0
        seen.add(identity)
        total = sys.getsizeof(value)
        if isinstance(value, dict):
            return total + sum(size(key) + size(item) for key, item in value.items())
        if isinstance(value, (tuple, list, set, frozenset)):
            return total + sum(size(item) for item in value)
        if isinstance(value, _IndexedDocument):
            return total + size(value.node_id) + size(value.title) + size(value.snippet)
        return total

    total = sys.getsizeof(index)
    seen.add(id(index))
    for name in FederatedShardSearchIndex.__slots__:
        value = getattr(index, name)
        total += sys.getsizeof(value) if name == "resident_bytes" else size(value)
    return total


def _index_drift_errors(
    idf: Mapping[str, Any],
    keyword: Mapping[str, Any],
    tfidf: Mapping[str, Any],
    document_count: int,
) -> list[str]:
    expected_idf = {
        term: round(
            math.log((max(document_count, 1) + 1) / (len(rows) + 1)) + 1.0,
            _ROUND_DIGITS,
        )
        for term, rows in keyword.items()
    }
    if any(float(idf[term]) != value for term, value in expected_idf.items()):
        return ["idf: weights drift from keyword posting document frequencies"]

    counts_by_document: list[dict[str, int]] = [dict() for _ in range(document_count)]
    for term, rows in keyword.items():
        for doc_index, title_count, body_count in rows:
            counts_by_document[doc_index][term] = title_count + body_count
    expected_postings: dict[str, list[list[int | float]]] = {term: [] for term in keyword}
    for doc_index, counts in enumerate(counts_by_document):
        total = sum(counts.values()) or 1
        vector = {term: count / total * expected_idf[term] for term, count in counts.items()}
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        for term in sorted(vector):
            expected_postings[term].append([doc_index, round(vector[term] / norm, _ROUND_DIGITS)])
    normalized_tfidf = {
        term: [[int(row[0]), float(row[1])] for row in rows] for term, rows in tfidf.items()
    }
    if normalized_tfidf != expected_postings:
        return ["tfidf_postings: weights drift from keyword postings and IDF"]
    return []


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in (match.casefold() for match in _WORD_RE.findall(value))
        if len(token) > 1 and token not in _STOP_WORDS
    )


def _page_text(page: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("description", "section", "slug"):
        value = page.get(key)
        if isinstance(value, str):
            parts.append(value)
    tags = page.get("tags")
    if isinstance(tags, (list, tuple)):
        parts.extend(str(value) for value in tags)
    sections = page.get("sections")
    if isinstance(sections, (list, tuple)):
        for section in sections:
            if not isinstance(section, Mapping):
                continue
            parts.extend(str(section.get(key) or "") for key in ("heading", "text"))
    return " ".join(part for part in parts if part).strip()


def _snippet(page: Mapping[str, Any], body: str) -> str:
    description = page.get("description")
    value = str(description).strip() if isinstance(description, str) else ""
    if not value:
        value = body
    return value[:320]
