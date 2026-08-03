# Editions

This document governs versioned documentation in Furatena. It defines how a git-backed
mount discovers release editions, freezes immutable Content IR shards, composes them
into one catalog, and exposes them to browsers and agents. The design belongs to the
hydra platform saga (#343) and is the implementation contract for #344 and #345.

The central invariant is:

> Index once per release, compose forever.

A release tag identifies immutable source. Furatena therefore indexes that source once
into an immutable, renderer-independent IR shard. Templates, themes, rendering heads,
and static-export policy may change without rebuilding the shard.

## Scope and terminology

| Term | Meaning |
|------|---------|
| **Mount** | One source namespace in the composed catalog, such as `chirp`. |
| **Edition** | A mount-scoped documentation version. `latest` follows a branch; a release edition resolves to one tag and commit. |
| **Edition id** | The stable, URL-safe id after prefix stripping, such as `0.8.2`. Aliases are never edition ids. |
| **Edition shard** | The immutable DCP and Content IR records produced from one mount at one release commit. |
| **Edition context** | The requested mount and public edition used for routing, navigation, search, canonical URLs, and retrieval policy. |
| **Release node** | Graph metadata for one public edition, identified as `release:<mount>:<edition>`. |
| **Shared node** | One content-identical page record referenced by several release nodes through `available_in` edges. |

This design covers local discovery, indexing, composition, routing, lifecycle behavior,
cross-edition resolution, and discovery artifacts. Remote publication and 500-repository
operation are deliberately assigned to the federation RFC; see
[Federation boundary](#federation-boundary).

## Governing invariants

1. Public page identity remains `mount:edition:slug`, as defined by DCP. `mount` and
   `edition` are always explicit in graph identity even when the latest URL is
   unprefixed.
2. `latest` is the only moving edition. Every release edition resolves to exactly one
   tag and peeled commit SHA.
3. A release edition shard is never overwritten in place. A moved tag or a different
   fingerprint for an existing `(mount, edition)` is an integrity failure, not an
   incremental update.
4. Edition shards contain semantic records, not rendered HTML. Presentation output is
   a replaceable cache derived from `(shard fingerprint, renderer fingerprint)`.
5. Alias routes such as `/latest/` and `/stable/` are routing metadata. They never
   create additional edition ids or shards; the reserved moving id `latest` remains
   the identity of head nodes.
6. Lifecycle and alias changes are composition metadata. They do not mutate or
   invalidate release IR.
7. An edition context cannot cross mount, edition, access-policy, or lifecycle
   boundaries implicitly. Any broader query requires an explicit filter or opt-in.

## Mount policy and edition discovery (#349)

Git-backed mounts use Bengal's `GitPreviousConfig` vocabulary directly:

```yaml
mounts:
  - id: chirp
    source:
      provider: git
      repo: https://github.com/lbliii/chirp.git
      ref: main
    editions:
      source: tags
      count: 3
      pattern: "v*"
      strip_prefix: "v"
      sort: semver-desc
      include_prereleases: false
      aliases:
        latest: latest
        stable: latest
      overrides:
        "0.7.1":
          status: legacy
          end_of_life: 2027-01-31
```

`source`, `count`, `pattern`, `strip_prefix`, `sort`, and
`include_prereleases` have the same names, defaults, validation, and selection behavior
as Bengal. The mount's configured source ref becomes `latest`; no second latest-ref
vocabulary is introduced.

`aliases` maps URL-safe public segments to `latest` or a normalized release id. When
omitted, `/latest/` and `/stable/` both redirect to the unprefixed latest edition;
mounts may pin `stable` to a discovered release as shown above. An alias never becomes
an edition id and a configured target that is not available for the resolved mount is
not routable.

Discovery runs as part of source sync:

1. Resolve the mount's head ref and all candidate tags through the `SourceProvider`.
   Annotated tags are peeled to commits.
2. Match `pattern`, strip `strip_prefix`, reject an empty or non-URL-safe edition id,
   and fail on two refs that normalize to the same id.
3. For `semver-desc`, reject non-semver candidates, sort stable releases ahead of
   prereleases at the same version, and omit prereleases unless explicitly included.
   `name-desc` remains the Bengal-compatible lexical alternative.
4. Select `count` release editions, then add `latest` from the head ref. `count: 0`
   means latest only.
5. Record source provider, repository, ref, peeled commit SHA, discovery time, and
   policy result per edition in source-sync state and `channels.json`.
6. Materialize changed discovery state with the existing lease and atomic-swap
   discipline. Readers see either the old complete set or the new complete set.

When a new qualifying tag arrives, it enters the selected set. An edition that falls
past `count` becomes `eol` by default and leaves the active set; an explicit lifecycle
override may keep it active. Local artifacts are not destructively deleted during
discovery. Long-term retention and garbage collection belong to the federation RFC.

`fura check` must diagnose an unsupported source or sort, a negative count, an invalid
glob, a normalization collision, an invalid override id, and a missing git source with
the mount id and corrective action.

## Immutable shard model (#350)

The local frozen layout is one directory per public edition:

```text
frozen/
  mounts/
    <mount>/
      <edition>/
        catalog.json
        content/
        ast/
        fingerprint.json
```

The names of internal sidecars may evolve, but the directory boundary and logical
contract are fixed. A release directory contains normalized DCP records, Content IR,
optional native AST, and the source provenance required to verify the result. Rendered
HTML, theme assets, templates, and renderer caches live outside this immutable shard.

The shard fingerprint covers:

- mount id, public edition id, source provider/repository/ref, and peeled commit SHA;
- normalized page and graph records, excluding volatile timestamps;
- source and mount configuration that changes semantic indexing;
- content-adapter/parser contract versions; and
- the DCP/IR schema version.

It excludes theme configuration, templates, rendering heads, HTML, static-export
layout, aliases, lifecycle status, observation timestamps, and deployment metadata.
Consequently a theme or renderer change leaves every release fingerprint unchanged.
An IR schema bump is deliberate invalidation: the old shard remains readable within
the supported compatibility window while a new fingerprinted representation is built.

Freeze behavior is:

- `latest` compares source and semantic fingerprints and may re-index after head sync.
- A release edition with a verified matching fingerprint is a no-op.
- A missing release shard is built in a temporary directory, verified, then atomically
  renamed into place.
- A release directory whose recorded ref or fingerprint disagrees with discovery fails
  closed and remains available as last-known-good evidence.
- Rendered responses may be regenerated lazily and cached by shard plus renderer
  fingerprint without mutating the shard.

These rules make steady-state freeze cost proportional to changed moving heads, not to
the total number of retained editions.

## Identity, release metadata, and graph edges

An ordinary edition page uses `mount:edition:slug`, for example:

```text
chirp:latest:docs/guide
chirp:0.8.2:docs/guide
```

Every public edition also has a `release:<mount>:<edition>` graph node. Release nodes
carry the edition label, source ref and commit, release date, end-of-life date, lifecycle
status, aliases, shard fingerprint, and mount. These are additive graph-node metadata;
they do not change page identity.

Furatena emits:

- `available_in` from every page record to each release node in which it is present;
- `supersedes` from a newer release node to the prior release node; and
- page-level `supersedes` when a page moved or was replaced and same-slug matching is
  insufficient.

This uses existing DCP edge kinds. No edition-specific edge taxonomy is added.

### Shared-content deduplication

Identical normalized Content IR and routing metadata must not be copied into every
edition shard. Freeze computes a semantic content digest per `(mount, slug)` and
canonicalizes identical records to one internal shared node. Its internal edition id is
`shared-<digest>` and therefore still obeys `mount:edition:slug`; it is never exposed as
a public edition or URL prefix. `available_in` edges connect that node to every public
release that uses it.

The composition index maps `(mount, requested edition, slug)` to the canonical shared
node, while the request retains its public edition context for URLs, banners, policy,
and response metadata. If the page differs between releases, each distinct digest is a
separate node. This permits more than one historical variant of the same slug without
making shared content mutable.

## URL and request-context contract (#351)

The edition prefix precedes the mount's normal URL path:

| Context | Canonical URL |
|---------|---------------|
| Latest | `/docs/guide/` |
| Release `0.8` | `/v0.8/docs/guide/` |
| `latest` alias | `/latest/docs/guide/` → 301 to `/docs/guide/` |
| `stable` alias | `/stable/docs/guide/` → 301 to the configured stable edition |

The public prefix is `v<edition>` unless the normalized id already starts with `v`.
The stored edition id remains the normalized id; URL decoration is not identity.
Mounts with their own URL prefix follow the same rule: the edition segment is inserted
before that prefix. Static export mirrors the live layout exactly.

Routing resolves an immutable edition context before page lookup. That context scopes
page resolution, navigation, backlinks, search defaults, semantic retrieval, catalog
queries, `llms.txt`, and sitemap output. Agent endpoints accept `edition=<id>`; if a
path prefix and query parameter disagree, the request fails with a clear 400 rather than
mixing contexts.

Canonical and Open Graph URLs always name the resolved canonical edition. Alias routes
redirect and are never indexed. Latest and each release have edition-scoped discovery
artifacts at their canonical prefix. Cross-edition links must be explicit; ordinary
relative links stay in the current edition.

## Lifecycle semantics and retrieval (#352)

Furatena adopts Bengal's lifecycle vocabulary and fields:

| Status | Default behavior |
|--------|------------------|
| `current` | Moving head; unprefixed canonical URLs; normal retrieval weight. |
| `legacy` | Supported prior release; directly routable and searchable with a modest recency penalty. |
| `deprecated` | Routable with a warning; excluded from broad retrieval unless explicitly requested or no supported result exists. |
| `preview` | Prerelease; direct URLs are routable, while broad search and retrieval include it only when preview results are requested. It cannot receive the `stable` alias. |
| `eol` | Direct URLs may remain for archival policy, but default search, semantic retrieval, MCP, and cross-shard fan-out never return it without opt-in. |

Policy defaults are `latest=current`, selected stable tags=`legacy`, included
prereleases=`preview`, and an edition aged out of `count`=`eol`. Per-edition overrides
may set `status`, `release_date`, `end_of_life`, and banner content. Invalid transitions
or a `stable` alias targeting `preview`, `deprecated`, or `eol` fail validation.

Lifecycle lives on release/namespace graph metadata so it can change without rewriting
IR. Browser banners render on every non-current edition and link to the best current
equivalent. Catalog, search, semantic, diff, MCP, `llms.txt`, and `channels.json`
responses carry the resolved edition and status. Explicit `edition=<id>` is sufficient
to retrieve that edition except for EOL, which additionally requires an EOL opt-in;
broad queries can use status filters.

## Cross-edition resolution and switcher (#353)

The switcher resolves within the current mount and never guesses across mounts:

1. Look up the same logical slug in the target edition's composition index.
2. Follow an explicit page-level `supersedes` relationship when a move or replacement
   was declared.
3. Walk the slug's ancestors in the target edition, nearest first.
4. Fall back to the target edition's mount landing page.

Only step 1 is a direct hit. Steps 2–4 show a notice explaining the fallback, and the
runtime records direct-hit and fallback-reason metrics for the pilot. If lifecycle
policy disallows the target, the switcher omits it rather than producing a dead link.

An htmx edition switch requests the target fragment in the new context, updates all
edition-dependent out-of-band regions, and pushes the canonical URL. It does not
require a full page reload. Navigation and related-content lookups use the target
context from the first response onward.

## `versions.json` and channel discovery (#354)

Each versioned mount exposes a Mike-compatible array at
`/versions/mounts/<mount-id>.json` (route pattern
`GET /versions/mounts/{mount_id}`), containing the four Bengal/Mike fields without
changing their meaning. `latest` is first and titled `Latest`; release titles preserve
their normalized edition id. The configured alias-to-edition map is inverted into each
entry's sorted `aliases` array:

```json
[
  {
    "version": "0.8.2",
    "title": "0.8.2",
    "aliases": ["stable"],
    "url_prefix": "/v0.8.2"
  }
]
```

`url_prefix` is root-relative and composes the deployment base path, canonical edition
segment, and mount prefix in that order. For example, release `0.8.2` on mount `/docs`
under deployment base `/project` uses `/project/v0.8.2/docs`; the same mount's moving
latest edition uses `/project/docs`.

The hub-level `/versions.json` is a keyed map so mount identity cannot collide with
another mount or with a per-mount Mike array:

```json
{
  "schema_version": 1,
  "mounts": {
    "chirp": [
      {
        "version": "0.8.2",
        "title": "0.8.2",
        "aliases": ["stable"],
        "url_prefix": "/v0.8.2"
      }
    ]
  }
}
```

The per-mount artifact is the interoperability surface; the keyed hub artifact is the
multi-repository extension. Both are served live, copied unchanged by static export,
and listed in the agent channel of `channels.json`. Lifecycle and provenance remain in
the graph and channel manifest rather than adding incompatible requirements to Mike's
entry shape.

## Content IR diff contract (#355)

Diff selects two public edition contexts in one mount, resolves a slug in each, and
compares normalized Content IR rather than HTML. Page results identify sections,
headings, directives, and links that were added, removed, moved, or changed. Mount
rollups classify pages as added, removed, changed, or unchanged by logical slug.

The HTTP and MCP surfaces share one stable result schema and the same lifecycle/access
checks. Shared nodes with the same content digest are an immediate unchanged result.
Diff never materializes presentation output and never mutates either shard.

## Bengal prior art and adoption decisions

Furatena credits Bengal's complete versioning subsystem and intentionally keeps one
versioning vocabulary across the product family.

| Bengal module or contract | Decision | Furatena treatment and rationale |
|---------------------------|----------|----------------------------------|
| `bengal/core/version.py` — `GitPreviousConfig` fields and defaults | **Adopt verbatim** | Use `source`, `count`, `pattern`, `strip_prefix`, `sort`, and `include_prereleases` with the same defaults and validation so configuration is portable. |
| `bengal/core/version.py` — statuses `current`, `legacy`, `deprecated`, `preview`, `eol`, plus dates and banners | **Adopt verbatim** | Preserve names and meanings; expose them as graph/namespace metadata so browser and agent surfaces share policy. |
| `bengal/core/version.py` — folder mode and mutable `Version` build model | **Diverge** | Furatena versions git-backed mounts and composes immutable IR shards; it does not copy `_versions/` trees or bake presentation per version. |
| `bengal/content/versioning/git_adapter.py` — tag matching, prefix stripping, semver ordering, prerelease filtering | **Adopt verbatim** | Maintain behavioral parity, including stable-before-prerelease ordering and actionable rejection of non-semver tags under `semver-desc`. |
| `bengal/content/versioning/git_adapter.py` — ref/commit tracking | **Adapt** | Resolve through Furatena's `SourceProvider`, persist provenance in source-sync state, and reuse leases plus atomic swaps. |
| `bengal/content/versioning/git_adapter.py` — cached worktrees and parallel HTML builds | **Diverge** | A temporary checkout may be an indexing mechanism, but worktrees are not artifacts and renderer changes never rebuild release shards. |
| `bengal/content/versioning/artifacts.py` — `versions.json` entry shape | **Adopt verbatim** | Per-mount arrays keep `version`, `title`, `aliases`, and `url_prefix` exactly; this is the Mike-compatible surface. |
| `bengal/content/versioning/artifacts.py` — one-site artifact | **Adapt** | Add a hub keyed by mount while retaining exact per-mount arrays for existing tooling. |
| `bengal/content/versioning/resolver.py` — latest unprefixed, older versions prefixed, alias and logical-slug resolution | **Adopt verbatim** | Preserve public URL and switcher expectations. |
| `bengal/content/versioning/resolver.py` — physical `_versions` paths and shared-directory injection | **Diverge** | Resolve through the catalog composition index; deduplicate normalized Content IR with `available_in` edges instead of copying files or pages. |
| `bengal/content/versioning/resolver.py` — cross-version link intent | **Adapt** | Resolve `mount + edition + slug` through DCP identity, access/lifecycle policy, and `supersedes` fallbacks rather than constructing an output path directly. |

## Implementation map

| Issue | Governing sections |
|-------|--------------------|
| #349 | [Mount policy and edition discovery](#mount-policy-and-edition-discovery-349) |
| #350 | [Immutable shard model](#immutable-shard-model-350) |
| #351 | [URL and request-context contract](#url-and-request-context-contract-351) |
| #352 | [Lifecycle semantics and retrieval](#lifecycle-semantics-and-retrieval-352) |
| #353 | [Identity, release metadata, and graph edges](#identity-release-metadata-and-graph-edges), [Cross-edition resolution and switcher](#cross-edition-resolution-and-switcher-353) |
| #354 | [`versions.json` and channel discovery](#versionsjson-and-channel-discovery-354) |
| #355 | [Content IR diff contract](#content-ir-diff-contract-355) |

No design question in #349–#355 is left implicit. Implementations may refine internal
types and filenames while preserving these observable contracts.

## Federation boundary

The federation RFC under #347/#359 owns decisions that are not required for local
edition implementation:

- the published shard archive layout, compression, signatures, and transport integrity;
- the hub manifest schema and artifact URL/authentication model;
- DCP reader/writer compatibility windows for remote shards;
- remote retention, garbage collection, rollback pins, and object-store naming;
- remote-shard failure isolation and last-known-good distribution;
- hot/warm/cold residency, eviction, and memory budgets;
- per-shard search-index packaging, rank merge, and global IDF refresh; and
- incremental cross-shard link reconciliation and sharded sitemap/`llms.txt` indexes.

Those choices may wrap or transport the local logical shard defined here, but they must
not weaken release immutability, change `mount:edition:slug`, or make renderer changes
invalidate edition fingerprints.
