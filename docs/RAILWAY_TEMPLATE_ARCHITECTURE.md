# Railway proprietary template architecture

Status: **Superseded in part (2026-08-05)** — open-source + PyPI is now the
primary distribution path; see
[OSS_DISTRIBUTION_DECISION.md](OSS_DISTRIBUTION_DECISION.md). Retained as the
historical ADR for the private-image + volume composer shape still used by
optional GHCR experiments.
Date: 2026-07-14 (supersession note 2026-08-05)
Decision owner: Furatena maintainers
Tracks: #451, #452, #454, #455, #436, #464–#469, #471

## Decision

**Original v1 decision (2026-07-14):** The first Furatena Railway marketplace
product is a public documentation portal served by a proprietary Furatena
container image. Railway stores the private registry pull credential and does
not expose the image source or credential to the deployer.

**Current decision (2026-08-05):** Furatena is MIT open source and publishes to
PyPI. Railway templates should install from PyPI (or build from public source)
and may earn marketplace kickbacks without a proprietary image boundary. A
digest-pinned GHCR image remains optional, not the license or IP boundary.

Adopter documentation remains outside the application image in a public Git
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

Every newly staged generation writes versioned `manifest.json` and
`verification.json` records before selector promotion. The manifest binds the
normalized configured repository/ref, requested and resolved commits, config
and presentation identity, image/build identity, renderer and freeze
fingerprints, actor, refresh operation receipt, lifecycle state, and the
complete source/frozen artifact inventory with byte sizes and SHA-256 digests.
Verification reparses the manifest and hashes every inventoried artifact before
promotion; startup does the same full verification before serving, and rollback
re-verifies the target plus its image/build compatibility before moving either
selector.

Generations written before this contract remain readable during the v1
migration window and are reported as `legacy_v1`; they are never represented as
cryptographically verified. All newly promoted generations require the v1
manifest and verification records. Corrupt new or recorded generations fail
closed and move to a bounded quarantine with a sanitized status code.

## Refresh operation

The semantic operation is:

```text
refresh(configured_repository, configured_ref, configured_subdirectory,
        expected_active_commit, requested_commit, idempotency_key,
        authenticated_actor)
```

The refresh controller:

1. authenticates the request and validates repository policy;
2. acquires the content-generation lease;
3. rejects a stale active commit and proves the exact requested commit is
   reachable from the configured ref;
4. returns the prior receipt for an identical idempotency key and digest;
5. fetches the exact commit into a new staging directory;
6. validates configuration, content, public-projection canaries, and limits;
7. freezes a complete generation and verifies its artifact inventory;
8. runs representative reader, search, catalog, and agent-surface checks;
9. atomically promotes the generation and advances last-known-good;
10. records the receipt, audit event, and operational status.

Authorization is site-wide in v1. One independently rotated bearer controls
the single configured service/site, repository, ref policy, and subdirectory.
The request cannot select a tenant, site, repository, ref, subdirectory, or
mount subset; all configured mounts and their browser, frozen/static, search,
catalog, and agent projections are rebuilt together. Multi-tenant or partial-
mount refresh requires a separately approved identity and policy contract.

The same idempotency key with a different semantic digest is a conflict. A
second request while one operation is pending is also a conflict under the
explicit single-replica v1 contract. Duplicate HTTP or CLI deliveries with the
same key and semantic digest converge on one receipt. A failed step leaves the
active pointer unchanged and preserves last-known-good.

The v1 implementation exposes bearer-authenticated HTTP plus the CLI over the
same durable operation model. It returns HTTP 202 with a sanitized local status
URL; key collision, stale active state, unreachable commit, and concurrent
pending work return deterministic HTTP 409 codes. Repository, ref,
subdirectory, and actor are never accepted from the body. Actor identity comes
from the authenticated credential or local process transport. Webhook transport
is deferred beyond v1.

An empty-body POST remains available only for the lifetime of the v1 contract.
It follows the configured ref for migration compatibility and explicitly makes
no exact-commit or caller-controlled idempotency guarantee. New integrations
must send `furatena.content-refresh.request` v1 with all three request fields.

Promotion, activation, restart scheduling, and readiness are distinct durable
states. Startup reconciliation is local: it marks interrupted pre-promotion
work failed, preserves failed staging evidence and last-known-good, and marks a
promoted generation ready only when the running generation, image digest, and
build commit match its receipt.

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

Reconciliation owns the same renewable `content-refresh` lease as checkout,
validation, promotion, rollback, and exact-commit resolution. Status and
readiness probes make a bounded attempt to reconcile; while that lease is busy,
they report `staging` from a read-only selector snapshot instead of waiting for
the build or removing its workspace.

If no generation exists and the first refresh fails, readiness fails. If a
previous generation exists, a later refresh failure preserves service and marks
content freshness degraded. Corrupt active and last-known-good manifests fail
closed.

Sanitized content status and readiness expose only lifecycle and immutable
identifiers. They distinguish `active`, `staging`, `degraded`, `stale`,
`rollback`, and `failed` without returning state-root, checkout, or artifact
filesystem paths. Quarantine status names only the generation, stable failure
code, quarantine identity, and timestamp so operators can recover without
exposing paths or content.

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

The public feed is `https://github.com/lbliii/furatena/releases.atom`. Each
stable `image-v<version>` entry attaches a schema-validated
`image-record.json`; digest-named deprecation assets and immutable
`image-revoked-<digest>` entries carry support windows and emergency
remediation. Promotion checks the revocation entries before accepting a digest.
The record pins a distinct last-known-good rollback digest and links the exact
source revision's compatibility, content/config contract, changelog, migration,
and support statements. The release tag remains discovery metadata, never
deployment identity.

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

The final Railway acceptance gate remains an external conformance run: record
one reviewed commit becoming active under the same Railway deployment ID and
unchanged application image digest, then retain the operation, generation, and
probe receipts as release evidence.

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

**Superseded (2026-08-05).** Open-source distribution via PyPI Trusted
Publishing is the accepted primary path. See
[OSS_DISTRIBUTION_DECISION.md](OSS_DISTRIBUTION_DECISION.md) and
[PYPI.md](PYPI.md). Railway templates should install from PyPI (or build from
public source) and may still earn marketplace kickbacks without a proprietary
image boundary.

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

The provider-neutral content refresh path is implemented across the HTTP, CLI,
startup, generation, rollback, readiness, and status contracts. The private-
image pipeline alone remains insufficient: the volume is part of the product
contract and must be included in clean-account conformance, backup, restore,
and cost evidence. Public repositories make v1 materially safer but do not
satisfy private-docs use cases. Image and content release operations must be
tested independently on every candidate.

This ADR accepts the product capability and its single-service v1 boundary. It
does not claim the external Railway acceptance gate: the same-deployment proof,
provider receipts, and live failure-injection evidence remain operational work
and are not inferred from repository tests.

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
