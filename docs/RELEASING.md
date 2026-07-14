# Release and incident runbook

Furatena releases are built from a version tag by
`.github/workflows/release.yml`. The workflow publishes the same verified wheel
and sdist to PyPI and a GitHub release. It does not use a stored PyPI API token.

## One-time trusted-publisher setup

Create a protected GitHub environment named `pypi`. Restrict deployments to
release tags and require reviewer approval. In the PyPI `furatena` project,
register this GitHub Actions trusted publisher:

| Field | Value |
| --- | --- |
| Owner | `lbliii` |
| Repository | `furatena` |
| Workflow | `release.yml` |
| Environment | `pypi` |

PyPI binds those values to the short-lived GitHub OIDC identity. Do not add a
`PYPI_TOKEN` secret or a password fallback to the workflow.

## Cut a release

1. Update `project.version` in `pyproject.toml` and `__version__` in
   `src/furatena/__init__.py`; refresh `uv.lock`.
2. Confirm every merged change has a reviewed `changelog.d/ISSUE.TYPE.md`
   fragment. Preview the exact generated notes with `make changelog-draft`.
3. Merge to `main` and wait for its complete CI workflow.
4. Create and push an annotated `vMAJOR.MINOR.PATCH` tag at that tested commit.
5. Approve the protected `pypi` environment after checking the workflow's tag,
   commit, manifest, and checksum output.

The release workflow rejects a tag that does not exactly match package metadata
or whose commit is not reachable from `main`. Its unprivileged jobs rerun fast,
contract, agent, and packaged wheel/sdist checks on CPython 3.14t with
`PYTHON_GIL=0`. The build job writes `SHA256SUMS` and
`release-manifest.json`. Separate jobs:

- verify the downloaded bundle and, when repository visibility supports it,
  create a GitHub/Sigstore provenance attestation with `actions/attest`;
- publish the distributions through PyPI Trusted Publishing, which also emits
  PyPI attestations;
- verify the bundle again and create the GitHub release with Towncrier notes,
  wheel, sdist, checksums, and manifest.

Only the attestation and publishing jobs receive `id-token: write`; repository
build code never runs with the PyPI publishing identity.

## Verify a release

Download the GitHub release assets into one directory, then run:

```bash
shasum -a 256 -c SHA256SUMS
```

For public repositories or private repositories with GitHub Enterprise Cloud,
also use `gh attestation verify`. GitHub-hosted attestation storage is not
available to user-owned private repositories, so Furatena's current private
release path records that skip explicitly instead of failing publication.

Every PyPI file must still carry the Trusted Publishing attestation generated
by the official PyPA action. Verify a downloaded file and its PyPI provenance:

```bash
pypi-attestations verify pypi \
  --repository https://github.com/lbliii/furatena \
  https://files.pythonhosted.org/.../furatena-VERSION-py3-none-any.whl
```

Compare the hashes printed by the PyPI publish job with `SHA256SUMS`, PyPI's
file details, and PyPI's Integrity API provenance. The manifest must name the
expected tag, commit, version, and free-threaded runtime. A mismatch is a
release incident; do not install or promote the artifacts.

## Failed or partial workflow

- Before PyPI upload: fix the workflow or code, delete any draft/unpublished
  release state, bump the version if any filename reached PyPI, and tag again.
- After PyPI succeeds but GitHub release creation fails: rerun only the failed
  GitHub release job. Do not rerun the successful PyPI job and do not enable
  `skip-existing`; duplicate filenames should fail loudly.
- Never move or recreate a published tag. Never overwrite a distribution;
  PyPI filenames are immutable.

## Yank and rollback

Use a PyPI yank for a broken, compatibility-violating, or vulnerable release.
On the PyPI release-management page, choose **Options → Yank** and record a
specific reason. Yanking is non-destructive: normal resolution ignores the
release while exact pins can still retrieve it. Do not delete the release or
its files because deletion is irreversible and destroys evidence.

Keep the GitHub release, tag, manifest, checksums, attestations, and workflow
logs. Add a prominent release-note warning and link the tracking issue or
security advisory. Revert or fix from the last known-good commit, assign a new
patch version, run the full workflow, and tell users which version to install.

## Compromised-release response

1. Stop active release workflows and remove approval from the `pypi`
   environment.
2. Disable or remove the PyPI trusted publisher until the trust boundary is
   understood. Revoke any unrelated PyPI tokens and suspicious sessions.
3. Yank affected versions with a security reason; preserve artifacts and logs.
4. Compare PyPI hashes, `SHA256SUMS`, GitHub attestations, tag/commit identity,
   environment approvals, and the GitHub/PyPI security histories.
5. Open a GitHub security advisory and publish impact, affected versions,
   mitigations, and the known-good replacement. Contact PyPI administrators if
   account or index compromise is suspected.
6. Repair the compromised identity or repository controls, restore the trusted
   publisher with reviewer-protected environment rules, and publish a new
   version. Never reuse the compromised version or filenames.

For an emergency security or data-loss exception to normal deprecation policy,
also follow the disclosure requirements in [COMPATIBILITY.md](COMPATIBILITY.md).
