"""Mermaid diagram fence rendering."""

from __future__ import annotations

from furatena.catalog.code_blocks import wrap_doc_html
from furatena.catalog.content_ir import extract_content_ir
from furatena.catalog.diagrams import mermaid_fence_html, transform_mermaid_fences
from furatena.catalog.render import DocsRenderer


class TestMermaidDiagrams:
    def test_transform_mermaid_fence_to_div(self) -> None:
        html = '<pre><code class="language-mermaid">flowchart TD\n  A --&gt; B</code></pre>\n'
        out = transform_mermaid_fences(html)
        assert '<div class="mermaid-wrapper">' in out
        assert '<div class="mermaid">' in out
        assert "flowchart TD" in out
        assert "A --> B" in out
        assert "language-mermaid" not in out

    def test_wrap_doc_html_applies_mermaid_transform(self) -> None:
        html = '<pre><code class="language-mermaid">graph LR\n  X</code></pre>'
        out = wrap_doc_html(html)
        assert 'class="mermaid"' in out
        assert "code-block-wrapper" not in out

    def test_mermaid_fence_html_escapes_markup(self) -> None:
        out = mermaid_fence_html('graph TD\n  A[<note>]')
        assert "&lt;note&gt;" not in out
        assert "&lt;note>" in out
        assert "A --> B" in mermaid_fence_html("A --> B")

    def test_renderer_emits_mermaid_div(self) -> None:
        renderer = DocsRenderer()
        md = """```mermaid
flowchart LR
  A --> B
```"""
        doc, _ir = renderer.parse(md)
        html = str(renderer.render_document(doc, source=md))
        assert 'class="mermaid"' in html
        assert "language-mermaid" not in html

    def test_content_ir_records_mermaid_feature(self) -> None:
        renderer = DocsRenderer()
        md = """```mermaid
flowchart TD
  A --> B
```"""
        _doc, ir = renderer.parse(md)
        assert "mermaid" in ir.features

    def test_non_mermaid_fence_has_no_mermaid_feature(self) -> None:
        renderer = DocsRenderer()
        _doc, ir = renderer.parse("```python\nprint('hi')\n```")
        assert "mermaid" not in ir.features
