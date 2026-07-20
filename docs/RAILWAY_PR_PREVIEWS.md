# Railway pull-request previews

Furatena uses Railway PR Environments as the ephemeral provider behind the
provider-neutral preview contract. Each eligible pull request receives a unique
environment and Railway domain. The runtime serves a protected
`/preview-manifest.json` that binds those provider IDs and URLs to the reviewed
head SHA, frozen catalog fingerprint, and readiness result.

## One-time project configuration

Configure the Railway project with:

- PR environments enabled (`prDeploys=true`);
- `production` selected explicitly as the base environment;
- bot PR environments disabled (`botPrEnvironments=false`);
- focused PR environments disabled (`focusedPrEnvironments=false`); and
- a Railway-provided domain on the base service so ephemeral domains are
  provisioned automatically.

The checked-in `railway.toml` keeps production deployment settings intact and
uses `[environments.pr.deploy]` only to remove overlap and draining time from
ephemeral environments.

Create a Railway workspace API token with access to this project, then add it
to the GitHub repository as the Actions secret `RAILWAY_API_TOKEN`. Create and
store that credential directly in the Railway and GitHub settings UIs; never
paste it into an issue, pull request, shell history, or workflow input. The
workflow pins Railway CLI `5.25.0` and uses the token only from the trusted
default-branch job.

## Per-PR identity and secret

Railway does not copy sealed variables into PR environments. On every eligible
internal, non-bot PR head, `.github/workflows/preview-report.yml` runs trusted
default-branch code that generates a fresh `FURA_PREVIEW_AUTH_TOKEN`, applies
it as a sealed Railway variable, and atomically applies these non-secret
identity variables before a source redeploy:

```text
FURA_PR_PREVIEW=1
FURA_PREVIEW_PR_NUMBER=<number>
FURA_PREVIEW_SHA=<full immutable head SHA>
FURA_PREVIEW_REVIEW_URL=https://github.com/lbliii/furatena/pull/<number>
```

The Docker build records `FURA_BUILD_GIT_SHA`. Startup fails closed unless it
matches `FURA_PREVIEW_SHA`, and all surfaces except `/healthz` and `/readyz`
require the per-preview Basic/Bearer token. The generated token remains only in
the controller process and sealed Railway configuration: it is never placed in
a command argument, repository output, log, manifest, or PR comment. Automated
conformance uses it before the job exits. Distribution of an interactive human
review credential is a separate, out-of-band operator action.

The controller re-reads the GitHub pull request before touching Railway. It
rejects forks, bots, and superseded SHAs; waits for the matching Railway PR
environment by explicit project ID and its deterministic `<repo>-pr-<number>`
name; applies the variables through `railway environment edit`; and performs an
explicit source redeploy. It never relies on mutable local Railway linking, so
the workspace-scoped automation token does not need account-wide project
discovery. A Railway success is reported as ready only when its provider
metadata and protected manifest both match the current 40-character PR head
SHA. New synchronize events cancel older controller runs.

## Lifecycle mapping

| Railway state | Furatena state | Required evidence |
| --- | --- | --- |
| `INITIALIZING`, `QUEUED`, `WAITING`, `BUILDING`, `DEPLOYING` | `building` | provider IDs and pending deployment check |
| `SUCCESS` | `ready` | exact head SHA, domain, frozen fingerprint, and passing `/readyz` |
| `FAILED`, `CRASHED`, `CANCELED`, `SKIPPED`, `SLEEPING` | `failed` | structured retryable failure |
| `NEEDS_APPROVAL` | `failed` | structured authorization failure |
| `REMOVING` | `stopping` | teardown in progress |
| `REMOVED` | `stopped` | terminal timestamp; repeated close/delete is a no-op |

An observed deployment for an older head SHA is `failed` with the conflict code
`superseded_deployment`; it can never be reported as ready.

## Dogfood checklist

For both a content/theme PR and a runtime/agent PR, record only non-secret
evidence:

1. PR number and full head SHA.
2. Railway environment, deployment ID, and unique domain.
3. Build start, successful deployment, and readiness timestamps.
4. The protected preview manifest's artifact fingerprint and source SHA.
5. A second head commit proving the old deployment is superseded.
6. Merge/close time and confirmation that the environment was removed.

Also exercise build failure, readiness failure, supersession, and close during
deploy. Compare the production deployment configuration and `/readyz` before
and after the rollout. Railway usage is bounded to one replica per open PR and
the environment is removed on merge or close; report build minutes and active
wall-clock time as the cost proxy.

If a preview stalls, inspect the deployment logs and unauthenticated `/readyz`
first. A missing sealed token is an expected fail-closed startup path. Repair
variables in the ephemeral environment, rebuild the same head SHA, then verify
the manifest before sharing the authenticated URL.

For trusted recovery after the secret is configured, run **Report Furatena PR
preview** manually with the PR number, current full head SHA, an initial state,
and `configure_railway=true`. The same fork, bot, and current-head checks still
apply. A close or merge remains Railway-owned teardown; the close event updates
the single GitHub check and marker comment to `removed`.
