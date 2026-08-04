# Hosted preview broker threat model

Status: Accepted for v1 design; implementation evidence required before GA
Date: 2026-08-03
Decision owner: Furatena maintainers
Tracks: #513, #520–#526

This threat model applies to the independently deployed broker specified in
[Hosted preview broker architecture](PREVIEW_BROKER_ARCHITECTURE.md). It covers
registration, GitHub user authorization, one-time exchange, grant signing,
JWKS, revocation, storage, telemetry, operator access, backups, and recovery.
It does not claim that the service exists or that the controls have been
implemented.

## Security objectives

1. A subject receives a capability only after current GitHub repository access
   is established for an active, exact repository/PR/head-SHA/origin binding.
2. Registration authority comes only from pinned trusted-workflow OIDC claims;
   pull-request code and deployment metadata cannot mint authority.
3. Codes, grants, callbacks, registrations, and revocations are single-purpose,
   bounded, replay-resistant, and tenant-isolated.
4. Preview runtimes verify locally with public keys and never receive a signing
   key, GitHub token, broker credential, or another preview's state.
5. GitHub user tokens, documentation content, and broad repository data are not
   retained.
6. Logs, telemetry, support output, backups, and errors cannot disclose a
   credential, personal identity, private repository identity, or preview URL.
7. Rotation, revocation, deletion, restore, rollback, and dependency failure
   preserve monotonic security decisions rather than resurrecting old access.
8. No tenant, operator, worker, cache, migration, or backup path can omit the
   installation and repository partition.

## Assets

| Asset | Security property |
| --- | --- |
| Managed signing keys and key state | Non-exportability, controlled use, monotonic rotation/revocation, availability |
| GitHub App private key and OAuth client secret | Confidentiality, narrow service access, independent rotation |
| GitHub user access result and transient token | Currentness, transaction-only use, non-retention |
| Trusted-workflow OIDC assertion | Exact issuer/audience/workflow/repository/event/ref/PR/SHA/time binding and one-time use |
| Registration and lifecycle state | Exact binding, authenticated provenance, atomic supersession/close, tenant isolation |
| Authorization/device codes, user codes, state, nonce, PKCE material, JTI, grants, and sessions | Secrecy where applicable, single use, expiry, binding, replay resistance |
| JWKS and revocation stream | Authenticity, freshness, ordered state, bounded availability |
| Retained tenant/audit/rate-limit data | Purpose limitation, minimization, isolation, deletion, redaction |
| Database, backups, migrations, and recovery records | Integrity, encryption, point-in-time consistency, no resurrection |
| Public issuer origin and DNS | Canonical identity, TLS integrity, takeover resistance, recoverability |
| SLO, audit, and incident evidence | Accuracy, bounded cardinality, redaction, access control |

Documentation source and rendered preview content are deliberately not broker
assets because they never cross the service boundary. Observing either in a
broker request, store, log, trace, or backup is a security defect.

## Actors and capabilities

| Actor | Intended authority | Relevant abuse or failure |
| --- | --- | --- |
| Authorized repository reader | Authorize one current registered preview | Reuse code/grant across binding, retain stale access, leak credential |
| CLI or agent user | Complete device flow and present one exact-bound Bearer grant | Poll abuse, local credential leakage, stale or cross-origin grant reuse |
| Unauthorized or removed GitHub user | None | Enumeration, brute force, stale-session use, social engineering |
| Malicious fork author or bot | Execute untrusted PR code | Forge registration, alter origin/SHA, exfiltrate controller or preview secrets |
| Trusted controller | Register/supersede/close exact preview bindings | Replay OIDC, confused deputy, stale event, compromised default-branch workflow |
| Preview runtime | Verify grants and maintain exact local sessions | Request signing authority, omit binding checks, expose content after revocation |
| GitHub/App installation | Authenticate and report current state | Outage, rate limit, stale event, deleted installation, compromised app secret |
| External attacker | Internet access to public endpoints | DoS, code guessing, callback abuse, SSRF, cache poisoning, origin takeover |
| Tenant administrator | Configure installation and optional team restriction | Permission expansion, cross-repository policy, accidental deletion |
| Broker deploy identity or worker | Execute one service role | Lateral movement, unrestricted database access, key misuse |
| Support operator | Aggregate diagnostics and approved tenant support | Curiosity access, unsafe export, secret exposure, cross-tenant query |
| Security/recovery operator | Time-bounded break-glass and recovery | Disable audit, reactivate compromised key, restore revoked access |
| Dependency or build-chain attacker | Compromise package, image, CI, DNS, KMS, store, or telemetry | Code execution, credential theft, false JWKS/revocation, data exfiltration |

## Trust boundaries

The architecture's [data-flow inventory](PREVIEW_BROKER_ARCHITECTURE.md#data-flows)
is authoritative. The threat boundaries are:

- public internet ↔ canonical broker edge;
- broker ↔ GitHub OAuth/API/OIDC endpoints;
- trusted controller ↔ versioned broker control API;
- broker process ↔ transactional store and managed key/secret services;
- broker ↔ preview runtime JWKS and authenticated revocation channel;
- broker ↔ privacy-filtered telemetry;
- deploy/support/security identities ↔ protected operator control plane;
- active state ↔ encrypted backup and restore boundary;
- staging ↔ production, which share no identity, key, data, or network trust.

TLS authenticates a transport endpoint; it does not authorize a registration,
user, runtime, tenant, or field. Every transition also validates its own
version, purpose, exact binding, lifecycle version, time, replay state, and
principal.

## Retained-field contract

Retention begins when a row is created. “Delete within 24 hours” applies to
active stores, caches, indexes, and telemetry after expiry, closure,
installation removal, or an approved deletion request. Encrypted backups expire
within 35 days and are never selectively queried for ordinary operation. A
restore immediately reruns deletion tombstones and lifecycle/revocation
reconciliation before serving traffic.

Access classes are `service` (tenant-scoped broker role), `aggregate` (metrics
without tenant identity), `support-elevated` (time-bounded audited tenant
access), and `security` (break-glass investigation). General logs permit only
public fixed values and non-reversible, domain-separated digests.

| Retained field or field group | Purpose | Maximum retention | Deletion rule | Access control | Redaction/export rule |
| --- | --- | --- | --- | --- | --- |
| Internal `tenant_id` | Partition one GitHub App installation without relying on a mutable name | Installation lifetime plus 30 days | Disable immediately; delete active row within 24 hours after hold | Service; support-elevated | Tenant-scoped HMAC digest only |
| GitHub `installation_id` | Verify installation identity and route current access checks | Installation lifetime plus 30 days | Same as tenant; installation deletion starts purge | Service; support-elevated | HMAC digest; never error prose |
| Installation permission snapshot and optional organization/team ID policy | Prove least privilege and apply an explicitly enabled team restriction | Installation/configuration lifetime plus 30 days | Delete with tenant; disable optional policy immediately on permission removal | Service; support-elevated; security audit | Immutable ID digests and permission enum only; no member list or team name |
| Immutable `repository_id` | Exact authorization and second tenant partition | Registration lifetime plus 30 days | Delete with final repository registration tombstone | Service; support-elevated | Tenant-scoped digest; no repository name |
| `registration_id` | Idempotency, lifecycle addressing, and support correlation | Active registration plus 30 days | Replace active data with minimal closed tombstone, then delete | Service; support-elevated | HMAC digest outside protected audit |
| `pull_request_number` and `head_sha` | Exact binding and stale-revision denial | Active registration plus 30 days | Delete with registration tombstone | Service; support-elevated | Combined binding digest; never public error text |
| Canonical `preview_origin` | Prevent cross-origin capability use | Active registration plus 30 days | Delete with registration; domain reuse requires new trusted registration | Service; support-elevated | Origin HMAC only; strip from logs/traces |
| Redirect paths, audience, protocol versions, and negotiated lifetimes | Validate exact callbacks/consumer and compatibility | Active registration plus 30 days | Delete with registration | Service; support-elevated | Fixed public defaults may be aggregated; tenant values omitted |
| Registration status, predecessor, lifecycle version, created/expires/superseded/closed timestamps | Atomic ordering, cleanup, and incident reconstruction | Active plus 30 days | Minimal terminal status/timestamps remain only through hold | Service; support-elevated; security | No binding in general logs; coarse aggregate age only |
| Trusted workflow identity digest, OIDC `jti` HMAC, decision, and expiry | Deny registration replay and prove policy source without retaining JWT | Token expiry plus 24 hours; decision event in audit for 90 days | Delete replay material within 24 hours after bound | Service; security | Never retain JWT; digest workflow identity/JTI |
| Policy version and exact authorization/denial reason code | Reproduce a decision and measure safe denial classes | 90 days | Delete with audit event | Service; aggregate reason counts; security | Closed reason code only; no arbitrary provider message |
| Subject tenant-scoped HMAC | Correlate access/revocation without a GitHub login/profile | Last active grant/session plus 30 days; audit form 90 days | Delete on approved subject request unless incident hold applies; destroy tenant audit key on tenant deletion | Service; support-elevated; security | HMAC only; never login, name, email, or avatar |
| Authorization request/exchange ID HMAC, client kind, outcome, and timestamps | Replay defense, flow debugging, and SLI | Secret lifetime plus 24 hours; outcome audit 90 days | Delete replay row after bound; audit follows audit deletion | Service; aggregate outcomes; security | Digest ID; fixed kind/outcome only |
| OAuth `state`, authorization/device code, user code, nonce, PKCE verifier/challenge, and JTI lookup keys | Atomic consumption, replay denial, and binding | Secret expiry plus 24 hours | Delete within 24 hours after expiry/consumption | Service only | Store broker-secret HMAC where lookup is required; plaintext never retained or exported |
| Pending device approval status and poll schedule | Coordinate bounded device flow and rate limits | Device-code expiry plus 24 hours | Delete with device replay row | Service only | No subject or binding in general logs |
| Signed grant | Deliver capability once | Not retained | Response memory cleared after send; crash output prohibited | Request process only | Never log, hash, trace, persist, or support-export the compact grant |
| GitHub user token, provider OAuth code, and user profile response | Make one fresh access decision and render minimal consent | Transaction memory only, no more than five minutes | Release immediately on completion/failure; never enqueue or back up | Isolated request worker | Entire value omitted; only subject HMAC and decision survive |
| Revoked JTI HMAC, subject HMAC, binding digest, key ID, sequence, reason code, and expiry | Monotonic grant/session invalidation | Greater of session expiry, key overlap, and 24 hours; audit event 90 days | Delete live lookup after safety window; retain only redacted audit | Service and registered runtime partition; security | HMAC/digest and closed reason only |
| Signing key ID, algorithm, public JWK, state, creation/activation/retirement/compromise timestamps, and managed-key reference | Publish/rotate keys and prove custody | Public JWK while verifiable; non-secret audit metadata one year | Destroy managed key after hold; remove public key after overlap; retain no private bytes | Signing role; public JWK endpoint; security metadata | Public JWK allowed; managed reference and timeline protected |
| GitHub App key and OAuth secret references | Locate managed credentials without copying them | Credential lifetime plus one rotation audit cycle | Disable immediately and destroy replaced version after incident/recovery hold | Dedicated secret-using role; security | Reference redacted; secret value never enters store/log/backup |
| Rate-limit bucket HMAC, window, and counters | Bound brute force, polling, tenant load, and cost | Window plus 24 hours | Expire automatically; purge with tenant | Service; aggregate saturation | HMAC of tenant/subject/network prefix; no raw IP or user agent |
| Audit event ID, time, fixed type/outcome/reason, policy/key version, region class, and correlation digests | Security investigation, deletion evidence, and SLO proof | 90 days unless a documented legal/security hold applies | Automated deletion; holds require reason, owner, expiry, and audit | Security; support-elevated subset; aggregates | No raw identity, URL, repository name, provider payload, request body, or credential |
| Migration version, job lease, deletion tombstone, and restore checkpoint | Safe schema transition, exactly-once cleanup, and no resurrection | Operational state while needed; tombstones 35 days beyond source deletion; audit 90 days | Automatic compaction only after backup horizon | Service/migration role; security | Tenant digest and counts only |
| Encrypted database backup and restore manifest | Disaster recovery and deletion reconciliation | Maximum 35 days | Provider lifecycle deletion; restored data is reconciled before use | Separate backup/recovery roles; no support access | Manifest contains aggregate counts/digests; no plaintext export |

No other field may be retained without updating this table, the public privacy
disclosure impact, deletion jobs, access roles, redaction tests, and capacity
model in the same reviewed change. Unknown input fields are rejected, not
logged for debugging.

## Data minimization and tenant isolation

- The broker stores immutable numeric GitHub IDs, never repository source,
  full repository names, user profiles, commit messages, pull-request text,
  team membership lists, or provider deployment objects.
- Human-readable repository/organization/login values used in consent UI are
  fetched for the transaction and discarded. Security decisions use immutable
  IDs.
- The tenant/install and repository pair appears in every compound key,
  relationship, cache key, lease, replay digest, revocation cursor, job,
  telemetry correlation, backup index, and support lookup.
- Database row policy is defense in depth; service methods also require an
  explicit tenant context. A privileged connection is reserved for migrations
  and recovery and cannot serve requests.
- Tenant-scoped HMAC keys prevent equality joins across installations. Key
  rotation supports cryptographic deletion after tenant removal.
- Aggregate metrics use closed low-cardinality labels. Tenant, repository,
  origin, subject, registration, PR, SHA, code, JTI, and error text are not
  labels.

## Abuse cases and mitigations

| Threat or failure scenario | Required mitigation and proof |
| --- | --- |
| Registration replay | Exact OIDC issuer/audience/workflow/repository/event/ref/PR/SHA/time checks; JTI HMAC replay row; atomic idempotency; same key/different intent conflict tests |
| Fork or bot controls trusted workflow | Trusted default-branch `pull_request_target` code only; no PR checkout before registration; explicit bot/fork policy; negative workflow/ref/actor fixtures |
| Controller confused deputy or origin substitution | Controller may register only claims-bound repository/PR/SHA; canonical HTTPS origin plus provider observation; no caller-selected issuer endpoints; exact tuple tests |
| Origin takeover, DNS reuse, or stale domain | Origin never authorizes by itself; close/revoke old registration before reuse; controller re-proves current provider binding; certificate/DNS monitoring; takeover exercise |
| Authorization/code/device/user-code/JTI replay | High entropy, broker-secret domain-separated HMAC lookup, atomic consume-before-grant, short expiry, bounded attempts, monotonic revocation, concurrent exchange tests |
| User-code brute force or account/repository enumeration | Generic responses, progressive per-network/subject/tenant/global limits, no binding disclosure before authorization, fixed timing envelope, abuse telemetry without raw IP |
| OAuth CSRF, PKCE downgrade, callback or open redirect abuse | Secret state consumed once; S256 only; exact pre-registered callback path and origin; no caller redirect after invalid input; callback matrix tests |
| Cross-tenant/repository/PR/SHA/origin/audience capability use | Every transition and runtime check compares the complete immutable binding and tenant partition; adversarial Cartesian negative suite |
| Removed user access | Fresh access decision for every new grant; verified event/poll advances subject/binding revocation; sessions end within five minutes or earlier expiry; no stale-positive cache |
| Deleted installation or repository access removal | Disable tenant/repository registrations and new grants immediately when observed; monotonic bulk revocation and bounded purge; deletion fixture and race tests |
| Closed PR or superseded SHA | Authenticated controller transition is atomic and ordered; new lifecycle version wins; old code/grant/session invalidated within objective; close-during-exchange test |
| GitHub outage or rate limit | Fail closed for new decisions, provider-deadline/jitter/backoff/single-flight, safe retry metadata, dependency-specific SLI; never lengthen cached positive access |
| Broker outage | Local runtime verification and established sessions continue only to encoded bounds; no broker hop on page requests; codes are not replayed after recovery; outage conformance |
| Store partition, replica race, or restore rollback | Transactional consume/supersede/revoke, monotonic versions/sequences, single-writer migration, consistency checks, restore reconciliation before traffic |
| Signing-key compromise | Stop affected key, publish replacement, advance compromise epoch, invalidate affected grants/sessions, preserve audit, notify, and prove no rollback reactivation |
| Managed key loss or KMS outage | No software-key fallback; stop issuance; recover service or create/revoke to new key; existing non-compromised locally verified grants remain bounded |
| GitHub App credential compromise | Independent secret role and rotation, outbound allowlist, stop authorization, rotate/revoke App credential, review issued decisions, and advance affected revocations |
| Compromised preview runtime | It possesses public keys and its registration only; cannot sign or inspect other tenants; revoke registration; no trusted content returned on invalid binding |
| Compromised controller | OIDC policy limits it to exact source/workflow/repository; lifecycle audit and anomaly limits; controller cannot assert a user or receive signing/GitHub secrets |
| Database or backup disclosure | Encryption, compound tenant partition, HMAC secrets/identifiers, no tokens/content/private keys, separate recovery role, deletion and restore audits |
| Log, trace, metric, error, or support leakage | Closed safe error copy, allowlisted telemetry fields, unknown-field omission, query scrubbing, secret scanners, canary credentials, redacted support tooling |
| SSRF through origin/callback/JWKS fields | Canonical validation; no broker request to preview origin; issuer endpoints remain same-origin and server configured; outbound host allowlist; redirect revalidation |
| Request/body/queue/resource exhaustion | Strict byte/item/time bounds, streaming-independent parsing cap, per-tenant/global concurrency, protected close/revocation/JWKS classes, backpressure and shedding tests |
| Cache poisoning or stale JWKS | Canonical issuer pin, TLS, exact key/algorithm binding, freshness bound, refresh-once unknown key, no stale extension on network failure |
| Dependency/build-chain compromise | Locked dependencies, reviewed provenance, immutable artifacts, isolated build identity, vulnerability/signature policy, staged promotion and rollback without key rollback |
| Insider or support curiosity | Least-privilege roles, aggregate-only default, time-bounded tenant elevation, immutable audit, dual approval for key/recovery operations, periodic access review |
| Region, DNS, certificate, or issuer-domain failure | Residency-approved recovery, tested DNS/certificate control, issuer/key/revocation continuity, explicit RPO/RTO, no split-brain registration writers |

## Rate-limit and abuse policy

Rate limiting is layered by canonical route, tenant, repository binding,
subject HMAC where known, device/user-code HMAC, and a privacy-minimal network
prefix HMAC. Authorization start, user-code submission, device polling, grant
exchange, OIDC registration, JWKS, revocation, and health each have separate
budgets. Limits are configurable operator values but have reviewed hard maxima;
a tenant cannot buy or configure its way around security limits.

Failed attempts consume budget. Successful authorization does not reset brute-
force history. Device polling returns the protocol's bounded replacement delay,
and repeated violations quarantine the code. Public errors do not reveal which
limiter fired or whether a registration, repository, user, or code exists.
Abuse mitigation cannot block registration close, emergency revocation, or
key-compromise propagation behind ordinary authorization traffic.

## Secrets, logs, and debugging

Request/response bodies are excluded from default tracing. Edge and application
access logs drop query strings for callback routes. Debug mode cannot be enabled
in production and does not change field redaction. Exception reporting uses a
fixed context allowlist; arbitrary local variables, headers, cookies, bodies,
database parameters, and provider responses are excluded.

Tests seed recognizable canaries for every credential, code, token, cookie,
origin, repository, user, and key reference, exercise success and failure, and
scan logs, traces, metrics, error bodies, support exports, CI output, screenshots,
and retained evidence. A canary finding blocks release; adding a redaction
exception or hashing a reusable low-entropy secret is not an acceptable repair.

## Availability and latency assumptions

Normal preview page requests and local session checks do not call the broker.
JWKS is public, bounded, cacheable verification material; an outage cannot
extend its freshness. New authorization requires the broker, transactional
store, managed signing key, and GitHub. Registration requires broker, store,
OIDC verification material, and the trusted controller. Revocation freshness
requires broker/store communication, but local sessions retain a hard expiry.

The architecture's [service objectives](PREVIEW_BROKER_ARCHITECTURE.md#service-objectives-and-support)
are release gates, not claims about an unimplemented service. Dependency
latency, human ceremony time, and broker processing are measured separately.
Single-flight calls may collapse identical GitHub/JWKS work, but access results
are never shared across subject/binding/tenant keys or extended after failure.

## Regional and data-residency assumptions

V1 has one active write region inside the publicly disclosed residency boundary
and no active/active cross-region mutation. Encrypted backups and recovery
resources stay within the same approved boundary. Telemetry receives only the
redacted field set and must use an approved location. GitHub remains an external
subprocessor/dependency with its own processing locations, which the public
privacy disclosure must state rather than implying all processing is regional.

Adding another write region requires a new decision proving globally monotonic
registration lifecycle, one-time consumption, revocation, key state, deletion,
and tenant isolation under partitions. DNS failover alone is insufficient.

## Residual risks and accepted assumptions

- GitHub is the source of repository-access truth. A GitHub control-plane
  compromise can produce a false decision; short grants, bounded sessions,
  lifecycle revocation, and audit reduce but do not remove that dependency.
- There is a bounded delay between access/lifecycle change and an established
  local session learning it. The five-minute objective and hard session expiry
  are accepted for non-production review content; production authorization must
  not reuse this design without a separate decision.
- An attacker controlling a preview origin can present content at that origin,
  but cannot mint a grant. Exact controller registration and origin/binding
  checks prevent that origin from becoming authority for another preview.
- Traffic analysis can reveal that the public broker is in use even though
  tenant and user identifiers are redacted. Padding and anonymity networks are
  out of scope for v1.
- A dedicated service increases operational complexity. GA is not permitted
  until the ownership, exercises, SLOs, cost approval, and public disclosures in
  the architecture record are complete.

## Verification obligations

Implementation cannot satisfy this document with unit tests alone. The release
evidence must include:

- schema/typed-contract, cryptographic, replay, state-machine, and deterministic
  GitHub policy tests;
- free-threaded concurrent consume/supersede/revoke/key-rotation tests;
- cross-tenant and full cross-binding negative matrices through public paths;
- browser PKCE, device, trusted OIDC, runtime-local verification, JWKS cache,
  revocation, outage, close, and supersession journeys;
- database migration/rollback, encrypted backup/restore, deletion, key loss,
  key compromise, region/domain, and dependency-failure exercises;
- sustained and burst load evidence for latency, saturation, bounded queues,
  rate limiting, and cost-model inputs without private figures in public files;
- secret/privacy canary scans over every diagnostic and retained-evidence
  channel; and
- independent security review with no unresolved P0/P1 finding before GA.

Issue #523 owns the end-to-end matrix. Issue #522 owns deployed operational and
recovery evidence. Passing either issue's narrower checks cannot waive a
missing obligation here.

## Separate disclosure and runbook deliverables

Public privacy/support material must explain data classes and purposes,
retention and deletion, GitHub dependency, residency, authorization/session
bounds, rate-limit behavior, support scope/hours, service status, incident
communication, security reporting, and deletion/access requests.

Protected operator runbooks must contain exact resource identities, secrets and
key references, role assignments, alert routing, thresholds, provider commands,
backup/restore and migration steps, compromise playbooks, cost ceiling,
capacity forecast, support escalation, evidence locations, and decommission
procedure. Those values must not be copied into public documentation, CI logs,
issues, or PR comments.

This threat model identifies both deliverable sets but does not create them,
deploy a service, grant credentials, or authorize GA.
