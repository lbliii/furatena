"""Cross-edition Content IR diff service, HTTP, and MCP contracts."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from chirp.testing.client import TestClient
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from furatena.catalog.access import AccessPolicy
from furatena.catalog.content_ir_diff import ContentIRDiffError, diff_content_ir
from furatena.catalog.mcp import FuraMCPServer
from furatena.catalog.models import (
    ContentDirective,
    ContentHeading,
    ContentIR,
    ContentLink,
    DocNode,
    SectionChunk,
)
from tests.test_edition_routing import _docs

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "src/furatena/catalog/schemas/content-ir-diff-v1.schema.json"
FIXTURE = REPO / "tests/fixtures/content-ir-diff/v1/page.json"


def test_page_diff_uses_structural_hashes_and_lazy_typed_detail(tmp_path: Path) -> None:
    docs = _docs(tmp_path)

    changed = diff_content_ir(
        docs.catalog,
        mount="docs",
        slug="guide",
        from_edition="1.0.0",
        to_edition="latest",
        limit=1,
    )
    unchanged = diff_content_ir(
        docs.catalog,
        mount="docs",
        slug="guide",
        from_edition="1.0.0",
        to_edition="1.0.0",
    )

    assert changed["kind"] == "page"
    assert changed["summary"]["status"] == "changed"
    assert changed["total"] >= 2
    assert len(changed["changes"]) == 1
    assert changed["next_offset"] == 1
    assert changed["page"]["from"]["node_id"] == "docs:1.0.0:guide"
    assert changed["page"]["to"]["node_id"] == "docs:latest:guide"
    assert changed["page"]["from"]["provenance"]["ref"]
    assert changed["page"]["from"]["provenance"]["edition"] == "1.0.0"
    assert changed["page"]["from"]["provenance"]["output_channel"] == "1.0.0"
    assert len(changed["page"]["structural_hashes"]["from"]) == 64
    assert len(changed["page"]["structural_hashes"]["to"]) == 64
    assert "body_html" not in json.dumps(changed)
    assert unchanged["summary"]["status"] == "unchanged"
    assert unchanged["changes"] == []
    assert unchanged["total"] == 0


def test_typed_detail_reports_heading_moves_directives_and_link_churn() -> None:
    def node(
        edition: str,
        *,
        headings: tuple[ContentHeading, ...],
        directives: tuple[ContentDirective, ...],
        links: tuple[ContentLink, ...],
        text: str,
    ) -> DocNode:
        return DocNode(
            url=f"/{edition}/guide/",
            slug="guide",
            title="Guide",
            description="",
            layout="doc",
            weight=1,
            section="docs",
            tags=frozenset(),
            body_md="ignored by the diff",
            body_html="<p>must never be compared</p>",
            toc=(),
            source_path="guide.md",
            meta={"source_provider": "git", "source_ref": edition},
            mount="docs",
            edition=edition,
            content_ir=ContentIR(
                headings=headings,
                directives=directives,
                links=links,
            ),
            sections=(SectionChunk(id="guide", heading="Guide", depth=1, text=text),),
        )

    old = node(
        "1.0.0",
        headings=(
            ContentHeading(2, "Alpha", "alpha", 2),
            ContentHeading(2, "Beta", "beta", 4),
        ),
        directives=(ContentDirective("note", {"kind": "old"}, 6),),
        links=(ContentLink("/old/", "Old", 8),),
        text="Old section text",
    )
    new = node(
        "latest",
        headings=(
            ContentHeading(2, "Beta", "beta", 2),
            ContentHeading(2, "Alpha", "alpha", 4),
        ),
        directives=(ContentDirective("note", {"kind": "new"}, 6),),
        links=(ContentLink("/new/", "New", 8),),
        text="New section text",
    )

    class Catalog:
        mounts = (SimpleNamespace(id="docs"),)
        default_mount = mounts[0]

        def __init__(self) -> None:
            self.active_channel = "latest"
            self._nodes = {"1.0.0": (old,), "latest": (new,)}

        @contextmanager
        def use_edition(self, edition: str):
            previous = self.active_channel
            self.active_channel = edition
            try:
                yield
            finally:
                self.active_channel = previous

        def has_edition(self, mount: str, edition: str) -> bool:
            return mount == "docs" and edition in self._nodes

        def discovered_editions_for(self, mount: str):
            assert mount == "docs"
            return (
                SimpleNamespace(id="1.0.0", status="legacy", ref="v1.0.0", resolved_ref="1" * 40),
                SimpleNamespace(id="latest", status="current", ref="main", resolved_ref="2" * 40),
            )

        def can_access_mount(self, *_args, **_kwargs) -> bool:
            return True

        def can_access_node(self, *_args, **_kwargs) -> bool:
            return True

        def get_by_slug(self, slug: str, *, mount: str):
            return next(
                (
                    item
                    for item in self._nodes[self.active_channel]
                    if item.slug == slug and item.mount == mount
                ),
                None,
            )

        @property
        def nodes(self):
            return self._nodes[self.active_channel]

    payload = diff_content_ir(
        Catalog(),
        mount="docs",
        slug="guide",
        from_edition="1.0.0",
        to_edition="latest",
    )
    repeated = diff_content_ir(
        Catalog(),
        mount="docs",
        slug="guide",
        from_edition="1.0.0",
        to_edition="latest",
    )

    assert payload == repeated
    assert [
        (item["component"], item["identity"], item["change"]) for item in payload["changes"]
    ] == [
        ("section", "guide", "changed"),
        ("heading", "alpha", "moved"),
        ("heading", "beta", "moved"),
        ("directive", "note", "changed"),
        ("link", "/new/", "added"),
        ("link", "/old/", "removed"),
    ]


def test_versioned_schema_validates_fixture_and_rejects_presentation_fields() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    validator.validate(payload)
    invalid = copy.deepcopy(payload)
    invalid["body_html"] = "<p>presentation must never enter this contract</p>"
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_mount_rollup_is_slug_stable_and_paginated(tmp_path: Path) -> None:
    docs = _docs(tmp_path)

    first = diff_content_ir(
        docs.catalog,
        mount="docs",
        from_edition="1.0.0",
        to_edition="latest",
        limit=2,
    )
    second = diff_content_ir(
        docs.catalog,
        mount="docs",
        from_edition="1.0.0",
        to_edition="latest",
        limit=2,
        offset=2,
    )

    assert first["kind"] == "mount"
    assert first["summary"]["added"] == 2
    assert first["summary"]["changed"] == 1
    assert first["summary"]["unchanged"] == 1
    assert first["total"] == 4
    assert first["next_offset"] == 2
    assert [item["slug"] for item in [*first["pages"], *second["pages"]]] == [
        "brand-new",
        "guide",
        "topic",
        "topic/new",
    ]
    assert second["next_offset"] is None
    Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(first)


def test_http_and_mcp_return_the_same_content_ir_diff_schema(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    client = TestClient(docs.create_app())

    async def request_http() -> dict:
        async with client:
            response = await client.get(
                "/catalog/diff?mount=docs&slug=guide&from=1.0.0&to=latest&limit=3"
            )
        assert response.status == 200
        return json.loads(response.text)

    http_payload = asyncio.run(request_http())
    mcp_result = FuraMCPServer(docs).call_tool(
        "diff_content_ir",
        {
            "mount": "docs",
            "slug": "guide",
            "from_edition": "1.0.0",
            "to_edition": "latest",
            "limit": 3,
        },
    )
    mcp_payload = mcp_result["structuredContent"]

    assert mcp_result["isError"] is False
    assert mcp_payload == http_payload
    Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(http_payload)
    tool = next(
        item for item in FuraMCPServer(docs).list_tools() if item["name"] == "diff_content_ir"
    )
    assert tool["inputSchema"]["required"] == ["from_edition", "to_edition"]
    assert set(tool["outputSchema"]["required"]) >= {
        "kind",
        "mount",
        "from",
        "to",
        "summary",
        "total",
        "changes",
        "pages",
    }


def test_milo_mcp_adapter_exposes_cross_edition_diff(tmp_path: Path) -> None:
    from milo.testing import MCPClient

    from furatena.catalog.mcp import build_milo_cli

    docs = _docs(tmp_path)
    client = MCPClient(build_milo_cli(FuraMCPServer(docs)))

    tool = next(item for item in client.list_tools() if item.name == "diff_content_ir")
    result = client.call(
        "diff_content_ir",
        from_edition="1.0.0",
        to_edition="latest",
        mount="docs",
        slug="guide",
    )

    assert tool.output_schema is not None
    assert result.is_error is False
    assert result.structured["kind"] == "page"
    assert result.structured["summary"]["status"] == "changed"


def test_eol_diff_requires_explicit_opt_in_on_both_transports(tmp_path: Path) -> None:
    docs = _docs(tmp_path, historical_status="eol")
    client = TestClient(docs.create_app())

    async def request_http() -> tuple[int, dict]:
        async with client:
            response = await client.get("/catalog/diff?mount=docs&slug=guide&from=1.0.0&to=latest")
        return response.status, json.loads(response.text)

    status, http_payload = asyncio.run(request_http())
    mcp_result = FuraMCPServer(docs).call_tool(
        "diff_content_ir",
        {
            "mount": "docs",
            "slug": "guide",
            "from_edition": "1.0.0",
            "to_edition": "latest",
        },
    )

    assert status == 403
    assert http_payload["error"]["code"] == "eol_opt_in_required"
    assert mcp_result["isError"] is True
    assert mcp_result["structuredContent"] == http_payload
    allowed = diff_content_ir(
        docs.catalog,
        mount="docs",
        slug="guide",
        from_edition="1.0.0",
        to_edition="latest",
        include_eol=True,
    )
    assert allowed["from"]["status"] == "eol"


def test_diff_filters_each_edition_before_classification(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    with docs.catalog.use_edition("1.0.0"):
        node = docs.catalog.get_by_slug("guide", mount="docs")
    assert node is not None
    shard = docs.catalog._edition_shards["1.0.0"]["docs"]
    private = replace(
        node,
        source_path="private-canary.md",
        meta={**node.meta, "visibility": "private"},
    )
    shard._nodes_by_slug["guide"] = private
    shard._nodes = [private if item.slug == "guide" else item for item in shard._nodes]

    payload = diff_content_ir(
        docs.catalog,
        mount="docs",
        slug="guide",
        from_edition="1.0.0",
        to_edition="latest",
    )

    assert payload["summary"]["status"] == "added"
    assert payload["page"]["from"] is None
    assert "private-canary" not in json.dumps(payload)


def test_diff_fails_closed_for_mount_access_and_edition_identity(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    docs.catalog.mounts = tuple(
        replace(mount, access=AccessPolicy(visibility="private")) for mount in docs.catalog.mounts
    )

    with pytest.raises(ContentIRDiffError) as denied:
        diff_content_ir(
            docs.catalog,
            mount="docs",
            slug="guide",
            from_edition="1.0.0",
            to_edition="latest",
        )
    assert denied.value.code == "mount_not_found"
    assert denied.value.status == 404

    identity_root = tmp_path / "identity"
    identity_root.mkdir()
    docs = _docs(identity_root)
    with docs.catalog.use_edition("1.0.0"):
        node = docs.catalog.get_by_slug("guide", mount="docs")
    assert node is not None
    shard = docs.catalog._edition_shards["1.0.0"]["docs"]
    mismatched = replace(node, edition="latest")
    shard._nodes_by_slug["guide"] = mismatched
    shard._nodes = [mismatched if item.slug == "guide" else item for item in shard._nodes]

    with pytest.raises(ContentIRDiffError) as mismatch:
        diff_content_ir(
            docs.catalog,
            mount="docs",
            slug="guide",
            from_edition="1.0.0",
            to_edition="latest",
        )
    assert mismatch.value.code == "identity_mismatch"
    assert mismatch.value.status == 409


def test_missing_content_ir_fails_closed_instead_of_diffing_html(tmp_path: Path) -> None:
    docs = _docs(tmp_path)
    with docs.catalog.use_edition("1.0.0"):
        node = docs.catalog.get_by_slug("guide", mount="docs")
    assert node is not None
    shard = docs.catalog._edition_shards["1.0.0"]["docs"]
    missing_ir = replace(node, content_ir=None)
    shard._nodes_by_slug["guide"] = missing_ir
    shard._nodes = [missing_ir if item.slug == "guide" else item for item in shard._nodes]

    try:
        diff_content_ir(
            docs.catalog,
            mount="docs",
            slug="guide",
            from_edition="1.0.0",
            to_edition="latest",
        )
    except ContentIRDiffError as exc:
        assert exc.code == "content_ir_unavailable"
        assert exc.status == 409
    else:
        raise AssertionError("diff must fail closed when normalized Content IR is absent")
