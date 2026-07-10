"""Browser coverage for local author surfaces."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Browser, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, proc: subprocess.Popen[bytes], *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1)
            raise RuntimeError(
                "fura serve exited before browser test could connect\n"
                f"stdout:\n{stdout.decode(errors='replace') if stdout else '(not captured)'}\n"
                f"stderr:\n{stderr.decode(errors='replace') if stderr else '(not captured)'}"
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise TimeoutError(f"fura serve did not start at {url}: {last_error}")


def _write_author_fixture(app_root: Path) -> Path:
    copy_app_theme(app_root, APP_ROOT)
    write_minimal_docs_yaml(app_root / "docs.yaml")
    content = app_root / "content"
    docs = content / "docs"
    docs.mkdir(parents=True)
    page = docs / "page.md"
    page.write_text(
        "---\ntitle: Page\n---\n# Page\n\n"
        "Hello from browser SSE.\n\n"
        "[Target](/docs/target/)\n"
        "[Catalog](/catalog.json)\n"
        "[LLMs](/llms.txt)\n"
        "[Sitemap](/sitemap.xml)\n",
        encoding="utf-8",
    )
    (docs / "target.md").write_text(
        "---\ntitle: Target\n---\n# Target\n\nPreloaded target response.\n",
        encoding="utf-8",
    )
    write_mounts_yaml(app_root / "mounts.yaml", content)
    return page


def _write_journey_fixture(app_root: Path) -> None:
    copy_app_theme(app_root, APP_ROOT)
    write_minimal_docs_yaml(app_root / "docs.yaml")
    with (app_root / "docs.yaml").open("a", encoding="utf-8") as handle:
        handle.write(
            """
catalog:
  append_unlisted: false
  sections:
    - { id: adopt, label: Adopt, sections: [get-started, about] }
    - { id: author, label: Author, sections: [authoring, theming] }
    - { id: publish, label: Publish, pages: [operations, operations/deploy] }
    - { id: operate, label: Operate, pages: [operations/serve-and-author] }
    - id: integrate
      label: Integrate
      href: /docs/operations/consume-agent-outputs/
      sections: [concepts, reference]
      pages: [operations/consume-agent-outputs]
"""
        )
    docs = app_root / "content" / "docs"
    docs.mkdir(parents=True)
    (docs / "_index.md").write_text("---\ntitle: Docs\n---\n# Docs\n", encoding="utf-8")
    pages = {
        "get-started/_index.md": "Get Started",
        "about/_index.md": "About",
        "authoring/_index.md": "Authoring",
        "theming/_index.md": "Theming",
        "concepts/_index.md": "Concepts",
        "reference/_index.md": "Reference",
        "operations/_index.md": "Operations",
        "operations/deploy.md": "Deploy",
        "operations/serve-and-author.md": "Serve and author",
        "operations/consume-agent-outputs.md": "Consume agent outputs",
    }
    for relative, title in pages.items():
        path = docs / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: {title}\n---\n# {title}\n", encoding="utf-8")
    write_mounts_yaml(app_root / "mounts.yaml", app_root / "content")


@pytest.fixture()
async def browser() -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except PlaywrightError as exc:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"Playwright Chromium is not installed: {exc}")
        try:
            yield browser
        finally:
            await browser.close()


@pytest.fixture()
def author_server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    page = _write_author_fixture(tmp_path)
    port = _free_port()
    env = os.environ.copy()
    env["FURA_APP_ROOT"] = str(tmp_path)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [
            str(Path(sys.executable).with_name("fura")),
            "serve",
            "--author",
            "--no-autodoc",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base_url}/docs/page/", proc)
        yield base_url, page
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture()
def journey_server(tmp_path: Path) -> Iterator[str]:
    _write_journey_fixture(tmp_path)
    port = _free_port()
    env = os.environ.copy()
    env["FURA_APP_ROOT"] = str(tmp_path)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [
            str(Path(sys.executable).with_name("fura")),
            "serve",
            "--author",
            "--no-autodoc",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(f"{base_url}/docs/operations/consume-agent-outputs/", proc)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _dirty_page(page_path: Path, body: str) -> None:
    page_path.write_text(f"---\ntitle: Page\n---\n# Page\n\n{body}\n", encoding="utf-8")
    future = time.time() + 5
    os.utime(page_path, (future, future))


async def _wait_for_request_count(
    items: list[str], *, count: int = 1, timeout: float = 10.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(items) >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("expected browser request was not observed")


async def _wait_for_htmx(page: Page) -> None:
    await page.wait_for_function("window.__furaHtmxReady === true", timeout=10_000)


async def _submit_studio(page: Page, *, button_name: str) -> None:
    async with page.expect_response(
        lambda response: response.request.method == "POST" and "/_author/studio" in response.url,
        timeout=20_000,
    ) as response_info:
        await page.get_by_role("button", name=button_name).click()
    response = await response_info.value
    assert response.ok, f"studio POST returned {response.status}: {await response.text()}"
    await page.locator('[data-author-studio-saved="true"]').wait_for(timeout=20_000)


@pytest.mark.browser
@pytest.mark.browser_smoke
@pytest.mark.browser_responsive
@pytest.mark.browser_full
async def test_journey_rail_is_complete_active_and_responsive(
    browser: Browser,
    journey_server: str,
) -> None:
    expected = ["Adopt", "Author", "Publish", "Operate", "Integrate"]
    for viewport in ({"width": 1280, "height": 900}, {"width": 390, "height": 844}):
        context = await browser.new_context(
            viewport=viewport,
            is_mobile=viewport["width"] < 500,
        )
        page = await context.new_page()
        try:
            await page.goto(
                f"{journey_server}/docs/operations/consume-agent-outputs/",
                wait_until="domcontentloaded",
            )
            rail = page.locator(
                ".chirp-theme-doc-catalog-rail__group--sections .chirp-theme-doc-catalog-rail__item"
            )
            assert await rail.count() == 5
            assert (
                await rail.evaluate_all(
                    "items => items.map(item => item.getAttribute('aria-label'))"
                )
                == expected
            )
            assert await rail.filter(has=page.locator("[aria-hidden='true']")).count() == 5
            assert await rail.filter(has_text="Integrate").get_attribute("aria-current") == "page"
            assert await page.evaluate("document.documentElement.scrollWidth") <= viewport["width"]
        finally:
            await context.close()


@pytest.mark.browser
@pytest.mark.browser_smoke
@pytest.mark.browser_authoring
@pytest.mark.browser_full
async def test_author_sse_updates_preview_without_polling(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, page_path = author_server
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    stale_requests: list[str] = []
    event_requests: list[str] = []
    author_reload_requests: list[str] = []

    def record_request(request: Any) -> None:
        if "/docs/_author/stale" in request.url:
            stale_requests.append(request.url)
        if "/docs/_author/events" in request.url:
            event_requests.append(request.url)
        if request.headers.get("hx-docs-author-reload") == "1":
            author_reload_requests.append(request.url)

    page.on("request", record_request)

    try:
        await page.goto(f"{base_url}/docs/page/", wait_until="domcontentloaded")
        await page.wait_for_function("window.__furaAuthorReloadMode === 'sse'")
        await _wait_for_request_count(event_requests)
        await asyncio.sleep(0.25)
        assert len(event_requests) == 1
        await page.locator("#page-root").get_by_text("Hello from browser SSE.").wait_for()

        await page.evaluate(
            """
            () => {
              const spacer = document.createElement("div");
              spacer.style.height = "1600px";
              spacer.setAttribute("data-e2e-spacer", "1");
              document.body.appendChild(spacer);
              const probe = document.createElement("textarea");
              probe.id = "fura-e2e-focus-probe";
              probe.value = "preserve-me";
              document.body.appendChild(probe);
              window.scrollTo(0, 320);
              probe.focus();
              probe.setSelectionRange(2, 8);
            }
            """
        )
        _dirty_page(page_path, "Updated through a real browser SSE event.")
        await page.wait_for_function("window.__furaAuthorReloadCount >= 1", timeout=20_000)
        await _wait_for_request_count(author_reload_requests, timeout=10.0)
        await page.wait_for_function("window.__furaAuthorLastReloadStatus !== undefined")
        assert await page.evaluate("window.__furaAuthorLastReloadStatus") == 200
        await (
            page.locator("#page-root")
            .get_by_text("Updated through a real browser SSE event.")
            .wait_for(timeout=10_000)
        )

        assert await page.evaluate("window.__furaAuthorReloadMode") == "sse"
        assert await page.evaluate("window.__furaAuthorReloadCount") == 1
        assert len(event_requests) == 1
        await page.wait_for_function(
            "document.activeElement && document.activeElement.id === 'fura-e2e-focus-probe'",
            timeout=5_000,
        )
        assert await page.evaluate("document.activeElement && document.activeElement.id") == (
            "fura-e2e-focus-probe"
        )
        assert await page.evaluate(
            """
            () => {
              const probe = document.getElementById("fura-e2e-focus-probe");
              return probe && [probe.selectionStart, probe.selectionEnd];
            }
            """
        ) == [2, 8]
        assert await page.evaluate("window.scrollY") >= 300
        assert stale_requests == []
    finally:
        await context.close()


@pytest.mark.browser
@pytest.mark.browser_authoring
@pytest.mark.browser_full
async def test_author_eventsource_fallback_owns_one_stream_without_htmx_sse(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, page_path = author_server
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    stale_requests: list[str] = []
    event_requests: list[str] = []

    def record_request(request: Any) -> None:
        if "/docs/_author/stale" in request.url:
            stale_requests.append(request.url)
        if "/docs/_author/events" in request.url:
            event_requests.append(request.url)

    page.on("request", record_request)
    await page.route("**/htmx-ext-sse.js", lambda route: route.abort())

    try:
        await page.goto(f"{base_url}/docs/page/", wait_until="domcontentloaded")
        await page.wait_for_function("window.__furaAuthorReloadMode === 'sse'")
        await _wait_for_request_count(event_requests)
        await asyncio.sleep(0.25)
        assert len(event_requests) == 1
        assert (
            await page.locator("#fura-author-sse").get_attribute("data-fura-sse-extension-active")
            is None
        )

        _dirty_page(page_path, "Updated through the EventSource compatibility fallback.")
        await page.wait_for_function("window.__furaAuthorReloadCount === 1", timeout=20_000)
        await (
            page.locator("#page-root")
            .get_by_text("Updated through the EventSource compatibility fallback.")
            .wait_for(timeout=10_000)
        )

        assert stale_requests == []
        assert len(event_requests) == 1
    finally:
        await context.close()


@pytest.mark.browser
@pytest.mark.browser_authoring
@pytest.mark.browser_full
async def test_htmx_cleanup_closes_author_sse_before_the_next_page_stream(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, _page_path = author_server
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    event_requests: list[str] = []
    ended_event_requests: list[str] = []
    stale_requests: list[str] = []

    def record_request(request: Any) -> None:
        if "/docs/_author/events" in request.url:
            event_requests.append(request.url)
        if "/docs/_author/stale" in request.url:
            stale_requests.append(request.url)

    def record_end(request: Any) -> None:
        if "/docs/_author/events" in request.url:
            ended_event_requests.append(request.url)

    page.on("request", record_request)
    page.on("requestfinished", record_end)
    page.on("requestfailed", record_end)

    try:
        await page.goto(f"{base_url}/docs/page/", wait_until="domcontentloaded")
        await page.wait_for_function("window.__furaAuthorReloadMode === 'sse'")
        await _wait_for_request_count(event_requests)
        assert len(event_requests) == 1

        await page.evaluate(
            """
            () => {
              const marker = document.getElementById("fura-author-sse");
              htmx.trigger(marker, "htmx:beforeCleanupElement", { elt: marker });
              marker.remove();
            }
            """
        )
        await _wait_for_request_count(ended_event_requests)
        assert len(ended_event_requests) == 1
        assert "slug=docs/page" in ended_event_requests[0]

        await page.goto(f"{base_url}/docs/target/", wait_until="domcontentloaded")
        await page.locator("#page-root").get_by_text("Preloaded target response.").wait_for()
        await page.wait_for_function("window.__furaAuthorReloadMode === 'sse'")
        await _wait_for_request_count(event_requests, count=2)
        await asyncio.sleep(0.25)

        assert len(event_requests) == 2
        assert len(ended_event_requests) == 1
        assert "slug=docs/target" in event_requests[1]
        assert stale_requests == []
    finally:
        await context.close()


@pytest.mark.browser
@pytest.mark.browser_responsive
@pytest.mark.browser_full
async def test_author_mobile_layout_keeps_actions_and_content_non_overlapping(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, _page_path = author_server
    viewport = {"width": 390, "height": 844}
    context = await browser.new_context(viewport=viewport, is_mobile=True)
    page = await context.new_page()
    try:
        await page.goto(f"{base_url}/docs/page/", wait_until="domcontentloaded")
        await page.wait_for_function("window.__furaAuthorReloadMode === 'sse'")

        chrome = page.locator("#fura-author-chrome")
        actions = page.locator("[data-chirp-page-actions]").first
        article = page.locator(".chirp-theme-docs-layout__article").first
        details = chrome.locator(".fura-author-chrome__details")
        details_panel = chrome.locator(".fura-author-chrome__details-panel")
        await chrome.wait_for()

        assert await chrome.locator(".fura-author-chrome__signal").count() == 3
        assert await details_panel.is_hidden()
        await details.locator("summary").click()
        await details_panel.wait_for(state="visible")

        boxes = {
            "chrome": await chrome.bounding_box(),
            "actions": await actions.bounding_box(),
            "article": await article.bounding_box(),
            "details": await details_panel.bounding_box(),
        }
        assert all(box is not None for box in boxes.values())
        assert boxes["actions"] is not None
        assert boxes["chrome"] is not None
        assert boxes["article"] is not None
        assert boxes["details"] is not None
        assert boxes["actions"]["width"] <= viewport["width"]
        assert boxes["chrome"]["width"] <= viewport["width"]
        assert boxes["details"]["width"] <= boxes["chrome"]["width"]
        assert boxes["chrome"]["y"] >= boxes["actions"]["y"] + boxes["actions"]["height"]
        assert boxes["article"]["y"] >= boxes["chrome"]["y"]
        assert await page.evaluate("document.documentElement.scrollWidth") <= viewport["width"]
        assert await page.locator("#fura-author-sse").count() == 1
        assert await page.evaluate("window.__furaAuthorReloadMode") == "sse"
    finally:
        await context.close()


@pytest.mark.browser
@pytest.mark.browser_authoring
@pytest.mark.browser_full
async def test_author_studio_save_and_create_refresh_preview(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, page_path = author_server
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    draft_path = page_path.parent / "studio-draft.md"
    await page.add_init_script(
        """
        document.addEventListener("htmx:load", () => {
          window.__furaHtmxReady = true;
        });
        """
    )

    try:
        await page.goto(
            f"{base_url}/docs/_author/studio?slug=docs/page", wait_until="domcontentloaded"
        )
        await _wait_for_htmx(page)
        await page.locator("#author-studio-workspace").wait_for()
        await (
            page.get_by_label("Rendered preview").get_by_text("Hello from browser SSE.").wait_for()
        )

        edited = "---\ntitle: Page\n---\n# Page\n\nSaved through the browser studio.\n"
        await page.locator("#author-studio-source").fill(edited)
        await _submit_studio(page, button_name="Save source")
        await (
            page.get_by_label("Rendered preview")
            .get_by_text("Saved through the browser studio.")
            .wait_for()
        )
        assert page_path.read_text(encoding="utf-8") == edited

        await page.goto(
            f"{base_url}/docs/_author/studio?new=1&slug=docs/studio-draft&title=Studio%20Draft",
            wait_until="domcontentloaded",
        )
        await _wait_for_htmx(page)
        await page.locator("#author-studio-workspace").wait_for()
        assert (
            await page.locator("#author-studio-workspace").get_attribute("data-author-studio-mode")
            == "create"
        )
        await page.locator("#author-studio-source").fill(
            "# Studio Draft\n\nCreated as a private draft.\n"
        )
        await _submit_studio(page, button_name="Create draft")
        await (
            page.get_by_label("Rendered preview")
            .get_by_text("Created as a private draft.")
            .wait_for()
        )

        assert draft_path.is_file()
        draft_source = draft_path.read_text(encoding="utf-8")
        assert "visibility: draft" in draft_source
        assert "draft: true" in draft_source
        assert "Created as a private draft." in draft_source
    finally:
        await context.close()


@pytest.mark.browser
@pytest.mark.browser_smoke
@pytest.mark.browser_full
async def test_search_result_navigates_to_document(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, _page_path = author_server
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    try:
        await page.goto(f"{base_url}/search?q=browser", wait_until="domcontentloaded")
        results = page.locator("#search-results-panel")
        await results.wait_for()
        result = results.locator('a[href="/docs/page/"]').first
        await result.wait_for()
        await result.click()
        # Boosted navigation updates history with pushState instead of a full load event.
        await page.wait_for_function(
            "url => window.location.href === url",
            arg=f"{base_url}/docs/page/",
        )
        await page.locator("#page-root").get_by_text("Hello from browser SSE.").wait_for()
    finally:
        await context.close()


@pytest.mark.browser
@pytest.mark.browser_smoke
@pytest.mark.browser_full
async def test_hover_preloads_boosted_link_and_click_uses_cached_response(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, _page_path = author_server
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    target_url = f"{base_url}/docs/target/"
    target_requests: list[Any] = []
    devtools = await context.new_cdp_session(page)
    await devtools.send("Network.enable")
    target_response_sources: list[dict[str, Any]] = []

    def record_request(request: Any) -> None:
        if request.url == target_url:
            target_requests.append(request)

    def record_response(event: dict[str, Any]) -> None:
        if event["response"]["url"] == target_url:
            target_response_sources.append(
                {
                    "fromDiskCache": event["response"].get("fromDiskCache"),
                    "fromPrefetchCache": event["response"].get("fromPrefetchCache"),
                    "fromServiceWorker": event["response"].get("fromServiceWorker"),
                }
            )

    page.on("request", record_request)
    devtools.on("Network.responseReceived", record_response)
    try:
        await page.goto(f"{base_url}/docs/page/", wait_until="domcontentloaded")
        target = page.locator('#page-content a[href="/docs/target/"]').first
        await target.wait_for()
        await page.wait_for_function(
            "document.querySelector('#page-content a[href=\\\"/docs/target/\\\"]').preloadState === 'READY'"
        )
        assert await target.get_attribute("hx-boost") == "true"
        assert await target.get_attribute("preload") == "mouseover"

        async with page.expect_response(
            lambda response: (
                response.url == target_url
                and response.request.headers.get("hx-preloaded") == "true"
            ),
            timeout=10_000,
        ) as response_info:
            await target.hover()
        preload_response = await response_info.value
        assert preload_response.ok
        assert preload_response.headers.get("cache-control") == "private, max-age=60"
        await page.wait_for_function(
            "document.querySelector('#page-content a[href=\\\"/docs/target/\\\"]').preloadState === 'DONE'"
        )
        assert len(target_requests) == 1

        await target.click()
        await page.wait_for_url(target_url)
        await page.locator("#page-root").get_by_text("Preloaded target response.").wait_for()
        assert len(target_requests) == 2
        # Playwright emits a request event for the cache read; Chromium confirms
        # that the click response was served from disk without a second network fetch.
        assert [source["fromDiskCache"] for source in target_response_sources] == [False, True]
    finally:
        await context.close()
