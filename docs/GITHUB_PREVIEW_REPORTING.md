# GitHub preview conformance reporting

Furatena reports one lifecycle check and one marker-addressable pull-request
comment per preview head SHA. Provider automation sends the portable state
(`queued`, `building`, `ready`, `failed`, or `removed`); the reporter owns GitHub
presentation and cross-surface verification.

The workflow in `.github/workflows/preview-report.yml` accepts trusted
`repository_dispatch` callbacks with event type `furatena-preview`, and also has
a manual dispatch for recovery. Opening or synchronizing an internal,
non-bot pull request publishes `queued`; closing it publishes `removed`. The
workflow always checks out the default branch under `pull_request_target`, so
untrusted PR code is never executed with write permissions.

For a ready callback, provide the PR number, full current head SHA, preview
origin, and optional provider diagnostics URL. Configure the reviewer token as
the sealed Actions secret `FURA_PREVIEW_AUTH_TOKEN`. The token is sent only to
the protected preview and never appears in the check, comment, JSON output, or
logs.

Before success, `scripts/preview_report.py` verifies:

- `/readyz` returns ready;
- `/preview-manifest.json` reports the current PR head and matching artifact SHA;
- HTML and `Accept: text/markdown` representations;
- the `.md` alias and `llms.txt`;
- catalog, catalog query, search, and metadata JSON surfaces.

The PR comment leads with review links, immutable build/freeze identity, and
concise results. Provider IDs stay in an expandable diagnostic block. Updates
reuse the `<!-- furatena-preview -->` marker and a stable check `external_id`, so
pushes and retries do not create comment spam. A newer head gets a new check;
the comment is replaced and never continues presenting an old URL as current.

Example provider callback:

```json
{
  "event_type": "furatena-preview",
  "client_payload": {
    "pr_number": 123,
    "state": "ready",
    "expected_sha": "0123456789abcdef0123456789abcdef01234567",
    "preview_url": "https://preview.example.test",
    "details_url": "https://provider.example.test/deployments/abc"
  }
}
```

On failure, send `state=failed`; the comment remains the single remediation
location. On teardown, send `state=removed` (the close event is a fallback),
which removes active review links from the comment and completes the check
neutrally.
