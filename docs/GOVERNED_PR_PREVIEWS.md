# Governed PR previews for adopters

Use this guide when a repository needs one protected review environment for its
website, negotiated Markdown, retrieval surfaces, and agent artifacts.

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

GitHub requires `checks: write` and `pull-requests: write`; contents remain
read-only. The `pull_request_target` job always runs trusted default-
branch tooling, rejects bots and external forks, and never checks out PR code.
Pin the reusable reporting workflow to a reviewed Furatena release or commit in
production; the starter's `@main` reference is deliberately visible as a setup
step while the package remains alpha.

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
