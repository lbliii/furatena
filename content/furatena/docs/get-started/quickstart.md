---
title: Quickstart
description: Run fura serve and edit your first documentation page
draft: false
weight: 20
lang: en
type: doc
tags: [quickstart, fura, serve]
keywords: [fura serve, author mode, htmx]
category: onboarding
---

Run the default Furatena app and edit markdown with live author reload.

## Start the server

From the repository root:

```bash
uv run fura serve
```

Or use the app wrapper:

```bash
./app/run
```

Open http://127.0.0.1:8001/

The default instance dogfoods this documentation corpus under `content/furatena/`.

## Edit a page

1. Open `content/furatena/docs/get-started/quickstart.md` in your editor
2. Change a paragraph and save
3. With the page open in the browser, Furatena swaps updated fragments via htmx — no manual refresh loop

## Useful commands

```bash
uv run fura check              # broken links + content lint + theme lint
uv run fura check --content-only
uv run fura freeze             # write catalog IR + HTML → app/frozen/
uv run fura export             # static HTML → app/public/
```

## Serve modes

| Mode | Command | Use when |
|------|---------|----------|
| Author (default) | `fura serve` | Editing content — live index + reload |
| Author forced | `fura serve --author` | Ignore frozen cache |
| Preview | `fura serve --preview` | Prod-like — frozen only |

## Next

→ [[docs/get-started/project-layout|Project layout]] — where config, content, and theme live.
