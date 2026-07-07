"""Lightweight semantic index over documentation chunks."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from furatena.catalog.chunks import DocChunk, chunk_catalog_nodes

_WORD_RE = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class SemanticHit:
    chunk: DocChunk
    score: float


class EmbeddingIndex:
    """Deterministic bag-of-words index with cosine similarity (no ML deps)."""

    def __init__(
        self,
        chunks: tuple[DocChunk, ...],
        *,
        provider: dict[str, Any] | None = None,
    ) -> None:
        self.chunks = chunks
        self.provider = dict(provider or _local_provider_metadata())
        self._chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        self._vectors: list[dict[str, float]] = []
        self._idf: dict[str, float] = {}
        self._build()

    def _build(self) -> None:
        tokenized = [self._tokens(chunk.text + " " + chunk.title + " " + chunk.heading) for chunk in self.chunks]
        doc_count = max(len(tokenized), 1)
        df: Counter[str] = Counter()
        for tokens in tokenized:
            df.update(set(tokens))
        self._idf = {
            term: math.log((doc_count + 1) / (freq + 1)) + 1.0 for term, freq in df.items()
        }
        self._vectors = [self._tfidf(tokens) for tokens in tokenized]

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return [t.lower() for t in _WORD_RE.findall(text) if len(t) > 2]

    def _tfidf(self, tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        total = sum(counts.values()) or 1
        vector: dict[str, float] = {}
        for term, count in counts.items():
            vector[term] = (count / total) * self._idf.get(term, 1.0)
        return vector

    @staticmethod
    def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        shared = set(a) & set(b)
        dot = sum(a[t] * b[t] for t in shared)
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def search(
        self,
        query: str,
        *,
        limit: int = 12,
        mount: str | None = None,
        edition: str | None = None,
    ) -> list[SemanticHit]:
        query_vec = self._tfidf(self._tokens(query))
        hits: list[SemanticHit] = []
        for chunk, vector in zip(self.chunks, self._vectors, strict=True):
            if mount is not None and chunk.mount != mount:
                continue
            if edition is not None and chunk.edition != edition:
                continue
            score = self._cosine(query_vec, vector)
            if score > 0:
                hits.append(SemanticHit(chunk=chunk, score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.title.lower()))
        return hits[:limit]

    def get_chunk(self, chunk_id: str) -> DocChunk | None:
        return self._chunk_map.get(chunk_id)

    def similar(self, chunk_id: str, *, limit: int = 6) -> list[SemanticHit]:
        try:
            index = next(i for i, chunk in enumerate(self.chunks) if chunk.chunk_id == chunk_id)
        except StopIteration:
            return []
        source = self.chunks[index]
        vector = self._vectors[index]
        hits: list[SemanticHit] = []
        for idx, (chunk, candidate) in enumerate(zip(self.chunks, self._vectors, strict=True)):
            if idx == index:
                continue
            if chunk.mount != source.mount or chunk.edition != source.edition:
                continue
            score = self._cosine(vector, candidate)
            if score > 0:
                hits.append(SemanticHit(chunk=chunk, score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.title.lower()))
        return hits[:limit]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "version": 2,
            "backend": "tfidf",
            "provider": dict(self.provider),
            "provenance": {
                "interface_version": 1,
                "index_fingerprint": self.fingerprint(),
                "chunk_count": len(self.chunks),
            },
            "chunk_count": len(self.chunks),
            "chunks": [
                {
                    "chunk_id": chunk.chunk_id,
                    "node_id": chunk.node_id,
                    "url": chunk.url,
                    "title": chunk.title,
                    "heading": chunk.heading,
                    "text": chunk.text,
                    "mount": chunk.mount,
                    "edition": chunk.edition,
                }
                for chunk in self.chunks
            ],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> EmbeddingIndex:
        chunks = tuple(
            DocChunk(
                chunk_id=item["chunk_id"],
                node_id=item["node_id"],
                url=item["url"],
                title=item["title"],
                heading=item["heading"],
                text=item["text"],
                mount=item.get("mount", "chirp"),
                edition=item.get("edition", "latest"),
            )
            for item in payload.get("chunks", [])
            if isinstance(item, dict)
        )
        provider = payload.get("provider")
        return cls(chunks, provider=provider if isinstance(provider, dict) else None)

    @classmethod
    def from_nodes(
        cls,
        nodes: list[Any],
        *,
        documents: dict[str, Any] | None = None,
    ) -> EmbeddingIndex:
        return cls(chunk_catalog_nodes(nodes, documents=documents))

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_json(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> EmbeddingIndex | None:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_json(payload)

    def fingerprint(self) -> str:
        """Return a stable content fingerprint for compatibility diagnostics."""
        digest = hashlib.sha256()
        for chunk in self.chunks:
            digest.update(
                "\0".join(
                    (
                        chunk.chunk_id,
                        chunk.node_id,
                        chunk.url,
                        chunk.title,
                        chunk.heading,
                        chunk.text,
                        chunk.mount,
                        chunk.edition,
                    )
                ).encode("utf-8")
            )
            digest.update(b"\n")
        return digest.hexdigest()

    def with_provider(self, provider: dict[str, Any]) -> EmbeddingIndex:
        """Return an equivalent index carrying explicit provider provenance."""
        return EmbeddingIndex(self.chunks, provider=provider)


def _local_provider_metadata() -> dict[str, Any]:
    return {
        "interface_version": 1,
        "id": "furatena-local-tfidf",
        "version": "1",
        "kind": "local",
        "model": "tfidf",
        "deterministic": True,
        "external": False,
    }
