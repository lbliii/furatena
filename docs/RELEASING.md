# Release and incident runbook

Furatena's **primary distribution** is the open-source Python package on PyPI
(`furatena`), published with Trusted Publishing when a GitHub Release is
created. Operator steps for that path are in [PYPI.md](PYPI.md). The product
decision is recorded in
[OSS_DISTRIBUTION_DECISION.md](OSS_DISTRIBUTION_DECISION.md).

An optional proprietary-era **GHCR private image** workflow remains for Railway
experiments that still pin digests. It is not the license boundary and is not
required for open-source adopters. Prefer PyPI installs in Railway templates
going forward.

## Private-image lifecycle (optional)

Furatena can still publish a container image to GitHub Container Registry
(GHCR) via `.github/workflows/private-image.yml`. That workflow builds each
candidate once, publishes it with an SBOM and provenance, scans the published
digest, and proves that the exact digest can boot. Promotion and rollback
always select an existing digest; they never rebuild it.

The public adopter content repository is a separate input. Publishing an image
must not grant content-refresh authority, and refreshing content must not grant
registry authority.

## Lifecycle and discovery contract

The five private-image channels are deliberately asymmetric:

| Channel | Meaning | Permitted deployment use |
| --- | --- | --- |
| `development` | Local or maintainer-only build at an exact source commit | Never an adopter rollback target |
| `candidate` | Built, scanned, attested, and smoke-tested exact digest | Canary validation only |
| `stable` | Protected approval plus a complete public release record | Production by exact digest |
| `deprecated` | Still available during a stated support window | Existing deployments while migrating |
| `revoked` | Unsafe or unsupported affected digest | No new deployment or promotion |

An OCI tag or GitHub release tag is only a discovery label. Every deployment,
upgrade, rollback, deprecation, and revocation record uses the immutable
`ghcr.io/lbliii/furatena@sha256:...` subject. Mutable tags such as `latest`,
`stable`, and `image-v1.2.3` are never rollback identity.

The public adopter update feed is the repository's GitHub Releases Atom feed:

```text
https://github.com/lbliii/furatena/releases.atom
```

Stable entries are named `image-v<version>` and attach `image-record.json`.
Deprecations add a digest-named asset to that stable release. Emergency
revocations publish a separate `image-revoked-<digest>` entry so a previously
revoked digest is discoverable and promotion can fail closed. Consumers must
validate attached records against the versioned schemas under
`src/furatena/catalog/schemas/private-image/v1/`, compare digests rather than
tags, and treat an unknown schema version as requiring manual review.

## One-time controls

1. Keep the `furatena` GHCR package private and grant the repository workflow
   write access to that package.
2. Create a protected GitHub environment named `private-image-production`.
   Require a reviewer for promotion and revocation runs, restrict deployment to
   `main`, and prevent self-review.
3. Store no long-lived registry credential in GitHub Actions. Candidate builds
   use the job-scoped `GITHUB_TOKEN`.
   Pull-request image conformance runs separately with only `contents: read`;
   pull-request code receives no package-write, OIDC, or attestation authority.
4. Create a least-privilege, read-only registry credential for Railway. Store
   it only in Railway's image registry credential fields, never as an
   application environment variable or template-visible value.
5. Configure production and the published template with an exact
   `ghcr.io/lbliii/furatena@sha256:...` subject. Do not deploy `latest`,
   `stable`, or another mutable tag.
6. Retain the last-known-good digest and its release record before every
   promotion.

All third-party actions in the workflow are pinned to audited commit SHAs. In
particular, the Trivy action is pinned to the safe 0.35.0 commit following the
March 2026 tag-compromise incident; do not replace it with a mutable tag.

## Candidate build

A merge to `main`, or a manual `candidate` operation, performs this sequence:

1. Build the `linux/amd64` image with the source commit, candidate channel, and
   image version embedded in OCI labels and runtime identity.
2. Push an immutable `candidate-sha-<commit>` reference to private GHCR and
   capture the registry digest returned by BuildKit.
3. Emit BuildKit provenance and an SBOM.
4. scan `image@digest` for high and critical vulnerabilities; an unreviewed
   finding fails the run;
5. create a deterministic `image-record.json` that joins image, digest, commit,
   channel, and timestamp;
6. publish a GitHub/Sigstore attestation for the registry subject;
7. pull the exact subject into a clean job, verify CPython 3.14t is GIL-disabled,
   boot it, require `/readyz` and the supported public surfaces to pass, and
   prove the managed-content diagnostic and unprivileged-volume contracts;
8. after every exact-digest gate passes, retain a schema-validated promotion
   receipt binding the digest and source commit to the exact workflow, `main`
   ref, workflow SHA, run ID, run attempt, and successful conclusion.

The candidate artifact is not production-approved merely because this job
passes. The workflow artifact is short-term CI evidence and is eligible for
promotion for seven days; stable promotion also creates a durable GitHub release
record.

## Promote a stable digest

Run **Publish proprietary image** manually with:

- `operation=promote`;
- a new immutable `MAJOR.MINOR.PATCH` `version`;
- the candidate's exact `sha256:...` `digest`;
- the candidate's forty-character source `commit`;
- the successful candidate workflow's numeric `candidate_run_id`;
- the prior known-good `rollback_digest`;
- the `rollback_version` whose durable stable record owns that digest;
- a concrete `compatibility` statement; and
- `migration_notes`, including an explicit no-migration statement when no
  adopter action is required.

The protected `private-image-production` environment supplies the human gate.
The lifecycle job first checks the exact digest-named revocation release, then
proves the subject still exists in GHCR. It accepts only provenance signed by
this repository's private-image workflow on a GitHub-hosted runner, from the
submitted source commit on `main`. It then downloads the exact run's promotion
receipt and compares that receipt with current GitHub Actions run metadata.
Missing artifacts, incomplete, failed, cancelled, or non-successful runs,
evidence older than seven days, and any digest, commit, repository, workflow,
ref, run, attempt, or timestamp mismatch fail closed. It also downloads
`image-record.json` from the exact `image-v<rollback_version>` release and
requires that canonical stable record to own the same image, version, and
`rollback_digest`. The rollback digest must not be revoked, must still exist in
GHCR, and must carry the same workflow-, commit-, main-ref-, and runner-bound
provenance. Only then does the job write a stable record and create
`image-v<version>` with that record as an asset. It does not invoke Docker
build. Never reuse a commercial version or move its release tag to a different
commit. A failed or unavailable revocation lookup or attestation check blocks
promotion rather than treating the digest as eligible; an invalid, missing, or
stale promotion receipt does the same.

The stable record includes the exact image and rollback subjects, compatibility
statement, supported content/config contract version and source-revision URLs,
changelog URL, migration notes, and support-policy URL. Promotion rejects a
rollback digest equal to the candidate digest and rejects any digest already
listed by its exact immutable revocation release; the lookup does not depend on
a bounded recent-release listing.

Verify the selected subject before changing Railway:

```bash
docker buildx imagetools inspect ghcr.io/lbliii/furatena@sha256:<digest>
gh attestation verify oci://ghcr.io/lbliii/furatena@sha256:<digest> \
  --repo lbliii/furatena
```

Update only the canary Railway application's image subject to the exact digest;
do not replace or detach its adopter-owned content volume, content repository,
or configuration. Wait for readiness, then run the live artifact verifier and
SLO probes. A failed readiness or conformance check leaves the production
service on the prior digest and restores the canary to the record's exact
rollback digest. Only after the canary passes should production and the
template reference be changed.

## Rollback

Rollback is an image selection, not a rebuild:

1. Find the prior stable GitHub release record and its last-known-good digest.
2. Confirm that exact subject still exists and its attestation verifies.
3. Change the Railway service image reference back to that digest.
4. Wait for deployment success and `/readyz`, then verify `/meta.json` reports
   the expected version, channel, digest, commit, and frozen fingerprint.
5. Run the live artifact verifier and preserve its receipt with the incident.

Do not delete the failed digest. Preserving it keeps the investigation,
attestation, SBOM, and scan evidence joinable.

## Deprecate a digest

Run the workflow with `operation=deprecate`, the affected stable `version` and
`digest`, a specific `reason`, an ISO-8601 `support_ends_at`, and the preferred
`replacement_digest` plus its owning `replacement_version` when available.
Before publishing, the job requires the exact `image-v<version>` stable record
to own the affected image and digest. Both the affected digest and any
replacement must be non-revoked, present in GHCR, and backed by repository
provenance; the replacement must also be owned by its exact stable record. The
resulting public feed record keeps the digest available, identifies the bounded
support window, and gives an exact replacement subject. Deprecation does not
silently move an adopter service.

## Revoke a digest

Run the workflow with `operation=revoke`, the affected `digest`, its commercial
`version`, a specific `reason`, and, when known, a `replacement_digest` and its
owning `replacement_version`. The exact `image-v<version>` stable record must
still provide the durable image/version/digest association. Emergency
revocation does not require the affected, potentially compromised registry
subject to remain available or attestable, but a replacement remains subject
to the full non-revoked, available, attested stable-target checks. The protected
lifecycle job publishes an immutable digest-named revocation entry with
affected digests and remediation. A later promotion of that digest is blocked
even if a mutable registry tag points to it.

Then:

1. roll every managed environment back to a known-good digest;
2. remove the revoked digest from template configuration and update notices;
3. rotate the Railway registry credential if credential exposure is possible;
4. block future promotion of the digest in the incident tracker;
5. publish impact, mitigation, and replacement guidance.

Do not delete the image during active response. Registry deletion destroys
useful evidence and can break adopters before they receive the replacement.

## Registry credential rotation

Registry pull credentials are Railway-owned delivery configuration, not
application variables. Rotate them without changing the selected image:

1. Create a new least-privilege, read-only GHCR credential without printing it
   to logs or storing it in repository, workflow, template, or application
   variables.
2. Replace the private-image credential on a canary service while keeping the
   same digest, volume, content configuration, and refresh credential.
3. Redeploy and require readiness, build identity, public projection, and
   content-generation continuity to match the pre-rotation evidence.
4. Move healthy production services to the new hidden credential one at a
   time, still on the same digest.
5. Revoke the old credential only after all services are healthy. Preserve
   redacted credential revision identifiers and probe receipts, never the
   credential value.

This procedure needs a real registry and Railway service to prove; unit tests
verify only that lifecycle records and documentation never contain credential
fields.

## Docker-image template update limitation

A Railway Docker-image template is not connected to this repository's commit
history, so repository-based template update notifications do not announce a
new private image. The GitHub release feed is the update signal. Adopters review
the compatibility and migration record, canary the exact subject, and
explicitly replace only the application image digest. Neither Furatena nor the
template silently follows a mutable tag.

## Compromised-release response

1. Disable candidate publication and remove approvals from
   `private-image-production`.
2. Rotate GHCR and Railway registry credentials; review package, repository,
   environment, and workflow audit histories.
3. Roll back deployments to the last-known-good digest and revoke every
   suspect digest with a reason.
4. Compare source commit, OCI manifest, SBOM, provenance, Trivy output,
   attestation, workflow logs, and live `/meta.json` identity.
5. Open a private security advisory and communicate affected versions,
   mitigations, and the known-good replacement.
6. Repair the trust boundary and publish a new candidate from a reviewed
   commit. Never reuse a revoked version, digest, or release record.

For an emergency security or data-loss exception to normal compatibility
policy, also follow [COMPATIBILITY.md](COMPATIBILITY.md).
