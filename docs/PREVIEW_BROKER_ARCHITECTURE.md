# Hosted preview broker architecture

Status: Accepted for v1
Date: 2026-08-03
Decision owner: Furatena maintainers
Tracks: #513, #518, #520–#526

## Decision

The hosted preview identity broker is a separately deployable Furatena service,
not a mode of the documentation runtime. Its only product responsibility is to
register an exact preview, establish current GitHub repository access, and
issue or revoke short-lived capabilities bound to that preview. It never reads,
stores, indexes, renders, or proxies documentation content.

Shared, versioned preview-authorization messages and validators belong in the
main Furatena repository and distribution. The deployable broker belongs in a
dedicated `furatena-preview-broker` repository and release artifact with its own
dependency lock, database migrations, deployment pipeline, security review,
and operational ownership. The repository name is the logical service
boundary; provisioning it and choosing a hosting account are deployment work,
not part of this decision.

The broker consumes a released preview-auth protocol version. It must not
import Furatena application internals, depend on a source checkout, or add
service-only fields to shared wire records. Protocol compatibility can
therefore advance independently from broker deployment, while a broker release
pins every protocol version it implements.

## Ownership and release boundaries

| Component | Source and package owner | Release unit | Security responsibility |
| --- | --- | --- | --- |
| Preview-auth schemas, canonicalization, typed records, fixtures, and redaction rules | Main Furatena repository and `furatena` distribution | Furatena release | Protocol compatibility and safe defaults |
| Broker HTTP service, GitHub integration, persistence, migrations, and background jobs | Dedicated `furatena-preview-broker` repository; application code is not a reusable library | Independently versioned immutable service artifact | Authorization, tenant isolation, key use, privacy, and abuse controls |
| Preview runtime verification and session middleware | Main Furatena repository | Furatena release and adopter runtime image | Local signature/binding verification and response protection |
| Trusted preview registration controller | Main Furatena repository, provider-neutral core plus deployment adapters | Controller workflow/tool release | Trusted workflow identity and exact lifecycle ordering |
| Broker infrastructure and operator runbooks | Broker deployment configuration repository or protected operations system | Independently promoted environment revision | Secrets, database, network, monitoring, recovery, and support |

No repository may copy the broker's signing implementation or private key into
a preview image. Cross-boundary calls use a released protocol record or the
versioned controller API described below. Database models and deployment
provider objects are private service details.

## Deployment shape

V1 is one stateless broker application tier, one durable transactional store,
one managed secret/key service, and a bounded background-worker pool. The web
and worker processes share no in-memory authorization truth; atomic code
consumption, registration supersession, revocation sequence, and tenant
partitioning are enforced in durable transactions. Horizontal replicas are
safe from the first release.

The public issuer has one canonical HTTPS origin. A network policy permits
outbound calls only to the configured GitHub API and identity endpoints,
managed key service, transactional store, and approved telemetry sink. The
store and operator surfaces have no public ingress. Staging and production use
different GitHub App identities, keys, stores, domains, telemetry, and backup
sets.

The service has no dependency on a preview deployment provider. Railway is the
first controller adapter, but provider credentials and objects never enter the
broker. The controller supplies only authenticated registration intent and the
canonical preview origin.

## Trust boundaries and principals

| Principal or zone | Trust established by | May do | Must not receive |
| --- | --- | --- | --- |
| Anonymous browser or machine client | Nothing | Begin a bounded authorization ceremony; read public JWKS and health | Registration data, access decisions, grants for another subject, or operator data |
| GitHub user | GitHub App browser or device flow plus a fresh repository-access decision | Receive a short-lived grant for one registered binding | GitHub App private key, another tenant's metadata, or broker signing key |
| CLI or agent client | GitHub device approval plus one-time code exchange | Receive and present a short-lived Bearer grant for one exact binding | Browser session, reusable GitHub token, client secret, or cross-binding grant |
| GitHub App installation | Fresh GitHub API state and immutable installation ID | Define one tenant and the repositories the App may inspect | Authority to approve a user without a separate current user-access decision |
| Trusted preview controller | GitHub Actions OIDC pinned to issuer, audience, immutable repository identity, trusted workflow source/ref, event, actor context, token identity, and time | Submit registration intent; register, supersede, or close only after the current PR/head and provider origin are independently revalidated | GitHub App private key, broker signing key, database credentials, or user tokens |
| Preview runtime | Issuer pin plus authenticated registration/revocation channel; local JWKS verification | Validate exact-bound grants and establish bounded local sessions | Private signing keys, GitHub tokens, provider credentials, or cross-preview state |
| GitHub | TLS plus pinned GitHub API/OAuth endpoints | Authenticate users and answer current installation/repository access checks | Preview content, broker signing keys, or provider credentials |
| Broker operator | Strong operator identity, least-privilege role, and audited elevation | Deploy, observe, rotate, recover, and respond to incidents | Plaintext user codes, grants, OAuth tokens, documentation content, or unrestricted tenant queries |
| Deployment provider/controller | Its own provider authentication | Allocate and remove preview origins; report exact deployment identity | Broker signing keys, GitHub App credentials, or retained user identity |

An authenticated principal is still untrusted for fields outside its authority.
In particular, the controller cannot assert user access, a user cannot choose a
registration binding, and a preview runtime cannot mint or broaden a grant.

## Data flows

The arrows below cross all material trust boundaries. Parentheses name the only
data allowed across each edge.

```text
Trusted controller -- OIDC + exact binding --> Broker control edge --> Store
                                                      |
Browser / CLI <---- code or signed grant ---- Broker service <----> GitHub
      |                                      |       |
      |                                      |       +----> KMS/HSM (sign only)
      |                                      +------------> redacted telemetry
      v
Preview runtime <---- JWKS + scoped revocation ---- Broker protocol edge
      |
      +---- local grant/session check ----> protected preview response
                    (no normal request-time broker hop)
```

1. Trusted controller → broker control API: GitHub OIDC token authenticating
   the trusted workflow context, plus bounded request intent for idempotency,
   repository/PR/head-SHA/origin, protocol endpoints, lifetimes, and lifecycle.
2. Broker → GitHub OIDC metadata: issuer keys and claim validation material.
   The broker does not exchange this token for a general GitHub token.
3. Browser or device client → broker: versioned authorization request, PKCE or
   device ceremony values, and one-time exchange material.
4. Broker ↔ GitHub: OAuth/device ceremony and a fresh installation,
   repository, pull-request, revision, and user-access decision. A GitHub user
   token exists only for this transaction.
5. Broker → client: one opaque code or one asymmetrically signed, exact-bound,
   short-lived grant. Secrets are never sent in a URL except provider-required
   OAuth callback parameters and the protocol's validated one-time browser
   callback.
6. Preview runtime → broker public edge: bounded JWKS retrieval. The runtime
   caches public keys and performs normal request verification locally.
7. Preview runtime ↔ authenticated broker channel: registration state and
   monotonic revocation updates. It carries no content and is never consulted
   for an ordinary page request.
8. Broker → telemetry: fixed event names, bounded metrics, and field-aware
   redacted correlations. Raw requests, credentials, tokens, origins, and
   repository names are excluded.
9. Operator → protected control plane: deployment, key-state transitions,
   recovery, and aggregate diagnostics through separately audited roles.

OIDC claims authenticate the trusted workflow context, not the submitted
pull-request number, head SHA, or preview origin. GitHub's
[supported OIDC claims](https://docs.github.com/en/actions/reference/security/oidc#oidc-token-claims)
include no pull-request-number claim, and for `pull_request_target` the workflow
[`GITHUB_SHA` and `GITHUB_REF`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request_target)
describe the trusted default-branch context rather than the pull-request head.
The binding fields therefore remain request intent until independently
revalidated against current GitHub state and the deployment-provider/controller
authority selected by #521. This record does not choose that control payload or
handoff topology.

There is intentionally no browser/client → preview → broker proxy path and no
broker → preview-content path. This prevents the broker from becoming a content
gateway or a synchronous dependency for documentation delivery.

## Endpoint inventory

All public protocol endpoints share the canonical issuer origin and exact HTTPS
certificate identity. They accept bounded bodies, reject unknown content
types/fields, return versioned safe errors, and emit `Cache-Control: no-store`
unless the row explicitly permits public caching. Secrets are prohibited in
paths and ordinary query strings.

| Method and stable path | Caller and authentication | Purpose | Data and cache policy |
| --- | --- | --- | --- |
| `POST /preview/authorize` | Browser runtime or device client; untrusted until ceremony completes | Start the versioned browser-PKCE or device flow | Bounded protocol request; no-store; enumeration-safe response |
| `GET /preview/device` | Human following the verification URI | Render the device verification form | No user code in path/query; no-store; no repository disclosure |
| `POST /preview/device` | Human with GitHub session and CSRF protection | Submit a user code and approve or deny its exact disclosed binding | Code in body only; no-store; generic failure copy |
| `GET /preview/github/callback` | GitHub browser redirect; state-bound | Complete the broker's GitHub App login | Provider-required code/state are consumed once and scrubbed from access logs |
| `POST /preview/grants` | Client holding a one-time browser/device code | Atomically exchange once for a signed grant | No-store; replay never returns the prior grant |
| `GET /preview/jwks.json` | Preview runtimes and clients; public | Publish current and bounded-overlap public verification keys | Public caching only through the declared freshness bound; never private key members |
| `POST /preview/revocations` | Issuer-pinned runtime with service authentication | Retrieve a bounded monotonic revocation page/checkpoint for registered bindings | No-store; tenant-scoped; response is a versioned revocation record set |
| `PUT /control/v1/registrations` | Trusted controller with exact GitHub Actions OIDC audience and claims | Idempotently create a registration or atomically supersede its predecessor | Identity and intent stay in the bounded body; same key with different intent is a conflict |
| `POST /control/v1/registrations/close` | Trusted controller with the same repository/workflow policy | Idempotently close a registration and begin revocation/retention handling | Identity stays in the bounded body; cannot target another repository or tenant |
| `GET /healthz` | Infrastructure; unauthenticated | Process liveness only | No tenant, dependency, key, or build detail; no-store |
| `GET /readyz` | Infrastructure; unauthenticated | Admission readiness for store/key/dependency configuration | Boolean-safe status only; no tenant or secret detail; no-store |

`/metrics`, traces, administrative key operations, database migrations, and
support queries are private operator surfaces, not public HTTP API. GitHub
webhooks are not an authorization source of truth: if an implementation accepts
them to accelerate invalidation, it verifies their signature, treats them as a
hint, and still performs the required current-state decision.

The exact payloads for authorization, grants, JWKS, and revocation are owned by
preview-auth v1. The control API is independently versioned because GitHub OIDC
registration is not a client authorization message. Changing a method, path,
authentication rule, binding meaning, or security default requires a reviewed
version transition rather than an in-place reinterpretation.

## Least-privilege GitHub App policy

| Permission or facility | V1 setting | Reason and guardrail |
| --- | --- | --- |
| Repository metadata | Read, required | Resolve immutable repository identity and installation coverage; GitHub supplies this implicit baseline |
| Pull requests | Read, required | Verify PR number, state, head SHA, and repository association |
| Organization members | None by default; optional read only | Used solely when a tenant explicitly enables a named team restriction; absence or API failure denies the optional restriction rather than falling back to repository access alone |
| Contents, actions, checks, deployments, environments, issues, discussions, administration, secrets, webhooks writes | None | The broker does not read content, mutate repositories, report checks, deploy previews, or administer GitHub resources |
| GitHub App user authorization | User-to-server browser/device flow | Intersects current user and installation authority; no classic broad `repo` scope and no general OAuth token retention |
| Pull-request lifecycle webhook | Optional, read-derived hint | May reduce revocation delay but cannot replace a fresh access/PR-state check or trusted controller close |

An operator cannot turn on organization-member read globally as a convenience.
Each tenant must explicitly opt into a configured organization/team policy, the
installation must grant the optional permission, and the authorization screen
must disclose it. Missing permission, ambiguous team identity, rate limiting,
or a membership lookup failure fails that restricted authorization closed. It
never silently expands the default app permission set or makes team membership
a substitute for repository access.

## Tenant and storage boundary

The security tenant is one GitHub App installation. Repository ID is the
mandatory second partition key for every registration, authorization,
revocation, audit, and rate-limit record. Human-readable repository names are
resolved transiently for consent UI and are not durable identity.

Every primary/unique/foreign key that can join tenant data includes the tenant
partition. The data layer requires a tenant context, emits no unrestricted
query API, and applies both database row policy and application-level compound
predicates. Background jobs lease work inside one tenant partition. Cache keys,
metrics, idempotency keys, object-store paths, backup indexes, and support tools
carry the same partition or a non-reversible tenant-scoped digest.

Cross-tenant access tests, migration tests, and restore tests are release
gates. A service administrator may view aggregate health by default; tenant
record access requires time-bounded audited elevation. The complete retained
field, deletion, access, and redaction contract is in the
[broker threat model](PREVIEW_BROKER_THREAT_MODEL.md#retained-field-contract).

## Signing-key and secret custody

V1 signs grants with ES256 in a managed KMS/HSM using a non-exportable private
key. Ed25519 is allowed by the shared protocol only when the selected managed
service provides equivalent non-exportable custody, audit, rotation, and
recovery. Broker processes receive permission to request signatures from the
active key; they never receive private key bytes. The database stores only
public JWKS material, provider-independent key ID, KMS reference, algorithm,
state, and timestamps.

The GitHub App private key and OAuth client secret are separate managed secrets
with separate identities and access policies. A preview runtime, controller,
image, log, trace, database backup, developer shell, or support export receives
none of them. Production break-glass roles cannot be used by the normal deploy
identity.

Routine signing-key rotation follows this order:

1. Create a non-exportable key and publish its public JWKS entry.
2. Wait until every admitted runtime can observe the new set.
3. Switch signing to the new key in one atomic key-state transaction.
4. Retain the previous public key through the greater of the negotiated
   rollover overlap and maximum still-valid grant window plus clock skew.
5. Stop verification admission for the old key, retain only non-secret audit
   metadata, and schedule managed-key destruction after the recovery hold.

Rotation occurs at least every 90 days and is rehearsed before GA. Emergency
compromise recovery immediately stops signing with the affected key, publishes
a new key, advances a key-compromise revocation epoch, invalidates grants and
sessions derived from the old key within the revocation objective, and invokes
the public incident process. Rollback never reactivates a retired or compromised
key.

Private key material is not copied into application backups. The managed key
service supplies durability within the approved residency boundary; recovery
restores key references and public metadata. If a managed private key is lost,
the service creates a new key and revokes the lost key's epoch rather than
restoring exported bytes. Encrypted database backups include no plaintext
credential and require a separate recovery role.

## Failure and degraded-operation contract

| Failure | New authorization/registration | Existing grant/session behavior | Recovery rule |
| --- | --- | --- | --- |
| GitHub API outage or rate limit | Fail closed with a retryable, bounded-backoff error; never use a stale positive access decision | Already established sessions continue only to their encoded/local bound unless a known revocation applies | Honor provider reset/retry signals, shed duplicate lookups with single-flight caching, and never extend authorization lifetime |
| Deleted App installation or repository removal | Deny immediately when observed; disable all registrations in the tenant/repository partition | Revoke affected grants/sessions within the revocation objective | Idempotent cleanup preserves only the retention-minimal tombstone/audit record |
| User repository access revoked | Deny new grants on the next decision | Invalidate within five minutes of a verified event/poll; never exceed session expiry | Monotonic subject/binding revocation; no stale-positive fallback |
| PR closed or SHA superseded | Trusted close/supersede disables new grants atomically | Old binding is revoked within five minutes and cannot be presented as current | Controller retries idempotently; newer lifecycle version always wins |
| Broker application outage | No new authorization, exchange, registration, or revocation fetch | Cached fresh JWKS and established local sessions keep preview reads available through their existing bounds; no normal page request calls the broker | Restore service without lengthening code, grant, session, or key freshness |
| Transactional store unavailable | Fail closed before code issue/consumption or lifecycle mutation | Local runtime verification remains available; unknown revocation state expires at its bounded checkpoint | No in-memory success fallback; recover to a transactionally consistent point |
| KMS/signing failure | Do not issue a grant | Existing grants signed by non-compromised published keys remain locally verifiable | Circuit-break signing, alert, and recover or rotate; never fall back to a software key |
| Origin takeover or DNS/domain reuse | Deny registration unless trusted controller identity and current provider binding agree | Revoke the prior registration before accepting reuse | Exact origin alone is never authority; repository/PR/SHA and lifecycle version must also match |
| Regional or issuer-domain outage | New operations unavailable in the affected region | Local validation continues to the documented bound | Restore in the approved residency boundary; failover must preserve issuer, keys, monotonic revocation, and single-writer lifecycle semantics |

Dependency calls have explicit deadlines and bounded retries with jitter.
Retries preserve one idempotency identity and never repeat a consumed exchange.
Queues, request bodies, database result sets, JWKS sets, revocation pages,
telemetry labels, and per-tenant concurrency are bounded. Overload rejects work
before acquiring scarce dependencies and keeps health, JWKS, revocation, and
close operations in protected priority classes.

## Service objectives and support

The broker protects non-production previews, so its failure must not take down
production documentation. V1 targets these monthly objectives after GA:

| Indicator | GA service-level objective |
| --- | --- |
| User-visible authorization, grant exchange, JWKS, and controller API availability | 99.9%, measured over valid eligible requests and including dependency-caused failures |
| Broker processing latency, excluding human interaction and GitHub response time | p95 ≤ 300 ms and p99 ≤ 1 s |
| JWKS retrieval from the public edge | p95 ≤ 200 ms, with protocol-bounded cache freshness |
| Trusted registration/supersession/close convergence | 99% within 60 seconds when GitHub and the deployment provider are available |
| Verified access/installation/PR revocation propagation | 99% within five minutes |
| Recovery point / recovery time for durable broker state | RPO ≤ 15 minutes; RTO ≤ four hours |

GitHub dependency availability and latency are reported separately, not hidden
inside broker SLO success. Human OAuth/device completion time is a product
funnel metric, not a server-latency SLI. No SLO permits extending a security
lifetime or accepting stale positive authorization.

Before GA, maintainers must assign a primary and backup operational owner,
publish a support route and status communication process, and establish
security escalation coverage. A pilot may operate with documented
maintainer-hours support only when the UI says so and no GA SLO is claimed.
Security reports use the repository security policy; tenant record inspection
requires audited elevation rather than ordinary issue support.

## Cost and capacity envelope

The cost model is bounded and provider-neutral:

`monthly cost = fixed service + transactional storage + KMS operations + GitHub/edge egress + retained telemetry and backups`

Capacity planning measures authorization ceremonies, grant exchanges,
registration transitions, revocation fan-out, stored active registrations, and
abusive requests separately. Normal preview page traffic is excluded because
it never traverses the broker. Every dimension has a configured per-tenant and
global quota, bounded concurrency, bounded retention, and a saturation metric.

The operator-only launch record sets the approved monthly ceiling, target cost
per successful authorization, forecast volume, headroom, and alert thresholds.
Crossing a threshold requires a reviewed capacity or product decision; it does
not silently weaken rate limits, retention, isolation, key custody, or SLOs.
Public documentation describes the cost drivers and quota behavior without
publishing private spend, provider discounts, tenant counts, or internal scale.

## General-availability gates

GA remains blocked until independent evidence proves all of these:

1. Browser PKCE and device flows share one authorization policy and produce
   exact preview-auth v1 grants.
2. OIDC registration denies wrong repository, workflow, event, ref, audience,
   PR, SHA, origin, time, replay, bot, and untrusted-fork inputs.
3. Cross-tenant, cross-repository, cross-PR, cross-SHA, cross-origin, replay,
   and privilege-escalation negative matrices are green.
4. Current GitHub access, deleted installations, removed users, closed PRs,
   superseded SHAs, and revocation meet the documented failure bounds.
5. Key rotation, compromise, revocation, managed-key loss, encrypted restore,
   region/domain failure, rollback, and migration rollback are rehearsed in
   staging with retained redacted evidence.
6. SLO dashboards, synthetic browser/device/OIDC/JWKS/revocation canaries,
   saturation alerts, privacy-safe traces, and error-budget policy are live.
7. Data retention/deletion and tenant-isolated backup/restore tests are
   auditable, including deletion propagation through backup expiry.
8. A clean deployment passes browser, device, registration, grant, local
   verification, and revocation smoke tests from immutable revisions.
9. A privacy notice, acceptable-use policy, support boundary, status process,
   and security contact are public; operator runbooks and cost/capacity approval
   are reviewed in the protected operations system.
10. A named primary and backup owner accept launch, incident, rollback, and
    decommission responsibilities. No unresolved P0/P1 security finding or
    exhausted error budget is waived silently.

Issues #520–#526 own implementation, controller integration, deployment,
client ergonomics, and end-to-end evidence. This ADR supplies constraints; it
does not close those deliverables.

## Public and operator follow-ups

| Public before GA | Operator-only before production |
| --- | --- |
| Privacy notice naming data classes, purposes, retention, deletion request route, subprocessors, and residency | Exact provider resources, account/project identities, network rules, database roles, and secret references |
| Support scope, support hours, status/incident communication, and security-reporting route | On-call roster, escalation targets, alert routing, break-glass roles, and access-review evidence |
| Acceptable-use and rate-limit behavior without exploitable thresholds | Exact quotas, abuse thresholds, WAF rules, capacity forecast, and approved cost ceiling |
| Protocol/version support, browser/device expectations, outage behavior, and session bounds | Key creation/rotation/destruction commands, KMS policy, recovery identifiers, and compromise checklist |
| Regional/data-residency commitment and deletion/backups summary | Backup topology, restore commands, residency evidence, RPO/RTO exercise, and migration rollback |
| Service objectives and dependency exclusions | Dashboard queries, synthetic identities, trace access, and error-budget response |

Public material contains no private people, tenant names, internal endpoints,
credentials, provider discounts, spend, or internal scale. Operator material is
access-controlled and must still use redacted identifiers and copy/paste-safe
procedures.

## Explicit exclusions

V1 does not:

- store documentation source, frozen artifacts, rendered output, search data,
  repository contents, diffs, comments, issue bodies, or commit messages;
- proxy content or ordinary preview requests;
- retain a GitHub user access token after the authorization transaction;
- place signing keys, GitHub App secrets, broker database credentials, or
  provider credentials in preview deployments or controller output;
- authorize content publication, production access, repository mutation,
  deployment mutation, or general GitHub operations;
- become a general identity provider, generic OAuth proxy, content gateway,
  secrets distributor, or support data warehouse;
- support arbitrary endpoint origins, caller-selected callbacks, classic broad
  GitHub OAuth scopes, or silent team-policy permission expansion;
- define CLI commands, runtime middleware, broker implementation, database
  schema, provider deployment, migrations, or production credentials in this
  decision record.

## Alternatives rejected

### Embed the broker in every preview

Rejected. It distributes signing authority into untrusted ephemeral workloads,
prevents global revocation, multiplies GitHub App credentials, and turns every
preview into an identity service.

### Put broker implementation in the main runtime package

Rejected. A deployable identity service has a different release, dependency,
secret, migration, incident, and support lifecycle. Sharing versioned protocol
code preserves interoperability without coupling those operational risks to
the documentation runtime.

### Proxy every protected request through the broker

Rejected. It makes documentation latency and availability depend on a central
service and gives that service access to content. Short-lived asymmetric grants
and locally cached public keys keep the hot path local.

### Retain GitHub user tokens for continuous access polling

Rejected. Installation and lifecycle signals plus fresh authorization-time
access checks and bounded sessions meet the product need without creating a
long-lived user-token vault.

### Use one shared secret for grants

Rejected. Symmetric verification gives every preview signing authority.
Managed asymmetric keys expose only public verification material to runtimes
and permit explicit rollover and compromise recovery.

## Consequences

The separate service adds deployment, storage, key, privacy, and on-call work,
but keeps the documentation runtime small and removes the broker from normal
page delivery. Protocol and service releases can move independently only if
compatibility fixtures and supported-version negotiation remain release gates.
Short sessions and fail-closed GitHub decisions limit stale authorization at
the cost of making new login unavailable during a dependency outage. Optional
team restrictions are safer but require an explicit tenant permission and
support path.

This decision is a design and review boundary only. It creates no endpoint,
credential, external repository, deployment, user record, or availability
claim by itself.
