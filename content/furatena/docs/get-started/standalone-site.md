---
title: Standalone site
description: Start a Furatena docs app without copying this repository's app directory.
weight: 18
lang: en
type: doc
tags: [standalone, init, cli]
category: onboarding
---

# Standalone site

Use `fura init` when you want Furatena as the docs platform for another project.

```bash
mkdir docs-site
cd docs-site
fura init --name "Acme Docs"
fura check --content-only --warnings-as-errors
fura serve
```

The scaffold writes:

| Path | Purpose |
|------|---------|
| `docs.yaml` | Site, theme, view, and mount configuration |
| `mounts.yaml` | Content mount definitions |
| `content/` | Markdown corpus |
| `theme/` | Minimal shell, views, search page, and branding |

## Target another app root

Every command can target an app root explicitly:

```bash
fura --app-root ./docs-site serve
fura --app-root ./docs-site check --content-only
fura --app-root ./docs-site query --heading "Get started"
```

Use `--config path/to/docs.yaml` when the config file does not live at
`APP_ROOT/docs.yaml`.

## Why this matters

The dogfood repository keeps a rich `app/` for Furatena itself, but a product team should
not need that structure. A standalone app only needs configuration, content, and optional
theme overrides; the catalog runtime, directives, search, and agent exports come from the
installed package.

## Customize the theme

Inspect a template before editing it:

```bash
fura theme inspect views/doc.html
fura theme eject directives/callout.html
fura theme diff directives/callout.html
```

See [[docs/theming/customization-cli|Customization CLI]] for the full override workflow.
