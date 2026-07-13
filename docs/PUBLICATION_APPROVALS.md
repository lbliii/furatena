# Publication approvals, warning waivers, and workflow audit

Furatena authorizes an immutable publication plan through durable decisions bound
to its canonical digest and current capability policy. An approval never means
“publish whatever is current.” Any source, diff, validation, configuration,
policy, path, environment, or resulting-lifecycle change creates a different plan
or scope and invalidates the prior record.

The implementation lives in `furatena.catalog.publication_approvals`. Version 1
record and evaluation schemas ship under
`furatena/catalog/schemas/publication-approval/v1/`.

## Records and scope

The existing `PublicationDecision` remains the immutable decision core. A
`PublicationApprovalRecord` adds the operational bindings required for recovery:

- idempotency key and canonical input digest;
- policy version and policy digest;
- correlation ID;
- tenant, workspace, site, mount, node, approved paths, environments, and
  resulting visibility;
- target record IDs for revoke, expire, and supersede decisions.

The approval-record digest covers every field. Trusted records include the
idempotency key, full actor roles/teams, reason, and scope. Audit and public
projections progressively remove private operational or identity detail.

## Decision guards

`PublicationApprovalService` reauthorizes the trusted actor before persistence.
Approvers must match an eligible role or team. No-self-approval compares both
actor and identity source. Multiple approvals from one identity count once, so a
two-person policy requires two distinct trusted identities. Team-owned policy
accepts only members of the configured eligible team.

Warning waivers require a reason, owner, explicit expiry, and a non-empty subset
of the plan's `waivable_warning_ids`. They cannot outlive the plan or suppress an
unlisted diagnostic. Emergency override requires the separate elevated override
capability and a mandatory reason.

Revoke, expire, and supersede records target existing non-control decisions from
the same plan digest. Cross-plan targets and chains that attempt to revoke a
control record fail before persistence.

## Evaluation and execution

Evaluation folds immutable records in timestamp/record-ID order and returns
stable effective, blocking, invalidated, and waived record IDs. It rechecks:

- exact plan digest, policy version/digest, and evaluated scope;
- decision and waiver expiry;
- current capability authorization for approvals, waivers, and overrides;
- current role/team eligibility and no-self-approval;
- distinct actor count;
- active reject/request-changes records;
- every explicitly waivable warning.

An emergency override can satisfy the gate only while its elevated capability,
plan binding, policy, and exact scope remain current. The publication workflow
consumes this evaluation immediately before execution. Evaluation never mutates
source or workflow state.

## Durability and retries

`PublicationApprovalStore` is separate from `AuditStore`. The in-memory
implementation supports tests and embedding. The JSON-directory implementation
uses private directories/files, create-if-absent immutable records, atomic
idempotency references, fsync, deterministic listing, and fail-closed identity
validation.

An exact idempotency retry returns the stored record without a duplicate audit
event. Reusing a key with different canonical input returns
`idempotency_conflict`. Operational state is reconstructed from approval records,
never from redacted audit events.

## Transports and audit

Browser, CLI, and MCP adapters pass server-derived trusted identity to the same
service method. Actor, role, team, or tenant fields supplied in an untrusted
command are ignored. Browser routes retain their existing session and CSRF checks.

Decision and evaluation audit events contain correlation, trusted actor, tenant,
site, plan/record identity, policy digest, decision kind, outcome, and safe reason
codes. They omit reasons, source bodies, diffs, secrets, credentials, and private
provider URLs while retaining enough stable IDs for incident review.

## Ownership boundaries

This module owns approval/waiver durability, eligibility and separation
evaluation, revocation and override semantics, and correlated audit. Capability
policy remains authoritative in `capability_policy`; immutable plan and decision
cores remain in `publication_contracts`; the publication workflow owns when the
evaluation is required before effects.
