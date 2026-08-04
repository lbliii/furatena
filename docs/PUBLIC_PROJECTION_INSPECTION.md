# Public projection inspection

Furatena's public inspector simulates one exact lifecycle transition without changing
source files, the in-memory catalog, workflow state, artifacts, or deployments. The
browser, `fura author inspect-public`, MCP, and trusted automation all adapt the same
`PublicProjectionInspector` result.

The response contract is `schema_version: 1` and is shipped as
`schemas/public-projection/v1/inspection.schema.json`. The schema is strict about
nested fields, successful inspections require each of the 19 surfaces exactly once,
and a checked fixture plus serializer sensitivity tests block version, shape, surface,
and privacy-status drift.

## Revision binding

Every inspection identifies the current source SHA-256 revision, a SHA-256 digest of
the proposed source change, the target DCP node ID, the lifecycle operation, and the
previous and resulting visibility. Those values form an immutable projection-plan
digest. When a governed `PublicationPlan` is composed, its existing plan ID and digest
can be used directly. A source, node, or lifecycle mismatch fails before rendering.

The result keeps source, frozen artifact, and deployed artifact identities separate.
Each artifact plane reports its state and freshness, and stale or unavailable delivery
truth remains explicit instead of being folded into source eligibility.

## Complete surface inventory

One inspection reports target presence, content digest, route, change, and proposed
preview for every supported public surface:

- anonymous HTML and route availability;
- navigation, sidebar, and breadcrumbs;
- search and suggestions;
- DCP catalog and structure records;
- channels, full static output, sitemap, and generated PDF output;
- page text and Markdown;
- `llms.txt`, `llms-full.txt`, and `tools.json`; and
- MCP public catalog, search, and structure resources.

Publish reports exact additions. Unpublish and archive report exact removals. Public
metadata updates report changed records only when their canonical digest changes.
Projection generation recomposes the production `DocsApp` over an isolated,
anonymous-only catalog clone. It runs the production static and PDF exporters, reads
their generated artifacts, and derives navigation, sidebar, and breadcrumbs from the
production render context as three distinct surfaces. A protected-content canary scan
then checks the complete proposed artifact tree. Missing surfaces, exceptions,
mutation, and canary matches fail closed with no partial success.

## Performance and invalidation

A complete uncached inspection intentionally performs both current and proposed
production builds. Repeated exact inspections use a lock-owned LRU cache limited to
eight entries and 64 MiB. Its key binds the plan, source and change digests, complete
catalog/build state, configuration and renderer fingerprints, anonymous visibility
policy, and frozen/deployed identities and freshness. A source, configuration,
renderer, policy, or delivery-plane change therefore forces a rebuild.

Same-key requests coalesce behind one build under free-threaded Python. Publication is
atomic under the cache lock, returned payloads are recursively immutable, and explicit
invalidation advances an epoch so an older in-flight build cannot repopulate the
cache. Incomplete, mutated, or privacy-failed inspections are never retained. The
focused projection tests measure the uncached and cached paths and ratchet concurrency,
identity invalidation, explicit invalidation, and count/byte bounds.

## Transport usage

The browser's **Inspect public output** action remains publisher-authorized and returns
the projection under `public_inspection`. It may preview a draft to that authorized
session, but it does not expose the draft through anonymous routes.

```bash
fura author inspect-public docs/proposed-guide --operation publish --json
fura author inspect-public docs/retired-guide --operation archive --json
```

The MCP `author_inspect_publication_impact` tool accepts the same optional `operation`
(`publish`, `unpublish`, or `archive`) and returns `public_projection` alongside
validation and stale-impact evidence.

## Adversarial conformance

`PublicationConformanceScenario` is the shared golden matrix for browser, CLI, MCP,
and automation. It covers the happy path; stale source, plan, validation, policy,
configuration, and approval; authorization and governance denials; retries,
duplicates, concurrency, crashes, and reconciliation; Git and build failures; privacy,
promotion, verification, and rollback. The validator requires the complete
scenario-by-transport product, identical policy/outcome decisions, and no partial
operation that falsely reports success.

Focused service tests remain the implementation proof for approval, provider,
artifact, promotion, rollback, concurrency, and recovery internals. The protected-team
author-to-production journey must still be run in its real external environment and
attached to saga #380 by an authorized human; local conformance does not invent that
operational evidence.
