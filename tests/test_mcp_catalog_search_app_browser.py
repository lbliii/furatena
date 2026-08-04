"""Real-browser contract for the public catalog-search MCP App."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from playwright.async_api import Browser, async_playwright
from playwright.async_api import Error as PlaywrightError

from furatena.catalog.mcp_apps import catalog_search_app_html


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


def _catalog_search_browser_html() -> str:
    host = """
<script>
window.__furatenaXssExecuted = false;
window.addEventListener('message', event => {
  const message = event.data || {};
  if (message.method === 'ui/initialize') {
    window.postMessage({ jsonrpc: '2.0', id: message.id, result: {} }, '*');
    return;
  }
  if (message.method !== 'tools/call' || message.params?.name !== 'semantic_search') return;
  const query = message.params.arguments?.query;
  if (query === 'repair') {
    window.postMessage({
      jsonrpc: '2.0',
      id: message.id,
      result: {
        structuredContent: {
          ranking: 'hybrid',
          filters: { include_private: false },
          results: [],
          diagnostics: [{
            message: 'Narrow the filter and retry.',
            rule_id: 'fura.search.repair'
          }]
        }
      }
    }, '*');
    return;
  }
  if (query === 'oversized') {
    window.postMessage({
      jsonrpc: '2.0',
      id: message.id,
      result: { structuredContent: { results: [{ title: 'x'.repeat(100001) }] } }
    }, '*');
    return;
  }
  const results = Array.from({ length: 30 }, (_, index) => ({
    node_id: `docs:latest:result-${index}`,
    title: index === 0
      ? '<img src=x onerror="window.__furatenaXssExecuted=true">'
      : `Result ${index}`,
    snippet: `Snippet ${index}`,
    score: 1 - index / 100,
    keyword_score: 0.75,
    semantic_score: 0.25,
    tags: ['docs'],
    provenance: { node_id: `docs:latest:result-${index}` }
  }));
  window.postMessage({
    jsonrpc: '2.0',
    id: message.id,
    result: {
      structuredContent: {
        ranking: 'hybrid',
        filters: { include_private: false },
        results
      }
    }
  }, '*');
});
</script>
"""
    return catalog_search_app_html().replace("<script>", f"{host}<script>", 1)


@pytest.mark.browser
@pytest.mark.browser_smoke
@pytest.mark.browser_full
async def test_catalog_search_app_renders_accessible_bounded_inert_results_and_repairs(
    browser: Browser,
) -> None:
    page = await browser.new_page()
    try:
        await page.set_content(_catalog_search_browser_html())
        await page.get_by_role("status").get_by_text("Ready.").wait_for()

        await page.get_by_label("Search terms").fill("bounded")
        await page.get_by_role("button", name="Search").click()
        await (
            page.get_by_role("status").get_by_text("Search complete. 25 results shown.").wait_for()
        )

        assert await page.get_by_role("heading", name="Results").is_visible()
        assert await page.locator("#results .result").count() == 25
        assert await page.locator("#results h3").first.inner_text() == (
            '<img src=x onerror="window.__furatenaXssExecuted=true">'
        )
        assert await page.locator("#results img").count() == 0
        assert await page.evaluate("window.__furatenaXssExecuted") is False

        await page.get_by_label("Search terms").fill("repair")
        await page.get_by_role("button", name="Search").click()
        repair = page.get_by_text("Narrow the filter and retry.")
        await repair.wait_for()
        assert await repair.is_visible()
        assert await page.locator("#diagnostics").get_attribute("aria-live") == "assertive"
        assert await page.get_by_text("fura.search.repair").is_visible()

        await page.get_by_label("Search terms").fill("oversized")
        await page.get_by_role("button", name="Search").click()
        bounded_error = page.get_by_text("The structured result exceeded the App safety limit.")
        await bounded_error.wait_for()
        assert await bounded_error.is_visible()
        assert await page.get_by_role("status").get_by_text("Search failed.").is_visible()
    finally:
        await page.close()
