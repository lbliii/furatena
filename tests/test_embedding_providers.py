"""Versioned local and external embedding-provider contracts."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from furatena.catalog.embedding_providers import (
    DEFAULT_EMBEDDING_PROVIDER,
    EmbeddingProviderError,
    EmbeddingProviderMetadata,
    ExternalEmbeddingProvider,
    LocalTfidfProvider,
    build_embedding_index,
)
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.models import DocNode


def _node() -> DocNode:
    return DocNode(
        url="/docs/search/",
        slug="docs/search",
        title="Search",
        description="Search provider contracts",
        layout="doc",
        weight=1,
        section="docs",
        tags=frozenset({"search"}),
        body_md="# Search\n\nDeterministic local retrieval.",
        body_html="<h1>Search</h1><p>Deterministic local retrieval.</p>",
        toc=(),
        source_path="docs/search.md",
        body_text="Search Deterministic local retrieval.",
    )


def test_local_provider_is_default_and_exports_versioned_provenance() -> None:
    index = build_embedding_index([_node()])
    payload = index.to_json()

    assert DEFAULT_EMBEDDING_PROVIDER.metadata.id == "furatena-local-tfidf"
    assert payload["schema_version"] == 2
    assert payload["provider"] == {
        "interface_version": 1,
        "id": "furatena-local-tfidf",
        "version": "1",
        "kind": "local",
        "model": "tfidf",
        "deterministic": True,
        "external": False,
    }
    assert payload["provenance"]["chunk_count"] == len(index.chunks) >= 1
    assert len(payload["provenance"]["index_fingerprint"]) == 64
    assert index.search("deterministic", limit=1)[0].chunk.node_id == _node().node_id


def test_external_provider_uses_same_index_and_metadata_contract() -> None:
    metadata = EmbeddingProviderMetadata(
        id="example-vectors",
        version="2026-07",
        kind="external",
        model="example-embed-1",
        deterministic=False,
        external=True,
    )
    provider = ExternalEmbeddingProvider(
        metadata,
        lambda nodes, documents: EmbeddingIndex.from_nodes(nodes, documents=dict(documents or {})),
    )

    index = build_embedding_index([_node()], provider=provider)

    assert index.to_json()["provider"] == metadata.to_dict()
    assert index.search("retrieval", limit=1)


def test_external_provider_failures_are_structured_and_classified() -> None:
    metadata = EmbeddingProviderMetadata(
        id="unavailable-vectors",
        version="1",
        kind="external",
        model="remote",
        deterministic=False,
        external=True,
    )

    def fail(nodes, documents):
        raise TimeoutError("provider timed out")

    provider = ExternalEmbeddingProvider(metadata, fail)

    with pytest.raises(EmbeddingProviderError) as raised:
        build_embedding_index([_node()], provider=provider)

    assert raised.value.to_dict() == {
        "type": "EmbeddingProviderError",
        "provider_id": "unavailable-vectors",
        "stage": "index",
        "code": "provider_unavailable",
        "retryable": True,
        "message": "provider timed out",
    }


def test_external_query_failures_share_the_structured_failure_contract() -> None:
    metadata = EmbeddingProviderMetadata(
        id="query-vectors",
        version="1",
        kind="external",
        model="remote",
        deterministic=False,
        external=True,
    )

    class FailingIndex:
        chunks = ()

        def search(self, query, *, limit=12, mount=None, edition=None):
            raise ConnectionError("query transport failed")

        def get_chunk(self, chunk_id):
            return None

        def similar(self, chunk_id, *, limit=6):
            return []

        def to_json(self):
            return {"schema_version": 2}

    provider = ExternalEmbeddingProvider(metadata, lambda nodes, documents: FailingIndex())
    index = build_embedding_index([_node()], provider=provider)

    with pytest.raises(EmbeddingProviderError) as raised:
        index.search("failure")

    assert raised.value.stage == "query"
    assert raised.value.code == "provider_query_failed"
    assert raised.value.retryable is True
    assert index.to_json()["provider"] == metadata.to_dict()


def test_incompatible_index_and_interface_versions_fail_closed() -> None:
    external = EmbeddingProviderMetadata(
        id="bad-vectors",
        version="1",
        kind="external",
        model="bad",
        deterministic=False,
        external=True,
    )
    provider = ExternalEmbeddingProvider(
        external,
        lambda nodes, documents: cast(Any, object()),
    )
    with pytest.raises(EmbeddingProviderError, match="incompatible search index") as incompatible:
        build_embedding_index([_node()], provider=provider)
    assert incompatible.value.retryable is False

    unsupported = LocalTfidfProvider(
        metadata=replace(DEFAULT_EMBEDDING_PROVIDER.metadata, interface_version=2)
    )
    with pytest.raises(EmbeddingProviderError, match="unsupported embedding provider interface"):
        build_embedding_index([_node()], provider=unsupported)


def test_legacy_index_payload_loads_with_local_provider_provenance() -> None:
    current = EmbeddingIndex.from_nodes([_node()]).to_json()
    legacy = {key: value for key, value in current.items() if key not in {"provider", "provenance"}}
    legacy["version"] = 1
    legacy.pop("schema_version", None)

    loaded = EmbeddingIndex.from_json(legacy)

    assert loaded.to_json()["provider"]["id"] == "furatena-local-tfidf"
    assert loaded.fingerprint() == EmbeddingIndex.from_nodes([_node()]).fingerprint()
