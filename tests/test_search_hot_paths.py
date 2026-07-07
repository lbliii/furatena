"""Regression guards for profiled index and search hot paths."""

from __future__ import annotations

from furatena.catalog import search as search_module
from furatena.catalog.models import DocNode
from furatena.catalog.sources import parse as parse_module


def _node(index: int) -> DocNode:
    return DocNode(
        url=f"/docs/page-{index}/",
        slug=f"docs/page-{index}",
        title=f"Page {index}",
        description="",
        layout="doc",
        weight=index,
        section="docs",
        tags=frozenset(),
        body_md=f"# Page {index}\n\nNeedle body.",
        body_html="",
        toc=(),
        source_path=f"docs/page-{index}.md",
        meta={},
        body_text="Needle body.",
    )


def test_search_builds_snippets_only_for_returned_hits(monkeypatch) -> None:
    nodes = [_node(index) for index in range(30)]
    snippet_calls: list[str] = []

    def snippet(node, *_args, **_kwargs) -> str:
        snippet_calls.append(node.node_id)
        return node.body_text

    monkeypatch.setattr(search_module, "_snippet", snippet)
    monkeypatch.setattr(
        search_module,
        "plain_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("body_text ignored")),
    )

    hits = search_module.search_nodes(nodes, "needle", limit=5)

    assert [hit.node.weight for hit in hits] == [0, 1, 2, 3, 4]
    assert len(snippet_calls) == 5


def test_explicit_frontmatter_is_loaded_once(monkeypatch) -> None:
    calls = 0
    safe_load = parse_module.yaml.safe_load

    def counted_safe_load(source: str):
        nonlocal calls
        calls += 1
        return safe_load(source)

    monkeypatch.setattr(parse_module.yaml, "safe_load", counted_safe_load)
    meta, body = parse_module.parse_source_text(
        "---\ntitle: Fast\nweight: 2\n---\n\n# Body\n",
        content_format="patitas-markdown",
    )

    assert calls == 1
    assert meta == {"title": "Fast", "weight": 2.0}
    assert body == "# Body"
