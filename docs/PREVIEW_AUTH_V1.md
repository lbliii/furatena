# Preview authorization protocol v1

Preview-auth v1 is the provider-neutral authorization boundary among a hosted
broker, preview runtime, browser, CLI or agent, and preview controller. It
defines messages and validation rules, not an HTTP server, identity provider,
deployment adapter, or authorization policy. No GitHub, Railway, or other
provider object is part of the contract.

The Python models live in `furatena.catalog.preview_auth_contracts`. Shipped
Draft 2020-12 schemas and canonical examples live under
`furatena.catalog/schemas/preview-auth/v1/` and
`furatena.catalog/fixtures/preview-auth/v1/`.

## Trust boundary

Parsing `PreviewSignedGrant` proves only that a compact JWS is bounded,
well-formed, uses canonical JSON, names an allowed algorithm and key ID, and
contains valid v1 claims. **Parsing is not cryptographic verification and does
not make a grant trusted.** A runtime must verify the JWS signature with a
fresh, issuer-bound JWKS before applying claims, then enforce time, revocation,
and the entire immutable binding.

The contract intentionally adds no crypto dependency. Broker signing and
runtime verification belong to the runtime tasks that consume this protocol.

## Messages

Every top-level message requires `schema_version: 1`, a closed `record_type`,
all declared fields, and no unknown fields.

| Record | Direction | Purpose |
|---|---|---|
| `registration` | controller → broker/runtime | Binds one repository, PR, head SHA, and origin to broker endpoints, callbacks, audiences, and lifetimes. |
| `authorization-request` | client → broker | Starts either a browser PKCE flow or a non-browser device flow. |
| `one-time-code` | broker → client | Returns an opaque authorization or device code with the original immutable binding. |
| `grant-exchange` | client → broker | Consumes a code exactly once; browser exchange includes the verifier and callback. |
| `signed-grant` | broker → client | Wraps one compact JWS bearer grant. |
| `grant-claims` | signed JWS payload | Carries issuer, audience, subject, repository, PR, SHA, origin, nonce, key, JTI, times, and scopes. |
| `jwks` | broker → runtime | Publishes only public verification keys plus cache freshness bounds. |
| `revocation` | broker → runtime | Invalidates one JTI for one exact subject and preview binding. |
| `error` | any responder → client | Returns a stable safe code, remediation, retry policy, redirect decision, and version diagnostics. |

All strings and arrays are bounded; callback paths, audiences, scopes, and JWKS
rollover sets have explicit maxima shared by schemas and typed validators.
Closed enums prevent a v1 reader from guessing at new security meaning.
Canonical bytes are UTF-8 JSON with sorted
keys, no insignificant whitespace, no NaN/Infinity, and no ASCII escaping.
Protected JWS headers and claim payloads must use those exact canonical bytes
and canonical unpadded base64url. Parsing rejects claims whose typed,
normalized serialization differs from the signed payload bytes;
the claim payload also carries `ver: 1` so it cannot be detached and
reinterpreted as another claim version.

All registration endpoints use the issuer's canonical HTTPS origin and contain
no credentials, query, or fragment. This keeps discovery from becoming a
cross-origin deputy; a future version is required before supporting separately
hosted endpoint origins.

## Immutable binding and origin

The binding is the tuple `(repository_id, pull_request_number, head_sha,
origin)`. A broker and runtime compare every member exactly at registration,
authorization, code issuance, exchange, grant verification, and revocation.
Changing a head SHA requires a new registration and invalidates codes, grants,
and sessions for the previous SHA. A matching origin or PR alone is never
sufficient.

An origin is canonicalized as follows:

1. Require HTTPS; there is no localhost or broad HTTP exception in v1.
2. Reject credentials, paths other than an input `/`, query, and fragment.
3. Reject percent-escaped, zone-qualified, malformed DNS, invalid punycode, and
   ambiguous numeric host spellings. Convert valid names with IDNA 2008/UTS 46
   non-transitional processing and STD3 rules, remove one terminal DNS dot, and
   compress canonical IP literals.
4. Omit port 443 and retain any other explicit valid port.
5. Serialize exactly as `https://host[:nondefault-port]` without a trailing
   slash.

Browser callback URIs contain a path but no credentials, query, or fragment;
callback query parameters are reserved for the protocol's exact `code`,
`state`, and `error` values. Their canonical origin must equal the registered preview origin, and
their path must be one of `registration.redirect_paths`. The runtime—not the
structural model—performs that allowlist lookup.

## Browser and machine clients

Browser requests require an unguessable `state`, an unguessable `nonce`, an
exact same-origin callback, and PKCE `S256`. `plain` and omitted PKCE methods
are invalid. The broker returns an authorization code only after authorization;
the callback exchanges it with the original redirect URI and code verifier.
The redirect query contains only the opaque code and exact original state; the
full one-time-code JSON record and subject are never placed in a URL.

Device clients prohibit redirect URI, state, challenge, and verifier fields.
They receive a secret device code, an unambiguous `XXXX-XXXX` user code, an
HTTPS verification URI, and a 1–30 second polling interval. The user completes
the out-of-band ceremony at that URI; the client exchanges the device code
directly and honors the interval. Before approval, the broker returns
`authorization_pending` with `retry_after_seconds`; polling faster returns
`slow_down` with a replacement delay. Both are direct, retryable responses and
can never redirect. The resulting compact grant is a Bearer
credential. Sending bearer grants in URLs, redirects, logs, or browser storage
is forbidden.

An error may redirect only when all of these are true: the original message was
a valid browser request, its callback passed registration and exact-origin
validation, and the response can return the exact original state. Errors before
that point, all device errors, invalid-origin and invalid-binding errors,
unsupported versions, and server errors set `redirect_allowed: false` and are
returned directly. A positive redirect carries both the validated `request_id`
and exact original secret `state`; consumers derive the callback from their
trusted stored request and never trust a caller-selected URI. Error
text never contains a code, verifier, grant, nonce, state, signature, subject,
or repository binding.

## One-time and replay rules

- Authorization codes and device codes contain at least 256 bits of random
  base64url material. Storage keeps only a broker-secret HMAC, never the
  plaintext code. `preview_auth_consumption_digest()` requires at least 32
  secret bytes and provides the issuer- and full-binding-separated storage key
  for codes, user codes, nonces, and JTIs. A database disclosure therefore does
  not permit offline enumeration of the lower-entropy human user-code space.
- A code row atomically changes from `issued` to `consumed` before a grant is
  returned. Concurrent or later exchanges return `replayed_code`; they never
  return the previous grant.
- Consumption checks code kind, expiry, client kind, audience, nonce, complete
  binding, redirect URI, and PKCE verifier together. A failed check consumes or
  quarantines the code according to broker policy; it never weakens a check on
  retry.
- Browser state is consumed at callback completion. Nonce is copied into the
  grant and consumed by the preview session establishment. A runtime rejects a
  nonce or JTI already consumed for that session boundary.
- Revocation sequence is monotonically increasing per broker stream. Duplicate
  `(issuer, jti, sequence)` signals are idempotent; an older sequence cannot
  undo newer revocation state.

Registration and revocation records are structurally validated but are not
self-authenticating. A runtime accepts them only over its authenticated,
issuer-pinned broker/controller channel. Receiving valid JSON from an
unauthenticated caller never registers a preview or revokes a grant.

## Lifetimes and time checks

| Control | v1 default | Accepted contract range |
|---|---:|---:|
| Authorization code | 120 seconds | 30–300 seconds |
| Signed grant | 300 seconds | 60–600 seconds |
| Preview session | 3,600 seconds | 300–43,200 seconds |
| JWKS cache freshness | 300 seconds | 30–900 seconds |
| Clock skew | 30 seconds | 0–60 seconds |
| Key rollover overlap | 600 seconds | 600–86,400 seconds |

Authorization requests expire within 300 seconds. One-time-code records cannot
exceed the 300-second contract maximum, and grant claims cannot exceed the
600-second contract maximum; producers use the registration's shorter defaults.
A verifier checks `iat`, `nbf`, and `exp` against its own clock with at
most the negotiated skew; it never extends the encoded `exp`. Sessions may not
outlive the registration, preview head, repository access, or revocation even
when the configured session lifetime is longer.

## Signing keys, rollover, and cache failure

Compact grants allow only `EdDSA` with an `OKP`/`Ed25519` public key or `ES256`
with an `EC`/`P-256` public key. The protected header contains exactly `alg`,
`kid`, and `typ: FURA-PREVIEW-GRANT+jwt`. Its `kid` must equal the claim `kid`.
JWKS entries require `use: sig` and `key_ops: [verify]`; private members such as
`d`, provider metadata, and other key operations are rejected.
Coordinates and the 64-byte JWS signature use canonical unpadded base64url;
alternate spellings with nonzero unused padding bits are rejected before
cryptographic verification.

During rollover, publish the new verification key before signing with it and
retain the old public key for at least the rollover overlap. On an unknown
`kid`, a verifier may refresh once; it then fails with `unknown_key`. Cached
keys are usable only through `stale_after`. Refresh failure before that time may
use the cached set; at or after that time it fails closed with `stale_key_set`.
It never extends freshness because a network request failed. Key-compromise
revocation bypasses cache convenience and invalidates affected grants.

## Furatena runtime integration

`furatena.catalog.preview_grant_runtime` implements the preview-side v1
boundary. `PreviewGrantRuntime` owns bounded pending-login, replay, revocation,
session, and immutable JWKS state behind explicit reentrant locks for
free-threaded CPython. Unknown-key refresh is single-flight, cached snapshots
are immutable, and all broker operations are isolated from normal local session
reads. `PreviewGrantSecurityMiddleware` moves the potentially blocking browser
exchange and key-refresh paths off the application event loop.

The integration validates issuer, audience, repository ID, pull request, head
SHA, canonical origin, registration window, grant times, scope, JTI, subject,
key ID, nonce, and revocation before granting access. Browser callback state,
authorization code, nonce, and session-establishment JTI are consumed once.
Only `/healthz` and `/readyz` bypass the middleware; every content and machine
surface remains behind the same authorization boundary.

## Versioning and compatibility

The v1 registration fixture advertises `supported_versions: [1]` and
`deprecated_versions: []`. The supported list may contain up to 16 positive
versions when a producer implements newer protocols, but it must include the
registration message's own v1 reader. A producer selects the highest mutually
supported version before sending any secret. No overlap
returns `unsupported_version`, `supported_versions`, no redirect, and an action
to retry with a listed version. A v1 reader rejects unknown record types,
unknown fields, missing fields, new enum values, changed types, and any
`schema_version` other than 1 rather than guessing.

JSON Schema is the portable structural gate and enforces every expressible wire
rule, including closed fields, canonical UTC spelling, URL shape, array bounds,
branch-specific nullability, and redirect/device-error correlations. Typed
validation is also mandatory before security decisions because standard JSON
Schema cannot compare two fields for exact same-origin/issuer binding or express
time-window arithmetic. The paired negative test corpus identifies those
semantic-only checks explicitly; schema-valid JSON is necessary but not by
itself authorization.

Additive semantics require either an explicitly defined optional extension
point or a new version; v1 has no generic extension object at this security
boundary. Required-field removal, field reinterpretation, enum widening,
algorithm changes, weaker binding, or weaker validation require v2. A version
may be listed in `deprecated_versions` only while it remains supported and must
include an announced removal window and migration instructions. V1 currently
has no deprecated version.

## Threat model

| Threat | Required defense |
|---|---|
| Code, nonce, state, or JTI replay | High-entropy values, broker-secret HMAC storage, atomic consumption, short expiry, and session/JTI replay cache. |
| Open redirect | Pre-registered callback path, exact canonical origin, state echo only after validation, and no redirect on early errors. |
| Confused deputy | Exact issuer, audience, subject, client kind, and full preview binding checks at every transition. |
| Cross-origin reuse | Origin in request, code, exchange, claims, and revocation; exact canonical comparison. |
| Cross-repository/PR/SHA reuse | Immutable repository ID, PR, and head SHA in every authorization artifact; any mismatch fails closed. |
| Algorithm/key confusion | Closed algorithm list, exact `kid`, kty/curve/alg binding, verify-only JWKS, no private material, and no `none` or symmetric algorithms. |
| Stale or compromised keys | Bounded cache, fail-closed stale expiry, overlap discipline, refresh-once unknown-key policy, and compromise revocation. |
| Log or redirect disclosure | Field-aware redaction, direct machine errors, no bearer/code/verifier in URLs, and safe fixed error messages. |

## Sensitivity and redaction

“Hash” means a one-way, domain-separated audit correlation value; it is not a
reversible encoding. Logs omit whole signed grants because their payload is
sensitive even though it is signed.

| Fields | Classification | Log/telemetry rule |
|---|---|---|
| schema version, record type, supported/deprecated versions, client/code kind, lifetimes, public endpoint names, algorithms, public JWKS | Public | May retain after normal URL hygiene. |
| repository ID, PR number, head SHA, preview origin, registration/request/exchange IDs | Confidential repository metadata | Hash; never place in error prose. |
| audience, redirect URI/path, code challenge, key ID, timestamps, scopes, revocation reason/sequence | Confidential | Retain only in access-controlled structured audit; omit from general logs. |
| subject | Pseudonymous personal data | Hash; never include in errors. |
| nonce and JTI | Confidential correlators | Hash and access-control; do not echo in errors. |
| validated browser state on a redirectable error | Secret callback correlation | Return only to the validated callback and redact everywhere else. |
| device `retry_after_seconds` | Public control | May retain; it contains no authorization result or identity. |
| browser state, authorization/device code, device user code, PKCE verifier, compact grant and signature | Secret | Replace with `[REDACTED]`; never hash into general telemetry or place in URLs except the one-time browser code callback. |
| safe message, remediation, retryable, redirect allowed | Public by construction | V1 uses fixed code-specific copy/retry semantics; arbitrary or secret-bearing copy is rejected. |

`redact_preview_auth()` implements the conservative general-log view: it
redacts secrets, hashes documented confidential values, retains only a closed
allowlist of public fields, and omits unknown fields by default. Broker/runtime
implementations must add storage-level controls and must not rely on redaction
as authorization.

## CLI, configuration, and generated-reference impact

V1 adds no `fura` command, option, `docs.yaml`/`mounts.yaml` field, or
`FURA_*`/`CHIRP_*` environment variable. Therefore the parser/config/environment
generated reference is not reached and must not be regenerated for this
contract-only change. Runtime configuration and CLI workflows will be added by
the broker and client implementation tasks, with their own generated-reference
updates. The public surface in this change is the versioned wire schema. The
Python module is the package's typed validator/reference implementation but is
not re-exported as a supported top-level Python API; there is no partially
functional command.
