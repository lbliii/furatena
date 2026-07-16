# Live SLOs and no-SSH operations

The mature Railway template is operated from public health contracts, immutable
release/content receipts, Railway's control plane, and GitHub Actions evidence.
An operator does not need a shell inside the running container.

## Service-level objectives

`config/live-slo.json` is the executable policy. The production objectives are:

| Indicator | 30-day objective | Point-in-time gate |
| --- | ---: | ---: |
| Successful public requests | 99.9% | every scheduled probe succeeds |
| `/readyz` p95 | at most 750 ms | at most 750 ms |
| Complete bulk-artifact verification | 100% | every scheduled verification succeeds |
| Runtime identity | exact private-image digest | required |
| Content identity | active generation + exact Git commit | required |

The monitor runs every five minutes. Each run samples readiness and the home
page, checks `/meta.json`, downloads and validates the complete catalog/search/
semantic/agent artifact set, and retains a JSON receipt for 90 days. A failed
run opens or updates one GitHub operational-alert issue; a later passing run
closes it with recovery evidence. GitHub's scheduler is the initial external
probe, so the 30-day calculation should be moved to a dedicated uptime vendor
before a contractual SLA is offered.

Run the same gate locally or from an incident workstation:

```console
python scripts/check_live_slo.py --output /tmp/furatena-slo.json
python scripts/verify-live-artifacts.py https://furatena-production.up.railway.app
```

## Identity and status

These calls identify the selected code and content without container access:

```console
curl --fail --silent https://furatena-production.up.railway.app/healthz
curl --fail --silent https://furatena-production.up.railway.app/readyz
curl --fail --silent https://furatena-production.up.railway.app/meta.json
curl --fail --silent https://furatena-production.up.railway.app/_fura/content/status
railway deployment list --service furatena --environment production --json
railway logs --service furatena --environment production --json
```

`/meta.json` must join the private-image digest, commercial image version and
channel, source commit, dependency versions, active content generation and Git
commit, and frozen fingerprint. The content status route never returns refresh
tokens or registry credentials.

## Refresh and rollback

The refresh credential is independent of the read-only GHCR credential. Store
it in Railway as `FURA_CONTENT_REFRESH_TOKEN`; do not pass it in URLs or logs.

```console
curl --fail --request POST \
  --header "Authorization: Bearer $FURA_CONTENT_REFRESH_TOKEN" \
  https://furatena-production.up.railway.app/_fura/content/refresh
```

The operation fetches the configured public repository/ref, stages and bounds
the checkout, validates and freezes it, atomically updates the active selector,
writes a receipt, and schedules a same-image restart. Readiness stays on the old
generation until the selector changes; a failed generation is never selected.

To roll back content:

```console
curl --fail --request POST \
  --header "Authorization: Bearer $FURA_CONTENT_REFRESH_TOKEN" \
  https://furatena-production.up.railway.app/_fura/content/rollback
```

Then wait for `/readyz`, confirm the expected content generation in
`/meta.json`, and run both verification scripts. Image rollback is separately
performed by changing the Railway image source to the prior stable digest from
its GitHub release record; see [RELEASING.md](RELEASING.md).

## Incident sequence

1. Acknowledge the operational-alert issue and link the failed workflow run.
2. Check `/healthz`, `/readyz`, `/meta.json`, content status, Railway deployment
   state, logs, and metrics. Do not begin with SSH.
3. Classify the fault as control plane, image, content, credential, upstream
   Git, or application behavior.
4. For bad content, call content rollback. For a bad image, select the prior
   digest. For credential exposure, rotate the relevant credential; image and
   refresh credentials have separate blast radii.
5. Wait for readiness and run the SLO plus artifact gates.
6. Preserve the image release record, content receipt, deployment ID, logs,
   metrics, monitor receipt, and recovery timestamps in the issue.
7. Close the alert only after a scheduled external probe also passes.

Shell access is a break-glass diagnostic, not an ordinary operational
dependency. If it is ever used, record why the public contracts and control
plane were insufficient and turn that gap into a tracked improvement.
