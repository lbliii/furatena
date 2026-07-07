"""Versioned embedding-provider contracts with a deterministic local default."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from furatena.catalog.embeddings import EmbeddingIndex, SemanticHit


@dataclass(frozen=True, slots=True)
class EmbeddingProviderMetadata:
    """Stable provider identity exported with every compatible index."""

    id: str
    version: str
    kind: str
    model: str
    deterministic: bool
    external: bool
    interface_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "interface_version": self.interface_version,
            "id": self.id,
            "version": self.version,
            "kind": self.kind,
            "model": self.model,
            "deterministic": self.deterministic,
            "external": self.external,
        }


@runtime_checkable
class EmbeddingSearchIndex(Protocol):
    """Query and serialization contract shared by local and external indexes."""

    chunks: tuple[Any, ...]

    def search(
        self,
        query: str,
        *,
        limit: int = 12,
        mount: str | None = None,
        edition: str | None = None,
    ) -> list[SemanticHit]: ...

    def get_chunk(self, chunk_id: str) -> Any | None: ...
    def similar(self, chunk_id: str, *, limit: int = 6) -> list[SemanticHit]: ...
    def to_json(self) -> dict[str, Any]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Provider interface for indexing catalog nodes into a queryable index."""

    metadata: EmbeddingProviderMetadata

    def build_index(
        self,
        nodes: list[Any],
        *,
        documents: Mapping[str, Any] | None = None,
    ) -> EmbeddingSearchIndex: ...


class EmbeddingProviderError(RuntimeError):
    """Structured provider failure safe for CLI and diagnostic serialization."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str,
        stage: str,
        code: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.provider_id = provider_id
        self.stage = stage
        self.code = code
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "provider_id": self.provider_id,
            "stage": self.stage,
            "code": self.code,
            "retryable": self.retryable,
            "message": str(self),
        }


@dataclass(frozen=True, slots=True)
class ProviderBackedIndex:
    """External index wrapper that enforces provenance and query failures."""

    backend: EmbeddingSearchIndex
    metadata: EmbeddingProviderMetadata

    @property
    def chunks(self) -> tuple[Any, ...]:
        return self.backend.chunks

    def search(
        self,
        query: str,
        *,
        limit: int = 12,
        mount: str | None = None,
        edition: str | None = None,
    ) -> list[SemanticHit]:
        try:
            return self.backend.search(
                query,
                limit=limit,
                mount=mount,
                edition=edition,
            )
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise self._query_error(exc) from exc

    def get_chunk(self, chunk_id: str) -> Any | None:
        return self.backend.get_chunk(chunk_id)

    def similar(self, chunk_id: str, *, limit: int = 6) -> list[SemanticHit]:
        try:
            return self.backend.similar(chunk_id, limit=limit)
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise self._query_error(exc) from exc

    def to_json(self) -> dict[str, Any]:
        payload = dict(self.backend.to_json())
        payload["provider"] = self.metadata.to_dict()
        provenance = payload.get("provenance")
        normalized = dict(provenance) if isinstance(provenance, Mapping) else {}
        normalized["interface_version"] = self.metadata.interface_version
        normalized["chunk_count"] = len(self.chunks)
        payload["provenance"] = normalized
        return payload

    def _query_error(self, exc: Exception) -> EmbeddingProviderError:
        return EmbeddingProviderError(
            str(exc),
            provider_id=self.metadata.id,
            stage="query",
            code="provider_query_failed",
            retryable=True,
        )


@dataclass(frozen=True, slots=True)
class LocalTfidfProvider:
    """Dependency-free deterministic provider used unless one is injected."""

    metadata: EmbeddingProviderMetadata = EmbeddingProviderMetadata(
        id="furatena-local-tfidf",
        version="1",
        kind="local",
        model="tfidf",
        deterministic=True,
        external=False,
    )

    def build_index(
        self,
        nodes: list[Any],
        *,
        documents: Mapping[str, Any] | None = None,
    ) -> EmbeddingIndex:
        return EmbeddingIndex.from_nodes(
            nodes,
            documents=dict(documents) if documents is not None else None,
        ).with_provider(self.metadata.to_dict())


@dataclass(frozen=True, slots=True)
class ExternalEmbeddingProvider:
    """Adapter for an injected external backend without built-in network coupling."""

    metadata: EmbeddingProviderMetadata
    index_builder: Callable[[list[Any], Mapping[str, Any] | None], EmbeddingSearchIndex]

    def __post_init__(self) -> None:
        if not self.metadata.external or self.metadata.kind != "external":
            raise ValueError("external providers require kind=external and external=true")

    def build_index(
        self,
        nodes: list[Any],
        *,
        documents: Mapping[str, Any] | None = None,
    ) -> EmbeddingSearchIndex:
        try:
            index = self.index_builder(nodes, documents)
        except EmbeddingProviderError:
            raise
        except Exception as exc:
            raise EmbeddingProviderError(
                str(exc),
                provider_id=self.metadata.id,
                stage="index",
                code="provider_unavailable",
                retryable=True,
            ) from exc
        if not isinstance(index, EmbeddingSearchIndex):
            raise EmbeddingProviderError(
                "provider returned an incompatible search index",
                provider_id=self.metadata.id,
                stage="index",
                code="incompatible_index",
                retryable=False,
            )
        if isinstance(index, EmbeddingIndex):
            return index.with_provider(self.metadata.to_dict())
        return ProviderBackedIndex(index, self.metadata)


DEFAULT_EMBEDDING_PROVIDER = LocalTfidfProvider()


def build_embedding_index(
    nodes: list[Any],
    *,
    documents: Mapping[str, Any] | None = None,
    provider: EmbeddingProvider | None = None,
) -> EmbeddingSearchIndex:
    """Build an index through the selected provider, defaulting to local TF-IDF."""
    active = provider or DEFAULT_EMBEDDING_PROVIDER
    if active.metadata.interface_version != 1:
        raise EmbeddingProviderError(
            f"unsupported embedding provider interface: {active.metadata.interface_version}",
            provider_id=active.metadata.id,
            stage="configure",
            code="unsupported_interface",
            retryable=False,
        )
    return active.build_index(nodes, documents=documents)
