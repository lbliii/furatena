VENV_DIR ?= .venv
UV_RUN = uv run

.PHONY: help install test lint serve stop freeze export pages-build check clean

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

install:
	uv sync --group dev

serve:
	$(UV_RUN) fura serve

stop:
	$(UV_RUN) fura stop

freeze:
	$(UV_RUN) fura freeze

export:
	$(UV_RUN) fura export

pages-build:
	./scripts/pages-build.sh

check:
	$(UV_RUN) fura check

test:
	$(UV_RUN) pytest -q --tb=short

lint:
	$(UV_RUN) ruff check src tests app

clean:
	rm -rf app/frozen app/public app/.docs-cache app/.docs-cache-test .pytest_cache .ruff_cache dist build *.egg-info
