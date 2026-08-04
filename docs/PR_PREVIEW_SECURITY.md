# Pull-request preview security

Furatena treats a pull-request preview as a distinct, commit-bound deployment,
not as another name for the normal frozen `preview` serve mode. The environment
must set `FURA_PR_PREVIEW=1` and provide all of this public identity:

| Variable | Purpose |
| --- | --- |
| `FURA_PREVIEW_PR_NUMBER` | Positive pull-request number shown in the review banner. |
| `FURA_PREVIEW_SHA` | Full 40- or 64-character reviewed commit SHA. |
| `FURA_PREVIEW_REVIEW_URL` | HTTPS link to the pull-request review. |
| `FURA_PREVIEW_ORIGIN` or `RAILWAY_PUBLIC_DOMAIN` | Dynamic HTTPS origin for canonical and Open Graph URLs. |
| `FURA_BUILD_GIT_SHA` | Immutable image source SHA; when present it must equal `FURA_PREVIEW_SHA`. |

The app fails closed when identity is missing, malformed, bound to a different
build, or started outside frozen preview mode. An inherited production
`FURA_BASE_URL` is deliberately ignored in a PR preview.

## Authentication boundary

The shared deployment token below remains the default preview gate. Furatena
also ships the runtime side of the versioned
[preview authorization protocol](PREVIEW_AUTH_V1.md): callers can inject a
`PreviewGrantRuntime` into `DocsApp.from_paths(preview_grant_runtime=...)` after
loading a trusted registration and broker adapter. Deployment-mode selection
and credential migration remain explicit orchestration concerns; constructing
a runtime does not silently replace the existing gate.

The hosted runtime completes browser PKCE authorization once, verifies Ed25519
or ES256 grants locally against an issuer-bound bounded JWKS cache, and creates
a commit-bound server session. Its browser cookie is named
`__Host-furatena-preview` and always uses `Secure`, `HttpOnly`, `SameSite=Lax`,
`Path=/`, and no `Domain`. Normal session reads and warm known-key Bearer reads
make no broker request. An existing session therefore remains usable during a
bounded broker or JWKS outage, while new sign-ins and stale-key verification
fail closed.

HTML navigations without a session redirect to the registered authorization
endpoint. Markdown, JSON, search, catalog/query, metadata, DCP, MCP, CLI-shaped,
and asset requests return a stable HTTP 401 problem response with a Bearer
challenge instead of HTML. Hosted protected responses use
`Cache-Control: private, no-store`, vary on both `Cookie` and `Authorization`,
and carry the preview robots policy. `/healthz` and `/readyz` remain the only
authorization bypasses.

`FURA_PREVIEW_AUTH_TOKEN` is a deployment-only secret of at least 32
characters. It must be injected at runtime as a sealed provider variable; it
must not be a Docker build argument, committed value, frozen artifact, URL
parameter, or rendered template value.

The outermost application middleware authenticates before route dispatch or
static-file handling. A browser can use HTTP Basic authentication with username
`preview` and the token as its password. An agent can send the same token as a
Bearer credential:

```console
curl -H "Authorization: Bearer $FURA_PREVIEW_AUTH_TOKEN" \
  https://<preview-origin>/catalog.json
```

In shared-token mode, HTML, htmx fragments, Markdown, JSON, XML, inventories,
theme files, scripts,
fonts, images, and error routes share this policy. Authorized and denied review
responses use `Cache-Control: private, no-store`, vary on `Authorization`, and
carry `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet`. Full HTML also
contains the equivalent robots meta directive and a persistent banner linking
the PR and showing the reviewed SHA.

`/healthz` and `/readyz` are the only unauthenticated exceptions because the
deployment provider cannot attach a review credential to its health check.
They contain operational status only, never catalog content, URLs, credentials,
source text, or reviewer identity, and still carry `X-Robots-Tag`.

## Threat model and orchestration requirements

This boundary prevents an unauthenticated internet client, crawler, or shared
cache from reading review content. It also prevents a preview from silently
advertising production canonical URLs and prevents a stale image from claiming
the current PR SHA. TLS termination and secret storage remain provider
responsibilities. A shared review token does not provide per-reviewer audit or
revocation; rotate it when membership changes or disclosure is suspected.

The application cannot establish whether a GitHub event came from a fork or a
bot. The orchestrator must deny untrusted forks and bot-authored pull requests
before creating an environment, use only sealed variables explicitly approved
for previews, and never copy production author sessions, source credentials,
publication credentials, or private mounts. Furatena's preview serve mode
independently excludes private and draft catalog nodes and exposes no author
mutation routes. Preview artifacts are review-only and cannot be promoted.

The Railway controller requires the provider-created PR environment to contain
an isolated instance of the configured service before it changes variables or
requests a redeploy. Its command boundary excludes source-link operations, and
it verifies before and after preview control that production remains on the
repository's `main` branch and never receives the pull-request head. A missing
service or unverifiable production state fails the preview without attempting
to repair provider configuration.

On close or merge, the provider adapter must delete the environment and its
domain. A teardown is complete only after provider state confirms removal, as
defined by the [preview lifecycle contract](PR_PREVIEW_CONTRACT.md).
