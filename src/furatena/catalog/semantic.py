"""Hybrid search and agent retrieval over the catalog graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from furatena.catalog.access import AccessPermission, AccessSubject, accessible_nodes
from furatena.catalog.chunks import chunk_node
from furatena.catalog.edition_lifecycle import lifecycle_rank_multiplier, lifecycle_statuses
from furatena.catalog.embedding_providers import EmbeddingSearchIndex
from furatena.catalog.embeddings import SemanticHit
from furatena.catalog.export import provenance_record
from furatena.catalog.search import search_nodes

if TYPE_CHECKING:
    from patitas.nodes import Document

    from furatena.catalog.models import DocNode
    from furatena.catalog.registry import CatalogRegistry


class CatalogLike(Protocol):
    def doc_nodes(self, *, lang: str | None = None) -> list[DocNode]: ...
    def get_by_node_id(self, node_id: str) -> DocNode | None: ...
    def backlinks_for(self, node: DocNode) -> list[dict[str, str]]: ...
    def body_html(self, node: DocNode) -> str: ...


@dataclass(frozen=True, slots=True)
class HybridHit:
    node: DocNode
    score: float
    snippet: str
    keyword_score: float
    semantic_score: float
    chunk_id: str | None = None


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """Page-level hybrid hits plus the semantic chunk hits used to build them."""

    hits: tuple[HybridHit, ...]
    semantic_hits: tuple[SemanticHit, ...]
    ranking: str


def semantic_index_json(
    catalog: CatalogLike,
    index: EmbeddingSearchIndex,
    *,
    include_private: bool = False,
    subject: AccessSubject | None = None,
) -> dict[str, Any]:
    """Return the persisted semantic index shape with catalog access filtering."""
    nodes = accessible_nodes(
        catalog,
        catalog.doc_nodes(),
        subject=subject,
        permission=AccessPermission.SEARCH,
        include_private=include_private,
    )
    allowed_node_ids = {node.node_id for node in nodes}
    payload = index.to_json()
    chunks = [
        chunk
        for chunk in payload.get("chunks", [])
        if isinstance(chunk, dict) and chunk.get("node_id") in allowed_node_ids
    ]
    payload["chunks"] = chunks
    payload["chunk_count"] = len(chunks)
    return payload


def _node_matches_filters(
    node: DocNode,
    *,
    mount: str | None = None,
    section: str | None = None,
    tag: str | None = None,
    edition: str | None = None,
    lang: str | None = None,
    url_prefix: str | None = None,
) -> bool:
    if mount is not None and node.mount != mount:
        return False
    if section is not None and node.section != section:
        return False
    if tag is not None and tag not in node.tags:
        return False
    if edition is not None and node.edition != edition:
        return False
    if url_prefix is not None and not node.url.startswith(url_prefix):
        return False
    return not (lang is not None and getattr(node, "lang", "en") != lang)


def hybrid_search(
    catalog: CatalogLike,
    index: EmbeddingSearchIndex,
    query: str,
    *,
    limit: int = 12,
    mount: str | None = None,
    edition: str | None = None,
    section: str | None = None,
    tag: str | None = None,
    semantic_limit: int | None = None,
    documents: dict[str, Document] | None = None,
    lang: str | None = None,
    url_prefix: str | None = None,
    include_private: bool = True,
    subject: AccessSubject | None = None,
    ranking: str = "keyword_guarded",
    status: str | None = None,
    include_preview: bool = False,
    include_eol: bool = False,
    include_federated: bool = True,
) -> HybridSearchResult:
    """Rank pages with keyword + TF-IDF chunk retrieval (one semantic scan)."""
    if ranking not in {"additive", "keyword_guarded"}:
        raise ValueError(f"unknown hybrid ranking mode: {ranking}")
    ast_documents = getattr(catalog, "_local_ast_documents", None)
    if not callable(ast_documents):
        ast_documents = getattr(catalog, "ast_documents", None)
    if documents is None and callable(ast_documents):
        documents = ast_documents()

    doc_nodes = getattr(catalog, "_local_doc_nodes", None)
    if not callable(doc_nodes):
        doc_nodes = catalog.doc_nodes
    nodes = accessible_nodes(
        catalog,
        doc_nodes(lang=lang),
        subject=subject,
        permission=AccessPermission.SEARCH,
        include_private=include_private,
    )
    selected_statuses = lifecycle_statuses(
        status=status, include_preview=include_preview, include_eol=include_eol
    )
    status_for = getattr(catalog, "edition_status_for", None)

    def node_status(node: DocNode) -> str:
        if callable(status_for):
            return str(status_for(node.mount, node.edition))
        return "current" if node.edition == "latest" else "legacy"

    nodes = [node for node in nodes if node_status(node) in selected_statuses]
    if any((mount, section, tag, edition, lang, url_prefix)):
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
                url_prefix=url_prefix,
            )
        ]

    remote_mount_ids = getattr(catalog, "_remote_mount_ids", lambda: set())()
    keyword_hits = search_nodes(
        nodes,
        query,
        limit=limit * 2,
        documents=documents,
        status_for=node_status,
        status=status,
        include_preview=include_preview,
        include_eol=include_eol,
    )
    chunk_limit = semantic_limit if semantic_limit is not None else max(limit * 3, 64)
    semantic_hits = index.search(query, limit=chunk_limit, mount=mount, edition=edition)
    accessible_node_ids = {node.node_id for node in nodes}

    combined: dict[str, HybridHit] = {}
    for hit in keyword_hits:
        combined[hit.node.node_id] = HybridHit(
            node=hit.node,
            score=float(hit.score),
            snippet=hit.snippet,
            keyword_score=hit.score,
            semantic_score=0.0,
        )

    federated_search = getattr(catalog, "federated_search_hits", None)
    if include_federated and callable(federated_search):
        for hit in federated_search(
            query,
            limit=limit * 2,
            subject=subject,
            include_private=include_private,
            mount=mount,
            edition=edition,
            section=section,
            tag=tag,
            lang=lang,
            url_prefix=url_prefix,
            status=status,
            include_preview=include_preview,
            include_eol=include_eol,
        ):
            keyword_score = round(hit.keyword_score * 100, 4)
            semantic_score = round(hit.tfidf_score * 100, 4)
            combined[hit.node.node_id] = HybridHit(
                node=hit.node,
                score=round(hit.score * 100, 4),
                snippet=hit.snippet,
                keyword_score=keyword_score,
                semantic_score=semantic_score,
            )

    for sem_hit in semantic_hits:
        node = catalog.get_by_node_id(sem_hit.chunk.node_id)
        if node is None:
            continue
        if node.mount in remote_mount_ids:
            continue
        if node.node_id not in accessible_node_ids:
            continue
        if not _node_matches_filters(
            node,
            mount=mount,
            section=section,
            tag=tag,
            edition=edition,
            lang=lang,
            url_prefix=url_prefix,
        ):
            continue
        sem_score = round(sem_hit.score * 100 * lifecycle_rank_multiplier(node_status(node)), 2)
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

    if ranking == "keyword_guarded":
        hits = sorted(
            combined.values(),
            key=lambda hit: (
                -int(hit.keyword_score > 0),
                -hit.keyword_score,
                -hit.semantic_score if hit.keyword_score == 0 else 0.0,
                hit.node.weight,
                hit.node.title.lower(),
                hit.node.node_id,
            ),
        )
    else:
        hits = sorted(
            combined.values(),
            key=lambda hit: (-hit.score, hit.node.title.lower(), hit.node.node_id),
        )
    return HybridSearchResult(
        hits=tuple(hits[:limit]),
        semantic_hits=tuple(semantic_hits),
        ranking=ranking,
    )


def retrieve_node(
    catalog: CatalogLike,
    index: EmbeddingSearchIndex,
    node_id: str,
    *,
    include_private: bool = True,
    subject: AccessSubject | None = None,
    include_eol: bool = False,
) -> dict[str, Any] | None:
    try:
        _mount, requested_edition, _slug = node_id.split(":", 2)
    except ValueError:
        requested_edition = ""
    active_edition = str(getattr(catalog, "active_channel", ""))
    use_edition = getattr(catalog, "use_edition", None)
    if requested_edition and requested_edition != active_edition and callable(use_edition):
        with use_edition(requested_edition):
            return retrieve_node(
                catalog,
                index,
                node_id,
                include_private=include_private,
                subject=subject,
                include_eol=include_eol,
            )
    node = catalog.get_by_node_id(node_id)
    if node is None or (
        not include_private
        and node
        not in accessible_nodes(
            catalog,
            [node],
            subject=subject,
            permission=AccessPermission.RETRIEVE,
        )
    ):
        return None
    status_for = getattr(catalog, "edition_status_for", None)
    edition_status = (
        str(status_for(node.mount, node.edition))
        if callable(status_for)
        else ("current" if node.edition == "latest" else "legacy")
    )
    if edition_status == "eol" and not include_eol:
        return None
    ast_documents = getattr(catalog, "ast_documents", None)
    documents = ast_documents() if callable(ast_documents) else None
    chunks = [
        {
            "chunk_id": chunk.chunk_id,
            "heading": chunk.heading,
            "text": chunk.text,
            "url": chunk.url,
        }
        for chunk in chunk_node(node, documents=documents)
    ]
    similar = (
        [
            {
                "chunk_id": hit.chunk.chunk_id,
                "node_id": hit.chunk.node_id,
                "title": hit.chunk.title,
                "url": hit.chunk.url,
                "score": hit.score,
            }
            for hit in index.similar(chunks[0]["chunk_id"], limit=5)
        ]
        if chunks
        else []
    )
    backlinks = catalog.backlinks_for(node)
    if not include_private:
        public_urls = {
            item.url
            for item in accessible_nodes(
                catalog,
                catalog.doc_nodes(),
                subject=subject,
                permission=AccessPermission.RETRIEVE,
            )
        }
        backlinks = [item for item in backlinks if item.get("href") in public_urls]
    payload: dict[str, Any] = {
        "node_id": node.node_id,
        "url": node.url,
        "title": node.title,
        "description": node.description,
        "mount": node.mount,
        "edition": node.edition,
        "edition_status": edition_status,
        "section": node.section,
        "tags": sorted(node.tags),
        "backlinks": backlinks,
        "chunks": chunks,
        "similar": similar,
    }
    api_operation = node.meta.get("api_operation")
    if isinstance(api_operation, dict):
        payload["api_operation"] = api_operation
    api_try_it = node.meta.get("api_try_it")
    if isinstance(api_try_it, dict):
        payload["api_try_it"] = api_try_it
    return payload


def semantic_search_json(
    catalog: CatalogRegistry,
    index: EmbeddingSearchIndex,
    query: str,
    *,
    base_url: str = "",
    limit: int = 12,
    mount: str | None = None,
    edition: str | None = None,
    tag: str | None = None,
    url_prefix: str | None = None,
    include_private: bool = False,
    subject: AccessSubject | None = None,
    status: str | None = None,
    include_preview: bool = False,
    include_eol: bool = False,
) -> dict[str, Any]:
    result = hybrid_search(
        catalog,
        index,
        query,
        limit=limit,
        mount=mount,
        edition=edition,
        tag=tag,
        url_prefix=url_prefix,
        include_private=include_private,
        subject=subject,
        status=status,
        include_preview=include_preview,
        include_eol=include_eol,
    )
    hits = result.hits
    return {
        "schema_version": 1,
        "query": query,
        "mode": "hybrid",
        "ranking": result.ranking,
        "filters": {
            "mount": mount,
            "edition": edition,
            "tag": tag,
            "url_prefix": url_prefix,
            "include_private": include_private,
            "status": status,
            "include_preview": include_preview,
            "include_eol": include_eol,
        },
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
                "edition_status": (
                    catalog.edition_status_for(hit.node.mount, hit.node.edition)
                    if hasattr(catalog, "edition_status_for")
                    else ("current" if hit.node.edition == "latest" else "legacy")
                ),
                "tags": sorted(hit.node.tags),
                "provenance": provenance_record(catalog, hit.node),
            }
            for hit in hits
        ],
    }


def _absolute_url(base_url: str, path: str) -> str:
    if not base_url:
        return path
    return f"{base_url.rstrip('/')}{path}"
