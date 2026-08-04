# Governed PR previews for adopters

Use this guide when a repository needs one protected review environment for its
website, negotiated Markdown, retrieval surfaces, and agent artifacts.

## Hosted access availability

> **Status: planned, not available in the current release.**

Hosted GitHub sign-in is not the released Railway default. The current package
and maintained template do not ship `FURA_PREVIEW_AUTH_MODE`, an installable
official Furatena GitHub App, a public broker issuer or JWKS endpoint, or trusted
controller registration with that broker. Protocol, runtime, and broker design
work does not make those services available to adopters.

The supported PR-preview gate remains the shared-token boundary described in
[Pull-request preview security](PR_PREVIEW_SECURITY.md). Keep
`FURA_PREVIEW_AUTH_TOKEN` configured for every active PR environment and keep
reviewer distribution out of PR comments and logs. Railway documents that
[sealed variables are not copied into PR environments](https://docs.railway.com/variables#sealed-variables),
so Furatena's current controller creates and injects a fresh sealed token for
each eligible PR head. Sealing a token only in the base environment is not a
replacement for that controller step.

Do not install an unofficial GitHub App, select a caller-provided broker, invent
hosted-mode variables, or remove the shared token based on draft contracts.
Doing so would advertise an authorization path that the released template
cannot complete or support.

### Planned migration gate for shared-token adopters

This is a release-readiness gate, not an actionable migration procedure. An
existing deployment should move to hosted access only after one release ships
and documents all of these together:

1. explicit `hosted`, `password`, and deliberately public mode selection,
   including restart, session invalidation, recovery, and downgrade behavior;
2. the official GitHub App installation target, public broker identifiers,
   privacy and retention disclosure, support route, and broker status process;
3. template and controller integration that registers the exact repository,
   pull request, head SHA, and Railway origin without placing App, broker,
   signing, reviewer, or controller credentials in the preview;
4. clean-account evidence for install, upgrade, current-SHA authorization,
   access removal, PR close, broker outage, and rollback to password mode; and
5. release notes that identify the compatible image, template revision,
   migration order, retained adopter overrides, and rollback target.

Until that release exists, no hosted migration is required. Continue rotating
the per-head shared token and use Basic username `preview` for browsers or the
same value as a Bearer credential for machines. A future hosted default must
retain an explicit password rollback path before adopters remove their existing
token workflow; an image, config, or documentation draft alone is not that
rollback path.

## Prerequisites

- A Furatena repository that freezes successfully on CPython 3.14t with the GIL disabled.
- A private Railway project connected to the GitHub repository.
- A Railway domain on the base service and `production` selected explicitly as the PR base.
- GitHub Actions permission to write checks and issue comments.
- A fresh reviewer token per PR, stored as a sealed Railway variable.

Start from `fura init --starter governed-preview`. The generated `railway.toml` preserves
normal production overlap/draining and overrides only ephemeral `pr`
environments.

## Provider and GitHub setup

Enable Railway PR Environments, disable bot PR environments, and keep focused
PR environments disabled unless every service is independently safe. Production
is the explicit base. Railway creates and removes the environment on PR
open/close; a provider callback sends lifecycle state to the Furatena reporting
workflow.

Each environment needs public identity variables `FURA_PR_PREVIEW=1`,
`FURA_PREVIEW_PR_NUMBER`, full `FURA_PREVIEW_SHA`, and
`FURA_PREVIEW_REVIEW_URL`. Set `FURA_PREVIEW_AUTH_TOKEN` as a sealed runtime
variable with at least 32 characters. Never put source credentials, production
author sessions, the reviewer token, or private mount credentials in Docker
arguments or comments.

Furatena's Railway dogfood repository automates that step from trusted
default-branch code. A Railway workspace token lives in the GitHub Actions
secret `RAILWAY_API_TOKEN`; the controller creates a fresh reviewer token for
each PR head, seals it in the ephemeral environment, then verifies the
protected manifest before reporting ready. Adopters may implement an equivalent
provider controller or retain the documented callback boundary. Interactive
reviewer credential distribution remains an out-of-band security decision.

GitHub requires `checks: write` and `pull-requests: write`; contents remain
read-only. The `pull_request_target` job always runs trusted default-
branch tooling, rejects bots and external forks, and never checks out PR code.
The generated reporting workflow runs only its trusted default-branch script; it
does not checkout code from Furatena or any pull-request head. Pin the Furatena
package dependency to a reviewed release before production use.

## Lifecycle and review

Provider callbacks use `queued`, `building`, `ready`, `failed`, and `removed`.
Before `ready`, the reporter verifies `/readyz`, exact head/artifact SHA equality,
HTML, negotiated Markdown, `.md`, `llms.txt`, catalog/query, search, and metadata.
One marker comment is replaced across retries and pushes. A new head receives a
new check; old URLs are never presented as current. Close or merge deletes the
environment, and repeated teardown observations are safe.

Authorized humans use Basic username `preview` with the separately shared token
as password. Agents may use the same value as a Bearer token. Every protected
response is private/no-store and noindex; probes contain no catalog data.

## Cost and latency controls

The main drivers are dependency installation, catalog indexing/freezing, image
export, health-check admission, replica count, and how long PRs remain open.
Furatena's dogfood builds exported images in roughly 14–53 seconds and became
ready in roughly 1–2 minutes. Treat those as observations, not a provider SLA.

Keep one ephemeral replica, zero overlap/draining, Docker layers before source
copy, a committed lockfile, and automatic removal. Disable bot/fork previews,
close stale PRs, and alert on long-lived environments. Approximate cost with
build minutes plus replica active wall-clock time; confirm current provider
pricing separately.

## Portable conformance recipe

Run against any provider implementing the preview manifest:

```bash
FURA_PREVIEW_AUTH_TOKEN=... python scripts/preview_report.py \
  --state ready \
  --origin https://preview.example.test \
  --expected-sha 0123456789abcdef0123456789abcdef01234567 \
  --output preview-conformance.json
```

Omit `--publish` for provider-neutral JSON only. Add GitHub repository, PR, and
token inputs to upsert the check and comment.

## Troubleshooting

| Symptom | Likely cause | Repair |
| --- | --- | --- |
| Build fails before freeze | `FURA_PREVIEW_SHA` differs from the provider commit | Replace it with the full current head SHA and rebuild. |
| Domain missing | Base service lacks a Railway domain | Add the base domain, then recreate the PR environment. |
| Reporter says stale SHA | A newer push superseded the observed deployment | Ignore the old URL and wait for the new head manifest. |
| `/readyz` fails | Freeze/source/index or startup failed | Follow readiness remediation and deployment logs. |
| 401 for authorized reviewer | Token absent, rotated, or sent with the wrong scheme | Reinject the sealed token and use Basic `preview` or Bearer. |
| Environment remains after close | Provider webhook or deletion failed | Retry idempotent deletion, verify no active deployment/domain, and alert. |

Furatena dogfood evidence is recorded on PRs #441 and #442: runtime and
content-only previews, SHA supersession, a mismatched-SHA build failure, missing-
credential readiness failure, and close-during-build teardown. The governed
preview starter is exercised as a separate generated repository fixture in CI.
