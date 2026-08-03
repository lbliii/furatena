# Railway proprietary template architecture

Status: Accepted for v1
Date: 2026-07-14
Decision owner: Furatena maintainers
Tracks: #451, #452, #454, #455, #436, #464–#469

## Decision

The first Furatena Railway marketplace product is a public documentation portal
served by a proprietary Furatena container image. Railway stores the private
registry pull credential and does not expose the image source or credential to
the deployer. Furatena is not published to PyPI for this product.

Adopter documentation remains outside the proprietary image in a public Git
repository created from a content-only starter. A Railway volume stores the
resolved source checkout, staged generations, active frozen generation,
last-known-good generation, manifests, and idempotency receipts. The application
image and adopter content therefore have independent identities and lifecycles.

V1 supports public Git content over HTTPS. Private repositories, customer Git
credentials, browser authoring, and private documentation are deferred until a
separate threat model and demand signal exist.

## Product boundary

| Boundary | Owner | Identity | Update mechanism |
| --- | --- | --- | --- |
| Furatena runtime | Maintainer | Image digest plus build manifest | Maintainer promotion and adopter-selected digest |
| Content | Adopter | Repository, resolved commit, optional subdirectory | Authenticated refresh operation |
| Active generation | Service | Content commit plus freeze fingerprint | Atomic staged promotion |
| Railway configuration | Adopter/template | Environment and service configuration revision | Staged Railway configuration change |
| Registry credential | Maintainer/Railway | Hidden credential revision | Railway template/service credential rotation |
| Refresh authority | Adopter | Independently generated secret revision | Railway sealed variable rotation |

The private image must not contain customer content, repository credentials,
Railway tokens, registry credentials, maintainer-only configuration, or Git
checkout metadata. Public build identity may contain the source commit of the
proprietary image, dependency versions, image digest, and release channel.

## V1 deployment shape

- One Railway application service sourced from a private container image.
- One volume mounted at `/data/furatena` for content generations and receipts.
- A stable application identity at UID/GID 65532. Railway starts only the
  volume-ownership bootstrap as root, after which PID 1 is replaced by the
  unprivileged Furatena process.
- One Railway-provided public domain, with documented custom-domain support.
- One replica initially. Operation leases and generation manifests must remain
  replica-safe so a later multi-replica profile does not change the contract.
- `/readyz` gates Railway deployment admission.
- External scheduled verification supplies continuous monitoring because the
  Railway deployment healthcheck is not a continuous liveness monitor.

The image contains a small fallback corpus used only before an adopter source is
configured or when the template explicitly selects demo mode. A configured
adopter source fails closed on the first deployment if no valid generation can
be produced. Later refresh failures continue serving the last-known-good
generation and report degraded freshness without promoting partial output.

## Configuration contract

| Variable | Required | Secret | Purpose |
| --- | --- | --- | --- |
| `FURA_CONTENT_REPOSITORY` | Template | No | HTTPS URL of the adopter-owned public Git repository |
| `FURA_CONTENT_REF` | No | No | Branch, tag, or exact commit policy input; defaults to `main` |
| `FURA_CONTENT_SUBDIRECTORY` | No | No | Repository-relative app/content root |
| `FURA_CONTENT_ALLOWED_HOSTS` | No | No | Comma-separated Git host allowlist; defaults to `github.com` |
| `FURA_CONTENT_STATE_ROOT` | No | No | Volume state root; defaults to `/data/furatena` |
| `RAILWAY_RUN_UID` | Template | No | Railway volume compatibility value `0`; permits the bounded ownership bootstrap before privilege drop |
| `FURA_CONTENT_REFRESH_TOKEN` | Template | Yes | Bearer secret for the refresh operation |
| `FURA_CONTENT_WEBHOOK_SECRET` | No | Yes | Separate GitHub webhook HMAC secret when webhook delivery is enabled |
| `FURA_CONTENT_MAX_BYTES` | No | No | Upper bound for fetched repository data |
| `FURA_CONTENT_MAX_FILES` | No | No | Upper bound for checked-out files |
| `FURA_CONTENT_REFRESH_ON_START` | No | No | Resolve and reconcile content before readiness; defaults to true |
| `FURA_CONTENT_RESTART_AFTER_PROMOTION` | No | No | Gracefully restart the process after an HTTP promotion; defaults to true |
| `FURA_IMAGE_CHANNEL` | No | No | Informational release channel recorded in build identity |
| `FURA_SERVER_WORKERS` | No | No | Pounce serving-process count; private-image v1 defaults to one |

`FURA_SERVER_WORKERS` is intentionally distinct from the catalog indexing pool
configured by `FURA_WORKERS`. V1 keeps exactly one serving process because the
mounted filesystem supplies the generation and lease authority. The
free-threaded runtime remains available for supported in-process catalog work;
Pounce resolves one serving worker to its single-process async mode.

The repository URL parser rejects non-HTTPS schemes, embedded credentials,
fragments, local/file paths, disallowed hosts, and ambiguous path traversal.
Redirects may not escape the configured host policy. V1 never asks the adopter
for a Git token.

## Persistent state layout

```text
/data/furatena/
├── active -> generations/<generation-id>
├── last-known-good -> generations/<generation-id>
├── generations/
│   └── <generation-id>/
│       ├── source/
│       ├── frozen/
│       ├── manifest.json
│       └── verification.json
├── staging/
├── receipts/
├── leases/
└── state.json
```

A generation ID binds the normalized repository identity, resolved commit,
configuration digest, renderer identity, and freeze fingerprint. Symlink or
pointer replacement is atomic within the volume. Generation manifests and
receipts use fsync-and-replace writes. Active source and frozen output always
move together.

## Refresh operation

The semantic operation is:

```text
refresh(repository, ref, expected_active_commit, requested_commit,
        idempotency_key, actor)
```

The refresh controller:

1. authenticates the request and validates repository policy;
2. acquires the content-generation lease;
3. resolves the ref and rejects a stale or superseded requested commit;
4. returns the prior receipt for an identical idempotency key and digest;
5. fetches the exact commit into a new staging directory;
6. validates configuration, content, public-projection canaries, and limits;
7. freezes a complete generation and verifies its artifact inventory;
8. runs representative reader, search, catalog, and agent-surface checks;
9. atomically promotes the generation and advances last-known-good;
10. records the receipt, audit event, and operational status.

The same idempotency key with a different semantic digest is a conflict. A
newer requested commit supersedes older pending work. Duplicate webhook or CLI
deliveries converge on one receipt. A failed step leaves the active pointer
unchanged.

The initial implementation exposes one authenticated HTTP operation plus a CLI
client. GitHub webhooks are an optional transport over the same operation and
must validate `X-Hub-Signature-256`, delivery identity, repository identity,
ref, and commit before dispatch. Transport timestamps and delivery IDs do not
change semantic request identity.

## Startup and reconciliation

Railway volumes are mounted root-owned, so the template sets the documented
`RAILWAY_RUN_UID=0` compatibility value. The startup wrapper accepts that root
identity only when `RAILWAY_VOLUME_MOUNT_PATH` exactly matches
`FURA_CONTENT_STATE_ROOT`, rejects symlinked roots and markers, repairs only the
mounted state tree, and immediately replaces itself with UID/GID 65532. The
unprivileged process then verifies its UID, the application-owned interpreter,
and GIL-disabled runtime before catalog work. It then:

1. verifies volume writability and the state schema;
2. reconciles expired leases and incomplete staging directories;
3. restores a valid orphaned active or last-known-good pointer;
4. optionally refreshes the configured source;
5. loads exactly one verified generation;
6. becomes ready only when the active generation and build identity agree.

If no generation exists and the first refresh fails, readiness fails. If a
previous generation exists, a later refresh failure preserves service and marks
content freshness degraded. Corrupt active and last-known-good manifests fail
closed.

## Image lifecycle

Private images have development, candidate, stable, deprecated, and revoked
states. Immutable digests are the only promotion and rollback identity. Tags are
discovery aids and may not be the recorded rollback target.

Every stable image publishes a public, source-free release record containing:

- image version, digest, build commit, build time, and compatibility contract;
- Python and b-stack dependency versions;
- SBOM and vulnerability-scan summaries;
- provenance/attestation references;
- configuration and content-schema changes;
- upgrade, rollback, deprecation, and revocation instructions.

Because Railway does not provide repository-based template update notifications
for Docker-image templates, the service exposes its image version/channel in
operational metadata and maintainers publish update notices through the template
support channel and public release feed. Adopters explicitly apply a new digest.

## Health and no-SSH operations

Protected private-image services cannot rely on deployer SSH. All required
diagnostics must be available through safe operational surfaces and Railway
logs/metrics:

- process health and serving readiness;
- image build identity and release channel;
- active and last-known-good content generation identities;
- source resolution, refresh state, freshness, and remediation;
- staging/lease/reconciliation status without filesystem paths or secrets;
- public-projection and artifact-integrity verification summaries.

The official demo has external probes for health, readiness, representative
HTML, search, catalog/query, `llms.txt`, build identity, content freshness, and
bulk artifact integrity. Alerts carry correlation IDs and stable check names,
not authored queries or content bodies.

## Security and privacy invariants

- Public deployments never expose draft, private, protected, or archived
  canaries through HTML, navigation, search, static/frozen artifacts, PDF,
  catalog, DCP, `llms` outputs, tools, or MCP resources.
- Refresh authority is independent from registry pull authority.
- Root authority is limited to the volume-ownership bootstrap; the Python,
  catalog, Git, and server processes run as UID/GID 65532.
- Public Git fetches have bounded time, byte, file-count, and path policies.
- Repository identity and resolved commit are audit fields; content bodies are
  not audit fields.
- Refresh errors redact URLs containing credentials even though such URLs are
  rejected.
- Author mutation and private-content modes remain disabled in the public v1
  template.
- Image upgrade cannot mutate adopter content. Content refresh cannot change the
  application image.

## Alternatives rejected for v1

### Publish Furatena to PyPI

Rejected. Railway can distribute a private image without public package or
source publication. PyPI adds an unnecessary distribution and support surface.

### Bake adopter content into the private image

Rejected. It couples every documentation edit to maintainer image publication,
prevents adopter ownership, and makes image rollback indistinguishable from
content rollback.

### Use a private adopter repository in v1

Deferred. It requires customer Git credential capture, storage, rotation,
redaction, and provider-specific authorization. Public documentation does not
need that trust boundary.

### Enable browser authoring in the public template

Deferred. It introduces identity, CSRF, mutation authorization, audit
persistence, and backup requirements unrelated to proving the first public docs
product.

### Add a second service or database

Rejected until evidence requires it. A volume supplies durable generations and
receipts while preserving a one-service product shape.

## Consequences

The template becomes useful only after the content refresh path is implemented;
the private-image pipeline alone is insufficient. The volume is part of the
product contract and must be included in clean-account conformance, backup,
restore, and cost evidence. Public repositories make v1 materially safer but do
not satisfy private-docs use cases. Image and content release operations must be
tested independently on every candidate.

## GitHub work reconciliation

- #436 is the selected v1 content-update mechanism, narrowed to credential-free
  public HTTPS Git plus exact resolved commits, bounded staging, frozen
  generations, and a Railway volume.
- #424 remains the pull-request environment proof for Furatena application
  development. It is useful maintainer evidence but is not required for an
  adopter to edit or publish content through the template.
- #292's remote/browser authoring direction is not enabled in v1. It requires a
  separately approved trusted identity gateway and mutation threat model.
- #455 consumes the immutable image/digest, promotion, rollback, revocation,
  and hidden registry credential decisions in this ADR.
- #456 consumes the one-service/one-volume composer contract and the separate
  public content starter boundary.
- #457 consumes the exact image/content identity, negative paths, fault
  injection, clean-account lifecycle, evidence, and teardown requirements.
- #458 consumes the published runbooks, live demo, SLO monitor, support
  boundary, and durable update notices required because image templates do not
  receive repository-based update notifications.
