---
title: Observability and incident recovery
owner: security-operations
reviewed_at: "2026-07-07"
description: Structured events, optional OpenTelemetry, rollout, rollback, backup, and recovery.
weight: 46
lang: en
type: doc
category: operations
tags: [observability, telemetry, incidents, backup, recovery]
---

# Observability and incident recovery

Furatena's canonical observability contract is a privacy-safe structured event
envelope. JSON logs work without a telemetry service. The optional OpenTelemetry
adapter projects the same envelope into spans and the
`furatena.operational.events` counter, so adding or replacing a vendor does not
change event identity or runbooks.

## Enable signals

Structured logs are opt-in:

```bash
export FURA_STRUCTURED_LOGS=1
uv run fura serve --preview
```

To also emit OpenTelemetry spans and metrics, install and configure your chosen
OpenTelemetry SDK/exporter alongside the API, then enable the adapter:

```bash
python -m pip install opentelemetry-api opentelemetry-sdk
export FURA_STRUCTURED_LOGS=1
export FURA_TELEMETRY=opentelemetry
uv run fura serve --preview
```

`FURA_TELEMETRY` accepts `none` (default) or `opentelemetry`. Requesting
OpenTelemetry without its API fails during startup instead of silently dropping
telemetry. Furatena does not select an exporter, endpoint, sampler, or vendor;
configure those through the installed SDK and its standard environment controls.

## Stable event envelope

Every event contains `schema_version`, `event_name`, `event_id`,
`correlation_id`, UTC `timestamp`, `severity`, `status`, and `attributes`.
Secrets and authored/query content are redacted before either logs or telemetry
receive the event. One operational-status observation uses one correlation id
across its health, readiness, freshness, and artifact events.

| Event name | Trigger or intended producer |
|---|---|
| `furatena.service.health` | Process-health observation |
| `furatena.service.readiness` | Safe-to-serve readiness observation |
| `furatena.content.freshness` | Source-to-artifact freshness observation |
| `furatena.artifact.status` | Freeze/export inventory and age observation |
| `furatena.source.sync` | Source-provider sync lifecycle |
| `furatena.index.status` | Mount index lifecycle |
| `furatena.freeze.completed` | Freeze completion |
| `furatena.export.completed` | Static export completion |
| `furatena.incident.recovery` | Operator-recorded recovery milestone |

Do not derive alert identity from message text. Alert on `event_name`, `status`,
and stable attributes. Search by `correlation_id` to join logs, spans, counters,
audit events, and one rollout or incident.

## Rollout

1. Record the current `/healthz`, `/readyz`, and
   `/catalog/operational-status.json` payloads as a baseline.
2. Enable `FURA_STRUCTURED_LOGS=1` on one canary. Verify event names, correlation
   ids, redaction, log volume, and that readiness behavior is unchanged.
3. Add alerts for repeated `not_ready`, `degraded`, or `stale` status. Route the
   payload's remediation to the owning source, index, or deployment team.
4. Configure an OpenTelemetry SDK/exporter in the canary environment, set
   `FURA_TELEMETRY=opentelemetry`, and verify spans plus the event counter arrive.
5. Roll out gradually. At each step require `/readyz` HTTP 200, current freeze
   and export ages, no new private-data findings, and bounded telemetry volume.

## Rollback

Observability is not on the content-serving data path unless explicitly enabled.
To remove telemetry while preserving service:

1. Set `FURA_TELEMETRY=none` and restart the affected workers.
2. If JSON log volume is contributing to the incident, set
   `FURA_STRUCTURED_LOGS=0` and restart.
3. Verify `/healthz` and `/readyz`, then compare the operational-status payload
   with the pre-rollout baseline.
4. If the application release itself is unhealthy, deploy the prior release and
   restore the last verified configuration. Do not mark the rollout recovered
   until readiness and artifact freshness both pass.

Disabling telemetry loses only observations; it does not mutate content, frozen
artifacts, exports, audit events, or rate-limit counters.

## Backup and restore

Treat the Git repository and external source providers as the source-of-truth
backup for authored content and configuration. `app/frozen/` and `app/public/`
are reproducible artifacts: retain a verified copy for fast rollback, but rebuild
them with `fura freeze` and `fura export --fresh` after source recovery.

For governed deployments, back up these durable runtime stores:

- copy audit JSONL while writers are stopped, or use the audit export snapshot;
- use SQLite's online backup mechanism for the shared rate-limit database rather
  than copying an active database and its WAL independently;
- preserve deployment configuration and secret *references*, never secret values
  in repository or artifact backups;
- record backup time, source revision, Furatena version, retention, encryption,
  restore test, recovery-point objective, and recovery-time objective.

Test restores in an isolated environment. A backup is not verified until the
restored app passes `/readyz`, its audit JSONL parses, rate-limit counters can be
read transactionally, and regenerated artifacts match the intended source ref.

## Incident recovery

1. Query `/healthz`. If it fails, restore the process/runtime before inspecting
   content. If it passes, do not assume the service is ready.
2. Query `/readyz`. Use failed check ids to distinguish `source:{mount}`,
   `index:{mount}`, and `freeze:required` failures.
3. Query `/catalog/operational-status.json`; compare source, freeze, and export
   timestamps and follow its remediation.
4. Search structured logs, traces, audit storage, and deployment records with the
   same correlation id. Confirm redaction before sharing evidence.
5. For source failure, restore credentials/network access and resync. For index
   failure, rebuild the live shard or restore a verified frozen shard. For stale
   freeze/export, run `fura freeze` then `fura export --fresh`.
6. If audit persistence is unavailable, preserve fail-closed controls and restore
   the store. If shared rate limiting is unavailable, keep the default deny
   fallback until atomic counters recover; do not switch to memory fallback on a
   multi-worker public service merely to clear readiness.
7. Verify `/readyz` returns 200, freshness returns `fresh`, artifact ages advance,
   and protected content remains excluded. Record a
   `furatena.incident.recovery` event or equivalent incident milestone.
8. Rotate exposed credentials, preserve evidence according to retention policy,
   document root cause and prevention, and run a post-incident restore test.

Never delete audit evidence, reset rate-limit state, or overwrite the active
source during diagnosis. Prefer a reversible artifact rollback and an isolated
restore over in-place repair with uncertain provenance.
