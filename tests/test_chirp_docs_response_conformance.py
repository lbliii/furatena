"""Full, boosted, and targeted response-shape contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

import pytest
from chirp.testing import (
    RouteSmokeCase,
    TestClient,
    assert_route_smoke,
    transition_coverage,
    transition_observation,
)

import furatena.catalog.route_registrars as route_registrars
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.cli.main import main

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


@pytest.fixture(scope="module")
def author_docs_client() -> TestClient:
    docs = DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
    )
    return TestClient(docs.create_app())


_DOCUMENT_PATH = "/docs/get-started/installation/"
_SEARCH_PATH = "/search?q=htmx"
_ERROR_SUGGEST_PATH = "/errors/suggest?q=routing"
_AUTHOR_DASHBOARD_PATH = "/docs/_author/dashboard"
_AUTHOR_STATUS_PATH = "/docs/_author/page.json?slug=docs/get-started&validate=1"

_DOCUMENT_ROUTE_ID = "route:GET:%2Fdocs%2F%7Bslug%3Apath%7D"
_SEARCH_ROUTE_ID = "route:GET:%2Fsearch"
_ERROR_SUGGEST_ROUTE_ID = "route:GET:%2Ferrors%2Fsuggest"
_AUTHOR_DASHBOARD_ROUTE_ID = "route:GET:%2Fdocs%2F_author%2Fdashboard"
_AUTHOR_STATUS_ROUTE_ID = "route:GET:%2Fdocs%2F_author%2Fpage.json"

_DOCUMENT_PAGE_ROOT_TRANSITION = (
    "transition:template_block:template%3Aviews%252Fdoc.html:block%3Aviews%252Fdoc.html%3Apage_root"
)
_DOCUMENT_PAGE_CONTENT_TRANSITION = (
    "transition:template_block:template%3Aviews%252Fdoc.html:"
    "block%3Aviews%252Fdoc.html%3Apage_content"
)
_SEARCH_PAGE_ROOT_TRANSITION = (
    "transition:template_block:template%3Asearch.html:block%3Asearch.html%3Apage_root"
)
_SEARCH_ROUTE_TEMPLATE_TRANSITION = (
    "transition:route_template:route%3AGET%3A%252Fsearch:template%3Asearch.html"
)
_ERROR_SUGGEST_TRANSITION = (
    "transition:template_block:template%3Apartials%252Ferror_suggest_panel.html:"
    "block%3Apartials%252Ferror_suggest_panel.html%3Aerror_suggest_panel"
)
_ERROR_SUGGEST_ROUTE_TEMPLATE_TRANSITION = (
    "transition:route_template:route%3AGET%3A%252Ferrors%252Fsuggest:"
    "template%3Apartials%252Ferror_suggest_panel.html"
)
_AUTHOR_DASHBOARD_TRANSITION = (
    "transition:template_block:template%3Aviews%252Fauthor_dashboard.html:"
    "block%3Aviews%252Fauthor_dashboard.html%3Apage_root"
)
_AUTHOR_DASHBOARD_ROUTE_TEMPLATE_TRANSITION = (
    "transition:route_template:route%3AGET%3A%252Fdocs%252F_author%252Fdashboard:"
    "template%3Aviews%252Fauthor_dashboard.html"
)

_PUBLIC_ROUTE_SMOKE_CASES = (
    RouteSmokeCase(
        _DOCUMENT_PATH,
        mode="full_page",
        name="document-full",
        template="views/doc.html",
        block="page_root",
    ),
    RouteSmokeCase(
        _DOCUMENT_PATH,
        mode="boosted",
        name="document-boosted",
        template="views/doc.html",
        block="page_root",
        target="page-root",
    ),
    RouteSmokeCase(
        _DOCUMENT_PATH,
        mode="fragment",
        name="document-targeted",
        template="views/doc.html",
        block="page_content",
        target="page-content",
    ),
    RouteSmokeCase(
        _SEARCH_PATH,
        mode="full_page",
        name="search-full",
        template="search.html",
        block="page_root",
    ),
    RouteSmokeCase(
        _SEARCH_PATH,
        mode="boosted",
        name="search-boosted",
        template="search.html",
        block="page_root",
        target="page-root",
    ),
    RouteSmokeCase(
        _SEARCH_PATH,
        mode="fragment",
        name="search-targeted",
        template="search.html",
        block="search_results_panel",
        target="search-results-panel",
    ),
    RouteSmokeCase(
        _ERROR_SUGGEST_PATH,
        mode="fragment",
        name="error-suggestion-targeted",
        template="partials/error_suggest_panel.html",
        block="error_suggest_panel",
        target="error-suggest-panel",
    ),
)

_AUTHOR_ROUTE_SMOKE_CASES = (
    RouteSmokeCase(
        _AUTHOR_DASHBOARD_PATH,
        mode="full_page",
        name="author-dashboard-full",
        template="views/author_dashboard.html",
        block="page_root",
    ),
    RouteSmokeCase(
        _AUTHOR_STATUS_PATH,
        mode="fragment",
        name="author-status-targeted",
        template="partials/author_chrome.html",
        block="author_chrome",
        target="author-page-status",
    ),
)


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
        if headers.get("HX-Boosted") == "true":
            return await docs_client.boosted(url, target="page-root")
        return await docs_client.get(url, headers=headers)

    response = asyncio.run(_fetch())
    assert response.status == 200
    for marker in required:
        assert marker in response.text
    for marker in forbidden:
        assert marker not in response.text


def test_public_route_smoke_reports_compiled_transition_evidence(
    docs_client: TestClient,
) -> None:
    responses = asyncio.run(assert_route_smoke(docs_client, _PUBLIC_ROUTE_SMOKE_CASES))

    document_full = transition_observation(responses[(_DOCUMENT_PATH, "full_page")])
    document_boosted = transition_observation(responses[(_DOCUMENT_PATH, "boosted")])
    document_targeted = transition_observation(responses[(_DOCUMENT_PATH, "fragment")])
    search_full = transition_observation(responses[(_SEARCH_PATH, "full_page")])
    search_boosted = transition_observation(responses[(_SEARCH_PATH, "boosted")])
    search_targeted = transition_observation(responses[(_SEARCH_PATH, "fragment")])
    error_targeted = transition_observation(responses[(_ERROR_SUGGEST_PATH, "fragment")])

    assert document_full.route_id == _DOCUMENT_ROUTE_ID
    assert document_full.compiled_transition_ids == (_DOCUMENT_PAGE_ROOT_TRANSITION,)
    assert document_boosted.route_id == _DOCUMENT_ROUTE_ID
    assert document_boosted.request_mode == "boosted"
    assert document_boosted.mode_tags == ("boosted", "oob")
    assert document_targeted.route_id == _DOCUMENT_ROUTE_ID
    assert document_targeted.compiled_transition_ids == (_DOCUMENT_PAGE_CONTENT_TRANSITION,)

    assert search_full.route_id == _SEARCH_ROUTE_ID
    assert search_full.compiled_transition_ids == (
        _SEARCH_ROUTE_TEMPLATE_TRANSITION,
        _SEARCH_PAGE_ROOT_TRANSITION,
    )
    assert search_boosted.route_id == _SEARCH_ROUTE_ID
    assert search_boosted.request_mode == "boosted"
    assert search_targeted.route_id == _SEARCH_ROUTE_ID
    assert search_targeted.mode_tags == ("targeted", "oob")

    assert error_targeted.route_id == _ERROR_SUGGEST_ROUTE_ID
    assert error_targeted.compiled_transition_ids == (
        _ERROR_SUGGEST_ROUTE_TEMPLATE_TRANSITION,
        _ERROR_SUGGEST_TRANSITION,
    )

    coverage = transition_coverage(
        responses,
        expected_modes=("normal", "boosted", "targeted", "oob"),
        expected_transition_ids=(
            _DOCUMENT_PAGE_ROOT_TRANSITION,
            _DOCUMENT_PAGE_CONTENT_TRANSITION,
            _SEARCH_PAGE_ROOT_TRANSITION,
            _ERROR_SUGGEST_TRANSITION,
        ),
    )
    assert coverage.complete, coverage.summary()


def test_author_route_smoke_reports_compiled_transition_evidence(
    author_docs_client: TestClient,
) -> None:
    responses = asyncio.run(assert_route_smoke(author_docs_client, _AUTHOR_ROUTE_SMOKE_CASES))

    dashboard = transition_observation(responses[(_AUTHOR_DASHBOARD_PATH, "full_page")])
    status = transition_observation(responses[(_AUTHOR_STATUS_PATH, "fragment")])

    assert dashboard.route_id == _AUTHOR_DASHBOARD_ROUTE_ID
    assert dashboard.compiled_transition_ids == (
        _AUTHOR_DASHBOARD_ROUTE_TEMPLATE_TRANSITION,
        _AUTHOR_DASHBOARD_TRANSITION,
    )
    assert status.route_id == _AUTHOR_STATUS_ROUTE_ID
    assert status.request_mode == "targeted"

    coverage = transition_coverage(
        responses,
        expected_modes=("normal", "targeted"),
        expected_transition_ids=(_AUTHOR_DASHBOARD_TRANSITION,),
    )
    assert coverage.complete, coverage.summary()


def test_route_smoke_failure_names_render_context(docs_client: TestClient) -> None:
    class FullDocumentLeak:
        async def fragment(self, path: str, **_kwargs: object):
            return await docs_client.get(path)

    case = RouteSmokeCase(
        _DOCUMENT_PATH,
        mode="fragment",
        name="document-targeted",
        template="views/doc.html",
        block="page_content",
        target="page-content",
    )

    with pytest.raises(AssertionError) as caught:
        asyncio.run(assert_route_smoke(FullDocumentLeak(), (case,)))

    failure = str(caught.value)
    for detail in (
        f"path={_DOCUMENT_PATH!r}",
        "intent=fragment",
        "name='document-targeted'",
        "template='views/doc.html'",
        "block='page_content'",
        "target='page-content'",
        "observed_shape='full_document'",
    ):
        assert detail in failure


def test_content_route_head_matches_get_metadata(
    docs_client: TestClient,
) -> None:
    async def _fetch():
        get_response = await docs_client.get("/docs/get-started/installation/")
        head_response = await docs_client.request("HEAD", "/docs/get-started/installation/")
        return get_response, head_response

    get_response, head_response = asyncio.run(_fetch())
    assert head_response.status == get_response.status == 200
    assert head_response.content_type == get_response.content_type
    assert head_response.header("Last-Modified") == get_response.header("Last-Modified")


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


@pytest.mark.parametrize(
    ("accept", "content_type"),
    (
        ("text/markdown", "text/markdown"),
        ("text/markdown, text/html;q=0.5", "text/markdown"),
        ("text/html, text/markdown;q=0.5", "text/html"),
        ("text/markdown;q=0", "text/html"),
        ("text/*", "text/html"),
        ("*/*", "text/html"),
    ),
)
def test_content_pages_negotiate_markdown_when_explicitly_preferred(
    docs_client: TestClient,
    accept: str,
    content_type: str,
) -> None:
    async def _fetch():
        return await docs_client.get(
            "/docs/get-started/installation/",
            headers={"Accept": accept},
        )

    response = asyncio.run(_fetch())
    assert response.status == 200
    assert response.content_type.startswith(content_type)
    if content_type == "text/markdown":
        assert response.header("Vary") == "Accept"
        assert response.text.startswith("# Installation")
        assert "<!DOCTYPE html>" not in response.text
    else:
        assert "<!DOCTYPE html>" in response.text


def test_home_page_supports_markdown_content_negotiation(docs_client: TestClient) -> None:
    async def _fetch():
        return await docs_client.get("/", headers={"Accept": "text/markdown"})

    response = asyncio.run(_fetch())
    assert response.status == 200
    assert response.content_type.startswith("text/markdown")
    assert response.header("Vary") == "Accept"
    assert response.text.startswith("# Publish polished docs from Markdown")


def test_content_pages_advertise_agent_discovery_resources(docs_client: TestClient) -> None:
    async def _fetch():
        return await docs_client.get("/docs/get-started/installation/")

    response = asyncio.run(_fetch())
    assert response.status == 200
    assert 'id="fura-llms-index"' in response.text
    assert 'href="/llms.txt"' in response.text
    assert 'id="fura-page-markdown"' in response.text
    assert "/docs/get-started/installation.md" in response.text
    assert 'id="fura-agent-discovery"' in response.text
    assert "For AI agents: a complete documentation index" in response.text


def test_markdown_pages_include_agent_discovery_directive(docs_client: TestClient) -> None:
    async def _fetch():
        return await docs_client.get("/docs/get-started/installation.md")

    response = asyncio.run(_fetch())
    assert response.status == 200
    assert response.content_type.startswith("text/markdown")
    assert "complete documentation index is available at [llms.txt](/llms.txt)" in response.text


def test_content_pages_support_http_conditional_requests(docs_client: TestClient) -> None:
    async def _fetch():
        html = await docs_client.get("/docs/get-started/installation/")
        markdown = await docs_client.get("/docs/get-started/installation.md")
        html_cached = await docs_client.get(
            "/docs/get-started/installation/",
            headers={"If-Modified-Since": "Thu, 09 Jul 2099 00:00:00 GMT"},
        )
        markdown_cached = await docs_client.get(
            "/docs/get-started/installation.md",
            headers={"If-None-Match": markdown.header("ETag") or ""},
        )
        return html, markdown, html_cached, markdown_cached

    html, markdown, html_cached, markdown_cached = asyncio.run(_fetch())
    assert html.status == 200
    assert html.header("Last-Modified") is None
    assert html.header("ETag") is None
    assert markdown.header("Last-Modified")
    assert markdown.header("ETag")
    assert html_cached.status == 200
    assert html_cached.header("Last-Modified") is None
    assert html_cached.header("ETag") is None

    initial_csp_nonce = re.search(r"'nonce-([^']+)'", html.header("Content-Security-Policy") or "")
    refreshed_csp_nonce = re.search(
        r"'nonce-([^']+)'", html_cached.header("Content-Security-Policy") or ""
    )
    assert initial_csp_nonce is not None
    assert refreshed_csp_nonce is not None
    assert initial_csp_nonce.group(1) != refreshed_csp_nonce.group(1)
    inline_nonces = re.findall(r'<script[^>]+\bnonce="([^"]+)"', html_cached.text)
    assert inline_nonces
    assert set(inline_nonces) == {refreshed_csp_nonce.group(1)}

    assert markdown_cached.status == 304
    assert not markdown_cached.body


def test_json_sidecars_support_etag_revalidation(docs_client: TestClient) -> None:
    async def _fetch():
        response = await docs_client.get("/catalog.json")
        cached = await docs_client.get(
            "/catalog.json",
            headers={"If-None-Match": response.header("ETag") or ""},
        )
        return response, cached

    response, cached = asyncio.run(_fetch())
    assert response.status == 200
    assert response.header("ETag")
    assert cached.status == 304
    assert not cached.body


def test_preview_public_reads_are_stateless_and_share_cacheable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Public Cache"])
    capsys.readouterr()
    main(["--app-root", str(app_root), "freeze", "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] is True

    frozen = app_root / "frozen"
    docs = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, frozen, True, False),
    )
    first_client = TestClient(docs.create_app())
    second_client = TestClient(docs.create_app())

    async def _fetch():
        first_catalog = await first_client.get("/catalog.json")
        second_page = await second_client.get("/docs/get-started/")
        second_catalog = await second_client.get("/catalog.json")
        return first_catalog, second_page, second_catalog

    first_catalog, second_page, second_catalog = asyncio.run(_fetch())

    assert first_catalog.status == second_catalog.status == 200
    assert second_page.status == 200
    for response in (first_catalog, second_page, second_catalog):
        assert response.header("Set-Cookie") is None

    assert first_catalog.header("Cache-Control") == "public, max-age=0, must-revalidate"
    assert first_catalog.header("ETag")
    assert second_catalog.body == first_catalog.body
    assert second_catalog.header("ETag") == first_catalog.header("ETag")


def test_frozen_bulk_sidecars_serve_exact_bytes_in_preview_and_hybrid(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Frozen Bytes"])
    capsys.readouterr()
    main(["--app-root", str(app_root), "freeze", "--json"])
    assert json.loads(capsys.readouterr().out)["ok"] is True

    frozen = app_root / "frozen"
    sidecars = {
        "/catalog.json": "catalog.json",
        "/catalog/api-operations.json": "catalog/api-operations.json",
        "/search.json": "search.json",
        "/semantic.json": "semantic.json",
        "/structure.json": "structure.json",
        "/tools.json": "tools.json",
        "/llms.txt": "llms.txt",
        "/llms-full.txt": "llms-full.txt",
    }
    catalog_bytes = (frozen / "catalog.json").read_bytes()
    assert b'\n  "' not in catalog_bytes

    def _unexpected_serialization(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("frozen sidecar route rebuilt its payload")

    for name in (
        "api_operations_json",
        "build_structure_index",
        "catalog_graph",
        "llms_full_txt",
        "llms_index_txt",
        "search_json",
        "semantic_index_json",
        "tools_manifest",
    ):
        monkeypatch.setattr(route_registrars, name, _unexpected_serialization)

    async def _fetch(client: TestClient):
        responses = {url: await client.get(url) for url in sidecars}
        catalog = responses["/catalog.json"]
        cached = await client.get(
            "/catalog.json",
            headers={"If-None-Match": catalog.header("ETag") or ""},
        )
        filtered = await client.get("/search.json?q=no-such-term")
        return responses, cached, filtered

    for mode in (ServeMode.PREVIEW, ServeMode.HYBRID):
        docs = DocsApp.from_paths(
            app_root / "docs.yaml",
            repo_root=app_root,
            autodoc=False,
            serve=ServeConfig(mode, frozen, True, False),
        )
        responses, cached, filtered = asyncio.run(_fetch(TestClient(docs.create_app())))

        for url, relative_path in sidecars.items():
            expected = (frozen / relative_path).read_bytes()
            response = responses[url]
            assert response.status == 200
            assert response.body == expected
            assert response.header("ETag") == f'"{hashlib.sha256(expected).hexdigest()}"'
            assert response.header("Last-Modified")
            assert response.header("Cache-Control") == "public, max-age=0, must-revalidate"

        assert cached.status == 304
        assert not cached.body
        assert filtered.status == 200
        assert filtered.body != (frozen / "search.json").read_bytes()
        assert json.loads(filtered.text)["count"] == 0

    monkeypatch.undo()
    author = DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=app_root,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
    )
    author_catalog = asyncio.run(TestClient(author.create_app()).get("/catalog.json"))
    assert author_catalog.status == 200
    assert author_catalog.body != catalog_bytes
    assert author_catalog.header("Cache-Control") is None


def test_meta_reports_deployed_build_identity(
    docs_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FURA_BUILD_GIT_SHA", "f9fd2413b7327d10f7b3f79b38ff1fc850f8cf0d")

    async def _fetch():
        return await docs_client.get("/meta.json")

    response = asyncio.run(_fetch())
    payload = json.loads(response.text)
    build = payload["build"]
    assert build["git_sha"] == "f9fd2413b7327d10f7b3f79b38ff1fc850f8cf0d"
    assert build["packages"]["bengal-chirp"]
    assert build["packages"]["bengal-pounce"]
    assert build["freeze_fingerprint"]


@pytest.mark.parametrize(
    "headers",
    (
        {},
        {"HX-Request": "true", "HX-Boosted": "true"},
    ),
)
def test_catalog_responses_front_load_content_before_navigation(
    docs_client: TestClient,
    headers: dict[str, str],
) -> None:
    async def _fetch():
        return await docs_client.get(
            "/docs/get-started/installation/",
            headers=headers,
        )

    response = asyncio.run(_fetch())
    article = response.text.index("chirp-theme-docs-layout__article")
    sidebar = response.text.index('id="docs-sidebar"')
    toc = response.text.index('id="toc-panel"')
    assert article < sidebar < toc
    assert article / len(response.text) < 0.2
