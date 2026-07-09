"""Shell link boost rules."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.links import boost_internal_links, is_shell_boost_href, shell_link_attrs


class TestShellBoostHrefs:
    def test_doc_paths_boost(self) -> None:
        assert is_shell_boost_href("/docs/get-started/")
        assert is_shell_boost_href("/api/reference/")
        assert is_shell_boost_href("/releases/")

    def test_asset_paths_do_not_boost(self) -> None:
        assert not is_shell_boost_href("/llms.txt")
        assert not is_shell_boost_href("/catalog.json")
        assert not is_shell_boost_href("/search.json")
        assert not is_shell_boost_href("/tools.json")
        assert not is_shell_boost_href("/sitemap.xml")

    def test_boost_filter_skips_assets(self) -> None:
        html = boost_internal_links(
            '<a href="/docs/foo">Doc</a><a href="/llms.txt">LLMs</a>',
            shell_link_attrs,
        )
        text = str(html)
        assert 'href="/docs/foo"' in text and 'hx-boost="true"' in text
        assert 'href="/docs/foo"' in text and 'preload="mouseover"' in text
        assert 'href="/llms.txt"' in text and 'hx-boost="false"' in text
        asset_tag = text.split('href="/llms.txt"', 1)[1].split(">", 1)[0]
        assert "preload=" not in asset_tag

    def test_boosted_links_get_hover_preload(self) -> None:
        attrs = shell_link_attrs("/docs/foo")
        assert attrs["hx-boost"] == "true"
        assert attrs["preload"] == "mouseover"

    @pytest.mark.parametrize("href", ("/catalog.json", "/llms.txt", "/sitemap.xml"))
    def test_machine_readable_links_do_not_get_preload(self, href: str) -> None:
        attrs = shell_link_attrs(href)
        assert attrs == {"hx-boost": "false"}
