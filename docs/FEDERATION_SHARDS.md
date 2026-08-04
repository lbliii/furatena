# RFC: published shards and federation manifests

Status: accepted executable contract for artifact schema v1 and hub schema v1.

This RFC extends DCP delivery without changing DCP page identity or Content IR.
A publisher turns one immutable local edition shard plus its derived search,
semantic, Content IR fragment, and presentation HTML objects into a public
content-addressed object set. A hub can then discover, verify, and compose that
shard without indexing its source or re-rendering it.

The normative schemas are:

- `schemas/federation/v1/published-shard.schema.json`
- `schemas/federation/v1/hub-manifest.schema.json`
- `schemas/federation/v1/channels-extension.schema.json`

`furatena.catalog.federation_artifacts` is the reference validator. The golden
pair under `tests/fixtures/federation/v1/` is the compatibility oracle.

## Evidence and constraints

The multi-mount pilot in #346 measured representative edition contexts and
showed that bounded, independently fetched objects were practical. The contract
uses conservative caps: 64 MiB per encoded or general decoded object, 4 MiB per
Content IR fragment, 16 MiB per decoded presentation, 100,000 inventory entries,
4 MiB per hub manifest, and 10,000 shard identities per hub. For `N` public
nodes, the object inventory is exactly `2N + 3`. These are validation limits,
not memory allocation targets: readers process one object at a time.

## Published object set

The artifact is an immutable object set rather than one compressed archive:

```text
shards/sha256/<published-fingerprint>/
  manifest.json
  objects/sha256/<encoded-sha256>.json
  objects/sha256/<encoded-sha256>.json.gz
  objects/sha256/<encoded-sha256>.json.zst
  objects/sha256/<encoded-sha256>.html
  objects/sha256/<encoded-sha256>.html.zst
```

`manifest.json` maps logical paths to content-addressed objects. It requires
exactly one DCP catalog, search index, and semantic index, plus exactly one
Content IR fragment and one presentation HTML object for every public catalog
node. Separate objects permit range-friendly storage, lazy fetch, cache reuse,
and independent integrity checks; a reader never needs to download or
decompress the whole shard.

The published fingerprint is SHA-256 over canonical JSON containing:

- mount and edition identity;
- artifact, DCP, Content IR, and adapter contract versions;
- the immutable local source-shard fingerprint; and
- the complete ordered inventory digest.

It is deliberately not the local source-shard fingerprint. Derived search or
semantic output can change while source Content IR does not; including the
inventory prevents two different published object sets from sharing an object
storage address.

Every inventory entry records `logical_path`, semantic `role`, relative
`object_url`, media type, content encoding, encoded and decoded byte sizes, and
SHA-256 for both encoded and decoded bytes. Fragment and presentation entries
also carry their exact `node_id`; singleton catalog/search/semantic entries are
forbidden from carrying one. Their O(1) logical paths are respectively
`fragments/nodes/<sha256(node_id)>.json` and
`presentations/nodes/<sha256(node_id)>.html`. The inventory is complete, sorted,
and duplicate-free by logical path and node mapping. Byte-identical logical
objects may share one content-addressed `object_url`; this preserves the 1:1
node mapping while deduplicating physical storage. Unlisted objects, missing
objects, digest mismatch, decompression past the declared bound, malformed
JSON, DCP schema failure, or mount/edition/node identity mismatch rejects the
artifact.

The object filename is the encoded-byte SHA-256 and its suffix must agree with
the declared encoding. Logical paths and source paths are non-empty relative
POSIX paths: absolute paths, backslashes, dot segments, and traversal are
invalid. Catalog, fragment, search, and semantic node inventories must close
over the same public node-id set. That closure prevents an otherwise valid
index object from smuggling a private, protected, draft, or archived record
that is absent from the public catalog.

Presentation HTML is render output, not semantic input. Its node-id set must
equal both the fragment and public catalog node-id sets, but its text, element
tree, and styling never enter graph, search, MCP, DCP, or agent semantics. This
is the dual-IR exception for a surface that requires faithful browser
presentation: Content IR remains the semantic source of truth, while the
published HTML IR is an inert, replaceable render artifact.

The reference validator decodes presentation bytes through the 16 MiB bound,
requires UTF-8, and parses the complete body with an HTML parser. It rejects
executable, document-head, form, active media/frame, and stylesheet-loading elements;
active SVG elements such as `foreignObject`, animation, external image, and
`use`; style, event, source-set, submission, autoplay, Alpine, htmx, Vue,
Turbo, and known host-action attributes; and scheme-relative URLs. URI-bearing
attributes use an allowlist: relative references, HTTP(S), `mailto`, and
base64 raster data images whose decoded signature matches their declared PNG,
JPEG, GIF, WebP, or AVIF media type. Control characters and entities cannot
hide a scheme, and the validator never case-normalizes the case-sensitive
base64 payload. These checks are parser-based rather than regex security
filtering. A consumer still inserts presentation only into the designated
inert body boundary; it never executes or reinterprets it as source.

Objects at or above the declared threshold use deterministic zstd by default.
Identity encoding remains valid for small JSON, while gzip is an explicit
compatibility encoding. Publisher settings must fix compressor implementation,
level, dictionary, and framing; timestamps and host metadata are forbidden.
Producers sort every map key and unordered list before canonical serialization.

## Provenance, lifecycle, and retention

The manifest carries the canonical repository URL, source ref, peeled commit
SHA, source path, local shard fingerprint, lifecycle status, optional release
and end-of-life dates, and contract versions. Artifact v1 is public-only. It
does not admit draft, private, protected, archived, or credential-bearing URLs.
A future private federation contract must define authenticated fetch and cache
partitioning instead of weakening v1.

All addressed objects are immutable. A release artifact is retained while any
hub advertises or pins it and for at least its declared minimum retention.
Superseded `latest` artifacts remain immutable and are retained for at least 30
days and while rollback-pinned. Garbage collection first removes hub
references, waits through the retention window, proves no pins remain, and only
then deletes unreachable objects. Reusing or overwriting an address is always
an integrity failure.

The hub repeats the source path and local shard fingerprint, and the reference
validator requires its contracts, provenance, audience, published fingerprint,
and manifest digest to equal the shard manifest. Lifecycle is deliberately hub
composition metadata: a signed hub generation may move an immutable release
from `legacy` to `deprecated` or `eol` without rewriting its object set.

## Integrity, signatures, and attestations

SHA-256 proves transport integrity, not publisher authority. A shard manifest
must reference at least one detached Sigstore bundle or DSSE signature and one
provenance attestation. Their subject is the published fingerprint. The hub
anchors the canonical shard-manifest digest and byte bound.

The hub has its own canonical payload digest over contracts, shard records,
channels, and discovery policy. Its detached signature subject is that digest.
The reference validator proves shapes, digests, inventory closure, and subject
binding. The remote reader additionally requires an injected cryptographic
verifier to approve configured issuer and workload identities before making a
shard readable. Missing, expired, malformed, untrusted, or mismatched
verification material fails closed and preserves only a previously verified
last-known-good generation.

## Hub manifest and channels discovery

`shards` is an object keyed by `mount:edition`, giving O(1) identity lookup.
Each value repeats the mount and edition and provides:

- published fingerprint and immutable `artifact_url`;
- lifecycle and public audience;
- artifact manifest SHA-256 and maximum byte size;
- artifact, DCP, and Content IR versions;
- repository/ref/commit provenance; and
- moving or release retention state and pin status.

`channels` maps each mount to `latest`, `stable`, and its ordered shard
identities. Every identity must exist in `shards` and stay in the same mount.
`latest` must be `current`; `stable` cannot be `preview` or `eol`.

`channels.json` gains one optional `remote_federation` discovery record with
the hub manifest URL, its expected digest from the trusted publication plane,
refresh interval, manifest/shard bounds, and the fixed failure mode
`last-known-good-or-unavailable`. Existing local `sources[].editions` remains
authoritative for locally indexed mounts. A mount cannot silently merge local
and remote authority for the same `mount:edition` identity.

## Residency, eviction, and derived global state

The contract resolves the local-edition RFC's residency questions without
selecting the later routing or ranking implementation:

- **Cold** means only the immutable origin object set and signed manifests are
  authoritative. Cold objects are fetched by content address.
- **Warm** means verified encoded objects are present in a disk cache keyed by
  encoded digest. Cache eviction is LRU within an operator budget and is not
  artifact garbage collection; it cannot remove origin retention pins.
- **Hot** means a verified hub generation, shard manifest, catalog identity
  map, and the explicitly selected per-shard indexes are active. Readers decode
  at most one bounded semantic or presentation object at a time and do not hold
  a complete shard archive merely because its manifest is hot.

Eviction never discards the selected last-known-good manifest, verification
receipt, or rollback pin. The 64 MiB object and 4 MiB fragment limits are hard
reader bounds; deployment-specific cache and aggregate-memory budgets may be
lower and must fail one shard closed rather than evicting unrelated active
identities.

Search and semantic indexes are complete shard-local objects. They contain
only the catalog's public node-id set and are fingerprinted with the artifact.
Cross-shard rank merging and global IDF refresh are derived hub state owned by
later work; neither can rewrite an upstream artifact. Likewise, outbound DCP
edges remain in their publishing shard. Cross-shard reconciliation and global
sitemap or `llms.txt` indexes are replaceable derived indexes, never additions
to the signed object inventory.

## Reader algorithm and failure isolation

Readers perform this bounded sequence:

1. Fetch `channels.json`, then the hub manifest within the 4 MiB limit.
2. Validate schema, canonical payload digest, signature, channel closure, and
   supported contract window before exposing any new identity.
3. Resolve `mount:edition` directly in `shards`.
4. Fetch the shard manifest within its advertised bound; verify its canonical
   digest against the hub, then validate its signature and attestation.
5. Validate the full inventory and published fingerprint.
6. Fetch only required objects. Verify encoded size/digest, decompress through
   a bounded stream, verify decoded size/digest, then validate DCP or typed
   index identity before cache promotion.
7. Atomically promote the verified generation. A failed shard is unavailable
   or stays on its previously verified generation; unrelated shards continue.

Catalog identity maps and paired per-node fragment/presentation maps provide
O(1) semantic and browser lookup. Search
rank merging, global IDF refresh, cross-shard link reconciliation, and global
sitemap/LLM indexes belong to the later #362+ routing, search, and reconciliation
work and cannot bypass these verification steps.

## Mounting a verified remote corpus

A hub application declares remote authority explicitly. Its `content_root` is
only a configuration placeholder and need not exist; the `remote-shard`
provider fails closed if any source scanner, adapter, or local indexer tries to
use it:

```yaml
mounts:
  - id: product
    label: Product documentation
    url_prefix: /product
    default: true
    content_root: corpus-does-not-exist
    source:
      provider: remote-shard
```

The host constructs `RemoteShardMountRegistry` with a `StrictHTTPSFetcher` and
an application-owned `RemoteCryptographicVerifier`, refreshes it, and injects
it through `DocsApp.from_paths(..., remote_shards=registry)`. Trust policy is
not inferred from manifest claims. The verifier must enforce the configured
issuer, workload identity, signature, attestation, and expiration policy for
both the hub and every shard before returning receipt evidence.

Activation stores immutable verified generations and receipts beneath the
registry state root, then atomically selects one last-known-good generation per
mount. `DocsApp.refresh_remote_shards()` refreshes those selections and
publishes the composed catalog plus derived graph caches as one lock-owned
generation. A failed mount retains only its own previous verified generation;
it does not block unrelated mounts. `rollback(mount, fingerprint)` pins a
retained generation until `unpin(mount)` explicitly resumes hub updates.

Catalog metadata is eager so routes, DCP, and navigation remain O(1) by node
identity. Presentation HTML and semantic indexes remain lazy. Concurrent first
reads of the same presentation coalesce behind a temporary per-node flight,
while reads for different nodes and mounts remain independent. Each in-flight
request owns the immutable generation from which it resolved the node, so an
upstream refresh cannot mix an old catalog record with new presentation bytes.
Each generation's presentation LRU is bounded to 256 entries and 64 MiB of
decoded sanitized HTML. The two caps bound both metadata-heavy small pages and
large bodies while holding at most four maximum-size v1 presentations.
Least-recently-used entries are evicted until both limits hold. A body larger
than the byte budget is still verified and served but is not cached; failed
fetches are never cached, and per-node flight records are removed on success or
failure.

## Compatibility and DCP migration plan

Existing executable policy proves DCP readers for versions 2 and 3. Artifact
v1 therefore advertises exactly that N-1 window. DCP v1 is rejected. Existing
policy does not prove an N-1 Content IR reader, so artifact v1 requires Content
IR v3 exactly; Content IR v2 is rejected rather than guessed compatible.

This is an additive DCP v3 delivery extension, not a DCP v4 change. The rollout
plan is:

1. Ship schemas, validators, fixtures, and optional discovery fields while all
   current local export paths remain unchanged.
2. #360 produces deterministic object sets and detached verification material
   from a verified local freeze.
3. #361 consumes remote hubs behind explicit configuration, using
   last-known-good isolation and the stated resource bounds.
4. Make remote discovery required only in a future major artifact contract.
5. Propose DCP v4 only if page/edge identity or required DCP field semantics
   change. Artifact or hub field breaks instead bump their own schema major.

Unknown optional DCP fields remain additive under DCP policy. Unknown artifact,
hub, or discovery fields are rejected in v1 because all three schemas are strict
and independently versioned.

## Security and migration failures

| Failure | Required behavior |
|---|---|
| HTTP, credentialed, query-bearing, or traversal URL | Reject before fetch |
| Private or protected audience in v1 | Reject before discovery |
| Unsupported artifact/DCP/Content IR version | Keep last-known-good or mark unavailable |
| Hub identity key disagrees with record | Reject the hub generation |
| Tag SHA, provenance, fingerprint, or manifest digest differs | Reject the shard generation |
| Signature/attestation subject or trust policy differs | Reject before object fetch |
| Missing, extra, oversized, corrupt, or decompression-bomb object | Reject only that shard generation |
| `latest` not current, or stable is preview/EOL | Reject the channel projection |
| Retention class disagrees with lifecycle | Reject the shard record |

No failure permits HTML fallback, re-indexing an untrusted remote source, a
partial cache promotion, or publication of a locally repaired artifact under
the upstream fingerprint.

## Publishing a repository shard

`fura publish-shard` is the CI entry point for one immutable release edition.
It runs the normal freeze, derives the catalog, search, semantic, and per-node
fragment objects, pairs each fragment with its verified frozen presentation
HTML, validates the complete local v1 artifact, uploads only
missing content addresses, and reads every remote object back through a
bounded stream. It creates `manifest.json` last with a create-only write. An
identical manifest is an idempotent no-op; different bytes at an immutable key
are a conflict. Authentication, partial-upload, and conflict failures use the
stable diagnostic rule IDs `fura.publish_shard.auth`,
`fura.publish_shard.partial`, and `fura.publish_shard.conflict` and return a
nonzero exit.

The presentation producer deliberately freezes `CatalogShard.resolve_body_html`
as the portable body fragment. That contract contains renderer-owned body
structure and directive markup, but never claims to be a complete browser
document: page shell, head metadata, theme assets, CSS, and runtime behavior
remain consumer concerns. Markup outside the inert v1 subset fails freeze with
the node identity instead of silently widening the remote execution boundary.

Under free-threaded Python, one freeze operation owns each node render through
validation and converts it to immutable bytes before atomic promotion. The
publisher performs bounded reads into per-object byte values. Neither stage
shares a mutable presentation buffer, parser, or writer between workers, so
correctness does not depend on the GIL.

The verification input contains detached material produced by the repository's
trusted CI signing and provenance steps. `publish-shard` binds each record to
the computed fingerprint; it does not mint signing identities or credentials.
The mount must opt into immutable tag editions so the freeze produces the
64-character source-shard fingerprint and peeled source commit required by v1:

```yaml
# mounts.yaml
mounts:
  - id: docs
    label: Product docs
    default: true
    source:
      provider: git
      repo: https://github.com/acme/docs.git
      ref: main
      path: docs
    editions:
      source: tags
      count: 10
```

For example, `federation-verification.json` is:

```json
{
  "signatures": [{
    "kind": "sigstore-bundle",
    "url": "https://artifacts.example.com/signatures/docs.sigstore.json",
    "sha256": "<64 lowercase hex characters>",
    "issuer": "https://token.actions.githubusercontent.com",
    "identity": "https://github.com/acme/docs/.github/workflows/publish.yml@refs/tags/v1.2.3"
  }],
  "attestations": [{
    "predicate_type": "https://slsa.dev/provenance/v1",
    "url": "https://artifacts.example.com/attestations/docs.slsa.json",
    "sha256": "<64 lowercase hex characters>"
  }]
}
```

S3-compatible credentials use the standard `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, and optional `AWS_SESSION_TOKEN` environment
variables. A tag workflow can publish and retain the exact hub `shards[key]`
entry as follows:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0 # edition discovery needs the release tags
- name: Publish public docs shard
  env:
    AWS_ACCESS_KEY_ID: ${{ secrets.SHARD_ACCESS_KEY_ID }}
    AWS_SECRET_ACCESS_KEY: ${{ secrets.SHARD_SECRET_ACCESS_KEY }}
  run: |
    uv run fura publish-shard --mount docs --edition "${GITHUB_REF_NAME#v}" \
      --public-base-url https://objects.example.com/docs/shards \
      --verification federation-verification.json \
      --s3-endpoint https://objects.example.com --s3-bucket docs \
      --hub-entry-output shard-entry.json --json
```

The bucket's public route must expose the configured object prefix beneath the
same public base URL. GitHub Release assets are not a direct-serving v1 backend:
release assets have flat names, while v1 requires nested relative object URLs
under `/sha256/<fingerprint>/`. A future mirror/gateway or a new artifact major
is required before that backend can claim reader compatibility.
