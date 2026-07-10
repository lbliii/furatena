# Publication workflow contracts

Furatena models publication as a versioned, fail-closed workflow. A caller first
creates an immutable `PublicationPlan`, records immutable decisions, and advances
a compare-and-swap `PublicationStateSnapshot`. Every successful state change emits
an immutable `PublicationEvent`. Provider execution and persistence are separate
services; these records are the shared boundary they consume.

The Python contracts live in `furatena.catalog.publication_contracts`, guarded
transitions live in `furatena.catalog.publication_state`, and the Draft 2020-12
JSON Schemas ship under `furatena/catalog/schemas/publication/v1/`.

## Records and source of truth

| Record | Purpose | Authority |
| --- | --- | --- |
| `PublicationPlan` | Binds the requested operation, exact source change, validation result, policy, approvals, expiry, and intended outputs. | Immutable authorization input. |
| `PublicationDecision` | Records an approval, rejection, revocation, waiver, expiry, override, or supersession against one plan and policy digest. | Immutable decision history. |
| `PublicationEvent` | Records one accepted state transition and any safe failure or provider-output references. | Immutable transition history. |
| `PublicationStateSnapshot` | Holds the current state and monotonically increasing `state_version`. | Operational source of truth. |

The compare-and-swap snapshot is the authority for current workflow state.
Decisions and events explain how it reached that state. Audit records, CLI text,
HTTP views, MCP responses, badges, and other UI labels are derived projections;
they must not be read back as workflow state. Source files and provider responses
also do not replace the snapshot. If provider state is uncertain, the workflow
records a `reconciliation_required` failure and requires an explicit reconciliation
guard before retrying execution.

## Canonical authorization digest

`PublicationPlan.plan_digest` is SHA-256 over canonical UTF-8 JSON with sorted
object keys, compact separators, finite JSON numbers, normalized RFC 3339
timestamps, and deterministically sorted set-like collections. The digest covers:

- creator identity and roles;
- expiry, operation, visibility change, identity, and exact diff digest;
- source, catalog, configuration, policy, and validation bindings;
- validation diagnostics and waivability;
- impact and risk;
- approval count, eligibility, self-approval rule, and separation rules;
- intended provider outputs.

Transport-only `correlation_id`, retry-only `idempotency_key`, and passive
`extensions` are excluded. Changing any authorization-relevant field creates a new
digest and plan ID, so an old decision cannot authorize a changed plan. The exact
diff digest is calculated over the original UTF-8 bytes.

## State transitions

The transition table is intentionally explicit:

| Current state | Allowed next states |
| --- | --- |
| `proposed` | `validating`, `cancelled`, `superseded`, `expired` |
| `validating` | `reviewable`, `failed`, `cancelled`, `superseded`, `expired` |
| `reviewable` | `validating`, `awaiting_approval`, `approved`, `cancelled`, `superseded`, `expired` |
| `awaiting_approval` | `reviewable`, `approved`, `cancelled`, `superseded`, `expired` |
| `approved` | `executing`, `awaiting_approval`, `failed`, `cancelled`, `superseded`, `expired` |
| `executing` | `applied`, `failed` |
| `failed` | guarded retry to `validating`, `approved`, or `executing`; otherwise `cancelled`, `superseded`, or `expired` |
| `applied`, `expired`, `cancelled`, `superseded` | none |

Every transition requires the current `state_version`. A mismatched version, plan
binding, source/configuration/policy/validation binding, expired plan, missing
approval, illegal edge, or terminal snapshot is rejected before a new snapshot or
event is returned. Execution rechecks both current bindings and approvals.

Failures use one of `retryable`, `terminal`, `conflict`, `authorization`, or
`reconciliation_required`. A failed workflow can retry only its declared
`retry_target`; an execution retry additionally requires recorded reconciliation.

## Projections and transport envelopes

`to_dict()` defaults to the trusted projection. It includes source paths, diffs,
provider URLs, idempotency data, full identities, and extensions and must remain
inside an authorized workflow boundary.

The audit projection removes diff content, source paths, provider URLs,
idempotency keys, passive extensions, and unnecessary identity attributes while
preserving stable digests, actor identifiers, safe failure text, and output IDs.
The public projection removes tenant/source identity, actors, validation details,
decisions, failures, and output references. Audit/public serialization is a
sanitized copy, never a persistence or authorization input.

CLI, HTTP, and MCP use their established outer envelopes but carry identical
trusted record bytes. For a plan, the shapes are:

- CLI: `{"data":{"publication_plan":...}}`
- HTTP: `{"publication_plan":...}`
- MCP: `{"structuredContent":{"publication_plan":...}}`

The corresponding keys are `publication_decision`, `publication_event`, and
`publication_state`. Consumers decode only the envelope expected for their
transport and reject missing or ambiguous record keys. They must also verify each
record's digest during decoding.

## Compatibility policy

All four records declare `schema_version: 1` and a fixed `record_type`. Readers
reject unknown schema versions, record types, digest mismatches, and unknown
fields in the JSON Schema contract. Within v1:

- adding optional data is allowed only inside `extensions`;
- changing required fields, enums, digest coverage, canonicalization, redaction,
  state meaning, or transition semantics requires a new schema version;
- new readers must continue accepting every valid v1 fixture;
- writers emit one version and never silently downgrade authorization fields;
- a version converter must create a new record and recompute its digest rather
  than mutate signed or approved history.

The checked-in v1 fixtures cover solo-local publication, a protected pull request,
deployment, rollback, rejection, expiry, and supersession. Contract tests validate
the Python round trips, schemas, envelopes, projections, digest stability, and
guarded transitions together.

## Ownership boundaries

This module defines records, serialization, redaction, and legal transitions. The
workflow service owns durable compare-and-swap persistence and event append; the
approval service evaluates eligible decisions; provider adapters create repository
or deployment effects; the validation service supplies the bound validation
snapshot. None of those components may weaken these guards or mutate an existing
plan, decision, or event in place.
