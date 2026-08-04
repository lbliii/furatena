# Issue 436 runtime-refresh control-plane profile

Profiled on CPython 3.14.2t with `PYTHON_GIL=0` on macOS 26.5.2 arm64. The
synthetic corpus contained one durable `restart_scheduled` operation receipt on
a temporary local filesystem. Five samples used `time.perf_counter`; each sample
performed 10,000 request parses, 100 duplicate replays, or 1,000 latest-status
reads.

| Control-plane operation | Median | Samples (ms per operation) |
| --- | ---: | --- |
| Parse and validate a v1 request | 0.00134 ms | 0.00129, 0.00133, 0.00136, 0.00134, 0.00139 |
| Replay a duplicate under the durable submission lease | 8.91 ms | 6.44, 12.66, 8.91, 10.11, 6.47 |
| Read the latest durable status | 0.092 ms | 0.092, 0.086, 0.092, 0.096, 0.101 |

The profile intentionally excludes Git/network time, source validation, and the
freeze because those costs depend on repository and corpus size; their bounded
300-second fetch timeout, file/byte limits, and pre-promotion correctness gates
are tested separately. The performance contract is structural: request parsing
is bounded by the five-field v1 schema, public status returns one sanitized
operation, duplicate delivery performs no checkout or freeze, and concurrent
distinct work fails before staging while one operation is pending.

These wall-clock values are informational across machines. Re-profile on the
same filesystem when changing receipt scanning, lease acquisition, or request
validation; do not convert them into portable CI thresholds.
