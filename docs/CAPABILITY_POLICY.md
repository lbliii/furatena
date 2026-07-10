# Scoped capability policy

Furatena's capability service authorizes governed authoring, review, repository,
and deployment operations. It extends the existing read and lifecycle RBAC model
without weakening its role, visibility, team, mount, or trusted-identity checks.
The service is provider-neutral, immutable, deterministic, and default-deny.

The implementation lives in `furatena.catalog.capability_policy`. Version 1 JSON
Schemas for policy, request, and decision records ship under
`furatena/catalog/schemas/capability/v1/`.

## Trust boundary

A capability subject is trusted only when server code constructs it from
`GatewayIdentity`, after the #189 transport, claim, signed-session, fingerprint,
and tenant/workspace/site checks, or from the explicit local-OS CLI boundary.
Actor, role, team, identity source, and identity fingerprint fields in an HTTP
body, browser form, MCP argument, CLI payload, or automation request are display
data and are not authorization inputs.

Deserializing a `CapabilityRequest` always produces an untrusted subject. The
server must authenticate independently and call `bind_trusted_capability_subject`
before evaluation. Evaluating the deserialized request directly returns
`untrusted_identity`. A client-provided `trusted` flag is neither serialized nor
accepted.

The subject's tenant, workspace, and site must exactly equal the resource scope.
Any mismatch returns `identity_scope_mismatch`; there is no cross-tenant fallback.

## Capabilities

Version 1 defines these independent duties:

- content: `read`, `edit`, `validate`, `request_review`, `review`, `approve`,
  `publish`, `unpublish`, `archive`, and `waive_warning`;
- repository: `create_change`, `open_pr`, `merge_observe`, and `build`;
- delivery: `promote`, `deploy`, `verify`, and `roll_back`;
- governance: `emergency_override` and `administer_policy`.

Rules grant capabilities explicitly. The governed evaluator does not infer that a
higher legacy role automatically has every lower or unrelated duty. Contributor,
reviewer, publisher, operator, and policy-administrator responsibilities can be
assigned independently through role, team, actor, ownership, and scope selectors.

The compatibility adapter maps current `AccessPermission` and author-operation
names onto these capabilities. Existing #151 behavior remains unchanged:
anonymous and reader subjects cannot mutate; contributors can draft/edit/create;
publishers additionally publish and unpublish; admins additionally archive.

## Versioned records

`CapabilityPolicy` contains a stable policy version, canonical SHA-256 digest,
sorted rules, and passive extensions. Extensions are excluded from the digest.
Changing a rule, capability, selector, approval requirement, separation rule,
reason, remediation, or cache policy changes the digest.

`CapabilityRequest` contains the trusted subject, requested capability, normalized
resource scope, correlation ID, evaluation time, and optional publication-plan
digest. Correlation and evaluation time do not change the decision-scope digest;
the trusted subject, capability, resource scope, policy digest, and plan digest do.

`CapabilityDecision` contains:

- allowed/denied and the required capability;
- stable reason code, human-safe reason, and remediation;
- matched rule ID/effect when a rule matched;
- policy version/digest;
- approval count and eligibility, self-approval setting, and separation rules;
- cache mode and maximum age;
- decision-scope digest, evaluation time, and optional plan digest.

Decision records contain no private source body, diff, token, or provider secret
and are safe for UI action metadata. They are explanatory output only. A browser
may hide a denied action, but every effect must be reauthorized server-side.

## Resource context

Every scope includes tenant, workspace, and site. Content capabilities also
require mount, logical path, and lifecycle state. Repository capabilities require
repository and branch. Delivery capabilities require a target environment.
Rules may additionally match:

- resulting lifecycle and public-impact status;
- content owners or owner teams;
- sensitivity;
- exact or explicit glob patterns for tenant, workspace, site, mount, path,
  repository, branch, and environment;
- subject roles, teams, actors, and trusted identity sources.

Logical paths are relative POSIX paths. Absolute paths and `..` traversal are
rejected in both resources and policy patterns. Missing capability-specific
context returns `context_incomplete` before any rule can allow the operation.

## Evaluation order

Evaluation is deterministic and has no mutable cache or ambient request state:

1. Reject an untrusted subject.
2. Reject tenant/workspace/site mismatch.
3. Reject missing capability-specific resource context.
4. Collect matching deny rules. Any deny overrides every allow; the highest
   priority deny wins, with rule ID as a stable tie-breaker.
5. Otherwise select the highest-priority matching allow, then the most specific
   scope, then rule ID.
6. With no allow, return `default_deny`.

Policy construction rejects unknown enum values, invalid rule IDs and paths,
duplicate rule IDs, invalid cache settings, and approval requirements on deny
rules. Schema or digest failures stop evaluation before an effect; callers must
treat those failures as denial and repair the configured policy.

## Approval and publication binding

An allowed decision returns `PublicationApprovalRequirements` using the exact
capability policy version and digest. A planner copies those values into the
immutable publication plan. Approval and exception handling reauthorize
`approve`, `waive_warning`, or `emergency_override` against that plan.

Immediately before an effect, the workflow reauthorizes the concrete capability
and calls `require_current_publication_policy`. Execution is denied unless:

- the decision is allowed for the expected capability;
- the decision binds the exact publication-plan digest; and
- decision and plan policy version/digest match.

Source, validation, configuration, scope, plan, or policy drift therefore cannot
reuse an earlier authorization.

## Cache and transport contracts

Mutation, approval, override, and policy-administration decisions are always
`no_store`, regardless of a rule's requested cache setting. Only the read-like
`read`, `validate`, `review`, `merge_observe`, and `verify` capabilities may use a
bounded private cache when their matched rule opts in. Denials are always
`no_store`.

CLI, HTTP/browser, MCP, and automation use their normal outer envelopes around
the same v1 inner record:

- CLI: `{"data":{"capability_decision":...}}`
- HTTP: `{"capability_decision":...}`
- MCP: `{"structuredContent":{"capability_decision":...}}`
- automation: `{"payload":{"capability_decision":...}}`

Requests use `capability_request` in the same locations. Envelope readers reject
missing or ambiguous capability records. Transport conformance tests compare the
inner records and prove that decoded request identity remains untrusted until the
server binds authenticated context.

## Ownership boundaries

This module owns policy parsing, digests, normalized scope matching, decisions,
legacy mappings, transport records, and publication-policy freshness checks. The
workflow service owns when reauthorization occurs. The approval service owns
durable decisions and separation-of-duties evaluation. Repository and deployment
providers consume an allowed decision but cannot reinterpret it or bypass a deny.
