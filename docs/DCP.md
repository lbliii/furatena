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

## Version policy

DCP follows a conservative compatibility model for external consumers:

| Change type | Policy |
|-------------|--------|
| Add optional top-level fields | Additive; allowed in the current major schema |
| Add optional page, edge, namespace, inventory, or content-IR fields | Additive; allowed in the current major schema |
| Add a new edge kind | Additive only when consumers can ignore unknown kinds; otherwise defer to a new major schema |
| Remove, rename, or change the type of a required field | Breaking; requires a new major schema |
| Change node id, URL, slug, mount, edition, or edge source/target semantics | Breaking; requires a new major schema |
| Make an optional field required | Breaking unless it is emitted for every supported frozen/exported catalog in a prior release |
| Tighten validation beyond what existing supported fixtures satisfy | Breaking; requires fixture migration and a new major schema |

The runtime currently validates DCP v2 and v3 sample exports. `fura check` always
validates the live merged catalog against the current schema and can also validate
compatibility fixtures or external sample exports:

```bash
fura check --content-only --dcp-fixtures
fura check --content-only --dcp-file path/to/catalog.json
```

Bundled compatibility fixtures live under `furatena.catalog/fixtures/dcp/`. Every
supported version covers edges, Content IR, inventories, and namespaces. DCP v3
fixtures additionally cover typed non-page `graph_nodes` such as API schemas.

## Three tiers

### Tier 1 — Catalog graph (required)

| Field | Description |
|-------|-------------|
| `node_id` | `mount:edition:slug` |
| `url`, `slug` | Routing |
| `title`, `description`, `section`, `weight`, `tags` | Metadata |
| `source_path` | Relative path or synthetic id |
| `source_kind` | `filesystem` \| `generated` |
| `source_provider`, `source_repo`, `source_ref` | Source provenance for impact reports |
| `generated_from` | Source/API/spec identifier that produced the node, when generated |
| `owner`, `team`, `tenant`, `site` | Ownership and tenancy grouping keys |
| `output_channel`, `last_indexed_at` | Export channel and index timestamp for stale analysis |
| `provenance` | Normalized provenance object with provider, repo, ref, path, owner/team, mount, edition, channel, and timestamp |
| `content_format` | Open string, e.g. `patitas-markdown`, `docutils-rst` |
| `api_operation` | Operation projection with id, method, path, summary, tags, schemas, examples, auth, environments, and source spec |
| `mount`, `edition`, `section_root` | Federation |
| `lang`, `translation_key` | i18n (v3.1+) |
| `edges[]` | Typed semantic relationships (see taxonomy below) |
| `graph_nodes[]` | Typed non-page graph targets such as API schemas, examples, auth schemes, environments, tags, responses, and operation ids |
| `namespaces[]` | Mount metadata |

### Edge taxonomy

| Kind | Typical source → target | Purpose |
|------|-------------------------|---------|
| `parent` | page → page | Hierarchical section membership |
| `link` | page → page | Resolved prose/content link |
| `nav_next`, `nav_prev` | page → page | Ordered navigation |
| `tag` | page → `tag:name` | Faceting and topic grouping |
| `translation` | localized page → anchor page | i18n sibling grouping |
| `explains` | prose page → `api:*`, `cli:*`, page, or `ref:*` | Conceptual documentation coverage |
| `implements` | page/API node → `schema:*`, `cli:*`, `sdk:*`, page, or `ref:*` | Implementation relationship |
| `api_tag` | API operation/page → `api-tag:name` | API tag grouping |
| `api_schema` | API operation/page → `schema:name` | Request/response schema dependency |
| `api_request_body` | API operation/page → `request-body:name` | Request payload model |
| `api_response` | API operation/page → `response:code-or-name` | Response model/status grouping |
| `api_example` | API operation/page → `example:name` | Runnable or documented API example |
| `api_auth` | API operation/page → `auth:scheme` | Required auth scheme |
| `api_environment` | API operation/page → `environment:name` | Available or required API environment |
| `generated_from` | generated output page → `source:*`, `api:*`, or page | Source-to-output provenance |
| `supersedes` | release/change page → older page/change | Replacement history |
| `breaks` | release/change page → API/schema/SDK target | Breaking-change impact |
| `available_in` | page/API/SDK node → `release:*` or channel id | Version availability |
| `owned_by` | page → `owner:team` | Routing findings to teams |
| `requires` | page/API/SDK node → dependency target | Dependency and prerequisite analysis |
| `validates` | test/check/report page → target | Evidence that a target is covered |

Front matter can add semantic edges with keys matching the edge names, for example
`implements: api:get-user`, `requires: /docs/auth/`, `generated_from: specs/openapi.yaml`,
`api_schemas: [User, Error]`, `api_auth: oauth2`, `api_environments: [prod, sandbox]`,
or `owner: docs-platform`. Existing links, tags, nav order, parents, and translations
continue to map into the same graph automatically.

### Graph node records

`graph_nodes[]` is an additive inventory for typed graph targets that are not
normal catalog pages. It lets static/headless consumers inspect API graph entities
without parsing edge target prefixes.

```json
{
  "id": "schema:User",
  "kind": "api_schema",
  "label": "User",
  "mount": "furatena",
  "edition": "latest"
}
```

Supported API graph node kinds are `api_operation`, `api_tag`, `api_schema`,
`api_request_body`, `api_response`, `api_example`, `api_auth`, and
`api_environment`.

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
| `GET /catalog/query.json` | Filtered DCP graph projection |
| `GET /graph/query.json` | Alias for filtered graph consumers |
| `GET /meta.json` | Compact page index with static impact-report provenance |
| `GET /search.json` | Search index with `sections` |
| `GET /catalog/retrieve?id=` | Node + chunks + backlinks |

`/catalog/query.json` and `/graph/query.json` accept these filters:

| Query parameter | Description |
|-----------------|-------------|
| `mount` | Match page mount id |
| `tag` | Match a page tag |
| `format` | Match `content_format`, `source`, or `source_kind` |
| `owner` / `team` | Match page ownership metadata or provenance |
| `locale` / `lang` | Match page language |
| `edge_kind` / `edge` / `kind` / `link_edge` | Match graph edge kind such as `link`, `requires`, or `owned_by` |
| `source` / `from` / `linked_from` | Match an edge source by node id, slug, or URL |
| `target` / `to` / `linked_to` | Match an edge target by node id, slug, URL, or external target id |
| `include_private=1` | Author-mode only; include private and draft nodes |

The response is DCP-shaped and contains `schema_version`, `channel`, `query`,
`page_count`, `edge_count`, `pages`, `edges`, `graph_nodes`, and `namespaces`. Page
filters narrow the source page set. Edge filters then return the matching graph
neighborhood so a head or agent can traverse relationships without downloading the
full catalog.

`/meta.json` keeps the page index compact but preserves the same impact-routing
provenance needed by static/offline consumers: `source_path`, `source_provider`,
`source_repo`, `source_ref`, `generated_from`, `owner`, `team`, `tenant`, `site`,
`mount`, `edition`, `output_channel`, `last_indexed_at`, and the normalized
`provenance` object. API operation pages also include the compact `api_operation`
projection so headless agents can inspect method/path/schema/example/auth metadata
without scraping rendered HTML.

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
