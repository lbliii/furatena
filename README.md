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

`uv run fura serve` is the canonical zero-install command from a checkout. `uv sync`
installs `fura` into the project environment, but does not place a bare `fura` command
on your global shell path. `make serve` and `./app/run` are convenience launchers for
the same CLI path.

To opt into a bare, globally available command backed by this checkout:

```bash
uv tool install --editable .
fura --help
fura serve
```

If the install reports that the uv tool directory is missing from `PATH`, run
`uv tool update-shell`, restart the shell, and retry `fura --help`. Use
`uv tool dir --bin` to inspect the executable directory.

Open http://127.0.0.1:8001/

The default **`app/`** instance dogfoods the Furatena documentation corpus under `content/furatena/`.

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
uv run fura recipes --json     # stable agent workflow recipes
uv run fura mcp --describe --json
uv run fura author status docs/get-started --json
```

Contributor and CI validation use the same risk-based Make targets. See
[CI lanes](docs/CI.md) for dependencies, scope, and expected runtimes.

## Layout

```
app/           Default deployment (docs.yaml, theme, mounts)
content/       Markdown corpora (content/furatena = default; content/chirp = test fixture)
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
runtime line is pinned in `pyproject.toml` (`>=0.9.0,<0.10.0`) so a fresh
`uv sync --group dev` does not depend on any local Chirp checkout.

The production server boundary is also pinned directly to
**`bengal-pounce>=0.9.2,<0.10.0`** so deploys retain structured readiness JSON
during listener drain and complete large buffered responses under downstream
backpressure.

## Docs

- [Dual IR](docs/DUAL_IR.md)
- [Editions and immutable release shards](docs/EDITIONS.md)
- [Views](docs/VIEWS.md)
- [Authoring lifecycle](docs/AUTHORING.md)
- [Author mutation threat model](docs/AUTHOR_MUTATION_THREAT_MODEL.md)
- [Agent workflows](docs/AGENT_WORKFLOWS.md)
- [Agent-readiness score operations](docs/AGENT_SCORE.md)
- [CLI contract](docs/CLI_CONTRACT.md)
- [HTTP QUERY prototype](docs/HTTP_QUERY.md)
- [htmx 4 preview report](docs/HTMX4_PREVIEW.md)
- [Pull-request preview contract](docs/PR_PREVIEW_CONTRACT.md)
- [Railway proprietary template architecture](docs/RAILWAY_TEMPLATE_ARCHITECTURE.md)
- [Live SLOs and no-SSH operations](docs/LIVE_OPERATIONS.md)
- [Compatibility and support policy](docs/COMPATIBILITY.md)
- [Release and incident runbook](docs/RELEASING.md)
- [CI lanes](docs/CI.md)
- [Roadmap](docs/ROADMAP.md)
