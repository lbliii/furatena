"""Hybrid search and agent retrieval over the catalog graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from furatena.catalog.chunks import chunk_node
from furatena.catalog.embeddings import EmbeddingIndex, SemanticHit
from furatena.catalog.search import search_nodes

if TYPE_CHECKING:
    from furatena.catalog.models import DocNode
    from furatena.catalog.registry import CatalogRegistry


class CatalogLike(Protocol):
    def doc_nodes(self) -> list[DocNode]: ...
    def get_by_node_id(self, node_id: str) -> DocNode | None: ...
    def backlinks_for(self, node: DocNode) -> list[dict[str, str]]: ...
    def body_html(self, node: DocNode) -> str: ...


@dataclass(frozen=True, slots=True)
class HybridHit:
    node: DocNode
    score: float
    snippet: str
    keyword_score: int
    semantic_score: float
    chunk_id: str | None = None


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """Page-level hybrid hits plus the semantic chunk hits used to build them."""

    hits: tuple[HybridHit, ...]
    semantic_hits: tuple[SemanticHit, ...]


def _node_matches_filters(
    node: DocNode,
    *,
    mount: str | None = None,
    section: str | None = None,
    tag: str | None = None,
    edition: str | None = None,
    lang: str | None = None,
) -> bool:
    if mount is not None and node.mount != mount:
        return False
    if section is not None and node.section != section:
        return False
    if tag is not None and tag not in node.tags:
        return False
    if edition is not None and node.edition != edition:
        return False
    return not (lang is not None and getattr(node, "lang", "en") != lang)


def hybrid_search(
    catalog: CatalogLike,
    index: EmbeddingIndex,
    query: str,
    *,
    limit: int = 12,
    mount: str | None = None,
    edition: str | None = None,
    section: str | None = None,
    tag: str | None = None,
    semantic_limit: int | None = None,
    documents: dict[str, object] | None = None,
    lang: str | None = None,
) -> HybridSearchResult:
    """Rank pages with keyword + TF-IDF chunk retrieval (one semantic scan)."""
    if documents is None and hasattr(catalog, "ast_documents"):
        documents = catalog.ast_documents()

    nodes = catalog.doc_nodes(lang=lang)
    if mount is not None or section is not None or tag is not None or edition is not None or lang is not None:
        nodes = [
            node
            for node in nodes
            if _node_matches_filters(
                node,
                mount=mount,
                section=section,
                tag=tag,
                edition=edition,
                lang=lang,
            )
        ]

    keyword_hits = search_nodes(
        nodes,
        query,
        limit=limit * 2,
        documents=documents,
    )
    chunk_limit = semantic_limit if semantic_limit is not None else max(limit * 3, 64)
    semantic_hits = index.search(query, limit=chunk_limit, mount=mount, edition=edition)

    combined: dict[str, HybridHit] = {}
    for hit in keyword_hits:
        combined[hit.node.node_id] = HybridHit(
            node=hit.node,
            score=float(hit.score),
            snippet=hit.snippet,
            keyword_score=hit.score,
            semantic_score=0.0,
        )

    for sem_hit in semantic_hits:
        node = catalog.get_by_node_id(sem_hit.chunk.node_id)
        if node is None:
            continue
        if not _node_matches_filters(node, mount=mount, section=section, tag=tag, edition=edition, lang=lang):
            continue
        sem_score = round(sem_hit.score * 100, 2)
        existing = combined.get(node.node_id)
        if existing is not None:
            combined[node.node_id] = HybridHit(
                node=node,
                score=existing.keyword_score + sem_score,
                snippet=existing.snippet or sem_hit.chunk.text[:240],
                keyword_score=existing.keyword_score,
                semantic_score=sem_score,
                chunk_id=sem_hit.chunk.chunk_id,
            )
        else:
            combined[node.node_id] = HybridHit(
                node=node,
                score=sem_score,
                snippet=sem_hit.chunk.text[:240],
                keyword_score=0,
                semantic_score=sem_score,
                chunk_id=sem_hit.chunk.chunk_id,
            )

    hits = sorted(combined.values(), key=lambda hit: (-hit.score, hit.node.title.lower()))
    return HybridSearchResult(
        hits=tuple(hits[:limit]),
        semantic_hits=tuple(semantic_hits),
    )


def retrieve_node(
    catalog: CatalogLike,
    index: EmbeddingIndex,
    node_id: str,
) -> dict[str, Any] | None:
    node = catalog.get_by_node_id(node_id)
    if node is None:
        return None
    documents = catalog.ast_documents() if hasattr(catalog, "ast_documents") else None
    chunks = [
        {
            "chunk_id": chunk.chunk_id,
            "heading": chunk.heading,
            "text": chunk.text,
            "url": chunk.url,
        }
        for chunk in chunk_node(node, documents=documents)
    ]
    similar = [
        {
            "chunk_id": hit.chunk.chunk_id,
            "node_id": hit.chunk.node_id,
            "title": hit.chunk.title,
            "url": hit.chunk.url,
            "score": hit.score,
        }
        for hit in index.similar(chunks[0]["chunk_id"], limit=5)
    ] if chunks else []
    return {
        "node_id": node.node_id,
        "url": node.url,
        "title": node.title,
        "description": node.description,
        "mount": node.mount,
        "edition": node.edition,
        "section": node.section,
        "tags": sorted(node.tags),
        "backlinks": catalog.backlinks_for(node),
        "chunks": chunks,
        "similar": similar,
    }


def semantic_search_json(
    catalog: CatalogRegistry,
    index: EmbeddingIndex,
    query: str,
    *,
    base_url: str = "",
    limit: int = 12,
    mount: str | None = None,
    edition: str | None = None,
) -> dict[str, Any]:
    result = hybrid_search(
        catalog,
        index,
        query,
        limit=limit,
        mount=mount,
        edition=edition,
    )
    hits = result.hits
    return {
        "schema_version": 1,
        "query": query,
        "mode": "hybrid",
        "count": len(hits),
        "results": [
            {
                "node_id": hit.node.node_id,
                "url": _absolute_url(base_url, hit.node.url),
                "title": hit.node.title,
                "snippet": hit.snippet,
                "score": hit.score,
                "keyword_score": hit.keyword_score,
                "semantic_score": hit.semantic_score,
                "chunk_id": hit.chunk_id,
                "mount": hit.node.mount,
                "edition": hit.node.edition,
            }
            for hit in hits
        ],
    }


def _absolute_url(base_url: str, path: str) -> str:
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"
