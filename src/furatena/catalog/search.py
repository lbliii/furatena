"""Catalog search with ranking and snippets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from furatena.catalog.models import DocNode
from furatena.catalog.patitas_bridge import excerpt_text, plain_text

if TYPE_CHECKING:
    from patitas.nodes import Document

_WORD_RE = re.compile(r"\w+")
_STOP_WORDS = {
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


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One search result with excerpt text."""

    node: DocNode
    score: int
    snippet: str


def search_nodes(
    nodes: list[DocNode],
    query: str,
    *,
    limit: int = 12,
    documents: dict[str, Document] | None = None,
) -> list[SearchHit]:
    """Rank doc nodes and attach a plain-text snippet."""
    needle = query.strip().lower()
    if not needle:
        return []

    terms = [t for t in _WORD_RE.findall(needle) if len(t) > 1 and t not in _STOP_WORDS]
    if not terms:
        terms = [needle]

    hits: list[SearchHit] = []
    for node in nodes:
        document = None
        if documents is not None:
            document = documents.get(node.node_id) or documents.get(node.slug)
        title_l = node.title.lower()
        desc_l = node.description.lower()
        path_l = " ".join(
            value
            for value in (
                node.url,
                node.slug,
                node.source_path,
                node.section,
            )
            if value
        ).lower()
        tag_l = " ".join(sorted(node.tags)).lower()
        meta_l = _metadata_text(node).lower()
        body_l = plain_text(node, document).lower()
        toc_l = " ".join(entry.text for entry in node.toc).lower()

        score = 0
        if needle in title_l:
            score += 20
        if needle in path_l:
            score += 16
        if needle in meta_l:
            score += 10
        for term in terms:
            if term in title_l:
                score += 8
            if term in desc_l:
                score += 5
            if term in tag_l:
                score += 10
            if term in meta_l:
                score += 6
            if term in path_l:
                score += 4
            if term in body_l:
                score += 2
            if term in toc_l:
                score += 6

        if score:
            hits.append(
                SearchHit(
                    node=node,
                    score=score,
                    snippet=_snippet(node, needle, terms, document=document),
                )
            )

    hits.sort(key=lambda h: (-h.score, h.node.weight, h.node.title.lower()))
    return hits[:limit]


def _metadata_text(node: DocNode) -> str:
    values: list[str] = []
    for key in ("keywords", "aliases", "summary", "source", "source_provider", "content_format"):
        values.extend(_flatten_meta(node.meta.get(key)))
    return " ".join(values)


def _flatten_meta(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        items: list[str] = []
        for child in value.values():
            items.extend(_flatten_meta(child))
        return items
    if isinstance(value, (list, tuple, set, frozenset)):
        items = []
        for child in value:
            items.extend(_flatten_meta(child))
        return items
    return []


def _snippet(
    node: DocNode,
    needle: str,
    terms: list[str],
    *,
    document: Document | None = None,
) -> str:
    if node.description:
        desc = node.description.strip()
        if needle in desc.lower() or any(t in desc.lower() for t in terms):
            return desc

    excerpt = excerpt_text(node, document, source=node.body_md, max_chars=240)
    lower = excerpt.lower()
    if needle in lower or any(term in lower for term in terms):
        return excerpt

    body = plain_text(node, document, source=node.body_md)
    lower = body.lower()
    idx = lower.find(needle)
    if idx < 0:
        for term in terms:
            idx = lower.find(term)
            if idx >= 0:
                break
    if idx < 0:
        return node.description or excerpt

    start = max(0, idx - 60)
    end = min(len(body), idx + 100)
    snippet = body[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(body):
        snippet = snippet + "…"
    return snippet
