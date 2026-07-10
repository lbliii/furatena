# Publication workflow service

`PublicationWorkflowService` is the single provider-neutral orchestration boundary
for browser, CLI, MCP, and automation publication requests. Transports authenticate
their trusted actor and preserve their existing CSRF/session controls, then submit
the same command to the service. They do not sequence workflow transitions or call
source/provider mutations directly.

## Ownership and collaborators

The service owns plan persistence, validation progression, execution preconditions,
cancellation, supersession, expiry, typed retry, reconciliation, operation receipts,
and audit emission. It composes the immutable publication records and state engine,
the current capability policy, generation-scoped validation, approval evaluation,
and a `PublicationExecutor`. Local authoring and repository providers implement that
executor boundary; they cannot weaken plan bindings or expand approved paths.

Planning remains read-only. Callers build a canonical `PublicationPlan`, whose ID
and digest exclude correlation and idempotency metadata, before `create_plan`
persists its initial `proposed` snapshot.

## Execution invariants

Every execution reloads the durable plan and snapshot while holding a per-plan
`OperationLease`, then rechecks:

- expected state version and approved state;
- source revision, catalog generation, configuration, policy, and the cached
  validation-snapshot binding;
- the actor's current scoped capability;
- approval count, eligibility, separation, waiver, and expiry rules.

The service writes a `started` operation receipt and the `executing` snapshot before
calling the executor. The executor receives the exact immutable plan. A successful
result becomes `applied`; a known failure becomes a typed `failed` snapshot; an
unknown effect becomes `reconciliation_required`. A restarted worker that finds a
started receipt never repeats the effect. It records the unknown outcome and routes
the plan through `reconcile`.

Exact idempotency-key replay returns the stored outcome. Reusing a key with a
different command, plan, expected version, actor, or canonical input digest fails
with `idempotency_conflict`. Plan leases serialize workers across processes, and
state compare-and-swap remains the final stale-writer guard.

## Operational persistence

`PublicationWorkflowStore` is separate from `AuditStore`. The in-memory
implementation supports tests and embedding. `JsonDirectoryPublicationWorkflowStore`
provides restart-safe standalone persistence using private directories and files,
create-if-absent plans/receipts, atomic snapshot replacement, immutable versioned
events, fsync, and strict identity validation. Corrupt records fail closed.

The operational store is the source of truth for recovery and exactly-once
sequencing. Audit receives a redacted copy after an operational commit; audit data
is never replayed to reconstruct state.

## Lifecycle commands

`validate` uses the plan's generation-scoped validation snapshot and never triggers
a request-time full-site validation. `execute` performs the guarded effect.
`cancel`, `supersede`, and `expire` are idempotent state-only commands. `retry`
follows only the target encoded by the current typed failure. Execution retries with
an unknown effect are prohibited until `reconcile` records provider/source evidence.

Read APIs expose the current projection, immutable event history, and operation
receipt status. Audit and public projections omit private paths, diffs, provider
URLs, actor roles/teams, and operation internals.

Version 1 command, response, and receipt schemas ship under
`furatena/catalog/schemas/publication-workflow/v1/`.
