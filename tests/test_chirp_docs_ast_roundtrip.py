"""AST round-trip checks for frozen Patitas sidecars."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from dataclasses import replace

from furatena.catalog.ast_store import ast_roundtrip_error, document_to_json
from furatena.catalog.check import check_ast_roundtrip
from furatena.catalog.render import DocsRenderer


class TestAstRoundtrip:
    def test_valid_ast_roundtrips(self) -> None:
        renderer = DocsRenderer()
        document, _ = renderer.parse("# Hello\n\nBody.\n")
        assert ast_roundtrip_error(document_to_json(document)) is None

    def test_invalid_ast_reports_error(self) -> None:
        broken = '{"_type":"Document","children":[{"_type":"Directive","name":"card","options":{"_type":"DirectiveOptions","class_":null}}]}'
        message = ast_roundtrip_error(broken)
        assert message is not None

    def test_check_warns_on_incompatible_frozen_ast(self, tmp_path: Path) -> None:
        from furatena.catalog import DocCatalog

        content = tmp_path / "content"
        docs = content / "docs"
        docs.mkdir(parents=True)
        page = docs / "stale.md"
        page.write_text("---\ntitle: Stale\n---\n# Stale\n", encoding="utf-8")

        catalog = DocCatalog(content, autodoc=False, autodoc_config=None)
        node = catalog.get_by_slug("docs/stale")
        assert node is not None

        broken = '{"_type":"Document","children":[{"_type":"Directive","name":"card","options":{"_type":"DirectiveOptions","class_":null}}]}'
        stale_node = replace(node, ast_json=broken)

        class _StubCatalog:
            nodes = (stale_node,)

        warnings = check_ast_roundtrip(_StubCatalog())
        assert any("frozen AST incompatible" in warning for warning in warnings)
