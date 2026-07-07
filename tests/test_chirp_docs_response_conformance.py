"""Full, boosted, and targeted response-shape contracts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from chirp.testing import TestClient

from furatena.catalog.docs_app import DocsApp

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
_DOCUMENT_WRAPPERS = ("<!DOCTYPE", "<html", "<body")


@pytest.fixture(scope="module")
def docs_client() -> TestClient:
    docs = DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
    )
    return TestClient(docs.create_app())


_RESPONSE_MATRIX = (
    pytest.param(
        "/docs/get-started/installation/",
        {},
        ("<!DOCTYPE html>", "<html", "<body", 'id="page-root"', "chirp-theme-docs-layout__article"),
        (),
        id="document-full",
    ),
    pytest.param(
        "/docs/get-started/installation/",
        {"HX-Request": "true", "HX-Boosted": "true"},
        ('id="page-root"', "chirp-theme-docs-layout__article", "hx-swap-oob"),
        _DOCUMENT_WRAPPERS,
        id="document-boosted",
    ),
    pytest.param(
        "/docs/get-started/installation/",
        {"HX-Request": "true", "HX-Target": "page-content"},
        ('id="page-content"', "chirp-theme-docs-layout__article"),
        (*_DOCUMENT_WRAPPERS, 'id="page-root"', "hx-swap-oob"),
        id="document-targeted",
    ),
    pytest.param(
        "/search?q=htmx",
        {},
        ("<!DOCTYPE html>", "<html", "<body", 'id="page-root"', 'id="search-results-panel"'),
        (),
        id="search-full",
    ),
    pytest.param(
        "/search?q=htmx",
        {"HX-Request": "true", "HX-Boosted": "true"},
        ('id="page-root"', "search-workspace__header", 'id="search-results-panel"'),
        _DOCUMENT_WRAPPERS,
        id="search-boosted",
    ),
    pytest.param(
        "/search?q=htmx",
        {"HX-Request": "true", "HX-Target": "search-results-panel"},
        ('id="search-results-panel"', "hx-swap-oob"),
        (*_DOCUMENT_WRAPPERS, 'id="page-root"', "search-workspace__header"),
        id="search-targeted",
    ),
)


@pytest.mark.parametrize(("url", "headers", "required", "forbidden"), _RESPONSE_MATRIX)
def test_response_shape_matrix(
    docs_client: TestClient,
    url: str,
    headers: dict[str, str],
    required: tuple[str, ...],
    forbidden: tuple[str, ...],
) -> None:
    async def _fetch():
        return await docs_client.get(url, headers=headers)

    response = asyncio.run(_fetch())
    assert response.status == 200
    for marker in required:
        assert marker in response.text
    for marker in forbidden:
        assert marker not in response.text


@pytest.mark.parametrize(
    ("url", "heading"),
    (
        ("/index.md", "# Publish polished docs from Markdown"),
        ("/docs.md", "# Documentation"),
        ("/docs/get-started/installation.md", "# Installation"),
        ("/docs/get-started/installation/index.md", "# Installation"),
    ),
)
def test_content_pages_expose_markdown_aliases(
    docs_client: TestClient,
    url: str,
    heading: str,
) -> None:
    async def _fetch():
        return await docs_client.get(url)

    response = asyncio.run(_fetch())
    assert response.status == 200
    assert response.content_type.startswith("text/markdown")
    assert heading in response.text
    assert "<!DOCTYPE html>" not in response.text
