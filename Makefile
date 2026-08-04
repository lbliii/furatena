VENV_DIR ?= .venv
FREE_THREADED = env PYTHON_GIL=0
UV_RUN = $(FREE_THREADED) uv run
COVERAGE = $(FREE_THREADED) $(VENV_DIR)/bin/coverage
PYTHON = $(FREE_THREADED) $(VENV_DIR)/bin/python
PYTEST = $(UV_RUN) pytest -q --tb=short

.PHONY: help install test lint format format-check hygiene changelog-draft ty-audit ty-ratchet benchmark shard-residency-benchmark link-reconciliation-benchmark author-benchmark retrieval-benchmark serve stop freeze export pages-build pdf-proof check clean \
	fast contract coverage browser browser-smoke browser-authoring browser-responsive agent release \
	ci-fast ci-contract ci-coverage ci-export ci-browser ci-browser-smoke \
	ci-browser-authoring ci-browser-responsive ci-browser-full ci-browser-htmx4-preview \
	ci-agent ci-pdf-proof ci-release

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
	tests/test_chirp_docs_link_and_inventory_contracts.py \
	tests/test_remote_shards.py
BROWSER_TESTS = tests/test_author_sse_browser.py
BROWSER_RESULTS ?= browser-results
PDF_PROOF_RESULTS ?= pdf-proof

help:
	@echo "Furatena"
	@echo "  make install      uv sync"
	@echo "  make serve        fura serve"
	@echo "  make stop         fura stop"
	@echo "  make freeze       fura freeze"
	@echo "  make export       fura export"
	@echo "  make pages-build  freeze + export for GitHub Pages"
	@echo "  make pdf-proof    build and verify browser/native PDF proof artifacts"
	@echo "  make check        fura check"
	@echo "  make test         pytest"
	@echo "  make lint         ruff check"
	@echo "  make ty-audit     report repo-wide ty diagnostics without blocking"
	@echo "  make ty-ratchet   enforce owned ty diagnostic budgets"
	@echo "  make format       apply Ruff 0.15.20 formatting"
	@echo "  make format-check verify Ruff 0.15.20 formatting"
	@echo "  make hygiene      changelog and actionable-error hygiene gates"
	@echo "  make changelog-draft preview unreleased Towncrier notes"
	@echo "  make benchmark    index/freeze/query/search timing report"
	@echo "  make shard-residency-benchmark  tiered 100-mount residency profile"
	@echo "  make link-reconciliation-benchmark  incremental 400-shard link profile"
	@echo "  make author-benchmark  author startup/request/validation timing report"
	@echo "  make retrieval-benchmark  known-answer ranking quality/cost report"
	@echo ""
	@echo "CI lanes (see docs/CI.md)"
	@echo "  make ci-fast      lint + ty ratchets + core unit tests (~30s)"
	@echo "  make ci-contract  hypermedia/content contract tests (~60s)"
	@echo "  make ci-coverage  core per-module coverage ratchets (~60s)"
	@echo "  make ci-export    export tests + Pages artifact build (~3m)"
	@echo "  make ci-browser   Playwright author browser tests (~60s)"
	@echo "  make ci-browser-smoke       critical search/navigation/reload browser paths"
	@echo "  make ci-browser-authoring   author preview, save, and create browser paths"
	@echo "  make ci-browser-responsive  responsive viewport browser paths"
	@echo "  make ci-browser-full        complete browser regression tier"
	@echo "  make ci-agent     agent/MCP lint and tests (~30s)"
	@echo "  make ci-pdf-proof cross-head PDF generation, structure, and raster proof"
	@echo "  make ci-release   isolated wheel + sdist install smoke (~3m)"

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

pdf-proof: ci-pdf-proof

check:
	$(UV_RUN) fura check

test:
	$(PYTEST)

lint:
	$(UV_RUN) ruff check src tests app

format:
	$(UV_RUN) ruff format .

format-check:
	$(UV_RUN) ruff format --check .

hygiene:
	$(UV_RUN) python scripts/check_changelog_fragments.py
	$(UV_RUN) python scripts/lint_raise_messages.py

changelog-draft:
	$(UV_RUN) towncrier build --draft --version "$${VERSION:-NEXT}"

ty-audit:
	$(UV_RUN) python scripts/check_ty_diagnostics.py --report-only --json

ty-ratchet:
	$(UV_RUN) python scripts/check_ty_diagnostics.py

benchmark:
	$(UV_RUN) python scripts/benchmark_catalog.py $(BENCHMARK_ARGS)

shard-residency-benchmark:
	$(UV_RUN) python scripts/benchmark_shard_residency.py $(BENCHMARK_ARGS)

link-reconciliation-benchmark:
	$(UV_RUN) python scripts/benchmark_link_reconciliation.py $(BENCHMARK_ARGS)

author-benchmark:
	$(UV_RUN) python scripts/benchmark_author_runtime.py $(BENCHMARK_ARGS)

retrieval-benchmark:
	$(UV_RUN) python scripts/benchmark_retrieval.py $(BENCHMARK_ARGS)

fast: ci-fast

contract: ci-contract

coverage: ci-coverage

browser: ci-browser

browser-smoke: ci-browser-smoke

browser-authoring: ci-browser-authoring

browser-responsive: ci-browser-responsive

agent: ci-agent

release: ci-release

ci-fast: format-check hygiene
	$(UV_RUN) ruff check src tests app
	$(MAKE) ty-ratchet
	$(UV_RUN) ty check \
		src/furatena/catalog/author_truth.py \
		src/furatena/catalog/record_types.py \
		src/furatena/catalog/export.py \
		src/furatena/catalog/graph_schema.py \
		src/furatena/catalog/query.py \
		src/furatena/catalog/retrieval_dataset.py \
		src/furatena/catalog/retrieval_metrics.py \
		src/furatena/catalog/audit_store.py \
		src/furatena/catalog/rate_limit.py \
		src/furatena/catalog/safe_html.py \
		src/furatena/catalog/operational_status.py \
		src/furatena/catalog/observability.py \
		src/furatena/catalog/source_sync_state.py \
		src/furatena/catalog/operation_lease.py \
		src/furatena/catalog/atomic_directory.py \
		src/furatena/catalog/edition_shards.py \
		src/furatena/catalog/publication_provider.py \
		src/furatena/catalog/capability_policy.py \
		src/furatena/catalog/publication_approvals.py \
		src/furatena/catalog/publication_contracts.py \
		src/furatena/catalog/publication_state.py \
		src/furatena/catalog/publication_workflow_store.py \
		src/furatena/catalog/publication_workflow.py \
		src/furatena/catalog/preview_contracts.py \
		src/furatena/catalog/preview_security.py \
		src/furatena/catalog/railway_preview.py \
		src/furatena/catalog/railway_preview_controller.py \
		src/furatena/catalog/preview_conformance.py \
		src/furatena/catalog/author_benchmarks.py \
		src/furatena/catalog/pdf_proof.py \
		src/furatena/catalog/mcp.py \
		src/furatena/catalog/loader.py \
		src/furatena/catalog/remote_shards.py \
		src/furatena/catalog/registry.py \
		src/furatena/catalog/link_reconciliation.py \
		src/furatena/catalog/link_reconciliation_benchmarks.py \
		src/furatena/catalog/shard_discovery.py \
		src/furatena/catalog/shard_residency_benchmarks.py \
		src/furatena/catalog/sources/git.py \
		src/furatena/catalog/sources/types.py \
		src/furatena/catalog/author_store.py \
		src/furatena/catalog/lifecycle.py \
		src/furatena/catalog/models.py \
		src/furatena/cli/authoring.py \
		src/furatena/cli/contracts.py \
		src/furatena/catalog/validation.py
	$(PYTEST) \
		tests/test_catalog_nav.py \
		tests/test_docs_journeys.py \
		tests/test_access_isolation.py \
		tests/test_support_policy.py \
		tests/test_release_publishing.py \
		tests/test_live_slo.py \
		tests/test_hygiene_baseline.py \
		tests/test_activation_measurement.py \
		tests/test_adoption_scorecard.py \
		tests/test_afdocs_score_lane.py \
		tests/test_audit_store.py \
		tests/test_rate_limit.py \
		tests/test_operational_status.py \
		tests/test_observability.py \
		tests/test_source_sync_state.py \
		tests/test_git_edition_discovery.py \
		tests/test_edition_shards.py \
		tests/test_operation_coordination.py \
		tests/test_publication_provider_contracts.py \
		tests/test_publication_provider_schemas.py \
		tests/test_capability_policy.py \
		tests/test_capability_schemas.py \
		tests/test_publication_approvals.py \
		tests/test_publication_approval_schemas.py \
		tests/test_publication_contracts.py \
		tests/test_publication_state.py \
		tests/test_publication_workflow_store.py \
		tests/test_publication_workflow_service.py \
		tests/test_publication_workflow_schemas.py \
		tests/test_publication_schemas.py \
		tests/test_publication_fixtures.py \
		tests/test_preview_contracts.py \
		tests/test_preview_schemas.py \
		tests/test_preview_fixtures.py \
		tests/test_preview_security.py \
		tests/test_railway_preview.py \
		tests/test_railway_preview_controller.py \
		tests/test_preview_conformance.py \
		tests/test_preview_reporting_workflow.py \
		tests/test_pdf_proof.py \
		tests/test_starter_repositories.py \
		tests/test_migration_playbooks.py \
		tests/test_benchmark_harness.py \
		tests/test_shard_link_reconciliation.py \
		tests/test_shard_residency_benchmark.py \
		tests/test_remote_shards.py \
		tests/test_author_benchmark_harness.py \
		tests/test_validation_snapshots.py \
		tests/test_chirp_docs_incremental.py \
		tests/test_docs_core.py \
		tests/test_domain_errors.py \
		tests/test_embedding_providers.py \
		tests/test_gateway_identity.py \
		tests/test_record_types.py \
		tests/test_retrieval_dataset.py \
		tests/test_retrieval_benchmarks.py \
		tests/test_retrieval_conformance.py \
		tests/test_retrieval_feedback.py \
		tests/test_retrieval_metrics.py \
		tests/test_search_hot_paths.py \
		tests/test_safe_html_boundaries.py \
		tests/test_site_config.py \
		tests/test_theme_lint.py \
		tests/test_theme_pack.py \
		tests/test_theme_preset.py \
		tests/test_ty_diagnostic_ratchet.py \
		tests/stewards

ci-contract:
	$(UV_RUN) fura check
	$(UV_RUN) fura docs-reference \
		--output content/furatena/docs/reference/generated-cli-config.md --check
	$(UV_RUN) fura docs-quality
	$(PYTEST) \
		tests/test_author_authorization.py \
		tests/test_author_truth.py \
		tests/test_docs_quality.py \
		tests/test_integrator_operations_reference.py \
		tests/test_chirp_docs_content_lint.py \
		tests/test_chirp_docs_response_conformance.py \
		tests/test_route_manifest.py \
		tests/test_kida_component_seams.py \
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
	$(MAKE) ci-browser-full

ci-browser-smoke:
	mkdir -p $(BROWSER_RESULTS)
	$(PYTEST) --junitxml=$(BROWSER_RESULTS)/smoke.xml \
		-m "browser and browser_smoke" $(BROWSER_TESTS)

ci-browser-authoring:
	mkdir -p $(BROWSER_RESULTS)
	$(PYTEST) --junitxml=$(BROWSER_RESULTS)/authoring.xml \
		-m "browser and browser_authoring" $(BROWSER_TESTS)

ci-browser-responsive:
	mkdir -p $(BROWSER_RESULTS)
	$(PYTEST) --junitxml=$(BROWSER_RESULTS)/responsive.xml \
		-m "browser and browser_responsive" $(BROWSER_TESTS)

ci-browser-full:
	mkdir -p $(BROWSER_RESULTS)
	$(PYTEST) --junitxml=$(BROWSER_RESULTS)/full.xml \
		-m "browser and browser_full" $(BROWSER_TESTS)

ci-browser-htmx4-preview:
	mkdir -p $(BROWSER_RESULTS)
	FURA_HTMX_PREVIEW=4.0.0-beta5 $(PYTEST) \
		--junitxml=$(BROWSER_RESULTS)/htmx4-preview.xml \
		-m "browser and browser_htmx4" $(BROWSER_TESTS)

ci-agent:
	$(UV_RUN) fura check --agent-only --json
	$(PYTEST) tests/test_fura_cli_standalone.py -k "agent or mcp or evals"

ci-pdf-proof:
	mkdir -p $(PDF_PROOF_RESULTS)
	FURA_BASE_URL=http://127.0.0.1 FURA_BASE_PATH=/ \
		FURA_FROZEN_DIR="$(PWD)/$(PDF_PROOF_RESULTS)/frozen" \
		./scripts/pages-build.sh "$(PWD)/$(PDF_PROOF_RESULTS)/site"
	FURA_FROZEN_DIR="$(PWD)/$(PDF_PROOF_RESULTS)/frozen" \
		$(UV_RUN) python scripts/pdf_proof.py \
		--app-root app \
		--site-root "$(PDF_PROOF_RESULTS)/site" \
		--output "$(PDF_PROOF_RESULTS)/artifacts" \
		--baseline config/pdf-proof-baseline.json

ci-release:
	$(FREE_THREADED) uv build --clear --no-sources
	$(PYTHON) scripts/check_distributions.py --dist-dir dist

clean:
	rm -rf app/frozen app/public app/.docs-cache app/.docs-cache-test .pytest_cache .ruff_cache dist build *.egg-info
