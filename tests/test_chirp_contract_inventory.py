"""Current template reachability and CSS-token inventory contracts."""

from __future__ import annotations

from pathlib import Path

from chirp.contracts import check_hypermedia_surface

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

# Chirp #707 canonicalizes these nested-root aliases by resolved source path.
_UPSTREAM_PHYSICAL_ALIASES = {
    "templates/partials/doc_article.html",
    "templates/partials/docs_shell_nav.html",
    "templates/partials/shell_nav_mega.html",
}


def test_current_contract_inventory_has_no_furatena_owned_false_positives() -> None:
    docs = DocsApp.from_paths(
        APP_ROOT / "docs.yaml",
        repo_root=REPO,
        autodoc=False,
        serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
    )
    docs.app.freeze()
    result = check_hypermedia_surface(docs.app)

    unknown_chirpui = {
        issue.template for issue in result.issues if issue.category == "chirpui_css_verify"
    }
    dead_templates = {issue.template for issue in result.issues if issue.category == "dead"}

    assert unknown_chirpui == set()
    assert dead_templates <= _UPSTREAM_PHYSICAL_ALIASES
    assert "views/develop.html" not in dead_templates
    assert "views/develop_export.html" not in dead_templates
