"""Browser coverage for local author surfaces."""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, sync_playwright
from playwright.sync_api import Error as PlaywrightError

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
                f"stdout:\n{stdout.decode(errors='replace')}\n"
                f"stderr:\n{stderr.decode(errors='replace')}"
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
    page.write_text("---\ntitle: Page\n---\n# Page\n\nHello from browser SSE.\n", encoding="utf-8")
    write_mounts_yaml(app_root / "mounts.yaml", content)
    return page


@pytest.fixture()
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as exc:
            if os.environ.get("CI"):
                raise
            pytest.skip(f"Playwright Chromium is not installed: {exc}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture()
def author_server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    page = _write_author_fixture(tmp_path)
    port = _free_port()
    env = os.environ.copy()
    env["FURA_APP_ROOT"] = str(tmp_path)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "fura",
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


def _dirty_page(page_path: Path, body: str) -> None:
    page_path.write_text(f"---\ntitle: Page\n---\n# Page\n\n{body}\n", encoding="utf-8")
    future = time.time() + 5
    os.utime(page_path, (future, future))


def _wait_for_request_count(items: list[str], *, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if items:
            return
        time.sleep(0.05)
    raise AssertionError("expected browser request was not observed")


@pytest.mark.browser
def test_author_sse_updates_preview_without_polling(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, page_path = author_server
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
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
        page.goto(f"{base_url}/docs/page/", wait_until="domcontentloaded")
        page.wait_for_function("window.__furaAuthorReloadMode === 'sse'")
        _wait_for_request_count(event_requests)
        page.locator("#page-root").get_by_text("Hello from browser SSE.").wait_for()

        page.evaluate(
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
        page.wait_for_function("window.__furaAuthorReloadCount >= 1", timeout=20_000)
        _wait_for_request_count(author_reload_requests, timeout=10.0)
        page.wait_for_function("window.__furaAuthorLastReloadStatus !== undefined")
        assert page.evaluate("window.__furaAuthorLastReloadStatus") == 200
        page.locator("#page-root").get_by_text("Updated through a real browser SSE event.").wait_for(
            timeout=10_000
        )

        assert page.evaluate("window.__furaAuthorReloadMode") == "sse"
        page.wait_for_function(
            "document.activeElement && document.activeElement.id === 'fura-e2e-focus-probe'",
            timeout=5_000,
        )
        assert page.evaluate("document.activeElement && document.activeElement.id") == (
            "fura-e2e-focus-probe"
        )
        assert page.evaluate(
            """
            () => {
              const probe = document.getElementById("fura-e2e-focus-probe");
              return probe && [probe.selectionStart, probe.selectionEnd];
            }
            """
        ) == [2, 8]
        assert page.evaluate("window.scrollY") >= 300
        assert stale_requests == []
    finally:
        context.close()


@pytest.mark.browser
def test_author_mobile_layout_keeps_actions_and_content_non_overlapping(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, _page_path = author_server
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = context.new_page()
    try:
        page.goto(f"{base_url}/docs/page/", wait_until="domcontentloaded")
        page.wait_for_function("window.__furaAuthorReloadMode === 'sse'")

        chrome = page.locator("#fura-author-chrome")
        actions = page.locator("[data-chirp-page-actions]").first
        article = page.locator(".chirp-theme-docs-layout__article").first
        chrome.wait_for()

        boxes = {
            "chrome": chrome.bounding_box(),
            "actions": actions.bounding_box(),
            "article": article.bounding_box(),
        }
        assert all(box is not None for box in boxes.values())
        assert boxes["actions"]["width"] <= 390
        assert boxes["chrome"]["width"] <= 390
        assert boxes["chrome"]["y"] >= boxes["actions"]["y"] + boxes["actions"]["height"]
        assert boxes["article"]["y"] >= boxes["chrome"]["y"]
        assert page.locator("#fura-author-sse").count() == 1
        assert page.evaluate("window.__furaAuthorReloadMode") == "sse"
    finally:
        context.close()


@pytest.mark.browser
def test_author_studio_save_and_create_refresh_preview(
    browser: Browser,
    author_server: tuple[str, Path],
) -> None:
    base_url, page_path = author_server
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    draft_path = page_path.parent / "studio-draft.md"

    try:
        page.goto(f"{base_url}/docs/_author/studio?slug=docs/page", wait_until="domcontentloaded")
        page.locator("#author-studio-workspace").wait_for()
        page.get_by_label("Rendered preview").get_by_text("Hello from browser SSE.").wait_for()

        edited = "---\ntitle: Page\n---\n# Page\n\nSaved through the browser studio.\n"
        page.locator("#author-studio-source").fill(edited)
        page.get_by_role("button", name="Save source").click()
        page.locator('[data-author-studio-saved="true"]').wait_for(timeout=10_000)
        page.get_by_label("Rendered preview").get_by_text(
            "Saved through the browser studio."
        ).wait_for()
        assert page_path.read_text(encoding="utf-8") == edited

        page.goto(
            f"{base_url}/docs/_author/studio?new=1&slug=docs/studio-draft&title=Studio%20Draft",
            wait_until="domcontentloaded",
        )
        page.locator("#author-studio-workspace").wait_for()
        assert page.locator("#author-studio-workspace").get_attribute(
            "data-author-studio-mode"
        ) == "create"
        page.locator("#author-studio-source").fill("# Studio Draft\n\nCreated as a private draft.\n")
        page.get_by_role("button", name="Create draft").click()
        page.locator('[data-author-studio-saved="true"]').wait_for(timeout=10_000)
        page.get_by_label("Rendered preview").get_by_text("Created as a private draft.").wait_for()

        assert draft_path.is_file()
        draft_source = draft_path.read_text(encoding="utf-8")
        assert "visibility: draft" in draft_source
        assert "draft: true" in draft_source
        assert "Created as a private draft." in draft_source
    finally:
        context.close()
