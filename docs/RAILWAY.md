# Railway deployment

The commercial Furatena service runs as one private, digest-pinned GHCR image
plus one Railway volume. Adopter-owned public Git content is fetched, validated,
frozen, and selected independently of image publication. Furatena itself is not
published to PyPI and the deployer receives neither source access nor an
interactive source-modification path.

See [RAILWAY_TEMPLATE_ARCHITECTURE.md](RAILWAY_TEMPLATE_ARCHITECTURE.md) for
the accepted architecture, [RELEASING.md](RELEASING.md) for image promotion and
revocation, [LIVE_OPERATIONS.md](LIVE_OPERATIONS.md) for SLOs and incidents,
and [RAILWAY_TEMPLATE_EXPERIMENT.md](RAILWAY_TEMPLATE_EXPERIMENT.md) for the
privacy-safe 90-day marketplace experiment policy.

## Service shape

- source: exact `ghcr.io/lbliii/furatena@sha256:...` private-image subject;
- registry authority: Railway registry credential, read-only and hidden from
  the application environment;
- runtime: CPython 3.14t with `PYTHON_GIL=0`, UID/GID 65532, one worker, one replica;
- persistence: one volume mounted for `/data/furatena`;
- admission: `/readyz` with five-second overlap and 15-second drain;
- content: HTTPS public Git URL, exact resolved commit, bounded checkout;
- activation: immutable generations plus atomic `active` and
  `last-known-good` selectors.

One replica is intentional in v1 because the filesystem lease and volume are
the generation authority. Multi-replica rollout requires a shared coordination
design and is outside this template version.

### Application-root composition

Managed startup does not replace the image's application tree with an adopter
checkout. It selects four independent roots:

- `FURA_APP_ROOT` is the receipt-bound site root containing adopter
  `docs.yaml`, mounts, content, locales, branding, and sparse overrides;
- `FURA_PLATFORM_ROOT=/app/app` is the immutable image-owned platform root,
  including supported semantic layouts;
- `FURA_RUNTIME_STATE_ROOT=/data/furatena/runtime-state` is the explicit
  writable cache, lease, and runtime-state root; and
- `FURA_OUTPUT_ROOT=/data/furatena/runtime-output` is the explicit writable
  output root.

The active receipt binds one generation's checkout, site, frozen artifacts,
and receipt. Startup fails if `FURA_APP_ROOT` or `FURA_FROZEN_DIR` names a
different generation. Promoted generation files are read-only. Template
precedence is project overrides, repository-local presentation paths,
image-owned platform layouts, packaged framework templates, then component
macros. Local applications retain the compatible single-root defaults unless
they explicitly configure these roots.

Managed configuration and mount paths must remain beneath the selected site
root. Absolute paths, traversal, symlink escapes, and generated/state
namespaces such as `.docs-cache`, `frozen`, `public`, and `dist` fail closed.
Local applications may continue to use explicit external mounts; an external
`--config` may not disagree with `--app-root`.

## Template variables

| Variable | Purpose |
| --- | --- |
| `FURA_BASE_URL` | Public HTTPS origin |
| `FURA_CONTENT_REPOSITORY` | Adopter-owned public HTTPS Git URL |
| `FURA_CONTENT_REF` | Branch, tag, or exact commit policy input |
| `FURA_CONTENT_SUBDIRECTORY` | App root within the repository; default `app` |
| `FURA_CONTENT_REFRESH_TOKEN` | Independent 32+ character refresh/rollback bearer |
| `FURA_IMAGE_VERSION` | Promoted commercial version |
| `FURA_IMAGE_CHANNEL` | `stable` for a production-approved digest |
| `FURA_IMAGE_DIGEST` | Exact deployed `sha256:...` digest for runtime identity |
| `FURA_SERVER_WORKERS` | Pounce serving-process count; defaults to `1` for the private image |
| `FURA_SESSION_SECRET` | Stable production session secret |
| `FURA_PLATFORM_ROOT` | Immutable image-owned platform application root; startup supplies `/app/app` |
| `FURA_RUNTIME_STATE_ROOT` | Writable runtime state/cache root; startup supplies a volume path |
| `FURA_OUTPUT_ROOT` | Writable generated-output root; startup supplies a volume path |
| `RAILWAY_RUN_UID` | Required value `0` for the bounded volume bootstrap; the application immediately drops to UID/GID 65532 |

`FURA_SERVER_WORKERS` is separate from `FURA_WORKERS`: the former controls
Pounce serving processes, while the latter controls parallel catalog indexing.
Keep the serving value at `1` for the v1 volume-backed template. Local
development leaves it unset so Pounce may size its serving pool automatically.

Optional bounds and behavior are documented in the architecture configuration
table. Never place the GHCR registry credential in a normal application
variable. Configure it only through Railway's private image credentials.

Every composer-visible variable has a purpose description. Optional variables
carry safe defaults, release-bound image identity is supplied by the promoted
release, and secrets use Railway's generated-secret function rather than a
committed value.

### Public domains and canonical base URL

The template enables public networking and derives `FURA_BASE_URL` from
`https://${{RAILWAY_PUBLIC_DOMAIN}}`. Railway defines that variable as the
service's public or customer domain, so the template does not commit a hostname.

For a custom domain, attach the domain to the Furatena service and add the DNS
ownership and routing records supplied by Railway. Wait until Railway reports
both domain verification and certificate readiness before making it canonical.
Confirm that `RAILWAY_PUBLIC_DOMAIN` resolves to the intended hostname; if the
service has multiple public domains, set `FURA_BASE_URL` to the one canonical
HTTPS origin and redeploy so generated URLs use the same origin.

Probe `/readyz`, representative reader and search routes, `/catalog.json`, and
`/llms.txt` through the custom origin. Keep the Railway-provided origin available
for comparison during DNS or certificate recovery, but do not change the image
or content generation when only the custom-domain path is unhealthy. Follow
Railway's [domain setup and verification guide](https://docs.railway.com/networking/domains/working-with-domains)
for the current DNS record requirements.

Railway mounts volumes as root, so a non-root image needs the platform's
`RAILWAY_RUN_UID=0` compatibility setting. Furatena uses that authority only to
validate `/data/furatena` and repair its ownership, then `exec`s the server as
UID/GID 65532. A missing or mismatched volume path fails before Python starts;
the application, Git checkout, freeze, and HTTP server never run as root.

## First activation

1. Promote a scanned candidate digest through the protected
   `private-image-production` environment.
2. Configure the Railway service from that exact digest and attach the volume.
3. Set required variables and deploy. Startup runs `fura content reconcile`;
   with no active generation it must successfully fetch and freeze content
   before the server starts.
4. Wait for the newest deployment to reach `SUCCESS` and `/readyz` to return
   HTTP 200.
5. Run the smoke and SLO gates below and preserve their JSON receipts.

Do not treat a queued deployment as complete. Do not switch the template or
production to a candidate tag, a channel tag, or an unverified digest.

### Startup failure recovery

Managed-content configuration and storage failures stop startup before the
server accepts traffic. Expected failures are reported as concise `content
failed:` diagnostics with identifier `fura.content_deployment`, without a
Python traceback. Correct the named `FURA_*` setting and redeploy.

`FURA_CONTENT_STATE_ROOT` defaults to `/data/furatena` and must resolve inside
the writable persistent Railway volume. A diagnostic that names a read-only,
permission-denied, quota, or disk-capacity failure means the volume is absent,
mounted at the wrong path, or cannot accept the generation state. Attach or
repair the volume at `/data/furatena`; do not make the application root
writable as a workaround. Startup remains nonzero when no last-known-good
generation is available.

## Smoke gate

Replace `$ORIGIN` with the Railway HTTPS origin and require every command to
succeed:

```console
curl --fail --silent --show-error "$ORIGIN/healthz"
curl --fail --silent --show-error "$ORIGIN/readyz"
curl --fail --silent --show-error "$ORIGIN/meta.json"
curl --fail --silent --show-error "$ORIGIN/_fura/content/status"
curl --fail --silent --show-error "$ORIGIN/catalog/query.json"
curl --fail --silent --show-error "$ORIGIN/search/semantic?q=deployment"
curl --fail --silent --show-error "$ORIGIN/tools.json"
curl --fail --silent --show-error "$ORIGIN/llms.txt"
curl --fail --silent --show-error "$ORIGIN/sitemap.xml"
curl --fail --silent --show-error \
  -H 'Accept: text/markdown' "$ORIGIN/docs/get-started/"
python scripts/verify-live-artifacts.py "$ORIGIN"
python scripts/check_live_slo.py --origin "$ORIGIN" --output /tmp/furatena-slo.json
```

`/meta.json` must report:

- `distribution=private-image`;
- the expected image version, `stable` channel, exact digest, and source commit;
- an active content generation and exact resolved Git commit;
- known server dependency versions and a non-empty freeze fingerprint.

The artifact verifier rejects truncated bodies and count mismatches across the
catalog, graph query, search, semantic, and agent bulk surfaces. The SLO probe
adds latency, availability, identity, and evidence checks.

## Routine changes

- Content-only change: POST a v1 refresh request containing the current active
  commit, exact requested commit, and idempotency key. HTTP 202 identifies the
  durable status operation; promotion remains distinct from restart and
  readiness. The image digest remains unchanged.
  Poll `GET /_fura/content/operations/{operation_id}` with the same bearer for
  the versioned operation state and sanitized verification result.
- Content rollback: call the rollback endpoint; it selects last-known-good and
  revalidates that recorded generation's manifest, artifact hashes, and
  image/build compatibility, then restarts without fetching or rebuilding.
- Application change: build one candidate image, scan/attest/smoke the digest,
  promote that digest, canary it, then update production.
- Image rollback: select the prior stable digest from its durable release
  record. Do not rebuild the old commit.

Railway Docker-image templates do not receive repository-based update
notifications. Discover stable, deprecated, and revoked image records through
the public `https://github.com/lbliii/furatena/releases.atom` feed, download the
attached `image-record.json`, and validate its v1 schema before changing a
service. Apply only the exact `image@sha256:...` subject; never use a mutable
channel or version tag as deployment or rollback identity. The stable record
names compatibility, migration, support, and the prior known-good digest.

For an upgrade, keep the existing volume and all adopter configuration attached
while changing only the application image subject. Canary the new digest and
require readiness, public-projection checks, build identity, and active content
continuity. A failed gate leaves production unchanged and restores the canary
to the recorded rollback digest.

All normal diagnosis and recovery uses HTTP contracts, GitHub evidence, and
Railway deployment/log/metrics controls. Container SSH is break-glass only and
is not part of the verification or rollback procedure.

The request body cannot override the one configured repository, ref, or
subdirectory, and cannot supply actor identity. Empty-body refresh is a v1-only
migration compatibility mode that follows the configured ref without exact
commit or caller idempotency guarantees. Webhooks are not a v1 transport.

New generations include `manifest.json` and `verification.json` beside their
receipt. These records bind source, configuration, presentation/runtime
fingerprints, build identity, refresh operation, and every source/frozen file
size and SHA-256 digest. Startup performs the full artifact check before
serving. Pre-contract generations are reported as `legacy_v1` during the v1
migration window and are not described as cryptographically verified.
