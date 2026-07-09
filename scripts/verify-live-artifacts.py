#!/usr/bin/env python3
"""Verify that a live Furatena service delivers complete bulk artifacts."""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import urllib.request
from typing import Any

_JSON_PATHS = (
    "/catalog.json",
    "/catalog/query.json",
    "/search.json",
    "/semantic.json",
)


def _fetch(origin: str, path: str, *, timeout: float) -> bytes:
    request = urllib.request.Request(
        f"{origin.rstrip('/')}{path}",
        headers={"Accept-Encoding": "identity", "User-Agent": "furatena-live-smoke/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        try:
            body = response.read()
        except http.client.IncompleteRead as exc:
            raise RuntimeError(
                f"{path}: response ended after {len(exc.partial)} bytes; "
                f"{exc.expected} more bytes were expected"
            ) from exc
        content_length = response.headers.get("Content-Length")
        if content_length is not None and len(body) != int(content_length):
            raise RuntimeError(
                f"{path}: received {len(body)} bytes; Content-Length is {content_length}"
            )
        return body


def _decode_json(path: str, body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path}: response is not complete JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path}: expected a JSON object")
    return payload


def verify_live_artifacts(origin: str, *, timeout: float = 120.0) -> dict[str, int]:
    """Fetch and validate every bulk response affected by slow origin drains."""
    payloads = {
        path: _decode_json(path, _fetch(origin, path, timeout=timeout))
        for path in _JSON_PATHS
    }
    catalog = payloads["/catalog.json"]
    page_count = catalog.get("page_count")
    pages = catalog.get("pages")
    if not isinstance(page_count, int) or not isinstance(pages, list) or page_count != len(pages):
        raise RuntimeError("/catalog.json: page_count does not match pages")

    for path, collection_key in (
        ("/catalog/query.json", "pages"),
        ("/search.json", "entries"),
    ):
        payload = payloads[path]
        collection = payload.get(collection_key)
        if payload.get("page_count") != page_count or not isinstance(collection, list):
            raise RuntimeError(f"{path}: page_count does not match the frozen catalog")

    semantic = payloads["/semantic.json"]
    chunks = semantic.get("chunks")
    if not isinstance(chunks, list) or semantic.get("chunk_count") != len(chunks):
        raise RuntimeError("/semantic.json: chunk_count does not match chunks")

    llms_full = _fetch(origin, "/llms-full.txt", timeout=timeout)
    if not llms_full.strip():
        raise RuntimeError("/llms-full.txt: response is empty")

    return {
        "page_count": page_count,
        "semantic_chunk_count": len(chunks),
        "llms_full_bytes": len(llms_full),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="HTTPS origin of the deployed Furatena service")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    try:
        result = verify_live_artifacts(args.origin, timeout=args.timeout)
    except (OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
