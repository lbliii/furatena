# Furatena — live documentation from markdown

**Furatena** turns markdown into a documentation site that **updates as you edit** —
with built-in search, navigation, static export, and agent-readable catalog data from
the same corpus.

The CLI is **`fura`** (short for Furatena).

**Under the hood:** markdown is indexed into a queryable graph and served as hypermedia
fragments (htmx) inside a persistent shell — no static rebuild loop, no client framework.
Export to GitHub Pages or feed `/catalog.json` to agents from the same corpus.

## Quick start

```bash
uv sync --group dev
uv run fura init /tmp/my-docs --name "My Docs"
uv run fura --app-root /tmp/my-docs check --content-only
uv run fura --app-root /tmp/my-docs serve
```

(`fura` lives in `.venv/bin/` — use `uv run`, `make serve`, or `./app/run` unless you've activated the venv.)

Open http://127.0.0.1:8001/

The default **`app/`** instance dogfoods the Furatena documentation corpus under `content/furatena/`.
The Chirp docs mount under `content/chirp/` remains available at `/chirp/` for testing.

## Commands

```bash
uv run fura serve              # hybrid when app/frozen/ exists
uv run fura init ./docs-site   # scaffold a standalone docs app
uv run fura serve --author     # force live index
uv run fura freeze             # catalog IR + HTML + assets → app/frozen/
uv run fura export             # static HTML → app/public/
uv run fura check              # Chirp contracts + corpus lint
uv run fura query --directive tabs
uv run fura migrate --dry-run  # preview MDX → Patitas markdown lowering
```

## Layout

```
app/           Default deployment (docs.yaml, theme, mounts)
content/       Markdown corpora (content/furatena = default; content/chirp = test mount)
data/          Collections, glossary, rewrites
config/        Autodoc and shared config
src/furatena/  Library (catalog graph, directives, CLI)
docs/          Furatena design notes (Dual IR, views, roadmap)
tests/         Catalog and runtime tests
```

## Naming

- **Furatena** — the product (from the Muzo legend of Fura and Tena)
- **Fura** — CLI and shorthand
- **Itoco** — internal name for the catalog graph (optional vocabulary)

## Chirp dependency

Furatena depends on the released **`bengal-chirp`** package from PyPI. The supported
runtime line is pinned in `pyproject.toml` (`>=0.8.2,<0.9.0`) so a fresh
`uv sync --group dev` does not depend on any local Chirp checkout.

## Docs

- [Dual IR](docs/DUAL_IR.md)
- [Views](docs/VIEWS.md)
- [Roadmap](docs/ROADMAP.md)
