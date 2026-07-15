# Private-image release and incident runbook

Furatena's commercial artifact is a proprietary image in GitHub Container
Registry (GHCR), not a PyPI package. `.github/workflows/private-image.yml`
builds each candidate once, publishes it with an SBOM and provenance, scans the
published digest, and proves that the exact digest can boot. Promotion and
rollback always select an existing digest; they never rebuild it.

The public adopter content repository is a separate input. Publishing an image
must not grant content-refresh authority, and refreshing content must not grant
registry authority.

## One-time controls

1. Keep the `furatena` GHCR package private and grant the repository workflow
   write access to that package.
2. Create a protected GitHub environment named `private-image-production`.
   Require a reviewer for promotion and revocation runs, restrict deployment to
   `main`, and prevent self-review.
3. Store no long-lived registry credential in GitHub Actions. Candidate builds
   use the job-scoped `GITHUB_TOKEN`.
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
   boot it, and require `/readyz` to pass.

The candidate artifact is not production-approved merely because this job
passes. The workflow artifact is short-term CI evidence; stable promotion also
creates a durable GitHub release record.

## Promote a stable digest

Run **Publish proprietary image** manually with:

- `operation=promote`;
- a new immutable `MAJOR.MINOR.PATCH` `version`;
- the candidate's exact `sha256:...` `digest`;
- the candidate's forty-character source `commit`.

The protected `private-image-production` environment supplies the human gate.
The lifecycle job first proves the subject still exists in GHCR, then writes a
stable record and creates `image-v<version>` with that record as an asset. It
does not invoke Docker build. Never reuse a commercial version or move its
release tag to a different commit.

Verify the selected subject before changing Railway:

```bash
docker buildx imagetools inspect ghcr.io/lbliii/furatena@sha256:<digest>
gh attestation verify oci://ghcr.io/lbliii/furatena@sha256:<digest> \
  --repo lbliii/furatena
```

Update a canary Railway environment to the exact digest, wait for readiness,
then run the live artifact verifier and SLO probes. Only after the canary passes
should production and the template reference be changed. Record the previous
digest as last-known-good in the change evidence.

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

## Revoke a digest

Run the workflow with `operation=revoke`, the affected `digest`, its commercial
`version`, a specific `reason`, and, when known, a `replacement_digest`. The
protected lifecycle job confirms that the subject exists and uploads a durable
revocation record to the matching image release.

Then:

1. roll every managed environment back to a known-good digest;
2. remove the revoked digest from template configuration and update notices;
3. rotate the Railway registry credential if credential exposure is possible;
4. block future promotion of the digest in the incident tracker;
5. publish impact, mitigation, and replacement guidance.

Do not delete the image during active response. Registry deletion destroys
useful evidence and can break adopters before they receive the replacement.

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
