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
calling the executor. The executor receives the exact immutable plan. Legacy
executors may still return output references directly, which means `applied`.
Context-aware executors return a `PublicationExecutionResult`: completed local and
commit effects become `applied`, while an open pull request returns to `reviewable`
with typed provider-review outputs. Review creation is never reported as deployment or application.
A known failure becomes a typed `failed` snapshot; an unknown effect becomes
`reconciliation_required`. A restarted worker that finds a started receipt never
repeats the effect. It records the unknown outcome and routes the plan through
`reconcile`.

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
Reconciliation also observes provider-backed `reviewable` snapshots: open or draft reviews remain pending,
merged reviews become `applied`, and closed, rejected, divergent, or unknown outcomes
remain explicit rather than being inferred as deployed.

`PublicationProviderExecutor` is the concrete adapter from provider profiles to this
workflow boundary. Trusted composition supplies a `PublicationProviderSelection`
for each plan: one explicit `local_only`, `commit`, or `pull_request` profile, the
exact repository base revision, and actor-bound commit attribution. The adapter does
not infer a mode from Git state, credentials, remotes, or installed SDKs. It sequences
the profile's provider operations and translates typed provider failures into typed
workflow failures.

Provider profiles and every validated result are durably recorded by
`JsonDirectoryPublicationProviderExecutionStore`. Workflow outputs reference the
profile digest and immutable result IDs/digests, plus the latest review, protection,
and reconciliation states. The JSON store writes mode-restricted immutable records
and an atomic order index; its in-memory counterpart owns state behind an explicit
lock for free-threaded embedding and tests.

## Immutable publication artifacts

`PublicationArtifactExecutor` wraps an executor result without changing provider
semantics. It starts a build only after the delegate reports `applied` and an output
contains an exact 40-character commit. Reviewable pull requests pass through
unchanged. Trusted composition supplies the approval references and the freeze/export
builder; the artifact request binds those references, the plan and workflow digests,
the source repository and commit, actor, and idempotency identity.

`GitPublicationArtifactCheckout` clones the repository into private staging, checks
out the exact commit with detached HEAD, and verifies the tree is clean before and
after the builder runs. Uncommitted author-workspace content therefore cannot enter
the build, and a builder that writes into its source checkout fails closed.

The builder returns explicit configuration, presentation, dependency-lock, runtime,
toolchain, builder, build-command, renderer, theme, catalog, mount, channel, edition,
Content IR, and frozen-output identities. It also maps every affected public
projection to generated files. The service inventories every output with media type,
size, and SHA-256; scans all generated projections for draft, private, protected, and
archived source canaries; and promotes staging only after full verification.

The content address covers every deterministic manifest field. Creation time and
optional provenance/attestation references are declared identity exclusions, so an
identical build retains one address while each stored manifest still records those
facts. Only a fully verified artifact can atomically advance the monotonic
current-artifact generation; failed, partial, or older replayed builds leave the last
complete pointer unchanged. Full
verification rejects changed, missing, symbolic-link, or uninventoried files.
`status` reads the manifest/verification contract, while `readiness` follows the
current pointer by default, performs full output verification, and fails closed with
remediation.

Version 1 manifest, verification, and current-pointer schemas ship under
`furatena/catalog/schemas/publication-artifact/v1/`.

Read APIs expose the current projection, immutable event history, and operation
receipt status. Audit and public projections omit private paths, diffs, provider
URLs, actor roles/teams, and operation internals.

Version 1 command, response, and receipt schemas ship under
`furatena/catalog/schemas/publication-workflow/v1/`.
