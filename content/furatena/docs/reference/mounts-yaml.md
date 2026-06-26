---
title: mounts.yaml reference
description: Content mounts, URL prefixes, and federation
draft: false
weight: 30
lang: en
type: doc
tags: [mounts, federation, config]
category: reference
---

`app/mounts.yaml` defines **content mounts** — separate corpora federated into one
`CatalogRegistry`.

## Example (this repository)

```yaml
mounts:
  - id: furatena
    label: Furatena Documentation
    content_root: ../content/furatena
    default: true

  - id: shared
    label: Shared Reference
    content_root: content/shared
    url_prefix: /shared
    extensions: [".md", ".html"]
    format_map:
      ".md": patitas-markdown
      ".html": html
```

## Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Stable mount identifier on `DocNode.mount` |
| `label` | no | Human label for portal and breadcrumbs |
| `content_root` | yes | Path to markdown corpus (relative to `app/`) |
| `default` | no | Exactly one mount should be `default: true` — serves `/` |
| `url_prefix` | no | URL prefix for non-default mounts (e.g. `/shared`) |
| `extensions` | no | File extensions to index (default `.md`) |
| `format_map` | no | Map extension → content adapter id |

## URL routing

| Mount | Example URL | Example slug |
|-------|-------------|--------------|
| Default (`furatena`) | `/docs/concepts/` | `docs/concepts` |
| Shared | `/shared/reference/foo/` | `reference/foo` |

Prefixed mounts register catch-all routes at `{url_prefix}/` and `{url_prefix}/{slug:path}`.

## Cross-mount links

Wikilink syntax with mount id:

```markdown
[[shared:reference/reference-inventories|Reference inventories]]
```

Or use absolute paths: `/shared/reference/reference-inventories/`

`fura check` resolves internal links across mounts, including prefix inference for legacy
`/docs/…` links inside prefixed corpora.

## Portal

`/portal/` lists all mounts with labels, page counts, and entry URLs.

## Freeze federation

`fura freeze` writes per-mount shards under `frozen/mounts/<id>/` plus a top-level
`registry.json` describing the federated graph.

## Related

- [[docs/concepts/federation|Federation]]
- [[docs/reference/docs-yaml|docs.yaml reference]]
