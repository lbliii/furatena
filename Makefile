VENV_DIR ?= .venv
FREE_THREADED = env PYTHON_GIL=0
UV_RUN = $(FREE_THREADED) uv run
COVERAGE = $(FREE_THREADED) $(VENV_DIR)/bin/coverage
PYTHON = $(FREE_THREADED) $(VENV_DIR)/bin/python
PYTEST = $(UV_RUN) pytest -q --tb=short

.PHONY: help install test lint serve stop freeze export pages-build check clean \
	fast contract coverage browser agent release \
	ci-fast ci-contract ci-coverage ci-export ci-browser ci-agent ci-release

CORE_COVERAGE_SOURCE = furatena.catalog.graph,furatena.catalog.graph_schema,furatena.catalog.access,furatena.catalog.export,furatena.catalog.loader
CORE_COVERAGE_TESTS = \
	tests/test_chirp_docs_graph_query.py \
	tests/test_chirp_docs_graph_structure.py \
	tests/test_chirp_docs_federation_and_semantic_search.py \
	tests/test_chirp_docs_rbac.py \
	tests/test_chirp_docs_catalog_surfaces.py \
	tests/test_chirp_docs_sources.py \
	tests/test_chirp_docs_static_export.py \
	tests/test_chirp_docs_reference_resolution.py \
	tests/test_chirp_docs_link_and_inventory_contracts.py

help:
	@echo "Furatena"
	@echo "  make install      uv sync"
	@echo "  make serve        fura serve"
	@echo "  make stop         fura stop"
	@echo "  make freeze       fura freeze"
	@echo "  make export       fura export"
	@echo "  make pages-build  freeze + export for GitHub Pages"
	@echo "  make check        fura check"
	@echo "  make test         pytest"
	@echo "  make lint         ruff check"
	@echo ""
	@echo "CI lanes (see docs/CI.md)"
	@echo "  make ci-fast      lint + core unit tests (~20s)"
	@echo "  make ci-contract  hypermedia/content contract tests (~60s)"
	@echo "  make ci-coverage  core per-module coverage ratchets (~60s)"
	@echo "  make ci-export    export tests + Pages artifact build (~3m)"
	@echo "  make ci-browser   Playwright author browser tests (~60s)"
	@echo "  make ci-agent     agent/MCP lint and tests (~30s)"
	@echo "  make ci-release   package build + CLI smoke test (~3m)"

install:
	$(FREE_THREADED) uv sync --group dev

serve:
	$(UV_RUN) fura serve

stop:
	$(UV_RUN) fura stop

freeze:
	$(UV_RUN) fura freeze

export:
	$(UV_RUN) fura export

pages-build:
	$(FREE_THREADED) ./scripts/pages-build.sh

check:
	$(UV_RUN) fura check

test:
	$(PYTEST)

lint:
	$(UV_RUN) ruff check src tests app

fast: ci-fast

contract: ci-contract

coverage: ci-coverage

browser: ci-browser

agent: ci-agent

release: ci-release

ci-fast:
	$(UV_RUN) ruff check src tests app
	$(PYTEST) \
		tests/test_catalog_nav.py \
		tests/test_docs_core.py \
		tests/test_site_config.py \
		tests/test_theme_lint.py \
		tests/test_theme_pack.py \
		tests/test_theme_preset.py

ci-contract:
	$(UV_RUN) fura check
	$(PYTEST) \
		tests/test_author_authorization.py \
		tests/test_chirp_docs_content_lint.py \
		tests/test_chirp_docs_response_conformance.py \
		tests/test_chirp_docs_template_stack.py \
		tests/test_chirp_docs_view_lint.py \
		tests/test_csp.py \
		tests/test_shell_boost_links.py

ci-coverage:
	$(COVERAGE) erase
	FURA_TEST_FROZEN_DIR="$$(mktemp -d)/frozen" \
		$(COVERAGE) run --branch --source=$(CORE_COVERAGE_SOURCE) -m pytest -q $(CORE_COVERAGE_TESTS)
	$(COVERAGE) json -o .coverage-core.json
	$(PYTHON) scripts/check_core_coverage.py .coverage-core.json
	$(COVERAGE) report -m

ci-export:
	env -u FURA_BASE_URL -u FURA_BASE_PATH -u FURA_WORKERS $(PYTEST) \
		tests/test_chirp_docs_static_export.py \
		tests/test_chirp_docs_workers.py
	FURA_BASE_URL=https://lbliii.github.io/furatena \
		FURA_BASE_PATH=/furatena \
		FURA_WORKERS=8 \
		$(MAKE) pages-build
	$(UV_RUN) python -m furatena.catalog.artifact_audit app/public \
		--base-path /furatena \
		--site-url https://lbliii.github.io/furatena

ci-browser:
	$(PYTEST) -m browser tests/test_author_sse_browser.py

ci-agent:
	$(UV_RUN) fura check --agent-only --json
	$(PYTEST) tests/test_fura_cli_standalone.py -k "agent or mcp or evals"

ci-release:
	$(FREE_THREADED) uv build
	$(UV_RUN) fura --help

clean:
	rm -rf app/frozen app/public app/.docs-cache app/.docs-cache-test .pytest_cache .ruff_cache dist build *.egg-info
