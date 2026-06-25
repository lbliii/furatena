---
title: Views and overrides
description: Template loader stack and sparse overrides
draft: false
weight: 20
lang: en
type: doc
tags: [templates, views, kida]
category: theming
---

Kida resolves templates **first match wins**:

```
1. templates/              ← project overrides (sparse)
2. theme/templates/        ← theme shadow dir (docs.yaml theme.templates)
3. theme/                  ← shell, views
4. catalog/_templates/     ← framework defaults
5. chirp-ui templates      ← component macros
```

## Override one file

To change sidebar markup without copying the whole theme:

```
app/templates/partials/docs_sidebar.html   # wins over framework default
```

Or shadow via theme pack path:

```
app/theme/templates/partials/docs_shell_nav.html
```

## Override a view

Per-slug in `docs.yaml`:

```yaml
overrides:
  docs/get-started/installation: views/landing.html
```

Or per-page front matter:

```yaml
view: views/page.html
```

## View kinds vs templates

Authors set **`layout:`** (view kind). Developers register templates in the **`views:`** map.
See [[docs/concepts/views-and-shell|Views and shell]].

## Directives

Directive HTML lives in `catalog/_templates/directives/`. Shadow individual directives under
`theme/templates/directives/` when markup changes are required.

## Lint

`fura check` validates Kida view templates against context contracts in `catalog/view_lint.py`.

## Related

- [VIEWS.md](https://github.com/lbliii/furatena/blob/main/docs/VIEWS.md)
