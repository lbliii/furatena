---
title: Generated CLI and configuration reference
owner: platform-docs
reviewed_at: "2026-07-07"
description: Parser-derived commands, options, defaults, config fields, and environment controls.
weight: 35
lang: en
type: doc
category: reference
---

# Generated CLI and configuration reference

This page is generated from the active CLI parser, configuration dataclasses, and
environment lookups. Edit the implementation or generator, then run
`uv run fura docs-reference --output content/furatena/docs/reference/generated-cli-config.md`;
do not hand-edit the tables.

## CLI commands and options

### `fura`

Fura — CLI for Furatena (hypermedia docs catalog)

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--version` | no | `==SUPPRESS==` | — | — | show program's version number and exit |
| `--app-root` | no | — | — | — | Furatena app root containing docs.yaml (default: cwd, ./app, or packaged dogfood app) |
| `--config` | no | — | — | — | Path to docs.yaml (default: APP_ROOT/docs.yaml) |
| `--autodoc-config` | no | — | — | — | Path to autodoc.yaml (default: REPO/config/autodoc.yaml when present) |

### `fura activation`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|

### `fura activation mark`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--session` | yes | — | — | — | Local session JSON path |
| `--event` | yes | — | `clean-migration`, `first-edit`, `first-publish` | — | — |
| `--automated-seconds` | no | `0.0` | — | `float` | — |
| `--manual-seconds` | no | `0.0` | — | `float` | — |
| `--replace` | no | false | — | — | Replace an existing milestone |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura activation report`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--session` | yes | — | — | — | Session JSON path |
| `--output` | no | — | — | — | Write the sanitized report JSON |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura activation start`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--journey` | yes | — | `imported-site`, `new-site` | — | — |
| `--session` | yes | — | — | — | Local session JSON path |
| `--consent` | no | false | — | — | Explicitly consent to local duration-only measurement |
| `--replace` | no | false | — | — | Replace an existing session file |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura agent-diff`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `old` | yes | — | — | — | Previous agent contract JSON fixture |
| `new` | yes | — | — | — | Candidate agent contract JSON fixture |
| `--decision` | no | — | — | — | Explicit compatibility decision required when breaking changes are present |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura api-diff`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `old` | yes | — | — | — | Old OpenAPI YAML/JSON spec |
| `new` | yes | — | — | — | New OpenAPI YAML/JSON spec |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura author`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|

### `fura author archive`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `target` | yes | — | — | — | Source path or page slug |
| `--mount` | no | — | — | — | Mount id from mounts.yaml |
| `--source-revision` | no | — | — | — | SHA-256 revision returned by author status/read |
| `--dry-run` | no | false | — | — | Preview without writing |
| `--yes` | no | false | — | — | Confirm source mutation |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura author draft`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `target` | yes | — | — | — | Source path or page slug |
| `--mount` | no | — | — | — | Mount id from mounts.yaml |
| `--source-revision` | no | — | — | — | SHA-256 revision returned by author status/read |
| `--dry-run` | no | false | — | — | Preview without writing |
| `--yes` | no | false | — | — | Confirm source mutation |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura author edit`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `target` | yes | — | — | — | Source path or page slug |
| `--old-text` | yes | — | — | — | Exact source span to replace |
| `--new-text` | yes | — | — | — | Replacement source text |
| `--source-revision` | no | — | — | — | SHA-256 revision returned by author status/read |
| `--mount` | no | — | — | — | Mount id from mounts.yaml |
| `--dry-run` | no | false | — | — | Preview without writing |
| `--yes` | no | false | — | — | Confirm source mutation |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura author new`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `slug` | yes | — | — | — | Page slug under the selected mount, e.g. docs/new-page |
| `--title` | no | — | — | — | Page title (default from slug) |
| `--mount` | no | — | — | — | Mount id from mounts.yaml |
| `--dry-run` | no | false | — | — | Preview without writing |
| `--yes` | no | false | — | — | Confirm source mutation |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura author publish`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `target` | yes | — | — | — | Source path or page slug |
| `--mount` | no | — | — | — | Mount id from mounts.yaml |
| `--source-revision` | no | — | — | — | SHA-256 revision returned by author status/read |
| `--dry-run` | no | false | — | — | Preview without writing |
| `--yes` | no | false | — | — | Confirm source mutation |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura author status`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `target` | yes | — | — | — | Source path or page slug |
| `--mount` | no | — | — | — | Mount id from mounts.yaml |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura author unpublish`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `target` | yes | — | — | — | Source path or page slug |
| `--mount` | no | — | — | — | Mount id from mounts.yaml |
| `--source-revision` | no | — | — | — | SHA-256 revision returned by author status/read |
| `--dry-run` | no | false | — | — | Preview without writing |
| `--yes` | no | false | — | — | Confirm source mutation |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura author validate`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `target` | yes | — | — | — | Source path or page slug |
| `--mount` | no | — | — | — | Mount id from mounts.yaml |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura check`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `app` | no | — | — | — | Optional app import string |
| `--content-only` | no | false | — | — | — |
| `--agent` | no | false | — | — | Include agent MCP/resource contract lint |
| `--agent-only` | no | false | — | — | Run only agent MCP/resource contract lint |
| `--warnings-as-errors` | no | false | — | — | — |
| `--deploy` | no | false | — | — | — |
| `--strict-edition-links` | no | false | — | — | — |
| `--dcp-file` | no | — | — | — | Validate an exported DCP catalog JSON file; may be repeated |
| `--dcp-fixtures` | no | false | — | — | Validate bundled DCP compatibility fixture exports |
| `--report-format` | no | — | `github`, `junit`, `checkstyle`, `markdown` | — | Emit a CI report format instead of human-readable output |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura content`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|

### `fura content reconcile`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|

### `fura content refresh`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--trigger` | no | `manual` | — | — | — |

### `fura content rollback`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|

### `fura content status`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|

### `fura docs-inventory`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--docs-root` | no | — | — | — | Documentation root |
| `--baseline` | no | — | — | — | Prior JSON inventory for stale detection |
| `--output` | no | — | — | — | Write the plain inventory JSON to this path |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura docs-quality`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--docs-root` | no | — | — | — | Documentation root |
| `--exemptions` | no | — | — | — | Reasoned exemption JSON path |
| `--inventory-baseline` | no | — | — | — | Prior inventory used to detect stale public-feature coverage |
| `--freshness-days` | no | `180` | — | `int` | Maximum age of reviewed_at metadata for operations/reference pages |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura docs-reference`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--output` | yes | — | — | — | Generated markdown path |
| `--check` | no | false | — | — | Fail if the output differs |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura evals`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--category` | no | — | — | — | Run one eval category or case id; may be repeated |
| `--include-private` | no | false | — | — | Exercise include-private author MCP evals |
| `--no-autodoc` | no | false | — | — | Skip autodoc slice |
| `--workers` | no | — | — | `int` | Parallel index workers |
| `--retrieval-thresholds` | no | — | — | — | Path to a retrieval threshold policy JSON file |
| `--approve-retrieval-regression` | no | — | — | — | Explicitly approve threshold regressions with a recorded reason |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura export`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `output` | no | — | — | — | Output directory (default app/public) |
| `--frozen` | no | — | — | — | Frozen catalog directory |
| `--base-path` | no | `/chirp` | — | — | URL path prefix |
| `--base-url` | no | `https://lbliii.github.io/chirp` | — | — | Public origin for canonical/OG URLs |
| `--no-index-txt` | no | false | — | — | — |
| `--incremental` | no | false | — | — | — |
| `--fresh` | no | false | — | — | Run freeze before export |
| `--allow-lifecycle-errors` | no | false | — | — | Export even when lifecycle safety checks find draft/private leaks |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura freeze`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--full` | no | false | — | — | Force full rebuild |
| `--workers` | no | — | — | `int` | Parallel workers |
| `--json` | no | false | — | — | Emit the standard command result JSON |
| `output` | no | — | — | — | Output directory (default app/frozen) |

### `fura impact`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--slug` | no | — | — | — | Optional slug used to scope stale-impact entries |
| `--frozen` | no | — | — | — | Frozen catalog directory (default app/frozen) |
| `--include-private` | no | false | — | — | Include private graph context |
| `--no-autodoc` | no | false | — | — | Skip autodoc slice |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura init`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `directory` | no | `.` | — | — | Target app directory (default current directory) |
| `--name` | no | `Furatena Docs` | — | — | Site name |
| `--starter` | no | `minimal` | `minimal`, `api-portal`, `multi-mount`, `governed-preview` | — | Maintained repository profile (default: minimal) |
| `--force` | no | false | — | — | Overwrite scaffold files |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura mcp`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--author` | no | false | — | — | Serve live source catalog data |
| `--preview` | no | false | — | — | Serve frozen catalog data |
| `--hybrid` | no | false | — | — | Serve frozen baseline with live overlay |
| `--frozen-dir` | no | — | — | — | Frozen catalog directory (default app/frozen) |
| `--no-autodoc` | no | false | — | — | Skip autodoc slice |
| `--base-url` | no | — | — | — | Public origin for absolute search URLs |
| `--workers` | no | — | — | `int` | Parallel index workers |
| `--include-private` | no | false | — | — | In author mode, expose draft/private nodes through MCP resources and tools |
| `--remote` | no | false | — | — | Apply remote MCP auth, audit, and safety policy |
| `--actor` | no | — | — | — | Actor id recorded in MCP audit events |
| `--role` | no | — | `anonymous`, `reader`, `contributor`, `publisher`, `admin` | — | Trusted MCP session role; may be repeated (remote defaults to anonymous) |
| `--team` | no | — | — | — | Trusted MCP session team; may be repeated |
| `--tenant` | no | — | — | — | Tenant id recorded in MCP audit events |
| `--site` | no | — | — | — | Site id recorded in MCP audit events |
| `--audit-store` | no | — | — | — | Persist sanitized MCP audit events to this JSONL path |
| `--audit-retention-days` | no | `90` | — | `int` | Retain MCP audit events for this many days (default 90) |
| `--privileged-token` | no | — | — | — | Token required by remote MCP clients before sensitive authoring tools can run |
| `--rate-limit` | no | `120` | — | `int` | Maximum MCP tool calls per actor per minute |
| `--tenant-rate-limit` | no | `600` | — | `int` | Maximum MCP tool calls per tenant per minute across actors |
| `--rate-limit-burst` | no | `20` | — | `int` | Maximum MCP tool calls per actor in a one-second burst |
| `--sensitive-rate-limit` | no | `30` | — | `int` | Maximum sensitive MCP tool calls per actor per minute |
| `--rate-limit-store` | no | — | — | — | Share restart-safe MCP rate limits through this SQLite path |
| `--rate-limit-fallback` | no | `deny` | `deny`, `memory` | — | Behavior when a configured shared rate-limit store is unavailable |
| `--timeout` | no | `15.0` | — | `float` | Declared MCP tool timeout in seconds for audit and client policy metadata |
| `--max-output-chars` | no | `200000` | — | `int` | Maximum serialized characters returned by one MCP tool before truncation |
| `--describe` | no | false | — | — | Describe MCP resources/tools and exit |
| `--json` | no | false | — | — | With --describe, emit the standard command result JSON |

### `fura migrate`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `paths` | no | — | — | — | Optional .mdx files |
| `--report` | no | false | — | — | Report migration readiness risks without writing files |
| `--apply-safe` | no | false | — | — | Create reversible canonical siblings only for conflict-free MDX conversions |
| `--dry-run` | no | false | — | — | — |
| `--keep-mdx` | no | false | — | — | — |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura pdf`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--page` | no | — | — | — | Page URL or slug to export |
| `--collection` | no | — | — | — | Collection, section, or mount to export |
| `output` | no | — | — | — | Output directory (default app/public/pdf) |
| `--base-url` | no | — | — | — | Public origin for channel manifest URLs |
| `--paper` | no | `letter` | `letter`, `a4` | — | Physical PDF page size (default letter) |
| `--grayscale` | no | false | — | — | Render a grayscale publication profile |
| `--frozen` | no | — | — | — | Frozen catalog directory to use for deterministic PDF generation |
| `--live` | no | false | — | — | Build PDFs directly from current source content |
| `--no-autodoc` | no | false | — | — | Skip autodoc slice |
| `--no-channels` | no | false | — | — | Do not refresh channels.json with generated PDF artifacts |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura query`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--directive` | no | — | — | — | — |
| `--heading` | no | — | — | — | — |
| `--mount` | no | — | — | — | — |
| `--edition` | no | — | — | — | — |
| `--tag` | no | — | — | — | — |
| `--url-prefix` | no | — | — | — | — |
| `--json` | no | false | — | — | — |
| `--no-autodoc` | no | false | — | — | — |

### `fura recipes`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `recipe` | no | — | — | — | Optional recipe id, e.g. init, inspect, validate, query, publish, repair, source-sync |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura scorecard`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--input` | yes | — | — | — | Versioned evidence manifest JSON |
| `--output` | no | — | — | — | Write the scorecard report JSON |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura serve`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--host` | no | — | — | — | Bind host (default 127.0.0.1) |
| `--port` | no | — | — | `int` | Bind port (default 8001) |
| `--author` | no | false | — | — | Live index from source |
| `--preview` | no | false | — | — | Frozen catalog only |
| `--hybrid` | no | false | — | — | Frozen baseline + live overlay |
| `--frozen` | no | false | — | — | Alias for --preview |
| `--no-autodoc` | no | false | — | — | Skip autodoc slice |
| `--channel` | no | — | — | — | Version channel (FURA_CHANNEL) |
| `--base-url` | no | — | — | — | Public origin (FURA_BASE_URL) |
| `--workers` | no | — | — | `int` | Parallel index workers |
| `--json` | no | false | — | — | Emit startup as standard command result JSON |

### `fura stop`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--host` | no | — | — | — | Bind host (default 127.0.0.1) |
| `--port` | no | — | — | `int` | Bind port (default 8001) |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura theme`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|

### `fura theme diff`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `path` | yes | — | — | — | Logical path, e.g. views/doc.html |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura theme eject`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `path` | no | — | — | — | Logical path, e.g. views/doc.html |
| `--all` | no | false | — | — | Eject every inspectable template/asset |
| `--force` | no | false | — | — | Overwrite existing local files |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura theme init`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `directory` | no | `app/theme-skin` | — | — | Output directory (default app/theme-skin) |
| `--force` | no | false | — | — | Overwrite existing scaffold files |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura theme inspect`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `path` | no | — | — | — | Logical path, e.g. views/doc.html |
| `--verbose` | no | false | — | — | Show full resolution chain |
| `--json` | no | false | — | — | Emit the standard command result JSON |

### `fura theme list`

Command parser contract.

| Argument or option | Required | Default | Choices | Type | Purpose |
|---|---:|---|---|---|---|
| `--json` | no | false | — | — | Emit the standard command result JSON |

## Configuration fields

Paths use dotted docs.yaml notation; `mounts[]` identifies one mounts.yaml entry.

| Field | Required | Type | Default | Implementation source |
|---|---:|---|---|---|
| `shell` | no | `str` | `shell.html` | `furatena.catalog.config:DocsConfig.shell` |
| `views` | no | `dict[str, str]` | `{}` | `furatena.catalog.config:DocsConfig.views` |
| `overrides` | no | `dict[str, str]` | `{}` | `furatena.catalog.config:DocsConfig.overrides` |
| `compose` | no | `dict[str, ComposeConfig]` | `{}` | `furatena.catalog.config:DocsConfig.compose` |
| `compose[].data` | no | `Path \| None` | — | `furatena.catalog.config:ComposeConfig.data` |
| `theme` | no | `ThemeConfig` | `ThemeConfig(id='furatena', use=None, tokens='theme/tokens.css', styles='theme/styles.css', templates='theme/templates', effects=ThemeEffectsConfig(code='flat', cards='flat', hero='wash'), measure=ThemeMeasureConfig(prose='80ch', reading='76ch', docs='80ch', container='90rem'), fonts=ThemeFontsConfig(sans='Inter', display='Inter'), overrides=ThemeOverridesConfig(tokens=None, styles=None, directives=None, js=None, fonts=None, templates=None))` | `furatena.catalog.config:DocsConfig.theme` |
| `theme.id` | no | `str` | `furatena` | `furatena.catalog.config:ThemeConfig.id` |
| `theme.use` | no | `str \| None` | — | `furatena.catalog.config:ThemeConfig.use` |
| `theme.tokens` | no | `str` | `theme/tokens.css` | `furatena.catalog.config:ThemeConfig.tokens` |
| `theme.styles` | no | `str` | `theme/styles.css` | `furatena.catalog.config:ThemeConfig.styles` |
| `theme.templates` | no | `str` | `theme/templates` | `furatena.catalog.config:ThemeConfig.templates` |
| `theme.effects` | no | `ThemeEffectsConfig` | `ThemeEffectsConfig(code='flat', cards='flat', hero='wash')` | `furatena.catalog.config:ThemeConfig.effects` |
| `theme.effects.code` | no | `str` | `flat` | `furatena.catalog.config:ThemeEffectsConfig.code` |
| `theme.effects.cards` | no | `str` | `flat` | `furatena.catalog.config:ThemeEffectsConfig.cards` |
| `theme.effects.hero` | no | `str` | `wash` | `furatena.catalog.config:ThemeEffectsConfig.hero` |
| `theme.measure` | no | `ThemeMeasureConfig` | `ThemeMeasureConfig(prose='80ch', reading='76ch', docs='80ch', container='90rem')` | `furatena.catalog.config:ThemeConfig.measure` |
| `theme.measure.prose` | no | `str` | `80ch` | `furatena.catalog.config:ThemeMeasureConfig.prose` |
| `theme.measure.reading` | no | `str` | `76ch` | `furatena.catalog.config:ThemeMeasureConfig.reading` |
| `theme.measure.docs` | no | `str` | `80ch` | `furatena.catalog.config:ThemeMeasureConfig.docs` |
| `theme.measure.container` | no | `str` | `90rem` | `furatena.catalog.config:ThemeMeasureConfig.container` |
| `theme.fonts` | no | `ThemeFontsConfig` | `ThemeFontsConfig(sans='Inter', display='Inter')` | `furatena.catalog.config:ThemeConfig.fonts` |
| `theme.fonts.sans` | no | `str` | `Inter` | `furatena.catalog.config:ThemeFontsConfig.sans` |
| `theme.fonts.display` | no | `str` | `Inter` | `furatena.catalog.config:ThemeFontsConfig.display` |
| `theme.overrides` | no | `ThemeOverridesConfig` | `ThemeOverridesConfig(tokens=None, styles=None, directives=None, js=None, fonts=None, templates=None)` | `furatena.catalog.config:ThemeConfig.overrides` |
| `theme.overrides.tokens` | no | `str \| None` | — | `furatena.catalog.config:ThemeOverridesConfig.tokens` |
| `theme.overrides.styles` | no | `str \| None` | — | `furatena.catalog.config:ThemeOverridesConfig.styles` |
| `theme.overrides.directives` | no | `str \| None` | — | `furatena.catalog.config:ThemeOverridesConfig.directives` |
| `theme.overrides.js` | no | `str \| None` | — | `furatena.catalog.config:ThemeOverridesConfig.js` |
| `theme.overrides.fonts` | no | `str \| None` | — | `furatena.catalog.config:ThemeOverridesConfig.fonts` |
| `theme.overrides.templates` | no | `str \| None` | — | `furatena.catalog.config:ThemeOverridesConfig.templates` |
| `site` | no | `SiteConfig` | `SiteConfig(name='Furatena', tagline='Live documentation from markdown', description="Write markdown. Get a fast, searchable doc site that reloads while you work — and exports to GitHub Pages when you're ready to ship.", mark='𐂛', home=SiteHomeConfig(aria_label='Overview', hero_points=(), cta_primary=SiteCtaConfig(label='Get started', href='/docs/get-started/'), cta_secondary=SiteCtaConfig(label='Reference', href='/docs/reference/'), metrics=(), metrics_head=None, visual=SiteHomeVisualConfig(aria_label='Product preview', eyebrow='Example interface', title='Docs as data, HTML on demand.', description='Your markdown becomes a live, queryable catalog — pages update instantly, no rebuild loop.', proof_tags=('htmx', 'catalog', 'freeze'), feature_title='Author reload', feature_body='Edit markdown and see partial swaps on the open page — no export loop.', cta_label='Open get started', cta_href='/docs/get-started/'), ideas=None, explore=None, pipeline=None, deployments=None, sources=None, exports=None, quick_start=None, workflows=None, brand=None, stack=None, cta=None), navigation=None)` | `furatena.catalog.config:DocsConfig.site` |
| `site.name` | no | `str` | `Furatena` | `furatena.catalog.config:SiteConfig.name` |
| `site.tagline` | no | `str` | `Live documentation from markdown` | `furatena.catalog.config:SiteConfig.tagline` |
| `site.description` | no | `str` | `Write markdown. Get a fast, searchable doc site that reloads while you work — and exports to GitHub Pages when you're ready to ship.` | `furatena.catalog.config:SiteConfig.description` |
| `site.mark` | no | `str` | `𐂛` | `furatena.catalog.config:SiteConfig.mark` |
| `site.home` | no | `SiteHomeConfig` | `SiteHomeConfig(aria_label='Overview', hero_points=(), cta_primary=SiteCtaConfig(label='Get started', href='/docs/get-started/'), cta_secondary=SiteCtaConfig(label='Reference', href='/docs/reference/'), metrics=(), metrics_head=None, visual=SiteHomeVisualConfig(aria_label='Product preview', eyebrow='Example interface', title='Docs as data, HTML on demand.', description='Your markdown becomes a live, queryable catalog — pages update instantly, no rebuild loop.', proof_tags=('htmx', 'catalog', 'freeze'), feature_title='Author reload', feature_body='Edit markdown and see partial swaps on the open page — no export loop.', cta_label='Open get started', cta_href='/docs/get-started/'), ideas=None, explore=None, pipeline=None, deployments=None, sources=None, exports=None, quick_start=None, workflows=None, brand=None, stack=None, cta=None)` | `furatena.catalog.config:SiteConfig.home` |
| `site.home.aria_label` | no | `str` | `Overview` | `furatena.catalog.config:SiteHomeConfig.aria_label` |
| `site.home.hero_points` | no | `tuple[str, ...]` | — | `furatena.catalog.config:SiteHomeConfig.hero_points` |
| `site.home.cta_primary` | no | `SiteCtaConfig` | `SiteCtaConfig(label='Get started', href='/docs/get-started/')` | `furatena.catalog.config:SiteHomeConfig.cta_primary` |
| `site.home.cta_primary.label` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.label` |
| `site.home.cta_primary.href` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.href` |
| `site.home.cta_secondary` | no | `SiteCtaConfig` | `SiteCtaConfig(label='Reference', href='/docs/reference/')` | `furatena.catalog.config:SiteHomeConfig.cta_secondary` |
| `site.home.cta_secondary.label` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.label` |
| `site.home.cta_secondary.href` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.href` |
| `site.home.metrics` | no | `tuple[SiteMetricConfig, ...]` | — | `furatena.catalog.config:SiteHomeConfig.metrics` |
| `site.home.metrics[].value` | yes | `str` | — | `furatena.catalog.config:SiteMetricConfig.value` |
| `site.home.metrics[].label` | yes | `str` | — | `furatena.catalog.config:SiteMetricConfig.label` |
| `site.home.metrics[].hint` | yes | `str` | — | `furatena.catalog.config:SiteMetricConfig.hint` |
| `site.home.metrics_head` | no | `SiteHomeMetricsHeadConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.metrics_head` |
| `site.home.metrics_head.eyebrow` | no | `str` | — | `furatena.catalog.config:SiteHomeMetricsHeadConfig.eyebrow` |
| `site.home.metrics_head.title` | no | `str` | — | `furatena.catalog.config:SiteHomeMetricsHeadConfig.title` |
| `site.home.metrics_head.subtitle` | no | `str` | — | `furatena.catalog.config:SiteHomeMetricsHeadConfig.subtitle` |
| `site.home.visual` | no | `SiteHomeVisualConfig` | `SiteHomeVisualConfig(aria_label='Product preview', eyebrow='Example interface', title='Docs as data, HTML on demand.', description='Your markdown becomes a live, queryable catalog — pages update instantly, no rebuild loop.', proof_tags=('htmx', 'catalog', 'freeze'), feature_title='Author reload', feature_body='Edit markdown and see partial swaps on the open page — no export loop.', cta_label='Open get started', cta_href='/docs/get-started/')` | `furatena.catalog.config:SiteHomeConfig.visual` |
| `site.home.visual.aria_label` | no | `str` | `Product preview` | `furatena.catalog.config:SiteHomeVisualConfig.aria_label` |
| `site.home.visual.eyebrow` | no | `str` | `Example interface` | `furatena.catalog.config:SiteHomeVisualConfig.eyebrow` |
| `site.home.visual.title` | no | `str` | `Docs as data, HTML on demand.` | `furatena.catalog.config:SiteHomeVisualConfig.title` |
| `site.home.visual.description` | no | `str` | `Your markdown becomes a live, queryable catalog — pages update instantly, no rebuild loop.` | `furatena.catalog.config:SiteHomeVisualConfig.description` |
| `site.home.visual.proof_tags` | no | `tuple[str, ...]` | `htmx`, `catalog`, `freeze` | `furatena.catalog.config:SiteHomeVisualConfig.proof_tags` |
| `site.home.visual.feature_title` | no | `str` | `Author reload` | `furatena.catalog.config:SiteHomeVisualConfig.feature_title` |
| `site.home.visual.feature_body` | no | `str` | `Edit markdown and see partial swaps on the open page — no export loop.` | `furatena.catalog.config:SiteHomeVisualConfig.feature_body` |
| `site.home.visual.cta_label` | no | `str` | `Open get started` | `furatena.catalog.config:SiteHomeVisualConfig.cta_label` |
| `site.home.visual.cta_href` | no | `str` | `/docs/get-started/` | `furatena.catalog.config:SiteHomeVisualConfig.cta_href` |
| `site.home.ideas` | no | `SiteHomeIdeasConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.ideas` |
| `site.home.ideas.eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomeIdeasConfig.eyebrow` |
| `site.home.ideas.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeIdeasConfig.title` |
| `site.home.ideas.subtitle` | yes | `str` | — | `furatena.catalog.config:SiteHomeIdeasConfig.subtitle` |
| `site.home.ideas.features` | yes | `tuple[SiteHomeFeatureConfig, ...]` | — | `furatena.catalog.config:SiteHomeIdeasConfig.features` |
| `site.home.ideas.features[].eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomeFeatureConfig.eyebrow` |
| `site.home.ideas.features[].title` | yes | `str` | — | `furatena.catalog.config:SiteHomeFeatureConfig.title` |
| `site.home.ideas.features[].body` | yes | `str` | — | `furatena.catalog.config:SiteHomeFeatureConfig.body` |
| `site.home.ideas.features[].code` | yes | `str` | — | `furatena.catalog.config:SiteHomeFeatureConfig.code` |
| `site.home.ideas.features[].action` | no | `SiteCtaConfig \| None` | — | `furatena.catalog.config:SiteHomeFeatureConfig.action` |
| `site.home.ideas.features[].action.label` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.label` |
| `site.home.ideas.features[].action.href` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.href` |
| `site.home.ideas.features[].reverse` | no | `bool` | false | `furatena.catalog.config:SiteHomeFeatureConfig.reverse` |
| `site.home.explore` | no | `SiteHomeExploreConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.explore` |
| `site.home.explore.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeExploreConfig.title` |
| `site.home.explore.links` | yes | `tuple[SiteCtaConfig, ...]` | — | `furatena.catalog.config:SiteHomeExploreConfig.links` |
| `site.home.explore.links[].label` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.label` |
| `site.home.explore.links[].href` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.href` |
| `site.home.pipeline` | no | `SiteHomePipelineConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.pipeline` |
| `site.home.pipeline.eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomePipelineConfig.eyebrow` |
| `site.home.pipeline.title` | yes | `str` | — | `furatena.catalog.config:SiteHomePipelineConfig.title` |
| `site.home.pipeline.subtitle` | yes | `str` | — | `furatena.catalog.config:SiteHomePipelineConfig.subtitle` |
| `site.home.pipeline.current_step` | yes | `int` | — | `furatena.catalog.config:SiteHomePipelineConfig.current_step` |
| `site.home.pipeline.steps` | yes | `tuple[SiteHomePipelineStepConfig, ...]` | — | `furatena.catalog.config:SiteHomePipelineConfig.steps` |
| `site.home.pipeline.steps[].id` | yes | `str` | — | `furatena.catalog.config:SiteHomePipelineStepConfig.id` |
| `site.home.pipeline.steps[].label` | yes | `str` | — | `furatena.catalog.config:SiteHomePipelineStepConfig.label` |
| `site.home.pipeline.modes` | yes | `tuple[SiteHomeModeConfig, ...]` | — | `furatena.catalog.config:SiteHomePipelineConfig.modes` |
| `site.home.pipeline.modes[].title` | yes | `str` | — | `furatena.catalog.config:SiteHomeModeConfig.title` |
| `site.home.pipeline.modes[].subtitle` | yes | `str` | — | `furatena.catalog.config:SiteHomeModeConfig.subtitle` |
| `site.home.pipeline.modes[].body` | yes | `str` | — | `furatena.catalog.config:SiteHomeModeConfig.body` |
| `site.home.pipeline.modes[].variant` | no | `str` | — | `furatena.catalog.config:SiteHomeModeConfig.variant` |
| `site.home.deployments` | no | `SiteHomeDeploymentsConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.deployments` |
| `site.home.deployments.eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomeDeploymentsConfig.eyebrow` |
| `site.home.deployments.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeDeploymentsConfig.title` |
| `site.home.deployments.subtitle` | yes | `str` | — | `furatena.catalog.config:SiteHomeDeploymentsConfig.subtitle` |
| `site.home.deployments.options` | yes | `tuple[SiteHomeDeploymentOptionConfig, ...]` | — | `furatena.catalog.config:SiteHomeDeploymentsConfig.options` |
| `site.home.deployments.options[].label` | yes | `str` | — | `furatena.catalog.config:SiteHomeDeploymentOptionConfig.label` |
| `site.home.deployments.options[].title` | yes | `str` | — | `furatena.catalog.config:SiteHomeDeploymentOptionConfig.title` |
| `site.home.deployments.options[].body` | yes | `str` | — | `furatena.catalog.config:SiteHomeDeploymentOptionConfig.body` |
| `site.home.deployments.future` | no | `SiteHomeDeploymentFutureConfig \| None` | — | `furatena.catalog.config:SiteHomeDeploymentsConfig.future` |
| `site.home.deployments.future.label` | yes | `str` | — | `furatena.catalog.config:SiteHomeDeploymentFutureConfig.label` |
| `site.home.deployments.future.items` | yes | `tuple[str, ...]` | — | `furatena.catalog.config:SiteHomeDeploymentFutureConfig.items` |
| `site.home.sources` | no | `SiteHomeSourcesConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.sources` |
| `site.home.sources.eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomeSourcesConfig.eyebrow` |
| `site.home.sources.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeSourcesConfig.title` |
| `site.home.sources.subtitle` | yes | `str` | — | `furatena.catalog.config:SiteHomeSourcesConfig.subtitle` |
| `site.home.sources.formats` | yes | `tuple[SiteHomeSourceFormatConfig, ...]` | — | `furatena.catalog.config:SiteHomeSourcesConfig.formats` |
| `site.home.sources.formats[].label` | yes | `str` | — | `furatena.catalog.config:SiteHomeSourceFormatConfig.label` |
| `site.home.sources.formats[].detail` | yes | `str` | — | `furatena.catalog.config:SiteHomeSourceFormatConfig.detail` |
| `site.home.sources.formats[].body` | yes | `str` | — | `furatena.catalog.config:SiteHomeSourceFormatConfig.body` |
| `site.home.sources.note` | no | `str` | — | `furatena.catalog.config:SiteHomeSourcesConfig.note` |
| `site.home.exports` | no | `SiteHomeExportsConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.exports` |
| `site.home.exports.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeExportsConfig.title` |
| `site.home.exports.subtitle` | yes | `str` | — | `furatena.catalog.config:SiteHomeExportsConfig.subtitle` |
| `site.home.exports.action` | yes | `SiteCtaConfig` | — | `furatena.catalog.config:SiteHomeExportsConfig.action` |
| `site.home.exports.action.label` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.label` |
| `site.home.exports.action.href` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.href` |
| `site.home.quick_start` | no | `SiteHomeQuickStartConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.quick_start` |
| `site.home.quick_start.eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomeQuickStartConfig.eyebrow` |
| `site.home.quick_start.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeQuickStartConfig.title` |
| `site.home.quick_start.commands` | yes | `str` | — | `furatena.catalog.config:SiteHomeQuickStartConfig.commands` |
| `site.home.quick_start.caption` | yes | `str` | — | `furatena.catalog.config:SiteHomeQuickStartConfig.caption` |
| `site.home.workflows` | no | `SiteHomeWorkflowsConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.workflows` |
| `site.home.workflows.eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomeWorkflowsConfig.eyebrow` |
| `site.home.workflows.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeWorkflowsConfig.title` |
| `site.home.workflows.items` | yes | `tuple[SiteHomeWorkflowItemConfig, ...]` | — | `furatena.catalog.config:SiteHomeWorkflowsConfig.items` |
| `site.home.workflows.items[].text` | yes | `str` | — | `furatena.catalog.config:SiteHomeWorkflowItemConfig.text` |
| `site.home.workflows.items[].link` | no | `SiteHomeWorkflowLinkConfig \| None` | — | `furatena.catalog.config:SiteHomeWorkflowItemConfig.link` |
| `site.home.workflows.items[].link.label` | yes | `str` | — | `furatena.catalog.config:SiteHomeWorkflowLinkConfig.label` |
| `site.home.workflows.items[].link.href` | yes | `str` | — | `furatena.catalog.config:SiteHomeWorkflowLinkConfig.href` |
| `site.home.brand` | no | `SiteHomeBrandConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.brand` |
| `site.home.brand.eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomeBrandConfig.eyebrow` |
| `site.home.brand.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeBrandConfig.title` |
| `site.home.brand.body` | yes | `str` | — | `furatena.catalog.config:SiteHomeBrandConfig.body` |
| `site.home.stack` | no | `SiteHomeStackConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.stack` |
| `site.home.stack.eyebrow` | yes | `str` | — | `furatena.catalog.config:SiteHomeStackConfig.eyebrow` |
| `site.home.stack.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeStackConfig.title` |
| `site.home.stack.intro` | yes | `str` | — | `furatena.catalog.config:SiteHomeStackConfig.intro` |
| `site.home.stack.footer` | yes | `str` | — | `furatena.catalog.config:SiteHomeStackConfig.footer` |
| `site.home.stack.rows` | yes | `tuple[SiteHomeStackRowConfig, ...]` | — | `furatena.catalog.config:SiteHomeStackConfig.rows` |
| `site.home.stack.rows[].mark` | yes | `str` | — | `furatena.catalog.config:SiteHomeStackRowConfig.mark` |
| `site.home.stack.rows[].name` | yes | `str` | — | `furatena.catalog.config:SiteHomeStackRowConfig.name` |
| `site.home.stack.rows[].description` | yes | `str` | — | `furatena.catalog.config:SiteHomeStackRowConfig.description` |
| `site.home.stack.rows[].href` | no | `str \| None` | — | `furatena.catalog.config:SiteHomeStackRowConfig.href` |
| `site.home.stack.rows[].here` | no | `bool` | false | `furatena.catalog.config:SiteHomeStackRowConfig.here` |
| `site.home.cta` | no | `SiteHomeCtaBandConfig \| None` | — | `furatena.catalog.config:SiteHomeConfig.cta` |
| `site.home.cta.title` | yes | `str` | — | `furatena.catalog.config:SiteHomeCtaBandConfig.title` |
| `site.home.cta.body` | yes | `str` | — | `furatena.catalog.config:SiteHomeCtaBandConfig.body` |
| `site.home.cta.secondary` | yes | `SiteCtaConfig` | — | `furatena.catalog.config:SiteHomeCtaBandConfig.secondary` |
| `site.home.cta.secondary.label` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.label` |
| `site.home.cta.secondary.href` | yes | `str` | — | `furatena.catalog.config:SiteCtaConfig.href` |
| `site.navigation` | no | `SiteNavigationConfig \| None` | — | `furatena.catalog.config:SiteConfig.navigation` |
| `site.navigation.documentation` | yes | `SiteNavSectionConfig` | — | `furatena.catalog.config:SiteNavigationConfig.documentation` |
| `site.navigation.documentation.menu_label` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.menu_label` |
| `site.navigation.documentation.dropdown_href` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.dropdown_href` |
| `site.navigation.documentation.overview_href` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.overview_href` |
| `site.navigation.documentation.overview_kicker` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.overview_kicker` |
| `site.navigation.documentation.overview_title` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.overview_title` |
| `site.navigation.documentation.overview_blurb` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.overview_blurb` |
| `site.navigation.documentation.links` | yes | `tuple[SiteNavLinkConfig, ...]` | — | `furatena.catalog.config:SiteNavSectionConfig.links` |
| `site.navigation.documentation.links[].href` | yes | `str` | — | `furatena.catalog.config:SiteNavLinkConfig.href` |
| `site.navigation.documentation.links[].label` | yes | `str` | — | `furatena.catalog.config:SiteNavLinkConfig.label` |
| `site.navigation.documentation.links[].blurb` | yes | `str` | — | `furatena.catalog.config:SiteNavLinkConfig.blurb` |
| `site.navigation.documentation.links[].icon` | no | `str` | `file-text` | `furatena.catalog.config:SiteNavLinkConfig.icon` |
| `site.navigation.develop` | yes | `SiteNavSectionConfig` | — | `furatena.catalog.config:SiteNavigationConfig.develop` |
| `site.navigation.develop.menu_label` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.menu_label` |
| `site.navigation.develop.dropdown_href` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.dropdown_href` |
| `site.navigation.develop.overview_href` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.overview_href` |
| `site.navigation.develop.overview_kicker` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.overview_kicker` |
| `site.navigation.develop.overview_title` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.overview_title` |
| `site.navigation.develop.overview_blurb` | yes | `str` | — | `furatena.catalog.config:SiteNavSectionConfig.overview_blurb` |
| `site.navigation.develop.links` | yes | `tuple[SiteNavLinkConfig, ...]` | — | `furatena.catalog.config:SiteNavSectionConfig.links` |
| `site.navigation.develop.links[].href` | yes | `str` | — | `furatena.catalog.config:SiteNavLinkConfig.href` |
| `site.navigation.develop.links[].label` | yes | `str` | — | `furatena.catalog.config:SiteNavLinkConfig.label` |
| `site.navigation.develop.links[].blurb` | yes | `str` | — | `furatena.catalog.config:SiteNavLinkConfig.blurb` |
| `site.navigation.develop.links[].icon` | no | `str` | `file-text` | `furatena.catalog.config:SiteNavLinkConfig.icon` |
| `catalog` | no | `CatalogNavConfig` | `CatalogNavConfig(sections=(), append_unlisted=True)` | `furatena.catalog.config:DocsConfig.catalog` |
| `catalog.sections` | no | `tuple[CatalogSectionConfig, ...]` | — | `furatena.catalog.catalog_nav:CatalogNavConfig.sections` |
| `catalog.sections[].id` | yes | `str` | — | `furatena.catalog.catalog_nav:CatalogSectionConfig.id` |
| `catalog.sections[].label` | no | `str \| None` | — | `furatena.catalog.catalog_nav:CatalogSectionConfig.label` |
| `catalog.sections[].icon` | no | `str \| None` | — | `furatena.catalog.catalog_nav:CatalogSectionConfig.icon` |
| `catalog.sections[].mark` | no | `str \| None` | — | `furatena.catalog.catalog_nav:CatalogSectionConfig.mark` |
| `catalog.sections[].sections` | no | `tuple[str, ...]` | — | `furatena.catalog.catalog_nav:CatalogSectionConfig.sections` |
| `catalog.sections[].pages` | no | `tuple[str, ...]` | — | `furatena.catalog.catalog_nav:CatalogSectionConfig.pages` |
| `catalog.sections[].href` | no | `str \| None` | — | `furatena.catalog.catalog_nav:CatalogSectionConfig.href` |
| `catalog.append_unlisted` | no | `bool` | true | `furatena.catalog.catalog_nav:CatalogNavConfig.append_unlisted` |
| `identity` | no | `CatalogIdentityConfig` | `CatalogIdentityConfig(tenant='default', workspace='default', site='default')` | `furatena.catalog.config:DocsConfig.identity` |
| `identity.tenant` | no | `str` | `default` | `furatena.catalog.config:CatalogIdentityConfig.tenant` |
| `identity.workspace` | no | `str` | `default` | `furatena.catalog.config:CatalogIdentityConfig.workspace` |
| `identity.site` | no | `str` | `default` | `furatena.catalog.config:CatalogIdentityConfig.site` |
| `delivery` | no | `DeliveryConfig` | `DeliveryConfig(head='live-shell', theme=DeliveryThemeConfig(id=None, use=None), mounts={})` | `furatena.catalog.config:DocsConfig.delivery` |
| `delivery.head` | no | `str` | `live-shell` | `furatena.catalog.config:DeliveryConfig.head` |
| `delivery.theme` | no | `DeliveryThemeConfig` | `DeliveryThemeConfig(id=None, use=None)` | `furatena.catalog.config:DeliveryConfig.theme` |
| `delivery.theme.id` | no | `str \| None` | — | `furatena.catalog.config:DeliveryThemeConfig.id` |
| `delivery.theme.use` | no | `str \| None` | — | `furatena.catalog.config:DeliveryThemeConfig.use` |
| `delivery.mounts` | no | `dict[str, DeliveryMountConfig]` | `{}` | `furatena.catalog.config:DeliveryConfig.mounts` |
| `delivery.mounts[].head` | no | `str \| None` | — | `furatena.catalog.config:DeliveryMountConfig.head` |
| `delivery.mounts[].theme` | no | `DeliveryThemeConfig` | `DeliveryThemeConfig(id=None, use=None)` | `furatena.catalog.config:DeliveryMountConfig.theme` |
| `delivery.mounts[].theme.id` | no | `str \| None` | — | `furatena.catalog.config:DeliveryThemeConfig.id` |
| `delivery.mounts[].theme.use` | no | `str \| None` | — | `furatena.catalog.config:DeliveryThemeConfig.use` |
| `mounts` | no | `Path \| None` | — | `furatena.catalog.config:DocsConfig.mounts_path` |
| `rewrites` | no | `Path \| None` | — | `furatena.catalog.config:DocsConfig.rewrites_path` |
| `inventories` | no | `Path \| None` | — | `furatena.catalog.config:DocsConfig.inventories_path` |
| `i18n` | no | `DocsI18nConfig` | `DocsI18nConfig(default_language='en', fallback_to_default=True, languages=(DocsLanguage(code='en', name='English', hreflang='', rtl=None, weight=0),), strategy='subdir', locale_content_dir='_locale')` | `furatena.catalog.config:DocsConfig.i18n` |
| `i18n.default_language` | no | `str` | `en` | `furatena.catalog.i18n:DocsI18nConfig.default_language` |
| `i18n.fallback_to_default` | no | `bool` | true | `furatena.catalog.i18n:DocsI18nConfig.fallback_to_default` |
| `i18n.languages` | no | `tuple[DocsLanguage, ...]` | `DocsLanguage(code='en', name='English', hreflang='', rtl=None, weight=0)` | `furatena.catalog.i18n:DocsI18nConfig.languages` |
| `i18n.languages[].code` | yes | `str` | — | `furatena.catalog.i18n:DocsLanguage.code` |
| `i18n.languages[].name` | yes | `str` | — | `furatena.catalog.i18n:DocsLanguage.name` |
| `i18n.languages[].hreflang` | no | `str` | — | `furatena.catalog.i18n:DocsLanguage.hreflang` |
| `i18n.languages[].rtl` | no | `bool \| None` | — | `furatena.catalog.i18n:DocsLanguage.rtl` |
| `i18n.languages[].weight` | no | `int` | `0` | `furatena.catalog.i18n:DocsLanguage.weight` |
| `i18n.strategy` | no | `str` | `subdir` | `furatena.catalog.i18n:DocsI18nConfig.strategy` |
| `i18n.locale_content_dir` | no | `str` | `_locale` | `furatena.catalog.i18n:DocsI18nConfig.locale_content_dir` |
| `locales_dir` | no | `Path \| None` | — | `furatena.catalog.config:DocsConfig.locales_dir` |
| `mounts[].id` | yes | `str` | — | `furatena.catalog.registry:MountConfig.id` |
| `mounts[].label` | yes | `str` | — | `furatena.catalog.registry:MountConfig.label` |
| `mounts[].content_root` | yes | `Path` | — | `furatena.catalog.registry:MountConfig.content_root` |
| `mounts[].url_prefix` | no | `str` | — | `furatena.catalog.registry:MountConfig.url_prefix` |
| `mounts[].default` | no | `bool` | false | `furatena.catalog.registry:MountConfig.default` |
| `mounts[].source` | no | `MountSourceConfig` | `MountSourceConfig(extensions=frozenset({'.md'}), index_files=frozenset({'_index.md'}), format_map={'.md': 'patitas-markdown', '.markdown': 'patitas-markdown'}, default_format='patitas-markdown', provider='filesystem', git=None)` | `furatena.catalog.registry:MountConfig.source` |
| `mounts[].source.extensions` | no | `frozenset[str]` | `.md` | `furatena.catalog.sources.types:MountSourceConfig.extensions` |
| `mounts[].source.index_files` | no | `frozenset[str]` | `_index.md` | `furatena.catalog.sources.types:MountSourceConfig.index_files` |
| `mounts[].source.format_map` | no | `dict[str, str]` | `{'.md': 'patitas-markdown', '.markdown': 'patitas-markdown'}` | `furatena.catalog.sources.types:MountSourceConfig.format_map` |
| `mounts[].source.default_format` | no | `str` | `patitas-markdown` | `furatena.catalog.sources.types:MountSourceConfig.default_format` |
| `mounts[].source.provider` | no | `str` | `filesystem` | `furatena.catalog.sources.types:MountSourceConfig.provider` |
| `mounts[].source.git` | no | `GitSourceConfig \| None` | — | `furatena.catalog.sources.types:MountSourceConfig.git` |
| `mounts[].source.git.repo` | yes | `str` | — | `furatena.catalog.sources.types:GitSourceConfig.repo` |
| `mounts[].source.git.ref` | no | `str` | `HEAD` | `furatena.catalog.sources.types:GitSourceConfig.ref` |
| `mounts[].source.git.path` | no | `str` | — | `furatena.catalog.sources.types:GitSourceConfig.path` |
| `mounts[].source.git.sync_root` | no | `str \| None` | — | `furatena.catalog.sources.types:GitSourceConfig.sync_root` |
| `mounts[].source.git.resolved_ref` | no | `str \| None` | — | `furatena.catalog.sources.types:GitSourceConfig.resolved_ref` |
| `mounts[].source.git.source_url` | no | `str \| None` | — | `furatena.catalog.sources.types:GitSourceConfig.source_url` |
| `mounts[].access` | no | `AccessPolicy` | `AccessPolicy(visibility='public', roles=frozenset(), teams=frozenset(), admin_only=False)` | `furatena.catalog.registry:MountConfig.access` |
| `mounts[].access.visibility` | no | `str` | `public` | `furatena.catalog.access:AccessPolicy.visibility` |
| `mounts[].access.roles` | no | `frozenset[AccessRole]` | — | `furatena.catalog.access:AccessPolicy.roles` |
| `mounts[].access.teams` | no | `frozenset[str]` | — | `furatena.catalog.access:AccessPolicy.teams` |
| `mounts[].access.admin_only` | no | `bool` | false | `furatena.catalog.access:AccessPolicy.admin_only` |
| `mounts[].editions` | no | `GitEditionPolicy \| None` | — | `furatena.catalog.registry:MountConfig.editions` |
| `mounts[].editions.source` | no | `str` | `tags` | `furatena.catalog.sources.types:GitEditionPolicy.source` |
| `mounts[].editions.count` | no | `int` | `0` | `furatena.catalog.sources.types:GitEditionPolicy.count` |
| `mounts[].editions.pattern` | no | `str` | `v*` | `furatena.catalog.sources.types:GitEditionPolicy.pattern` |
| `mounts[].editions.strip_prefix` | no | `str` | `v` | `furatena.catalog.sources.types:GitEditionPolicy.strip_prefix` |
| `mounts[].editions.sort` | no | `str` | `semver-desc` | `furatena.catalog.sources.types:GitEditionPolicy.sort` |
| `mounts[].editions.include_prereleases` | no | `bool` | false | `furatena.catalog.sources.types:GitEditionPolicy.include_prereleases` |
| `mounts[].editions.aliases` | no | `dict[str, str]` | `{'latest': 'latest', 'stable': 'latest'}` | `furatena.catalog.sources.types:GitEditionPolicy.aliases` |
| `mounts[].editions.overrides` | no | `dict[str, GitEditionOverride]` | `{}` | `furatena.catalog.sources.types:GitEditionPolicy.overrides` |
| `mounts[].editions.overrides[].status` | no | `str` | `legacy` | `furatena.catalog.sources.types:GitEditionOverride.status` |
| `mounts[].editions.overrides[].release_date` | no | `str \| None` | — | `furatena.catalog.sources.types:GitEditionOverride.release_date` |
| `mounts[].editions.overrides[].end_of_life` | no | `str \| None` | — | `furatena.catalog.sources.types:GitEditionOverride.end_of_life` |
| `mounts[].editions.overrides[].banner` | no | `str \| None` | — | `furatena.catalog.sources.types:GitEditionOverride.banner` |

## Environment controls

Read/write modes and defaults are derived from package source. `None` means the
implementation treats absence as significant or supplies behavior elsewhere.

| Variable | Modes | Observed defaults | Implementation sources |
|---|---|---|---|
| `CHIRP_ENV` | read | None | `furatena/catalog/docs_app.py:244` |
| `CHIRP_SECRET_KEY` | read | None | `furatena/catalog/docs_app.py:250` |
| `CHIRP_SKIP_CONTRACT_CHECKS` | read | '' | `furatena/catalog/docs_app.py:423` |
| `FURA_APP_ROOT` | read | None | `furatena/cli/commands/_shared.py:41` |
| `FURA_AUTODOC` | read, write | '1', None | `furatena/catalog/docs_app.py:1747`, `furatena/cli/commands/serve.py:42` |
| `FURA_BASE_PATH` | read, write | '', None | `furatena/catalog/route_registrars.py:1187`, `furatena/catalog/route_registrars.py:1206`, `furatena/catalog/static_export.py:189`, `furatena/catalog/static_export.py:327`, `furatena/catalog/static_export.py:333`, `furatena/cli/commands/export.py:28` |
| `FURA_BASE_URL` | read, write | '', None | `furatena/catalog/seo.py:24`, `furatena/catalog/static_export.py:193`, `furatena/catalog/static_export.py:326`, `furatena/catalog/static_export.py:331`, `furatena/cli/commands/export.py:26`, `furatena/cli/commands/pdf.py:26`, `furatena/cli/commands/serve.py:46` |
| `FURA_BUILD_GIT_SHA` | read | '', None | `furatena/catalog/build_identity.py:43`, `furatena/catalog/preview_security.py:103` |
| `FURA_CHANNEL` | read, write | 'latest', None | `furatena/catalog/versions.py:36`, `furatena/cli/commands/serve.py:44` |
| `FURA_CONTENT_RESTART_AFTER_PROMOTION` | read | '1' | `furatena/catalog/route_registrars.py:108` |
| `FURA_CONTENT_STATE_ROOT` | read | '/data/furatena' | `furatena/catalog/build_identity.py:17`, `furatena/cli/commands/content.py:63` |
| `FURA_DISTRIBUTION` | read | '', 'source' | `furatena/catalog/build_identity.py:49`, `furatena/catalog/docs_app.py:279` |
| `FURA_ENV` | read | None | `furatena/catalog/docs_app.py:244` |
| `FURA_FROZEN` | read | None | `furatena/cli/commands/serve.py:80` |
| `FURA_FROZEN_DIR` | read | '' | `furatena/cli/commands/serve.py:71` |
| `FURA_HTMX_PREVIEW` | read | '' | `furatena/catalog/vendor_paths.py:33` |
| `FURA_IMAGE_CHANNEL` | read | 'development' | `furatena/catalog/build_identity.py:52` |
| `FURA_IMAGE_DIGEST` | read | 'unknown' | `furatena/catalog/build_identity.py:53`, `furatena/catalog/content_deployment.py:152` |
| `FURA_IMAGE_VERSION` | read | 'development' | `furatena/catalog/build_identity.py:54` |
| `FURA_KEEP_ALIVE_TIMEOUT` | read | '5' | `furatena/catalog/docs_app.py:260` |
| `FURA_LANG` | read | '' | `furatena/catalog/i18n.py:112` |
| `FURA_MODE` | write | None | `furatena/cli/commands/serve.py:36`, `furatena/cli/commands/serve.py:38`, `furatena/cli/commands/serve.py:40` |
| `FURA_OPERATION_LEASE_SECONDS` | read | '3600' | `furatena/catalog/operation_lease.py:221` |
| `FURA_OPERATION_LOCK_TIMEOUT` | read | '30' | `furatena/catalog/operation_lease.py:216` |
| `FURA_PORT` | read, write | '8001', None | `furatena/catalog/dev_reload.py:149`, `furatena/cli/commands/serve.py:48`, `furatena/cli/commands/serve.py:95`, `furatena/cli/commands/stop.py:17` |
| `FURA_PREVIEW_AUTH_TOKEN` | read | '' | `furatena/catalog/preview_security.py:159` |
| `FURA_PREVIEW_ORIGIN` | read | '' | `furatena/catalog/preview_security.py:105` |
| `FURA_PREVIEW_PR_NUMBER` | read | '' | `furatena/catalog/preview_security.py:101` |
| `FURA_PREVIEW_REVIEW_URL` | read | '' | `furatena/catalog/preview_security.py:104` |
| `FURA_PREVIEW_SHA` | read | '' | `furatena/catalog/preview_security.py:102` |
| `FURA_PR_PREVIEW` | read | '' | `furatena/catalog/preview_security.py:100` |
| `FURA_RELOAD_SRC` | read | '' | `furatena/catalog/dev_reload.py:174` |
| `FURA_SERVER_WORKERS` | read | '' | `furatena/catalog/docs_app.py:277` |
| `FURA_SESSION_SECRET` | read | None | `furatena/catalog/docs_app.py:250` |
| `FURA_STATIC` | read, write | None | `furatena/catalog/static_export.py:328`, `furatena/catalog/static_export.py:336` |
| `FURA_STRUCTURED_LOGS` | read | '' | `furatena/catalog/observability.py:136` |
| `FURA_TELEMETRY` | read | 'none' | `furatena/catalog/observability.py:142` |
| `FURA_WORKERS` | read, write | '', None | `furatena/catalog/workers.py:20`, `furatena/cli/commands/freeze.py:26`, `furatena/cli/commands/serve.py:50` |

## Error and remediation examples

All JSON-capable commands return `ok`, `exit_code`, `summary`, `diagnostics`,
and `data`. Validation failures use exit code 2; configuration failures use 3;
source conflicts use 4. Each diagnostic includes a stable `rule_id` where available
and a `next_action` remediation.

```json
{
  "ok": false,
  "exit_code": 2,
  "summary": "check completed with 1 error(s)",
  "diagnostics": [{
    "severity": "error",
    "rule_id": "fura.content",
    "message": "broken internal link /missing/",
    "next_action": "Fix the target or update the link, then rerun fura check."
  }]
}
```

For mutation conflicts, rerun `fura author status`, review the new source revision,
and repeat the dry run. Do not retry with a stale revision or discard diagnostics.
