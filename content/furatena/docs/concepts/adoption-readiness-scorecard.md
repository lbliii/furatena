---
title: Beta adoption-readiness scorecard
description: Reproducible go/no-go gates across activation, migration, builds, retrieval, agents, and buyer confidence.
draft: false
weight: 75
lang: en
type: doc
tags: [adoption, scorecard, metrics, beta]
category: concepts
---

# Beta adoption-readiness scorecard

Current decision on **2026-07-07: NO-GO**. The measurement protocol and fixed
policy exist, but representative pilot evidence is not complete. This is an
evidence gap, not a free-threading limitation: scorecard evaluation and its
concurrency tests run on CPython 3.14t with `PYTHON_GIL=0`.

The repository publishes the exact input at `docs/adoption-evidence-v1.json`
and its generated decision at `docs/adoption-scorecard-v1.json`. A test
regenerates the report and rejects drift between those artifacts.

## Fixed gates

Policy `furatena-beta-adoption-readiness` version `1.0.0` contains 16 gates:

| Area | Gates |
|------|-------|
| Activation | New-site first-edit median at most 600 seconds; imported-site first-edit median at most 1,800 seconds; at least one first-publish and clean-migration sample |
| Migration | At least 90% of indexed pages clean after the first automated pass; remaining blockers grouped by owner and source |
| Build | Check P95 under 300 seconds; static-export P95 under 600 seconds; representative PDF batch documented |
| Retrieval | Known-answer top-3 recall at least 80%; zero private leaks |
| Agent | Zero errors; zero private leaks; every stale or safety warning has explicit remediation |
| Buyer confidence | One supported deployment profile selected; remaining approval blockers named |

The supported profile identifiers are `local-author`, `static-pages`,
`cloud-live`, and `self-hosted-enterprise`. Thresholds come from
[[docs/concepts/adoption-research|Adoption research]]; an evidence manifest
cannot override them.

## Current unmet evidence

| Area | Owner | Required remediation before GO |
|------|-------|--------------------------------|
| Activation | `docs-product` | Run opted-in new-site and imported-site pilots through first publish and clean migration. |
| Migration | `platform-docs` | Complete representative MDX, Sphinx/MyST, OpenAPI, and multi-mount pilots; record clean-page ratios and grouped blockers. |
| Build | `release-engineering` | Capture representative check, static-export, and PDF batch distributions. |
| Retrieval | `search-platform` | Publish representative known-answer results and confirm zero public/private boundary leaks. |
| Agent | `agent-platform` | Publish representative MCP eval results with zero errors/leaks and actionable warnings. |
| Buyer confidence | `product` | Record a selected deployment profile and named approval blockers for each pilot. |

## Evidence manifest

The input is strict JSON with `schema_version: 1`, an ISO `decision_date`, and
exactly six evidence objects. Each object contains non-empty `owner` and
`remediation` strings plus its measured fields. Use `null` when a measurement
is unavailable; that gate remains unmet instead of being silently omitted.

```json
{
  "schema_version": 1,
  "decision_date": "2026-07-07",
  "evidence": {
    "activation": {
      "owner": "docs-product",
      "remediation": "Run the remaining opted-in pilot sessions.",
      "new_site_first_edit_median_seconds": null,
      "imported_site_first_edit_median_seconds": null,
      "first_publish_sample_count": null,
      "clean_migration_sample_count": null
    },
    "migration": {
      "owner": "platform-docs",
      "remediation": "Complete representative migration pilots.",
      "clean_page_ratio": null,
      "blockers_grouped_by_owner_source": null
    },
    "build": {
      "owner": "release-engineering",
      "remediation": "Measure representative build distributions.",
      "check_p95_seconds": null,
      "static_export_p95_seconds": null,
      "pdf_batch_documented": null
    },
    "retrieval": {
      "owner": "search-platform",
      "remediation": "Publish representative known-answer evidence.",
      "recall_at_3": null,
      "private_leaks": null
    },
    "agent": {
      "owner": "agent-platform",
      "remediation": "Publish representative MCP evaluation evidence.",
      "error_count": null,
      "private_leaks": null,
      "warnings_with_remediation": null
    },
    "buyer_confidence": {
      "owner": "product",
      "remediation": "Record profile selection and approval blockers.",
      "deployment_profile": null,
      "approval_blockers_named": null
    }
  }
}
```

Run the decision locally or in CI:

```bash
fura scorecard \
  --input adoption-evidence.json \
  --output adoption-scorecard.json \
  --json
```

The output includes every gate, every unmet gate with owner and remediation,
the decision date, and a canonical evidence SHA-256. `GO` exits successfully;
`NO-GO` writes the complete report and exits with validation code `2`. Each
unmet gate also emits a stable `fura.scorecard.*` diagnostic whose suffix is
the gate identifier.
