# Publication profiles and change providers

Furatena turns an approved publication plan into a source change through an
explicit profile and a provider-neutral operation contract. Git, GitHub, branch
names, pull requests, and remote credentials are execution details; they are not
part of the core publication-plan schema.

The implementation lives in `furatena.catalog.publication_provider`. Version 1
JSON Schemas ship under `furatena/catalog/schemas/publication-provider/v1/`.

The concrete commit and pull-request adapter lives in
`furatena.catalog.publication_git_provider`. `GitChangesetProvider` receives the
source checkout, an external private state directory, and a changeset loader that
returns the unified diff bound to each request. Pull-request profiles additionally
provide a `GitReviewGateway`; that boundary translates provider-specific review
APIs while Git isolation and branch safety remain provider-neutral.

`furatena.catalog.publication_provider_executor.PublicationProviderExecutor` wires
these providers into `PublicationWorkflowService`. Trusted composition selects the
profile, repository base revision, attribution, provider registry, and durable
provider-record store. The adapter supports `local_only`, `commit`, and
`pull_request`; the Git provider supplies the latter two, while a local source
provider may implement the same protocol for `local_only`.

## Profiles

Every workflow selects one `PublicationProfileConfig`. The selection and its
canonical digest appear in the change request, and the profile appears in every
provider result.

| Profile | Effects | Required infrastructure |
| --- | --- | --- |
| `local_only` | Apply and reindex only approved paths; finish in explicit `dirty_working_tree` state. | Writable local source. No Git credentials or remote. |
| `commit` | Prepare an isolated changeset and create one intentional commit. | A provider capable of inspection, isolation, and commit. Push is not implied. |
| `pull_request` | Prepare, commit, create/update a deterministic branch, propose review, and observe review/protection state. | A repository provider with review capabilities. |
| `external` | Produce a canonical signed change bundle and hand it to another system. | An external target and signing boundary; Git is optional. |

Profile selection is never inferred from the presence of `.git`, credentials, a
remote, or a provider SDK. `pull_request` requires an explicit branch template and
review target. `external` requires an explicit handoff target. Only `local_only`
may opt into preserving unrelated dirty paths.

## Provider protocol

A `PublicationChangeProvider` implements six operations:

1. `inspect` describes repository identity, revisions, branch/detached/fork state,
   protection, permissions, and exact staged, unstaged, untracked, ignored, and
   conflicted paths.
2. `prepare` creates an isolated or local path-scoped changeset.
3. `commit` creates the intentional provider commit.
4. `propose_review` creates or updates the deterministic review request.
5. `observe_review` reports draft/open/closed/rejected/merged state and merge
   revision.
6. `reconcile` resolves an uncertain or externally changed provider effect.

The protocol accepts and returns Furatena domain records. A GitHub, GitLab,
filesystem, SaaS, or custom provider translates its own IDs and states at the
adapter boundary. Core code does not know token types, merge methods, GitHub
status names, or provider-specific repository IDs.

Provider operations are idempotent on provider, target repository, plan digest,
deterministic change ID, and operation. Retrying the same plan returns or updates
the same isolation, branch, commit, review, or handoff. The request
`idempotency_key` can vary between transport retries without changing the request
digest or change ID.

## Records and source of truth

`RepositoryInspection` is an immutable observation. It is not permission to act.
The workflow validates it against the current profile and request immediately
before the next operation.

`PublicationChangeRequest` binds:

- profile and profile digest;
- provider, source repository, target repository, and repository base revision;
- publication plan ID/digest and exact changeset digest;
- approved paths plus explicit create/modify/delete/move records;
- previous and resulting source revisions;
- branch/review/external target;
- distinct source author, workflow committer, and trusted workflow actor;
- dry-run intent.

`PublicationChangeResult` records the current provider observation: operation,
outcome, revisions, changed and preserved paths, branch, commit, review, merge,
protection, failure, reconciliation, and evidence references. The result is
provider state only. An open or merged review is not a deployment, promotion, or
verification result.

The #386 workflow snapshot remains the operational workflow source of truth.
Provider records are immutable inputs/history attached to its events and outputs.
An open or draft review places that snapshot in `reviewable`; only an observed
merge places the repository-review workflow in `applied`. Output references retain
the selected profile digest, each immutable provider result ID/digest, and the latest
review, protection, and reconciliation state. Full trusted results remain in the
provider execution store for restart-safe reconciliation.

## Path and revision invariants

`validate_change_request` rejects a request unless its plan/profile IDs, digests,
paths, changeset, provider, and previous/resulting revisions match the immutable
plan and explicit profile.

Path changes model `create`, `modify`, `delete`, and `move`. A move names both old
and new paths. The union of every path-change record must exactly equal the
approved plan path set. Providers cannot infer a rename, add generated metadata,
include a submodule, or absorb a newly discovered file.

`validate_provider_result` rejects:

- a result belonging to another request, plan, or deterministic change;
- any changed path absent from the approved set;
- an observed base that differs from the approved source revision;
- a successful prepared/commit/review/merge/handoff result whose resulting source
  revision differs from the plan.

These checks run after every provider operation and before the result can feed the
next effect.

## Dirty worktrees, detached heads, forks, and protection

Local-only execution may preserve unrelated dirty paths only when the profile
explicitly enables it. Dirty or conflicted approved paths always stop with a
`conflict`. Existing local changes remain user-owned and are never staged or
rewritten by the provider contract.

Commit and pull-request profiles require `inspection.isolated`. Implementations
use an isolated worktree, temporary checkout, or equivalent provider workspace at
the exact approved base. The user's current branch, index, staged files, untracked
files, ignored files, and conflicts are not inputs.

`GitChangesetProvider` requires a full Git commit ID for
`repository_base_revision` and keeps its state directory outside the source
repository. It applies the digest-verified patch to a detached worktree, compares
Git's staged create/modify/delete/move records with the exact approved operations,
and commits there with explicit author and workflow-committer identities. Commit
trailers correlate the commit to the publication plan, deterministic change ID,
and trusted workflow actor. It never runs a broad staging command.

Pull-request branches use the request's deterministic branch name. Local refs are
created only when absent, remote refs are pushed with a create-only lease, and any
different local or remote revision is a conflict rather than a force update. The
review gateway must upsert by deterministic change ID and return its observed head
and base; mismatches stop reconciliation. Protected review targets are recorded
and never bypassed.

A detached checkout is acceptable only as an isolated workspace with an explicit
base revision and deterministic change identity. Source and target repository IDs
are separate, so forks and remote mounts cannot silently select a push target.

Direct commit to a protected base returns an authorization failure. Pull-request
mode records protection and required review; it never bypasses protection. Missing
inspect/prepare/commit/review permissions also fails before the next effect.

## Failures and reconciliation

Provider failures reuse the workflow dispositions:

- `retryable`: the effect is known not to have occurred and the named operation
  may be retried;
- `terminal`: unsupported behavior or invalid provider output;
- `conflict`: base, path, source, branch, or external state drift;
- `authorization`: missing permission, protection, signature, or target access;
- `reconciliation_required`: a timeout or partial failure may have produced an
  external effect.

`effect_may_have_occurred=true` is legal only with
`reconciliation_required`. Reconciliation reports `pending`, `matched`,
`diverged`, or `manual_action_required`; it does not guess that a timed-out commit,
push, review creation, or external handoff failed.

The Git adapter persists atomic, mode-restricted correlation metadata under one
directory per change and serializes operations with a file lock, which is safe for
threads and cooperating worker processes under GIL-disabled Python. After a
timeout, `reconcile` inspects the correlated commit, remote branch, and review
instead of repeating an uncertain effect. A retry reuses the same worktree,
commit, branch, and review. `cleanup` removes only the isolated worktree and local
metadata, and refuses cleanup if the provider branch contains an external
revision. This makes preparation and commit failures resumable without touching
the user's checkout; provider-side branch or review cleanup remains an explicit
operator action.

## Dry runs

A dry-run request may perform only `inspect` or `prepare`. Its report lists the
exact approved paths, source and repository bases, deterministic branch/change
identity, commit attribution/message, review or external target, and ordered
provider operations. Commit, review creation, remote writes, merge, and external
handoff are forbidden during dry run.

## External bundles

`PublicationChangeBundle` contains the exact plan identity, profile/provider
target, path set, diff bytes/digest, and previous/resulting revisions. The bundle
digest covers all of those fields. `ProviderBundleSignature` binds that digest to
an algorithm and workflow key ID; external handoff validation requires a
signature.

Bundles never contain credentials. The trusted projection contains the approved
diff for the receiving system. Audit and public projections remove diff content;
the audit projection retains stable IDs, digests, paths, revisions, and signature
metadata.

## Projections and envelopes

Trusted records may include local dirty paths, attribution, review URLs, diff
content, and extensions. Audit records remove workflow actor/commit attribution
from requests, private review URLs from results, and diff bodies from bundles.
Public records expose only safe profile and high-level provider state.

CLI, HTTP, MCP, and automation use their established outer envelopes around the
same trusted inner record. The keys are `publication_change_request`,
`publication_change_result`, and `publication_change_bundle`. Readers reject
missing, ambiguous, or unknown change records.

## Ownership boundaries

The contract module owns profiles, immutable provider records, canonical IDs/digests,
path/revision validation, dry-run plans, external bundles, projections, and the
provider protocol. The Git adapter owns isolated Git worktrees, commits,
deterministic branch publication, and review reconciliation. #387 owns workflow
orchestration, persistence, idempotent operation sequencing, and when to enter
reconciliation. Provider adapters cannot weaken plan guards or expand the approved
path set.
