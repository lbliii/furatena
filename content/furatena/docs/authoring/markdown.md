---
title: Markdown and front matter
description: Patitas markdown, YAML front matter, and page metadata
draft: false
weight: 10
lang: en
type: doc
tags: [markdown, front-matter, patitas]
category: authoring
---

Furatena corpora are **Patitas markdown** files with YAML front matter. The loader turns
each file into a catalog node with slug, URL, Content IR, and rendered HTML.

## File layout

```
content/furatena/
├── _index.md              → /
└── docs/
    ├── get-started/
    │   ├── _index.md      → /docs/get-started/
    │   └── quickstart.md  → /docs/get-started/quickstart/
    └── reference/
        └── cli.md         → /docs/reference/cli/
```

- **`_index.md`** — section or site root page
- **Folder structure** — becomes slug path and parent edges in the graph
- **`.md` extension** — indexed as `patitas-markdown`

## Common front matter

```yaml
---
title: Page title
description: One-line summary for search, OG tags, and doc lists
layout: doc          # view kind — doc, doc_list, home, collection, page
weight: 10           # sort order within a section (lower first)
draft: false         # omit from nav and search when true
lang: en
tags: [concepts, catalog]
keywords: [furatena, graph]
category: authoring  # optional grouping label
icon: book-open      # sidebar / card icon name
---
```

### View kind (`layout` or `kind`)

Selects which Kida view renders the page. See [[docs/concepts/views-and-shell|Views and shell]].

| Kind | Typical use |
|------|-------------|
| `doc` | Standard documentation page |
| `doc_list` | Section index (also inferred for section roots with children) |
| `home` | Site root marketing page |
| `collection` | Multi-node read-through pillar |
| `page` | Simple app-surface page |

Override the template explicitly when needed:

```yaml
view: views/page.html   # bypasses docs.yaml views map
```

### Cascade

Apply defaults to a subtree:

```yaml
cascade:
  type: doc
  layout: doc
```

Child pages inherit unset keys from the nearest ancestor `_index.md`.

## Links

**Wikilinks** — resolve against the federated catalog:

```markdown
[[docs/get-started/quickstart|Quickstart]]
[[docs/concepts/catalog-graph]]
```

**Markdown links** — internal paths validated by `fura check`:

```markdown
[CLI reference](/docs/reference/cli/)
```

**Cross-mount links** — include mount id when targeting another corpus:

```markdown
[Chirp docs](/chirp/docs/get-started/)
```

## Code blocks

Fenced blocks with optional titles and Rosettes highlighting:

````markdown
```python title="serve.py"
from furatena.catalog.docs_app import DocsApp
```
````

Use `::::{code-tabs}` for tabbed install snippets (see [[docs/authoring/directives|Directives]]).

## Plain text export

Each doc page exposes author markdown at `{url}index.txt` — useful for agents and diffing.

## Next

→ [[docs/authoring/directives|Directives]] — rich blocks inside prose.
