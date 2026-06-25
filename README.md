# Furatena — hypermedia documentation catalog

**Furatena** is a live documentation surface for [Chirp](https://github.com/lbliii/chirp): markdown
indexed into an in-memory graph, served as htmx fragments inside a persistent shell, with dual
Content + Presentation IR, semantic retrieval, and static export for GitHub Pages.

The CLI is **`fura`** (short for Furatena).

## Quick start

```bash
uv sync --group dev
uv run fura serve
```

(`fura` lives in `.venv/bin/` — use `uv run`, `make serve`, or `./app/run` unless you've activated the venv.)

Open http://127.0.0.1:8001/

The default **`app/`** instance dogfoods the Furatena documentation corpus under `content/furatena/`.
The Chirp docs mount under `content/chirp/` remains available at `/chirp/` for testing.

## Commands

```bash
uv run fura serve              # hybrid when app/frozen/ exists
uv run fura serve --author     # force live index
uv run fura freeze             # catalog IR + HTML + assets → app/frozen/
uv run fura export             # static HTML → app/public/
uv run fura check              # Chirp contracts + corpus lint
uv run fura query --directive tabs
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

Furatena requires a released **`bengal-chirp`** PyPI package. For local development against a
sibling Chirp checkout, uncomment `[tool.uv.sources]` in `pyproject.toml`.

## Docs

- [Dual IR](docs/DUAL_IR.md)
- [Views](docs/VIEWS.md)
- [Roadmap](docs/ROADMAP.md)
