# Live SLOs and no-SSH operations

The official Railway demo is operated from public HTTP contracts, immutable
image/content receipts, Railway's control plane, and GitHub Actions evidence.
Routine diagnosis and recovery never require a shell in the running container.

## Objectives and alert thresholds

`config/live-slo.json` is the executable policy. The 30-day objectives and the
threshold that fails one scheduled run are:

| Indicator | 30-day objective | One-run alert threshold |
| --- | ---: | --- |
| Successful readiness and representative-HTML requests | 99.9% | less than 100% of samples succeed |
| `/readyz` latency | p95 at most 750 ms | sampled p95 exceeds 750 ms |
| Representative-HTML latency | p95 at most 1,000 ms | sampled p95 exceeds 1,000 ms |
| Catalog freshness | 100% fresh observations | `/catalog/freshness.json` is not `ok=true`, `status=fresh` |
| Complete bulk-artifact verification | 100% | any required artifact is absent, truncated, malformed, or inconsistent |
| Runtime identity | 100% exact private-image identity | distribution or exact `sha256:` digest is absent |
| Content identity | 100% active managed-content identity | active generation or exact resolved Git commit is absent |

The GitHub evidence monitor runs every six hours. Each run retains an immutable
JSON receipt for 30 days and opens or updates one operational-alert issue after
any failed gate. A later passing run closes the alert with its recovery receipt.
The committed schedule bounds repository CI cost and supplies audit evidence;
it is not a low-latency uptime service. Use a dedicated five-minute external
probe before offering a contractual availability or response-time SLA.

The monitor verifies all of these public surfaces from outside Railway:

- `/healthz` and sampled `/readyz` responses;
- representative HTML at `/`;
- `/catalog/freshness.json`;
- build, image, content-revision, and content-fingerprint identity in `/meta.json`;
- search, semantic search data, and `/catalog/query.json` bulk contracts;
- both `/llms.txt` and `/llms-full.txt`;
- complete catalog, search, query, semantic, and agent artifact bodies and counts.

Run the same checks from an incident workstation:

```console
python scripts/check_live_slo.py --origin "$ORIGIN" --output /tmp/furatena-slo.json
python scripts/verify-live-artifacts.py "$ORIGIN"
```

## Ownership, response, and communication

The maintainer who promoted the active stable digest owns the initial response.
The repository owner is the escalation owner when that maintainer is unavailable
or the failure crosses Railway, GHCR, DNS, or GitHub boundaries.

- During an announced live-demo window, acknowledge within 15 minutes, begin
  diagnosis immediately, and escalate an unowned or unresolved P1 after 30
  minutes.
- Outside a staffed demo window, acknowledge within four business hours. This
  demo does not claim a staffed 24-hour response commitment.
- Treat public unavailability, failed readiness, TLS failure, or a known-bad
  image/content rollout as P1. Treat an isolated latency, freshness, identity,
  or integrity signal as P2 until it repeats or another signal corroborates it.
- Put the acknowledgement, current impact, exact active identities, next action,
  and next update time in the operational-alert issue. During a P1, update at
  least every 60 minutes. Close only after the external monitor passes and link
  the recovery receipt.

There is no standing excluded maintenance period. Announce planned maintenance
in the operational issue at least 24 hours in advance, name its start/end and
rollback owner, and keep monitoring enabled. Label measurements from that period
as planned maintenance; do not delete them or silently remove them from the
30-day history.

## Safe alert and evidence payloads

An alert names only the service, exact image digest, active content revision,
content fingerprint, failed check, remediation, and immutable workflow receipt.
Those identifiers are sufficient to select a rollback target. Alerts and
receipts must not contain request authorization, registry credentials, refresh
tokens, cookies, environment dumps, response bodies, repository credentials, or
authored search/query text. The monitor uses fixed repository-owned paths and
never copies a query response into an issue.

Retention and cost are bounded by four scheduled runs per day, a ten-minute job
timeout, 30-day evidence retention, and one deduplicated open alert. A failed
run opens an alert immediately, but an operator does not roll back on one
uncorroborated network timeout: rerun the workflow, compare Railway and provider
status, and mark a recovered one-off as a monitor false positive. Identity,
freshness, and deterministic artifact mismatches do not need a second failure
before recovery begins.

## Identity and diagnosis

Use the public contracts and control plane; do not begin with SSH:

```console
curl --fail --silent "$ORIGIN/healthz"
curl --fail --silent "$ORIGIN/readyz"
curl --fail --silent "$ORIGIN/catalog/freshness.json"
curl --fail --silent "$ORIGIN/meta.json"
curl --fail --silent "$ORIGIN/_fura/content/status"
railway deployment list --service furatena --environment production --json
railway logs --service furatena --environment production --json
```

`/meta.json` joins the private-image digest, commercial image version/channel,
source commit, server dependency versions, active content generation and Git
commit, and frozen fingerprint. The content status route never returns refresh
tokens or registry credentials.

For every incident, preserve the failed monitor receipt, alert timestamps,
Railway deployment ID/state, relevant redacted logs and metrics, active and
expected identities, recovery action, and first passing recovery receipt.

## Failure-specific runbook

| Failure | Diagnose without SSH | Recovery | Required recovery proof |
| --- | --- | --- | --- |
| Registry outage | Check GHCR status, Railway pull/deployment events, and whether the current replica is still serving its exact digest. | Keep a healthy current replica running. Retry the same digest after registry recovery; if a bad rollout is involved, select the prior promoted digest. | Railway deployment event plus `/meta.json` showing the selected digest and a passing monitor. |
| Bad image | Correlate the first failed run with the deployment ID, digest, readiness, logs, and metrics. | In Railway, select the prior stable digest from its GitHub release record and redeploy that digest; never rebuild the old commit. | Before/after deployment IDs, old/new exact digests, readiness, and a passing monitor. |
| Bad content | Compare `/meta.json`, content status, the last two generation receipts, and the failing public surface. | Call the authenticated content rollback endpoint, which atomically selects `last-known-good`, then wait for restart/readiness. | Before/after generation IDs and resolved refs, unchanged image digest, and a passing artifact/SLO receipt. |
| Failed refresh | Preserve the HTTP failure, content diagnostic, active selector, and Railway logs. A failed generation must not become active. | Correct the source/ref/subdirectory or upstream access and retry. If service behavior changed, select `last-known-good`; if no good generation exists, restore valid configuration and redeploy. | Failed refresh receipt, proof the prior selector remained active, and the successful replacement receipt. |
| Stale or corrupt artifact | Inspect `/catalog/freshness.json`, `/catalog/artifacts.json`, and the exact verifier failure. | Refresh from the intended content revision. If it does not reproduce complete artifacts, roll back content. | Expected revision/fingerprint, complete artifact counts, and passing freshness/integrity gates. |
| Domain or TLS failure | Compare the Railway-provided origin with the custom domain, then inspect Railway domain state, certificate state, DNS records, and provider status. | Correct DNS/domain configuration or wait for certificate/provider recovery. Do not change the image or content when the Railway origin is healthy. | Both origins' probe results, DNS/TLS change record, and a passing external-domain receipt. |
| Railway degradation | Check Railway status, deployment state, logs, metrics, volume attachment, and restarts across unchanged identities. | Freeze application/content changes, preserve the healthy generation, follow Railway recovery guidance, and escalate to Railway with deployment IDs. Avoid repeated speculative deploys. | Platform incident link or support receipt, stable identities, and the first passing external probe. |

## Independent rollback procedures

The refresh credential is independent of the read-only GHCR credential. Store
it only as `FURA_CONTENT_REFRESH_TOKEN`; never put it in a URL or log.

Content rollback does not change the image:

```console
curl --fail --request POST \
  --header "Authorization: Bearer $FURA_CONTENT_REFRESH_TOKEN" \
  "$ORIGIN/_fura/content/rollback"
```

Wait for `/readyz`, confirm the expected content generation and unchanged image
digest in `/meta.json`, then run both verification scripts. Image rollback is a
separate Railway control-plane action: select the prior stable digest from its
durable release record, deploy it, confirm that the content generation did not
change, and rerun both gates. See [RELEASING.md](RELEASING.md) for digest
promotion and revocation.

## Non-production recovery drill

Run both injections against a disposable Railway environment with the scheduled
workflow manually dispatched using its HTTPS origin. Never inject them into the
official production service.

1. Record the healthy deployment ID, exact image digest, active content
   generation/revision/fingerprint, and a passing baseline receipt.
2. For the application failure, use a staging-only Railway start-command
   override that exits nonzero and redeploy the same image digest. Preserve the
   readiness alert and logs, remove the override, redeploy the previously
   recorded digest, and preserve the first passing receipt.
3. Establish a healthy `last-known-good` content generation. Activate a
   staging-only content revision whose home template intentionally returns a
   non-HTML body while still passing the content build. Preserve the
   representative-HTML alert, call content rollback, and preserve the response,
   generation transition, unchanged image digest, and first passing receipt.
4. Repeat the content drill with an invalid revision that fails refresh and
   prove the active selector never moves. This is evidence for failed-refresh
   containment, not a substitute for the rollback drill in step 3.
5. Attach one timeline for each drill: injection time, detection time, alert
   time, acknowledgement, recovery command/control-plane action, readiness
   return, and external verification time. Redact all credentials and response
   bodies before attachment.

Shell access remains break-glass only. If it is ever used, record why the public
contracts and control plane were insufficient and track that missing capability
as a separate operational defect; do not count that run as no-SSH recovery
proof.
