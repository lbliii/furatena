# Issue 436 runtime-refresh control-plane profile

Profiled on CPython 3.14.2t with `PYTHON_GIL=0` on macOS 26.5.2 arm64. The
synthetic corpus contained one durable `restart_scheduled` operation receipt on
a temporary local filesystem. Status used a separate, uninitialized deployment
store on that filesystem; the contended case held its renewable
`content-refresh` lease while probing. Five samples used `time.perf_counter`;
each sample performed 10,000 request parses, 100 duplicate replays, 1,000
latest-receipt reads, or 20 deployment-status snapshots.

| Control-plane operation | Median | Samples (ms per operation) |
| --- | ---: | --- |
| Parse and validate a v1 request | 0.00146 ms | 0.00159, 0.00146, 0.00134, 0.00146, 0.00151 |
| Replay a duplicate under the durable submission lease | 10.83 ms | 9.63, 8.17, 10.83, 11.53, 19.47 |
| Read the latest durable receipt | 0.133 ms | 0.119, 0.171, 0.133, 0.161, 0.110 |
| Read deployment status without contention | 10.86 ms | 8.35, 13.79, 8.37, 12.86, 10.86 |
| Read deployment status while refresh ownership is held | 12.20 ms | 12.33, 12.20, 12.39, 12.13, 12.15 |

The profile intentionally excludes Git/network time, source validation, and the
freeze because those costs depend on repository and corpus size; their bounded
300-second fetch timeout, file/byte limits, and pre-promotion correctness gates
are tested separately. The performance contract is structural: request parsing
is bounded by the five-field v1 schema, public status returns one sanitized
operation, duplicate delivery performs no checkout or freeze, and concurrent
distinct work fails before staging while one operation is pending.

These wall-clock values are informational across machines. Re-profile on the
same filesystem when changing receipt scanning, lease acquisition, or request
validation. Contended status includes the bounded 10 ms reconciliation attempt
before its read-only `staging` snapshot; do not convert these values into
portable CI thresholds.
