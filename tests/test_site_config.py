"""Site branding configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"


@pytest.fixture(scope="module")
def docs_config():
    from furatena.catalog.config import load_docs_config

    return load_docs_config(APP_ROOT / "docs.yaml")


class TestSiteConfig:
    def test_site_name_from_docs_yaml(self, docs_config) -> None:
        assert docs_config.site.name == "Furatena"
        assert docs_config.site.mark == "𒀭"

    def test_default_navigation_has_doc_links(self, docs_config) -> None:
        nav = docs_config.site.navigation
        assert nav is not None
        hrefs = {link.href for link in nav.documentation.links}
        assert "/docs/get-started/" in hrefs
        assert "/docs/reference/" in hrefs

    def test_home_ctas(self, docs_config) -> None:
        home = docs_config.site.home
        assert home.cta_primary.href == "/docs/get-started/"
        assert len(home.metrics) == 3

    def test_theme_id_furatena(self, docs_config) -> None:
        assert docs_config.theme.id == "furatena"
