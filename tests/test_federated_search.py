"""Publish-time federated keyword and TF-IDF index contracts."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import cast

import pytest
from jsonschema import Draft202012Validator

from furatena.catalog.federated_search import (
    FederatedSearchHit,
    FederatedSearchResult,
    FederatedShardSearchIndex,
    build_federated_search_index,
    rank_merge_federated_hits,
    validate_federated_search_index,
)
from furatena.catalog.federation_artifacts import load_federated_search_schema
from furatena.catalog.models import DocNode
from furatena.catalog.registry import CatalogRegistry


def _pages() -> list[dict[str, object]]:
    return [
        {
            "node_id": "alpha:latest:install",
            "title": "Install the client",
            "description": "Set up the command-line client.",
            "slug": "install",
            "tags": ["setup"],
            "sections": [{"heading": "Install", "text": "Run the package installer."}],
        },
        {
            "node_id": "alpha:latest:tokens",
            "title": "API tokens",
            "description": "Create and rotate an API token.",
            "slug": "tokens",
            "tags": ["security"],
            "sections": [{"heading": "Rotate", "text": "Rotate tokens without downtime."}],
        },
    ]


def test_index_is_deterministic_valid_and_queries_without_rebuilding() -> None:
    pages = _pages()

    first = build_federated_search_index(pages, mount="alpha", edition="latest")
    second = build_federated_search_index(reversed(pages), mount="alpha", edition="latest")
    index = FederatedShardSearchIndex(first)
    hits = index.search("rotate api token", limit=4)

    assert first == second
    assert validate_federated_search_index(first) == []
    assert first["schema_version"] == 2
    assert first["algorithm"] == "keyword-tfidf-v1"
    assert list(first["idf"]) == sorted(first["idf"])
    assert hits[0].node_id == "alpha:latest:tokens"
    assert 0 < hits[0].keyword_score <= 1
    assert 0 < hits[0].tfidf_score <= 1


def test_shipped_schema_accepts_v2_and_the_v1_rolling_reader_shape() -> None:
    validator = Draft202012Validator(load_federated_search_schema())
    current = build_federated_search_index(_pages(), mount="alpha", edition="latest")
    legacy = {
        "schema_version": 1,
        "mount": "alpha",
        "edition": "latest",
        "documents": [
            {
                "node_id": "alpha:latest:guide",
                "title": "Legacy guide",
                "text": "rolling upgrade compatibility",
            }
        ],
    }

    validator.validate(current)
    validator.validate(legacy)


def test_validator_rejects_nondeterminism_out_of_range_postings_and_node_duplicates() -> None:
    payload = build_federated_search_index(_pages(), mount="alpha", edition="latest")
    broken = copy.deepcopy(payload)
    broken["documents"].append(copy.deepcopy(broken["documents"][0]))
    first_term = next(iter(broken["tfidf_postings"]))
    broken["tfidf_postings"][first_term][0][0] = 99

    errors = validate_federated_search_index(broken)

    assert "documents: node ids must be unique and sorted" in errors
    assert any("document indexes must be unique, sorted, and in range" in error for error in errors)


def test_validator_rejects_idf_and_vector_drift() -> None:
    payload = build_federated_search_index(_pages(), mount="alpha", edition="latest")
    idf_drift = copy.deepcopy(payload)
    first_term = next(iter(idf_drift["idf"]))
    idf_drift["idf"][first_term] += 0.01
    vector_drift = copy.deepcopy(payload)
    vector_drift["tfidf_postings"][first_term][0][1] -= 0.01

    assert "idf: weights drift from keyword posting document frequencies" in (
        validate_federated_search_index(idf_drift)
    )
    assert "tfidf_postings: weights drift from keyword postings and IDF" in (
        validate_federated_search_index(vector_drift)
    )


@pytest.mark.parametrize(
    "case",
    (
        "boolean_version",
        "boolean_idf",
        "boolean_posting_index",
        "boolean_keyword_count",
        "boolean_tfidf_weight",
        "overlong_node_id",
        "overlong_snippet",
        "mismatched_empty_indexes",
        "zero_keyword_counts",
    ),
)
def test_schema_and_reader_consistently_reject_bounded_malformed_values(case: str) -> None:
    payload = build_federated_search_index(_pages(), mount="alpha", edition="latest")
    broken = copy.deepcopy(payload)
    first_term = next(iter(broken["idf"]))
    if case == "boolean_version":
        broken["schema_version"] = True
    elif case == "boolean_idf":
        broken["idf"][first_term] = True
    elif case == "boolean_posting_index":
        broken["keyword_postings"][first_term][0][0] = True
    elif case == "boolean_keyword_count":
        broken["keyword_postings"][first_term][0][1] = True
    elif case == "boolean_tfidf_weight":
        broken["tfidf_postings"][first_term][0][1] = True
    elif case == "overlong_node_id":
        broken["documents"][0]["node_id"] = "alpha:latest:" + "x" * 1012
    elif case == "overlong_snippet":
        broken["documents"][0]["snippet"] = "x" * 321
    elif case == "mismatched_empty_indexes":
        broken["idf"] = {}
    elif case == "zero_keyword_counts":
        broken["keyword_postings"][first_term][0][1:] = [0, 0]

    schema_errors = list(Draft202012Validator(load_federated_search_schema()).iter_errors(broken))

    assert schema_errors
    assert validate_federated_search_index(broken)
    with pytest.raises(ValueError):
        FederatedShardSearchIndex(broken)


def test_tokenless_public_document_builds_valid_empty_indexes() -> None:
    payload = build_federated_search_index(
        [
            {
                "node_id": "alpha:latest:a",
                "title": "a",
                "description": "a an and the",
                "slug": "a",
                "tags": ["a"],
                "sections": [{"heading": "a", "text": "an and the"}],
            }
        ],
        mount="alpha",
        edition="latest",
    )

    Draft202012Validator(load_federated_search_schema()).validate(payload)
    assert payload["idf"] == {}
    assert payload["keyword_postings"] == {}
    assert payload["tfidf_postings"] == {}
    assert validate_federated_search_index(payload) == []
    assert FederatedShardSearchIndex(payload).search("anything", limit=4) == ()


def test_v1_document_indexes_remain_readable_during_rolling_upgrade() -> None:
    legacy = {
        "schema_version": 1,
        "mount": "alpha",
        "edition": "latest",
        "documents": [
            {
                "node_id": "alpha:latest:guide",
                "title": "Legacy guide",
                "text": "rolling upgrade compatibility",
            }
        ],
    }

    hits = FederatedShardSearchIndex(legacy).search("upgrade", limit=1)

    assert hits[0].node_id == "alpha:latest:guide"
    assert hits[0].tfidf_score == 0


def test_rank_merge_has_a_total_order_for_cross_shard_score_ties() -> None:
    def hit(mount: str, node_id: str) -> FederatedSearchHit:
        return FederatedSearchHit(
            node_id=node_id,
            title="Same title",
            snippet="same",
            mount=mount,
            edition="latest",
            shard_fingerprint=mount,
            score=0.75,
            keyword_score=0.5,
            tfidf_score=0.5,
        )

    expected = ("alpha:latest:a", "alpha:latest:b", "beta:latest:a")

    assert (
        tuple(
            item.node_id
            for item in rank_merge_federated_hits(
                [
                    hit("beta", "beta:latest:a"),
                    hit("alpha", "alpha:latest:b"),
                    hit("alpha", "alpha:latest:a"),
                ],
                limit=3,
            )
        )
        == expected
    )


def test_catalog_intersects_authorized_nodes_before_fanout_and_merge() -> None:
    class RemoteSearch:
        def __init__(self) -> None:
            self.calls = 0
            self.result_node_id = "alpha:latest:allowed"

        def search(self, *args: object, **kwargs: object) -> FederatedSearchResult:
            self.calls += 1
            result_edition = self.result_node_id.split(":", 2)[1]
            return FederatedSearchResult(
                hits=(
                    FederatedSearchHit(
                        node_id=self.result_node_id,
                        title="Allowed",
                        snippet="allowed",
                        mount="alpha",
                        edition=result_edition,
                        shard_fingerprint="a" * 64,
                        score=1.0,
                        keyword_score=1.0,
                        tfidf_score=1.0,
                    ),
                    FederatedSearchHit(
                        node_id="alpha:latest:not-authorized",
                        title="Not authorized",
                        snippet="not authorized",
                        mount="alpha",
                        edition="latest",
                        shard_fingerprint="a" * 64,
                        score=1.0,
                        keyword_score=1.0,
                        tfidf_score=1.0,
                    ),
                ),
                searched_shards=("alpha:latest",),
                skipped_shards=(),
            )

    remote = RemoteSearch()

    class CatalogStub:
        remote_shards = remote
        active_channel = "latest"

        @staticmethod
        def _remote_mount_ids() -> set[str]:
            return {"alpha"}

    catalog = CatalogStub()
    assert (
        CatalogRegistry.federated_search_hits(
            cast(CatalogRegistry, catalog),
            "allowed",
            nodes=[],
            limit=4,
        )
        == ()
    )
    assert remote.calls == 0
    allowed = DocNode(
        url="/alpha/allowed/",
        slug="allowed",
        title="Allowed",
        description="",
        layout="doc",
        weight=0,
        section="",
        tags=frozenset(),
        body_md="",
        body_html="",
        toc=(),
        source_path="allowed.md",
        mount="alpha",
    )
    assert (
        CatalogRegistry.federated_search_hits(
            cast(CatalogRegistry, catalog),
            "allowed",
            nodes=[allowed],
            edition="old",
            limit=4,
        )
        == ()
    )
    assert remote.calls == 0

    hits = CatalogRegistry.federated_search_hits(
        cast(CatalogRegistry, catalog),
        "allowed",
        nodes=[allowed],
        limit=4,
    )

    assert [hit.node.node_id for hit in hits] == ["alpha:latest:allowed"]
    assert remote.calls == 1

    concrete = replace(allowed, edition="2026.1")
    remote.result_node_id = concrete.node_id
    concrete_hits = CatalogRegistry.federated_search_hits(
        cast(CatalogRegistry, catalog),
        "allowed",
        nodes=[concrete],
        limit=4,
    )

    assert [hit.node.node_id for hit in concrete_hits] == ["alpha:2026.1:allowed"]
    assert remote.calls == 2
