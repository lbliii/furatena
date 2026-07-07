# Trusted gateway and SSO identity

Furatena treats gateway identity as trusted only when deployment-owned code has
already authenticated the direct transport. Examples include an mTLS-verified
proxy peer, a platform middleware assertion unavailable to clients, or an
in-process SSO adapter. Never derive `trusted_transport=True` from
`X-Forwarded-*`, `X-User`, `X-Roles`, or a client-provided “verified” header.

`map_gateway_claims` converts provider claims into an `AccessSubject` plus the
tenant, workspace, and site boundary. The default aliases are provider-neutral:

| Furatena field | Accepted default claims | Requirement |
|---|---|---|
| actor | `sub`, `actor` | required, one unambiguous scalar |
| roles | `roles`, `role` | required, built-in Furatena roles only |
| teams | `teams`, `team` | optional string or string array |
| tenant | `tenant`, `tenant_id`, `tid` | required and catalog-bound |
| workspace | `workspace`, `workspace_id` | optional; defaults to `default` |
| site | `site`, `site_id` | required and catalog-bound |

If two aliases are present, their normalized values must agree. Unknown roles,
invalid scalar values, missing required claims, and tenant/workspace/site
conflicts all fail closed with `GatewayIdentityError`.

## Provider-neutral example

```python
from furatena.catalog.gateway_identity import map_gateway_claims

identity = map_gateway_claims(
    verified_claims,
    trusted_transport=deployment_middleware_verified_peer,
    expected_identity={
        "tenant": docs.config.identity.tenant,
        "workspace": docs.config.identity.workspace,
        "site": docs.config.identity.site,
    },
)

# Store only in the server-signed session, never in an unsigned client cookie.
session["fura_author_subject"] = identity.to_session()
```

For a provider with different names, configure aliases rather than adding
provider-specific authorization logic:

```python
from furatena.catalog.gateway_identity import (
    GatewayClaimMapping,
    TrustedGatewayPolicy,
)

policy = TrustedGatewayPolicy(
    mapping=GatewayClaimMapping(
        actor=("user_name",),
        roles=("entitlements",),
        teams=("groups",),
        tenant=("organization",),
        workspace=("space",),
        site=("application",),
    )
)
```

Use `identity_from_trusted_session` only after the framework has verified the
session signature. It checks the server-owned marker, canonical fingerprint,
and configured catalog identity again before returning the subject.

The verified subject is reused across direct browser pages, JSON and text
exports, search and semantic retrieval, DCP graph queries, and MCP resources
and tools. An author-mode `include_private=1` request enables protected output
but does not bypass page or mount role/team policy. If the signed identity is
missing or inconsistent, these surfaces evaluate the request as anonymous and
omit protected content.

## Failure behavior

| Error code | Meaning |
|---|---|
| `untrusted_transport` | Claims arrived outside the deployment trust boundary. |
| `missing_claim` | A required claim or role set is absent. |
| `conflicting_claim` | Multiple accepted aliases disagree. |
| `invalid_claim` | A claim has an unsafe type or value. |
| `unknown_role` / `role_denied` | A role is unknown or outside deployment policy. |
| `tenant_denied` / `site_denied` | Deployment allowlists reject the boundary. |
| `identity_conflict` | Tenant, workspace, or site differs from the catalog. |
| `untrusted_session` / `session_conflict` | A signed-session payload is not canonical. |

On every error, reject the request or keep it anonymous; do not partially apply
claims or fall back to client headers. The mapper is immutable and is covered by
concurrent CPython free-threading tests.
