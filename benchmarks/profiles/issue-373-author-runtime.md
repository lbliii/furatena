# Issues 373/374 author-runtime profile

Both profiles used the v1 author-runtime schema, a deterministic 12-page/1-mount
corpus, three samples per repeated operation, one worker, autodoc disabled, CPython
3.14.2t, and `PYTHON_GIL=0` on the same machine.

- `issue-373-before.json` records the request-time `check_catalog()` behavior before
  generation-scoped snapshots.
- `issue-374-after.json` records the snapshot service and built Kida environment reuse.

Median results:

| Operation | Before | After | Structural result |
| --- | ---: | ---: | --- |
| Cold startup contracts | 1504.2 ms | 1123.9 ms | secondary apps 1 → 0 |
| Warm startup contracts | 971.3 ms | 574.5 ms | validation runs 1 → 0 |
| Cold author page | 413.1 ms | 66.2 ms | apps/checks 1/1 → 0/0 |
| Warm author page | 495.3 ms | 25.9 ms | apps/checks 1/1 → 0/0 |
| Cold status JSON | 380.6 ms | 8.1 ms | apps/checks 1/1 → 0/0 |
| Warm status JSON | 324.1 ms | 7.5 ms | apps/checks 1/1 → 0/0 |
| Explicit validation | 316.6 ms warm | 32.7 ms warm | validation retained; secondary apps 1 → 0 |

Wall-clock values remain informational across machines. The proposed fast-CI gate is
structural: an unchanged ordinary author page or status request must perform zero full
validation runs and construct zero secondary `DocsApp` instances. For same-machine
profiling, investigate when warm status exceeds 25% of its checked-in before median or
warm page rendering exceeds 50%; these are diagnostic relative budgets, not portable
hard failures.
