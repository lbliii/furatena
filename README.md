# Furatena — hypermedia documentation catalog

**Furatena** is a live documentation surface for [Chirp](https://github.com/lbliii/chirp): markdown
indexed into an in-memory graph, served as htmx fragments inside a persistent shell, with dual
Content + Presentation IR, semantic retrieval, and static export for GitHub Pages.

The CLI is **`fura`** (short for Furatena).

## Quick start

```bash
uv sync --group dev
fura serve
```

Open http://127.0.0.1:8001/

The default **`app/`** instance dogfoods the Chirp documentation corpus under `content/chirp/`.

## Commands

```bash
fura serve              # hybrid when app/frozen/ exists
fura serve --author     # force live index
fura freeze             # catalog IR + HTML + assets → app/frozen/
fura export             # static HTML → app/public/
fura check              # Chirp contracts + corpus lint
fura query --directive tabs
```

## Layout

```
app/           Default deployment (docs.yaml, theme, mounts)
content/       Markdown corpora (content/chirp = Chirp docs)
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
