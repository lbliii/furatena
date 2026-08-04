# Pull-request preview contract

Furatena preview schema version 1 defines the portable boundary between GitHub
or another review transport, a deployment provider, Furatena verification, and
reviewers. Railway is the first intended adapter, but no Railway resource name
appears in the core records.

Preview creation is a library/provider interface. A future `fura preview`
command or CI integration may call that interface, but the command is not the
contract. This keeps GitHub Actions, MCP, and other orchestrators on the same
records and lifecycle.

## Records and compatibility

The package ships Draft 2020-12 JSON Schemas under
`furatena.catalog/schemas/preview/v1/`:

- `request.schema.json` describes idempotent create, update, and stop intent.
- `manifest.schema.json` describes observed lifecycle state, reviewed source,
  provider identity, surfaces, build identity, access policy, checks, and safe
  failure details.
- `common.schema.json` contains the shared closed types.

Consumers must reject unsupported `schema_version` values and unknown record
types. Additive provider data belongs in `extensions`; changing a required
field, state meaning, or security default requires a new schema version.

## Lifecycle

| State | Meaning | Valid next states |
| --- | --- | --- |
| `requested` | An eligible review event was accepted. | `building`, `failed`, `stopping` |
| `building` | A provider is creating or refreshing the environment. | `ready`, `failed`, `stopping` |
| `ready` | Required surfaces and reviewed build identity passed readiness. | `building`, `failed`, `stopping` |
| `failed` | Structured failure and remediation are available. | `requested`, `building`, `stopping` |
| `stopping` | Teardown was requested and is being reconciled. | `stopped`, `failed` |
| `stopped` | Provider resources are confirmed removed. | `requested` for an explicit reopen |

`ready` is intentionally strict. It requires provider environment and
deployment identities, a frozen artifact whose source SHA equals the reviewed
head SHA, all recorded checks settled, and a passing `readiness` check.

This lifecycle is ephemeral. It contains no `approved`, `promoted`,
`production`, or rollback state. Promotion and rollback remain governed by the
publication contracts in `docs/PUBLICATION_WORKFLOW.md`; a preview container is
never an implicitly promotable artifact.

## Identity and metadata ownership

The stable `preview_id` is derived from repository identity and pull-request
number, so a push refreshes the same review environment. The immutable
`source.head_sha` changes with each reviewed revision and must equal
`artifact.source_sha` before the manifest can become `ready`.

| Frozen artifact identity | Runtime/provider identity |
| --- | --- |
| Source head SHA | Provider/project/environment/deployment IDs |
| Freeze fingerprint | Lifecycle state and state version |
| Build ID and catalog schema version | Dynamic preview URLs |
| Freeze timestamp | Creation, update, expiry, and teardown timestamps |
| Human and agent graph provenance | Checks, failures, and remediation |

Every provider must expose a human URL, Markdown URL template, `llms.txt`,
catalog, query, search, metadata, health, and readiness URLs. Additional
surfaces are capability-advertised through provider capabilities and extension
objects; consumers cannot assume those extensions exist.

## Idempotency and ordering

Each request carries an orchestrator-owned `idempotency_key` plus a deterministic
`request_digest` over the semantic action, reviewed source, provider, expected
state version, expiry, previous preview, and extensions.

- The same key and digest is a replay and must return the prior receipt/result.
- The same key with a different digest is a conflict and must not mutate state.
- A non-create request must bind `previous_preview_id` and should bind the
  observed `expected_state_version`; stale versions fail closed.
- A newer head SHA supersedes older create/update work. Providers may finish an
  old build, but it cannot become the current `ready` manifest.
- Stop is idempotent. Repeated close/merge deliveries converge on `stopped`.

Transport timestamps, correlation IDs, and delivery-specific idempotency keys
do not alter semantic request identity. They remain available for audit and
diagnostics.

## Provider adapter

Adapters implement `PreviewProvider` from
`furatena.catalog.preview_contracts`:

```python
class PreviewProvider(Protocol):
    id: str

    def request(self, request, previous): ...
    def observe(self, manifest): ...
    def stop(self, request, manifest): ...
```

`request` creates or refreshes resources, `observe` maps provider state into a
new manifest version, and `stop` requests/reconciles teardown. Provider-native
states are translated into the six Furatena states and may be retained only in
extensions for diagnostics.

## Security baseline

Version 1 can represent several authentication schemes but has one indexing
policy: `noindex,nofollow`. Untrusted-fork and bot policies are explicit and
default to `deny`. The contract records these guarantees; enforcement across
HTML and machine-readable routes is implemented by the preview security task.

Canonical examples live in `tests/fixtures/preview/v1/`. Integrators should
validate both incoming requests and emitted manifests against the shipped
schemas before acting on them.

Runtime enforcement, deployment variables, authentication instructions, and
the threat model are documented in [Pull-request preview security](PR_PREVIEW_SECURITY.md).
The provider-neutral reviewer authorization wire protocol is specified
separately in [Preview authorization protocol v1](PREVIEW_AUTH_V1.md); it does
not alter the deployment-provider lifecycle records on this page.
