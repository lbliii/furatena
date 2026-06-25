# Document Catalog Protocol (DCP) v3

DCP is the interchange format for Furatena' **format-agnostic catalog graph**. It
describes indexed documentation pages, typed edges, and normalized content structure
without requiring consumers to know the source authoring format.

See also [DUAL_IR.md](DUAL_IR.md) for how Content IR and Presentation IR align at
runtime.

## Design principles

1. **Catalog graph is format-neutral** — nodes, edges, search, and retrieve work the
   same whether the source was markdown, RST, HTML, or generated autodoc.
2. **Content IR is the semantic API** — agents and linters query headings, links, and
   extensions; they do not grep raw source.
3. **Native AST is optional** — format-specific trees (Patitas JSON, etc.) are sidecars
   for incremental diff; not required for protocol consumers.
4. **v2 compatibility** — v3 records include legacy `body_md`, `source`, `ast_path`,
   and `content.directives` aliases during transition.

## Three tiers

### Tier 1 — Catalog graph (required)

| Field | Description |
|-------|-------------|
| `node_id` | `mount:edition:slug` |
| `url`, `slug` | Routing |
| `title`, `description`, `section`, `weight`, `tags` | Metadata |
| `source_path` | Relative path or synthetic id |
| `source_kind` | `filesystem` \| `generated` |
| `content_format` | Open string, e.g. `patitas-markdown`, `docutils-rst` |
| `mount`, `edition`, `section_root` | Federation |
| `lang`, `translation_key` | i18n (v3.1+) |
| `edges[]` | `parent`, `link`, `nav_next`, `nav_prev`, `tag`, `translation` |
| `namespaces[]` | Mount metadata |

### Tier 2 — Content IR (recommended)

Normalized semantics any adapter must populate:

```json
{
  "headings": [{"level": 2, "text": "Install", "anchor": "install", "line": 12}],
  "links": [{"href": "/docs/foo/", "text": "Foo", "line": 20}],
  "extensions": [{"name": "note", "options": {}, "line": 24}],
  "directives": [{"name": "note", "options": {}, "line": 24}]
}
```

Index-time derived fields (v3):

| Field | Description |
|-------|-------------|
| `body_text` | Plain text for search and LLM export |
| `sections[]` | `{id, heading, depth, text}` heading-bounded slices |
| `body_source` | Authoring source body |
| `body_md` | Legacy alias when source is markdown |

### Tier 3 — Native AST (optional)

```json
{
  "native_ast": {
    "format": "patitas-markdown",
    "path": "docs/get-started.json"
  },
  "ast_path": "docs/get-started.json"
}
```

## Registered content formats (initial)

| `content_format` | Adapter | Status |
|------------------|---------|--------|
| `patitas-markdown` | `PatitasMarkdownAdapter` | Shipped |
| `html` | `HtmlAdapter` | Shipped |
| `docutils-rst` | `RstAdapter` | Shipped (requires `docutils`) |
| `mdx` | `MdxAdapter` | Shipped (JSX lowered to extension blocks) |
| `autodoc-python` | Autodoc provider | Shipped (synthetic) |

## Mount configuration

```yaml
# mounts.yaml — mixed-format mount example
mounts:
  - id: chirp
    content_root: ../../content/chirp
    extensions: [".md", ".rst", ".html", ".mdx"]
    index_files: ["_index.md", "index.rst", "index.html"]
    format_map:
      ".md": patitas-markdown
      ".rst": docutils-rst
      ".html": html
      ".mdx": mdx
    default_format: patitas-markdown
```

## HTTP export

| Endpoint | Schema |
|----------|--------|
| `GET /catalog.json` | DCP v3 (default) |
| `GET /meta.json` | Compact page index |
| `GET /search.json` | Search index with `sections` |
| `GET /catalog/retrieve?id=` | Node + chunks + backlinks |

JSON Schema for v3 exports ships with the runtime at
``catalog/schemas/catalog-v3.schema.json`` (validated by ``fura check`` and freeze).

## Adapter contract

Ingestion follows: **scan → adapt → graph**.

```
FilesystemScanner / SourceProvider
        │
        ▼
   PageSource (content_format, meta, body)
        │
        ▼
   ContentAdapter.adapt() → AdaptedContent
        │
        ▼
   DocNode → graph edges → catalog.json
```

Implementing a new format requires:

1. Register `content_format` in mount `format_map`
2. Implement `ContentAdapter` (parse, adapt, invalidation_regions)
3. Populate Tier 1 + Tier 2 fields on `DocNode`

**Optional dependencies:**

| Format | Python package |
|--------|----------------|
| `.rst` | `docutils` (`pip install docutils`) |
| `.md`, `.mdx` | `patitas[syntax]` (already required for Furatena) |

No changes to graph export, search, or agent endpoints are required.
