"""Route structure and representative behavior drift contracts."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from pathlib import Path

from chirp.testing.client import TestClient

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.route_manifest import route_manifest_entries
from furatena.catalog.runtime import ServeConfig, ServeMode

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
STRUCTURE_SNAPSHOT = REPO / "tests" / "fixtures" / "route_manifest.snapshot"
BEHAVIOR_SNAPSHOT = REPO / "tests" / "fixtures" / "route_behavior_snapshot.json"


def _docs() -> DocsApp:
    return DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )


def test_route_manifest_matches_pre_refactor_snapshot() -> None:
    docs = _docs()
    app = docs.create_app()

    entries = route_manifest_entries(app, catalog=docs.catalog)
    actual = [entry.snapshot_line() for entry in entries]
    origin_digest = hashlib.sha256(
        "\n".join(entry.handler_origin for entry in entries).encode()
    ).hexdigest()
    origin_header = next(
        line for line in STRUCTURE_SNAPSHOT.read_text(encoding="utf-8").splitlines()
        if line.startswith("# handler-origins-sha256: ")
    )
    expected = [
        line
        for line in STRUCTURE_SNAPSHOT.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert actual == expected
    assert origin_digest == origin_header.partition(": ")[2]


def test_route_manifest_records_required_contract_metadata() -> None:
    docs = _docs()
    entries = route_manifest_entries(docs.create_app(), catalog=docs.catalog)

    assert len(entries) >= 46
    for entry in entries:
        assert entry.path.startswith("/")
        assert entry.methods
        assert entry.mount
        assert entry.handler
        assert entry.handler_origin.startswith(
            ("furatena.catalog.docs_app:", "furatena.catalog.route_registrars:")
        )
        assert entry.response_contract
    save = next(entry for entry in entries if entry.path == "/docs/_author/studio/save")
    assert save.methods == ("POST",)
    assert save.template == "views/author_studio.html"
    assert save.fragment == "author_studio_workspace"
    og_image = next(entry for entry in entries if entry.path == "/og/{name}")
    assert og_image.handler_origin == "furatena.catalog.route_registrars:og_image"


def test_moved_route_handlers_remain_source_inspectable() -> None:
    app = _docs().create_app()
    moved = [
        route.handler
        for route in app._pending_routes
        if route.handler.__module__ == "furatena.catalog.route_registrars"
    ]

    assert moved
    for handler in moved:
        assert f"def {handler.__name__}" in inspect.getsource(handler)


def _summarize(response) -> dict[str, object]:
    text = response.text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        contracts = {
            "page-root": 'id="page-root"',
            "search-results-panel": 'id="search-results-panel"',
            "page-not-found": "Page not found",
        }
        markers = [name for name, marker in contracts.items() if marker in text]
        return {"status": response.status, "format": "html", "markers": markers}
    return {
        "status": response.status,
        "format": "json",
        "keys": sorted(payload) if isinstance(payload, dict) else [],
        "rule_ids": sorted(
            item.get("rule_id", "")
            for item in payload.get("diagnostics", [])
            if isinstance(payload, dict) and isinstance(item, dict) and item.get("rule_id")
        ),
    }


def test_representative_route_behavior_matches_snapshot() -> None:
    docs = _docs()
    client = TestClient(docs.create_app())

    async def capture() -> dict[str, dict[str, object]]:
        async with client:
            responses = {
                "page": await client.get("/docs/"),
                "boosted_page": await client.get(
                    "/docs/", headers={"HX-Request": "true", "HX-Boosted": "true"}
                ),
                "catalog": await client.get("/catalog.json"),
                "routes": await client.get("/routes.json"),
                "search": await client.get("/search?q=export"),
                "missing_page": await client.get("/docs/does-not-exist/"),
                "transition_get": await client.get("/docs/_author/transition"),
                "transition_post": await client.post("/docs/_author/transition"),
            }
        for name in ("catalog", "routes", "transition_get", "transition_post"):
            assert responses[name].content_type.startswith("application/json")
        return {name: _summarize(response) for name, response in responses.items()}

    actual = asyncio.run(capture())
    expected = json.loads(BEHAVIOR_SNAPSHOT.read_text(encoding="utf-8"))

    assert actual == expected
