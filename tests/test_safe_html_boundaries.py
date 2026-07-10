"""Security fixtures for trusted HTML producers and template consumers."""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from pathlib import Path

from chirp.testing import TestClient

from furatena.catalog.directives.html import render_inline_text
from furatena.catalog.directives.icons import render_icon_html
from furatena.catalog.directives.kida_render import (
    render_directive,
    render_doc_tabs,
    trusted_renderer_html,
)
from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.search_experience import highlight_search_terms
from furatena.catalog.seo import json_ld_script
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"
_ATTACK = '<img data-attack="fixture" src=x onerror="alert(1)">'


class _ExecutableHTMLProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attack_markers: list[str] = []
        self.event_attributes: list[str] = []
        self.unsafe_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("data-attack"):
            self.attack_markers.append(str(values["data-attack"]))
        self.event_attributes.extend(name for name, _value in attrs if name.startswith("on"))
        for name in ("href", "src", "action", "formaction", "xlink:href"):
            value = str(values.get(name) or "").replace(" ", "").lower()
            if value.startswith(("javascript:", "vbscript:", "data:")):
                self.unsafe_urls.append(value)


def _assert_no_executable_markup(html: str) -> _ExecutableHTMLProbe:
    probe = _ExecutableHTMLProbe()
    probe.feed(html)
    assert probe.event_attributes == []
    assert probe.unsafe_urls == []
    assert "raw-script" not in probe.attack_markers
    assert "jsonld-breakout" not in probe.attack_markers
    assert "front-title" not in probe.attack_markers
    return probe


def test_plain_and_rich_producers_classify_malicious_directive_values() -> None:
    inline = render_inline_text(f"**Label** {_ATTACK}")
    assert "<strong>Label</strong>" in inline
    assert "&lt;img" in inline

    highlighted = highlight_search_terms(_ATTACK, "fixture")
    assert "<mark>fixture</mark>" in highlighted
    assert "&lt;img" in highlighted
    assert "fixture" not in _assert_no_executable_markup(highlighted).attack_markers

    assert render_icon_html("../../attack-onload") == ""
    literal = render_directive(
        "literalinclude",
        caption=_ATTACK,
        body=trusted_renderer_html("<pre>escaped code</pre>"),
    )
    probe = _assert_no_executable_markup(literal)
    assert "fixture" not in probe.attack_markers
    assert "&lt;img" in literal

    tabs = render_doc_tabs(
        [("safe-id", _ATTACK, "", True)],
        ["<p>renderer-owned panel</p>"],
        icons=["../../attack-onload"],
    )
    probe = _assert_no_executable_markup(tabs)
    assert "fixture" not in probe.attack_markers
    assert "renderer-owned panel" in tabs


def test_json_ld_escapes_script_breakout_sequences() -> None:
    payload = json_ld_script(
        {"name": '</script><script data-attack="jsonld-breakout">alert(1)</script>'}
    )

    assert "</script>" not in payload
    assert "<script" not in payload
    assert "\\u003c/script\\u003e" in payload


def test_malicious_author_content_is_safe_in_full_fragment_search_and_studio(
    tmp_path: Path,
) -> None:
    copy_app_theme(tmp_path, APP_ROOT)
    write_minimal_docs_yaml(tmp_path / "docs.yaml")
    content = tmp_path / "content"
    docs_root = content / "docs"
    docs_root.mkdir(parents=True)
    (content / "_index.md").write_text(
        "---\ntitle: Home\nlayout: home\n---\n# Home\n\n[Attack fixture](/docs/attack/).\n",
        encoding="utf-8",
    )
    (docs_root / "attack.md").write_text(
        """---
title: 'Attack </title><img data-attack="front-title" src=x onerror="alert(1)">'
description: '</script><script data-attack="jsonld-breakout">alert(1)</script>'
---
# Attack fixture

<script data-attack="raw-script">alert(1)</script>
<img data-attack="raw-image" src="x" onerror="alert(1)">
<a data-attack="raw-url" href="javascript:alert(1)">unsafe URL</a>
""",
        encoding="utf-8",
    )
    write_mounts_yaml(tmp_path / "mounts.yaml", content)
    docs = DocsApp.from_paths(
        tmp_path / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
        workers=1,
    )
    client = TestClient(docs.create_app())

    async def _fetch():
        return (
            await client.get("/docs/attack/"),
            await client.get(
                "/docs/attack/",
                headers={"HX-Request": "true", "HX-Target": "page-content"},
            ),
            await client.get("/search?q=attack"),
            await client.get(
                "/search?q=attack",
                headers={"HX-Request": "true", "HX-Target": "search-results-panel"},
            ),
            await client.get("/docs/_author/studio?slug=docs/attack"),
        )

    responses = asyncio.run(_fetch())
    assert all(response.status == 200 for response in responses)
    for response in responses:
        _assert_no_executable_markup(response.text)
