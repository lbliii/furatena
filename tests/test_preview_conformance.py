"""Human-and-agent preview conformance and review-comment contracts."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import replace
from http.client import HTTPMessage

import pytest

from furatena.catalog.preview_conformance import (
    PreviewHTTPResponse,
    _SameOriginRedirectHandler,
    inspect_preview,
    preview_comment_markdown,
)
from tests.preview_support import HEAD_SHA, sample_ready_manifest, sample_surfaces


def _fetch(url: str, token: str | None, accept: str | None) -> PreviewHTTPResponse:
    manifest = sample_ready_manifest()
    if url.endswith("/readyz"):
        body = json.dumps({"ok": True, "status": "ready"}).encode()
        return PreviewHTTPResponse(200, "application/json", body, url)
    assert token == "review-token"
    if url.endswith("/preview-manifest.json"):
        body = json.dumps(manifest.to_dict()).encode()
        return PreviewHTTPResponse(200, "application/json", body, url)
    if accept == "text/markdown" or url.endswith(".md"):
        return PreviewHTTPResponse(200, "text/markdown; charset=utf-8", b"# Preview", url)
    if url.endswith("/llms.txt"):
        return PreviewHTTPResponse(200, "text/plain; charset=utf-8", b"# LLMs", url)
    if accept == "text/html":
        return PreviewHTTPResponse(200, "text/html; charset=utf-8", b"<h1>Preview</h1>", url)
    return PreviewHTTPResponse(200, "application/json", b"{}", url)


def test_conformance_verifies_one_manifest_across_human_and_agent_surfaces() -> None:
    result = inspect_preview(
        "https://preview-422.example.test",
        "review-token",
        HEAD_SHA,
        fetch=_fetch,
    )

    assert result.ok is True
    assert result.manifest == sample_ready_manifest()
    assert {check.check_id for check in result.checks} == {
        "manifest-state",
        "immutable-head-sha",
        "immutable-origin",
        "readiness",
        "html",
        "negotiated-markdown",
        "markdown-alias",
        "llms",
        "catalog",
        "catalog-query",
        "search",
        "metadata",
    }


def test_conformance_rejects_a_superseded_head() -> None:
    result = inspect_preview(
        "https://preview-422.example.test",
        "review-token",
        "b" * 40,
        fetch=_fetch,
    )

    assert result.ok is False
    stale = next(check for check in result.checks if check.check_id == "immutable-head-sha")
    assert stale.ok is False
    assert "Discard the stale deployment" in str(stale.remediation)


def test_conformance_rejects_cross_origin_manifest_surfaces_before_probing() -> None:
    surfaces = replace(
        sample_surfaces(),
        catalog_url="https://credential-sink.example.test/catalog.json",
    )
    manifest = replace(sample_ready_manifest(), surfaces=surfaces, manifest_digest="")
    calls: list[tuple[str, str | None]] = []

    def fetch(url: str, token: str | None, accept: str | None) -> PreviewHTTPResponse:
        calls.append((url, token))
        assert accept == "application/json"
        return PreviewHTTPResponse(
            200,
            "application/json",
            json.dumps(manifest.to_dict()).encode(),
            url,
        )

    result = inspect_preview(
        "https://preview-422.example.test",
        "review-token",
        HEAD_SHA,
        fetch=fetch,
    )

    assert result.ok is False
    assert [url for url, _token in calls] == [
        "https://preview-422.example.test/preview-manifest.json"
    ]
    origin = next(check for check in result.checks if check.check_id == "immutable-origin")
    assert origin.ok is False
    assert "credential-sink.example.test" not in origin.summary


def test_credentialed_fetch_refuses_cross_origin_redirects_without_leaking_secret() -> None:
    request = urllib.request.Request(
        "https://preview-422.example.test/catalog.json",
        headers={"Authorization": "Bearer review-token"},
    )

    with pytest.raises(urllib.error.URLError, match="cross-origin") as raised:
        _SameOriginRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            HTTPMessage(),
            "https://credential-sink.example.test/catalog.json",
        )

    assert "review-token" not in str(raised.value)


def test_comment_is_idempotently_addressable_and_never_contains_the_token() -> None:
    result = inspect_preview(
        "https://preview-422.example.test",
        "review-token",
        HEAD_SHA,
        fetch=_fetch,
    )
    comment = preview_comment_markdown("ready", HEAD_SHA, result=result)

    assert comment.count("<!-- furatena-preview -->") == 1
    assert "Open human preview" in comment
    assert "Negotiated Markdown" in comment
    assert "`llms.txt`" in comment
    assert "Provider diagnostics" in comment
    assert "review-token" not in comment


def test_removed_comment_does_not_present_a_stale_url() -> None:
    comment = preview_comment_markdown("removed", HEAD_SHA)

    assert "have been removed" in comment
    assert "https://" not in comment
