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
        assert docs_config.site.mark == "𐂛"

    def test_navigation_separates_product_and_resource_links(self, docs_config) -> None:
        nav = docs_config.site.navigation
        assert nav is not None
        product_hrefs = {link.href for link in nav.documentation.links}
        resource_hrefs = {link.href for link in nav.develop.links}
        assert nav.documentation.overview_href == "/platform/"
        assert product_hrefs == {"/agents/", "/migration/", "/proof/"}
        assert "/docs/get-started/" in resource_hrefs
        assert "/docs/operations/consume-agent-outputs/" in resource_hrefs

    def test_home_ctas(self, docs_config) -> None:
        home = docs_config.site.home
        assert home.cta_primary.href == "/platform/"
        assert home.cta_secondary.href == "/proof/"
        assert len(home.metrics) == 3
        assert home.ideas is not None
        assert len(home.ideas.features) == 3
        assert home.pipeline is not None
        assert len(home.pipeline.modes) == 3
        assert home.stack is None
        assert home.sources is not None
        assert len(home.sources.formats) == 4
        assert home.deployments is not None
        assert len(home.deployments.options) == 2
        assert home.quick_start is not None
        assert home.metrics_head is not None
        assert home.explore is not None
        assert len(home.explore.links) == 4

    def test_theme_id_furatena(self, docs_config) -> None:
        assert docs_config.theme.id == "furatena"

    def test_identity_defaults(self, docs_config) -> None:
        assert docs_config.identity.tenant == "default"
        assert docs_config.identity.workspace == "default"
        assert docs_config.identity.site == "default"

    def test_identity_from_docs_yaml(self, tmp_path: Path) -> None:
        from furatena.catalog.config import load_docs_config

        config_path = tmp_path / "docs.yaml"
        config_path.write_text(
            """
site:
  name: Enterprise Docs
identity:
  tenant: acme
  workspace: platform
  site: developer-docs
""".lstrip(),
            encoding="utf-8",
        )

        config = load_docs_config(config_path)

        assert config.identity.tenant == "acme"
        assert config.identity.workspace == "platform"
        assert config.identity.site == "developer-docs"

    def test_identity_site_id_alias(self, tmp_path: Path) -> None:
        from furatena.catalog.config import load_docs_config

        config_path = tmp_path / "docs.yaml"
        config_path.write_text(
            """
tenant: acme
workspace: docs
site:
  id: support-kb
  name: Support Knowledge Base
""".lstrip(),
            encoding="utf-8",
        )

        config = load_docs_config(config_path)

        assert config.identity.tenant == "acme"
        assert config.identity.workspace == "docs"
        assert config.identity.site == "support-kb"
