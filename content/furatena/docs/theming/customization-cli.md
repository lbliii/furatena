---
title: Customization CLI
description: Inspect, eject, and diff template or asset overrides
draft: false
weight: 25
lang: en
type: doc
tags: [theming, templates, overrides, cli]
category: theming
---

# Customization CLI

Furatena themes are inspectable. Before copying a template, ask the CLI where it comes
from and where a local override should live.

```bash
fura theme inspect views/doc.html
```

The output shows the resolution chain:

```text
project overrides  templates/
theme shadow       theme/templates/
theme root         theme/
framework          catalog/_templates/
```

First existing file wins.

## Eject one template

Copy the active upstream template into the local override path:

```bash
fura theme eject directives/callout.html
```

Framework partials and directive templates eject into `theme/templates/`:

```text
theme/templates/directives/callout.html
```

Views and shell templates eject into `theme/`:

```bash
fura theme eject views/doc.html
fura theme eject shell.html
```

Each ejected text file includes a short provenance comment with the source path and the
diff command.

## Diff an override

Compare a local override with the next upstream source:

```bash
fura theme diff directives/callout.html
```

Immediately after ejecting, this should report no differences. After local edits, the
command shows a unified diff against the upstream template.

## Assets

Branding and theme assets use the same workflow:

```bash
fura theme inspect assets/branding
fura theme eject assets/branding
```

Assets eject into `theme/assets/`.

## Eject everything

```bash
fura theme eject --all
```

Use this only when you want to own the full local theme. For most projects, eject one file
at a time so future upstream changes remain visible through `fura theme diff`.

## Validate after editing

Run checks after a template override:

```bash
fura check --content-only --warnings-as-errors
fura export --fresh
```

`fura check` validates registered view templates against Furatena's view contracts, and
`fura export --fresh` proves the override still renders in the static path.

## Related

- [[docs/theming/views-overrides|Views and overrides]]
- [[docs/theming/branding|Branding]]
- [[docs/reference/cli|CLI reference]]
